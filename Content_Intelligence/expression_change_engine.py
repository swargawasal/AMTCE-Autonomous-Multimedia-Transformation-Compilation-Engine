"""
Content_Intelligence/expression_change_engine.py
------------------------------------------------
Expression Change Detector — Reaction-Style Video Signal Engine.

Detects facial expression changes for reaction-style videos where traditional
motion/emotion signals may be weak. Uses face embedding deltas to identify
moments when a subject's expression changes significantly.

Algorithm:
    1. Extract face embeddings per frame (using face detection from subject_tracking)
    2. Compute delta: delta = cosine_distance(embedding[t], embedding[t-1])
    3. If delta > adaptive_threshold: mark moment
    
    Adaptive threshold: percentile(delta_series, 85)

Pipeline position: Step 1e.5 (after Moment Miner, before Retention Curve)

Inputs (read from profile_data):
    video_path         — path to the video file (for frame extraction)
    subject_tracking   — face tracking data with bbox per frame
    duration           — float, seconds

Outputs:
    profile_data["expression_moments"]      — list of expression change moments
    profile_data["expression_summary"]      — change_count / strongest_change / change_times
    expression_change_debug.json            — full debug export (written to job_dir)

Expected expression moment shape:
    {
        "time": float,              # moment timestamp
        "expression_score": float,  # 0.0-1.0 delta score
        "change_type": str,         # "expression_shift" | "face_reaction"
        "embedding_delta": float    # raw cosine distance
    }

Expected log output:
    [EXPRESSION_ENGINE] changes_detected=N
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("expression_change_engine")

# ── Tuning constants ───────────────────────────────────────────────────────────
EMBEDDING_SIZE = 32  # Face ROI resize dimension — 32x32 is sufficient for cosine delta
MIN_FACE_AREA = 1000  # Minimum face area in pixels to process
ADAPTIVE_PERCENTILE = 85  # Percentile for adaptive threshold
MIN_CHANGE_GAP = 1.0  # Minimum seconds between reported changes
MIN_EXPRESSION_SCORE = 0.3  # Minimum normalized score to qualify
FRAME_STRIDE = 4          # Process every Nth face frame (reduces 1680 → ~420 embeddings)

# Safe empty returns
DEFAULT_MOMENTS: List[Dict] = []
DEFAULT_RESULT: Dict = {
    "expression_moments": [],
    "expression_summary": {"change_count": 0, "strongest_change": 0.0, "change_times": []},
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Face embedding extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_face_embedding(frame: np.ndarray, bbox: List[int]) -> Optional[np.ndarray]:
    """
    Extract a normalized face embedding from frame region defined by bbox.
    
    Algorithm:
        1. Extract ROI from frame using bbox [x, y, w, h]
        2. Resize to standard EMBEDDING_SIZE x EMBEDDING_SIZE
        3. Convert to grayscale
        4. Flatten and L2-normalize to create embedding vector
    
    Returns:
        Normalized embedding vector (float32) or None if extraction fails.
    """
    try:
        if len(bbox) < 4:
            return None
        
        x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        
        # Filter out too-small faces
        if w * h < MIN_FACE_AREA:
            return None
        
        # Ensure bbox is within frame bounds
        frame_h, frame_w = frame.shape[:2]
        x = max(0, min(x, frame_w - 1))
        y = max(0, min(y, frame_h - 1))
        w = min(w, frame_w - x)
        h = min(h, frame_h - y)
        
        if w < 10 or h < 10:
            return None
        
        # Extract ROI
        face_roi = frame[y:y+h, x:x+w]
        if face_roi.size == 0:
            return None
        
        # Resize to standard size
        face_resized = cv2.resize(face_roi, (EMBEDDING_SIZE, EMBEDDING_SIZE))
        
        # Convert to grayscale if color
        if len(face_resized.shape) == 3:
            face_gray = cv2.cvtColor(face_resized, cv2.COLOR_BGR2GRAY)
        else:
            face_gray = face_resized
        
        # Apply mild smoothing to reduce noise
        face_smooth = cv2.GaussianBlur(face_gray, (3, 3), 0)
        
        # Flatten to create embedding
        embedding = face_smooth.flatten().astype(np.float32)
        
        # L2 normalize for cosine distance compatibility
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        
        return embedding
        
    except Exception as e:
        logger.debug(f"Face embedding extraction failed: {e}")
        return None


def _compute_cosine_distance(emb1: np.ndarray, emb2: np.ndarray) -> float:
    """
    Compute cosine distance between two normalized embeddings.
    
    cosine_distance = 1 - dot_product (since embeddings are L2 normalized)
    
    Returns:
        Distance in range [0.0, 2.0] where:
        - 0.0 = identical embeddings (same expression)
        - 1.0 = orthogonal (uncorrelated)
        - 2.0 = opposite (rare for normalized embeddings)
    """
    try:
        # Both embeddings are L2 normalized, so cosine similarity = dot product
        cosine_similarity = np.dot(emb1, emb2)
        # Clamp to valid range for numerical stability
        cosine_similarity = np.clip(cosine_similarity, -1.0, 1.0)
        # Convert to distance: 0 = same, 2 = opposite
        cosine_distance = 1.0 - cosine_similarity
        return float(cosine_distance)
    except Exception as e:
        logger.debug(f"Cosine distance computation failed: {e}")
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Timeline construction
# ═══════════════════════════════════════════════════════════════════════════════

def _build_expression_timeline(
    video_path: str,
    face_data: List[Dict],
    duration: float,
) -> List[Dict]:
    """
    Build a timeline of expression change scores by analyzing face embeddings.

    OPTIMIZED: Single sequential video pass instead of per-frame random seeks.
    Random seeks on H.264/H.265 video force codec re-decode from the nearest
    I-frame, making 1680 seeks extremely CPU-heavy. This version:
      1. Applies FRAME_STRIDE to thin the face set (e.g. 1680 → 420 frames)
      2. Builds a dict {frame_idx → face_entry} sorted ascending
      3. Reads the video linearly, only processing frames that are in the dict
      4. Never calls cap.set(POS_FRAMES) — one continuous read.

    Returns:
        List of {"time": float, "embedding_delta": float, "frame": int}
    """
    timeline: List[Dict] = []

    if not face_data or len(face_data) < 2:
        logger.info("[EXPRESSION_ENGINE] Insufficient face data for expression analysis")
        return timeline

    if not os.path.exists(video_path):
        logger.warning(f"[EXPRESSION_ENGINE] Video not found: {video_path}")
        return timeline

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning(f"[EXPRESSION_ENGINE] Could not open video: {video_path}")
        return timeline

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # ── Apply stride to thin the face set ──────────────────────────────────────
    sorted_faces = sorted(
        face_data,
        key=lambda f: f.get("frame", int(f.get("time", 0.0) * fps))
    )
    strided_faces = sorted_faces[::FRAME_STRIDE]

    # Build lookup: frame_idx → face_entry (ascending order guaranteed)
    frame_lookup: Dict[int, Dict] = {}
    for entry in strided_faces:
        frame_idx = entry.get("frame")
        if frame_idx is None:
            frame_idx = int(entry.get("time", 0.0) * fps)
        frame_idx = int(frame_idx)
        if 0 <= frame_idx < total_frames:
            frame_lookup[frame_idx] = entry

    target_frames = sorted(frame_lookup.keys())  # ascending — no seeks needed

    if not target_frames:
        cap.release()
        return timeline

    logger.info(
        f"[EXPRESSION_ENGINE] Sequential pass: {len(strided_faces)} sampled frames "
        f"(stride={FRAME_STRIDE}, original={len(face_data)})"
    )

    # ── Single linear read pass ─────────────────────────────────────────────────
    prev_embedding: Optional[np.ndarray] = None
    processed_count = 0
    current_video_frame = 0
    target_iter = iter(target_frames)
    next_target = next(target_iter, None)

    while next_target is not None:
        # Skip frames until we reach the next target
        if current_video_frame < next_target:
            # Grab (decode + discard) is cheaper than seek for small gaps;
            # for large gaps (>150 frames) use a seek to avoid decoding many frames.
            gap = next_target - current_video_frame
            if gap > 150:
                cap.set(cv2.CAP_PROP_POS_FRAMES, next_target)
                current_video_frame = next_target
                continue # Re-check logic next loop

        ret, frame = cap.read()
        if not ret:
            break

        if current_video_frame == next_target:
            face_entry = frame_lookup[next_target]
            bbox = face_entry.get("bbox", [])
            embedding = _extract_face_embedding(frame, bbox)

            if embedding is not None:
                time_sec = face_entry.get("time", next_target / fps)
                if prev_embedding is not None:
                    delta = _compute_cosine_distance(prev_embedding, embedding)
                    timeline.append({
                        "time": round(time_sec, 3),
                        "embedding_delta": round(delta, 4),
                        "frame": next_target,
                    })
                    processed_count += 1
                prev_embedding = embedding

            next_target = next(target_iter, None)

        current_video_frame += 1

    cap.release()
    logger.info(f"[EXPRESSION_ENGINE] Computed {processed_count} embedding deltas")
    return timeline



def _compute_adaptive_threshold(delta_series: List[float]) -> float:
    """
    Compute adaptive threshold using percentile of delta series.
    
    threshold = percentile(delta_series, ADAPTIVE_PERCENTILE)
    
    Returns:
        Threshold value (float), defaults to MIN_EXPRESSION_SCORE if insufficient data.
    """
    if not delta_series or len(delta_series) < 2:
        return MIN_EXPRESSION_SCORE
    
    try:
        threshold = float(np.percentile(delta_series, ADAPTIVE_PERCENTILE))
        # Ensure minimum threshold
        threshold = max(threshold, MIN_EXPRESSION_SCORE)
        return threshold
    except Exception as e:
        logger.debug(f"Adaptive threshold computation failed: {e}")
        return MIN_EXPRESSION_SCORE


def _detect_expression_changes(
    timeline: List[Dict],
    min_gap: float = MIN_CHANGE_GAP,
) -> Tuple[List[Dict], float]:
    """
    Detect expression change moments above adaptive threshold.
    
    Algorithm:
        1. Extract delta series from timeline
        2. Compute adaptive_threshold = percentile(deltas, 85)
        3. Collect all points above threshold
        4. Apply min_gap filter to suppress nearby detections
    
    Returns:
        (expression_changes, threshold)
    """
    if not timeline:
        return [], 0.0
    
    deltas = [entry["embedding_delta"] for entry in timeline]
    threshold = _compute_adaptive_threshold(deltas)
    
    logger.info(f"[EXPRESSION_ENGINE] Adaptive threshold={threshold:.4f}")
    
    # Collect candidates above threshold
    candidates = [
        entry for entry in timeline
        if entry["embedding_delta"] >= threshold
    ]
    
    if not candidates:
        return [], threshold
    
    # Sort by delta descending
    candidates.sort(key=lambda x: x["embedding_delta"], reverse=True)
    
    # Apply minimum gap filter
    changes: List[Dict] = []
    used_times: List[float] = []
    
    for cand in candidates:
        t = cand["time"]
        if all(abs(t - used_t) >= min_gap for used_t in used_times):
            # Normalize delta to 0-1 score for consistency with other engines
            max_delta = max(deltas) if deltas else 1.0
            normalized_score = min(1.0, cand["embedding_delta"] / max(max_delta, 0.001))
            
            changes.append({
                "time": round(t, 3),
                "expression_score": round(normalized_score, 4),
                "change_type": "expression_shift",
                "embedding_delta": round(cand["embedding_delta"], 4),
            })
            used_times.append(t)
    
    # Sort by time for chronological output
    changes.sort(key=lambda x: x["time"])
    
    return changes, threshold


# ═══════════════════════════════════════════════════════════════════════════════
#  Main engine class
# ═══════════════════════════════════════════════════════════════════════════════

class ExpressionChangeDetector:
    """
    Detects facial expression changes using embedding cosine distance.
    
    Designed for reaction-style videos where expression changes are strong
    signals for highlight moments.
    
    Formula:
        delta = cosine_distance(embedding[t], embedding[t-1])
        threshold = percentile(delta_series, 85)
    """
    
    def detect(
        self,
        profile_data: Dict[str, Any],
        job_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Public entry point. Never raises — returns DEFAULT_RESULT on any error.
        
        Args:
            profile_data: Pipeline profile dict. Reads:
                            - video_path or input_path (for frame access)
                            - subject_tracking (face bbox data)
                            - duration (float, seconds)
            job_dir: Optional path where debug JSON will be written.
        
        Returns:
            {
                "expression_moments": list[dict],  # expression change moments
                "expression_summary": dict         # change_count / strongest_change / change_times
            }
        """
        try:
            return self._run(profile_data, job_dir)
        except Exception as exc:
            logger.warning(
                f"[EXPRESSION_ENGINE] detect() failed unexpectedly: {exc}. "
                "Returning safe defaults."
            )
            import traceback
            logger.debug(traceback.format_exc())
            return DEFAULT_RESULT.copy()
    
    def _run(
        self,
        profile_data: Dict[str, Any],
        job_dir: Optional[str],
    ) -> Dict[str, Any]:
        
        # ── 0. Resolve video path ────────────────────────────────────────────
        video_path = self._resolve_video_path(profile_data)
        if not video_path or not os.path.exists(video_path):
            logger.warning("[EXPRESSION_ENGINE] No valid video path found")
            return DEFAULT_RESULT.copy()
        
        # ── 1. Pull face tracking data ───────────────────────────────────────
        face_data: List[Dict] = profile_data.get("subject_tracking", [])
        if not face_data:
            logger.info("[EXPRESSION_ENGINE] No face tracking data available")
            return DEFAULT_RESULT.copy()
        
        # ── 2. Estimate duration ───────────────────────────────────────────
        duration = profile_data.get("duration", 0.0)
        if duration <= 0:
            # Estimate from face data
            if face_data:
                duration = max(f.get("time", 0.0) for f in face_data)
        
        logger.info(
            f"[EXPRESSION_ENGINE] Starting analysis — "
            f"video={os.path.basename(video_path)} | "
            f"duration={duration:.1f}s | "
            f"face_frames={len(face_data)}"
        )
        
        # ── 3. Build expression timeline ─────────────────────────────────────
        timeline = _build_expression_timeline(video_path, face_data, duration)
        
        if not timeline:
            logger.info("[EXPRESSION_ENGINE] No expression timeline generated")
            return DEFAULT_RESULT.copy()
        
        # ── 4. Detect expression changes ─────────────────────────────────────
        changes, threshold = _detect_expression_changes(timeline, min_gap=MIN_CHANGE_GAP)
        
        # ── 5. Build summary ─────────────────────────────────────────────────
        expression_summary = {
            "change_count": len(changes),
            "strongest_change": round(
                max((c["expression_score"] for c in changes), default=0.0), 4
            ),
            "change_times": [round(c["time"], 2) for c in changes],
            "adaptive_threshold": round(threshold, 4),
        }
        
        logger.info(
            f"[EXPRESSION_ENGINE] changes_detected={expression_summary['change_count']} | "
            f"threshold={threshold:.4f} | "
            f"strongest={expression_summary['strongest_change']:.4f}"
        )
        
        if changes:
            logger.info(
                f"[EXPRESSION_ENGINE] Change times: {expression_summary['change_times']}"
            )
        
        # ── 6. Export debug file ─────────────────────────────────────────────
        self._export_debug(
            timeline=timeline,
            changes=changes,
            threshold=threshold,
            summary=expression_summary,
            job_dir=job_dir,
        )
        
        # ── 7. Write back to profile_data ────────────────────────────────────
        profile_data["expression_moments"] = changes
        profile_data["expression_summary"] = expression_summary
        
        # ── 8. Add to candidate_moments for SignalFusionEngine ──────────────
        self._add_to_candidate_moments(profile_data, changes)
        
        return {
            "expression_moments": changes,
            "expression_summary": expression_summary,
        }
    
    def _resolve_video_path(self, profile_data: Dict[str, Any]) -> Optional[str]:
        """Resolve video path from profile_data."""
        # Try various path keys
        for key in ["video_path", "input_path", "clean_source", "current_video_source"]:
            path = profile_data.get(key)
            if path and isinstance(path, str) and os.path.exists(path):
                return path
        
        # Try context nested keys
        context = profile_data.get("context", {})
        if isinstance(context, dict):
            for key in ["video_path", "clean_source"]:
                path = context.get(key)
                if path and os.path.exists(path):
                    return path
        
        return None
    
    def _add_to_candidate_moments(
        self,
        profile_data: Dict[str, Any],
        expression_changes: List[Dict],
    ) -> None:
        """
        Add expression change moments to candidate_moments for SignalFusionEngine.
        
        Converts expression moments to candidate_moment format:
            {
                "time": float,
                "score": float,
                "type": "expression_change",
                ...
            }
        """
        if not expression_changes:
            return
        
        candidate_moments: List[Dict] = profile_data.get("candidate_moments", [])
        
        for change in expression_changes:
            candidate = {
                "time": change["time"],
                "score": change["expression_score"],
                "type": "expression_change",
                "face_present": True,
                "expression_delta": change["embedding_delta"],
                "intensity": change["expression_score"],
            }
            candidate_moments.append(candidate)
        
        profile_data["candidate_moments"] = candidate_moments
        logger.info(
            f"[EXPRESSION_ENGINE] Added {len(expression_changes)} moments to candidate_moments"
        )
    
    def _export_debug(
        self,
        timeline: List[Dict],
        changes: List[Dict],
        threshold: float,
        summary: Dict,
        job_dir: Optional[str],
    ) -> None:
        """Export full debug data to JSON."""
        if not job_dir:
            return
        
        try:
            debug_data = {
                "export_timestamp": datetime.now().isoformat(),
                "threshold": round(threshold, 4),
                "change_count": len(changes),
                "expression_summary": summary,
                "changes": changes,
                "timeline": timeline[:100],  # First 100 points for brevity
                "config": {
                    "embedding_size": EMBEDDING_SIZE,
                    "min_face_area": MIN_FACE_AREA,
                    "adaptive_percentile": ADAPTIVE_PERCENTILE,
                    "min_change_gap": MIN_CHANGE_GAP,
                    "min_expression_score": MIN_EXPRESSION_SCORE,
                },
            }
            
            debug_path = os.path.join(job_dir, "expression_change_debug.json")
            with open(debug_path, "w") as f:
                json.dump(debug_data, f, indent=2)
            
            logger.info(f"[EXPRESSION_ENGINE] Debug export: {debug_path}")
            
        except Exception as e:
            logger.warning(f"[EXPRESSION_ENGINE] Debug export failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════════

_engine_instance = None


def get_engine() -> ExpressionChangeDetector:
    """Singleton accessor for the engine."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = ExpressionChangeDetector()
    return _engine_instance


def detect_expression_changes(
    profile_data: Dict[str, Any],
    job_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function for orchestrator integration.
    
    Args:
        profile_data: Pipeline profile data
        job_dir: Optional job directory for debug export
    
    Returns:
        {
            "expression_moments": list[dict],
            "expression_summary": dict
        }
    """
    return get_engine().detect(profile_data, job_dir)
