"""
PHASE 4: Moment Miner Engine

Extracts micro-events across the entire timeline so the Creative Director can build
a new narrative rather than trimming a few scenes. Produces candidate_moments with
scoring based on motion, face presence, beat alignment, and scene changes.

The system produces 10-20 candidate moments per video.
"""

import json
import logging
import os
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("moment_miner")

# Target moment count range
MIN_CANDIDATES = 15
MAX_CANDIDATES = 30

# Duration context for each moment type
DURATION_HINTS = {
    "appearance": 2.5,
    "reaction": 1.8,
    "motion_peak": 2.0,
    "beat": 2.2,
    "dialogue": 3.5,
}


class MomentMiner:
    """
    Extracts high-value micro-moments from video analysis data.

    Scoring weights:
        - 0.35 * motion_spike
        - 0.25 * face_presence
        - 0.20 * beat_alignment
        - 0.20 * scene_change_proximity
    """

    def __init__(self, profile_data: Dict[str, Any]):
        self.profile = profile_data
        self.motion_data: List[Dict] = profile_data.get("motion_scores", [])
        self.face_data: List[Dict] = profile_data.get("subject_tracking", [])
        self.beat_data: Dict = profile_data.get("beat_data", {})
        self.shots: List[Dict] = profile_data.get("shots", [])
        self.duration: float = self._estimate_duration()

        # Scoring weights (as per spec)
        self.WEIGHT_MOTION = 0.35
        self.WEIGHT_FACE = 0.25
        self.WEIGHT_BEAT = 0.20
        self.WEIGHT_SCENE = 0.20

    def _estimate_duration(self) -> float:
        """Estimate video duration from available data."""
        if self.shots:
            return max(s.get("end", 0.0) for s in self.shots)
        if self.motion_data:
            return max(m.get("time", 0.0) for m in self.motion_data)
        if self.face_data:
            return max(f.get("time", 0.0) for f in self.face_data)
        return 30.0  # Default fallback

    def _normalize_motion_score(self, raw_score: float) -> float:
        """Normalize motion score to 0.0-1.0 range."""
        if not self.motion_data:
            return 0.0
        scores = [m.get("score", 0.0) for m in self.motion_data]
        max_score = max(scores) if scores else 1.0
        return min(1.0, raw_score / max_score) if max_score > 0 else 0.0

    def _detect_motion_peaks(self) -> List[Dict]:
        """
        Detect motion peaks using an adaptive percentile-based threshold.

        Algorithm:
          1. Sort motion data by time; build energy series from frame scores.
          2. Compute frame-to-frame motion spikes:
                 spike[t] = abs(energy[t] - energy[t-1])
          3. Adaptive threshold = 80th percentile of all spike values.
             This automatically scales to the clip's own motion distribution
             so it works equally well for dance, talking-head, reaction, etc.
          4. Candidate peak wherever spike[t] >= adaptive_threshold.
          5. Minimum-distance suppression (1.2 s): when two candidates are
             within that window, only the one with the larger spike is kept.

        Diagnostic logs emitted:
            [MOMENT_MINER] Motion spikes computed: N
            [MOMENT_MINER] Adaptive motion threshold=X.XXX
        The final "Motion peaks detected: N" is logged by run().
        """
        # ── Guard: need at least two data points to compute a delta ────────
        if not self.motion_data or len(self.motion_data) < 2:
            logger.info("[MOMENT_MINER] Motion spikes computed: 0")
            logger.info("[MOMENT_MINER] Adaptive motion threshold=0.000")
            return []

        # ── Step 1: Build time-sorted energy series ─────────────────────────
        sorted_motion = sorted(self.motion_data, key=lambda m: m.get("time", 0.0))
        times = [m.get("time", 0.0) for m in sorted_motion]
        energies = [float(m.get("score", 0.0)) for m in sorted_motion]

        # ── Step 2: Frame-to-frame motion spikes ────────────────────────────
        # spike[i] = abs(energy[i+1] - energy[i]), associated with times[i+1]
        motion_spikes = [
            abs(energies[i] - energies[i - 1]) for i in range(1, len(energies))
        ]

        logger.info(f"[MOMENT_MINER] Motion spikes computed: {len(motion_spikes)}")

        if not motion_spikes:
            logger.info("[MOMENT_MINER] Adaptive motion threshold=0.000")
            return []

        # ── Step 3: Adaptive threshold = 75th percentile of spike values ────
        try:
            import numpy as _np  # local import — avoids module-level unbound risk

            adaptive_threshold = float(_np.percentile(motion_spikes, 75))
        except ImportError:
            # Pure-Python linear-interpolation percentile (no external deps)
            sorted_spikes = sorted(motion_spikes)
            k = (len(sorted_spikes) - 1) * 0.75
            lo = int(k)
            hi = min(lo + 1, len(sorted_spikes) - 1)
            frac = k - lo
            adaptive_threshold = (
                sorted_spikes[lo] + (sorted_spikes[hi] - sorted_spikes[lo]) * frac
            )

        logger.info(
            f"[MOMENT_MINER] Adaptive motion threshold={adaptive_threshold:.3f}"
        )

        adaptive_threshold *= 0.6
        logger.info(
            f"[MOMENT_MINER] Boosted motion threshold={adaptive_threshold:.3f}"
        )

        # Fallback: if adaptive_threshold is too low, use minimum threshold
        if adaptive_threshold <= 0.001:
            adaptive_threshold = 0.05
            logger.info(
                f"[MOMENT_MINER] Using fallback motion threshold={adaptive_threshold:.3f}"
            )

        # Guard: if all spikes are effectively zero the clip is static —
        # selecting every frame as a "peak" would be meaningless.
        if adaptive_threshold < 1e-9:
            return []

        # ── Step 4: Collect candidate peaks above adaptive threshold ─────────
        # spike index i corresponds to the frame arriving at times[i+1].
        raw_candidates: List[Dict] = []
        for i, spike in enumerate(motion_spikes):
            if spike >= adaptive_threshold:
                raw_candidates.append(
                    {
                        "time": times[i + 1],
                        "spike": spike,
                    }
                )

        # Fallback: if no peaks detected, halve the threshold and retry
        if not raw_candidates:
            adaptive_threshold *= 0.5
            logger.info(
                f"[MOMENT_MINER] No peaks detected, halving threshold to {adaptive_threshold:.3f}"
            )
            logger.info("[MOMENT_MINER] motion_threshold_adjusted=True")
            for i, spike in enumerate(motion_spikes):
                if spike >= adaptive_threshold:
                    raw_candidates.append(
                        {
                            "time": times[i + 1],
                            "spike": spike,
                        }
                    )

        if not raw_candidates:
            return []

        # ── Step 5: Minimum-distance suppression (1.2 seconds) ──────────────
        # Greedy approach: process candidates strongest-first so that when two
        # peaks are within the exclusion window the larger spike wins.
        MIN_PEAK_DISTANCE_SEC = 1.2

        raw_candidates.sort(key=lambda x: -x["spike"])  # descending spike

        accepted: List[Dict] = []
        accepted_times: List[float] = []

        for cand in raw_candidates:
            t = cand["time"]
            too_close = any(
                abs(t - at) < MIN_PEAK_DISTANCE_SEC for at in accepted_times
            )
            if not too_close:
                accepted.append(cand)
                accepted_times.append(t)

        # Restore chronological order for downstream consumers
        accepted.sort(key=lambda x: x["time"])

        # ── Build output in the existing pipeline dict format ─────────────────
        # intensity is the spike value normalised to [0.0, 1.0] relative to the
        # strongest accepted peak so the field stays meaningful to _calculate_moment_score.
        max_spike = max(c["spike"] for c in accepted) if accepted else 1.0

        peaks: List[Dict] = []
        for cand in accepted:
            intensity = round(min(1.0, cand["spike"] / max(max_spike, 1e-9)), 3)
            peaks.append(
                {
                    "time": round(cand["time"], 3),
                    "intensity": intensity,
                    "type": "motion_peak",
                }
            )

        return peaks

    def _detect_face_moments(self) -> List[Dict]:
        """Detect moments with strong face presence.

        Handles TWO data formats:
        1. OpenCV/bbox format: {"time": t, "bbox": [x, y, w, h]}
        2. Gemini semantic format: {"time": t, "focus": "face", "focus_strength": 0.9}
        """
        moments = []
        if not self.face_data:
            return moments

        for face in self.face_data:
            t = float(face.get("time", face.get("timestamp", 0.0)))

            # --- FORMAT 1: Gemini semantic tracking (no bbox) ---
            focus = face.get("focus", face.get("subject", "")).lower()
            focus_strength = float(face.get("focus_strength", 0.0))

            if focus in ("face", "body", "person") and focus_strength >= 0.3:
                # Map focus_strength directly to intensity
                intensity = min(1.0, focus_strength)
                moments.append({
                    "time": t,
                    "intensity": intensity,
                    "type": "appearance",
                    "face_present": True,
                })
                continue

            # --- FORMAT 2: OpenCV bbox tracking ---
            bbox = face.get("bbox", [])
            if len(bbox) >= 4:
                face_area = bbox[2] * bbox[3]
                # Use actual frame dimensions if available, else assume 1080x1920
                frame_area = 1080 * 1920
                face_ratio = face_area / frame_area

                # Lowered threshold: >2% of frame (was 5%, too strict for wide shots)
                if face_ratio > 0.02:
                    moments.append({
                        "time": t,
                        "intensity": min(1.0, face_ratio * 10),
                        "type": "appearance",
                        "face_present": True,
                    })

        return moments

    def _detect_reaction_moments(self) -> List[Dict]:
        """
        Detect potential reaction moments.

        Handles TWO formats:
        1. Gemini format: fires on change_event=True with focus on face/body
        2. OpenCV bbox format: fires on rapid bbox position change (>50px)
        """
        reactions = []
        if not self.face_data or len(self.face_data) < 2:
            return reactions

        sorted_faces = sorted(self.face_data, key=lambda x: float(x.get("time", x.get("timestamp", 0.0))))

        for i in range(1, len(sorted_faces)):
            prev = sorted_faces[i - 1]
            curr = sorted_faces[i]
            t = float(curr.get("time", curr.get("timestamp", 0.0)))

            # --- FORMAT 1: Gemini semantic — change_event signals attention shift ---
            if curr.get("change_event", False):
                focus = curr.get("focus", curr.get("subject", "")).lower()
                focus_strength = float(curr.get("focus_strength", 0.5))
                if focus in ("face", "body", "person") or focus_strength >= 0.5:
                    reactions.append({
                        "time": t,
                        "intensity": min(1.0, focus_strength),
                        "type": "reaction",
                        "face_present": True,
                    })
                continue

            # --- FORMAT 2: OpenCV bbox — measure position delta ---
            prev_bbox = prev.get("bbox", [])
            curr_bbox = curr.get("bbox", [])

            if len(prev_bbox) >= 4 and len(curr_bbox) >= 4:
                prev_center = (prev_bbox[0] + prev_bbox[2] / 2, prev_bbox[1] + prev_bbox[3] / 2)
                curr_center = (curr_bbox[0] + curr_bbox[2] / 2, curr_bbox[1] + curr_bbox[3] / 2)
                distance = ((curr_center[0] - prev_center[0]) ** 2 + (curr_center[1] - prev_center[1]) ** 2) ** 0.5

                if distance > 50:
                    reactions.append({
                        "time": t,
                        "intensity": min(1.0, distance / 200),
                        "type": "reaction",
                        "face_present": True,
                    })

        return reactions

    def _detect_beat_moments(self) -> List[Dict]:
        """Detect beat-aligned moments."""
        moments = []
        beats = (
            self.beat_data.get("beats", []) if isinstance(self.beat_data, dict) else []
        )

        if not beats:
            return moments

        for beat_item in beats:
            beat_time = beat_item.get("time", 0.0) if isinstance(beat_item, dict) else float(beat_item)
            # Check if beat aligns with other signals
            motion_near = any(
                abs(m.get("time", 0.0) - beat_time) < 0.5 for m in self.motion_data
            )
            face_near = any(
                abs(f.get("time", 0.0) - beat_time) < 0.5 for f in self.face_data
            )

            # Score based on alignment with other signals
            alignment_score = 0.5
            if motion_near:
                alignment_score += 0.25
            if face_near:
                alignment_score += 0.25

            moments.append(
                {
                    "time": beat_time,
                    "intensity": alignment_score,
                    "type": "beat",
                    "beat_aligned": True,
                }
            )

        return moments

    def _detect_scene_boundary_moments(self) -> List[Dict]:
        """Detect moments near shot/scene boundaries."""
        moments = []
        if not self.shots:
            return moments

        for i, shot in enumerate(self.shots):
            # Start of shot is a potential moment
            start_time = shot.get("start", 0.0)

            # Check if start aligns with face or motion
            face_near = any(
                abs(f.get("time", 0.0) - start_time) < 0.3 for f in self.face_data
            )
            motion_near = any(
                abs(m.get("time", 0.0) - start_time) < 0.3 for m in self.motion_data
            )

            score_boost = 0.0
            if face_near:
                score_boost += 0.3
            if motion_near:
                score_boost += 0.3

            moments.append(
                {
                    "time": start_time,
                    "intensity": 0.4 + score_boost,  # Base 0.4 + boosts
                    "type": "appearance",
                    "face_present": face_near,
                }
            )

            # End of shot (for transition moments)
            if i < len(self.shots) - 1:
                end_time = shot.get("end", 0.0)
                moments.append(
                    {
                        "time": end_time,
                        "intensity": 0.3,  # Lower base for transitions
                        "type": "motion_peak",  # Often motion at cuts
                    }
                )

        return moments

    def _calculate_moment_score(self, moment: Dict) -> float:
        """
        Calculate final moment score using weighted formula:
        moment_score = 0.35 * motion_spike + 0.25 * face_presence + 0.20 * beat_alignment + 0.20 * scene_change
        """
        motion_spike = moment.get("motion_intensity", 0.0)
        if motion_spike == 0.0 and moment.get("type") == "motion_peak":
            motion_spike = moment.get("intensity", 0.0)

        face_presence = 1.0 if moment.get("face_present", False) else 0.0

        beat_aligned = 1.0 if moment.get("beat_aligned", False) else 0.0

        # Scene change proximity
        time = moment.get("time", 0.0)
        scene_proximity = 0.0
        for shot in self.shots:
            if (
                abs(shot.get("start", 0.0) - time) < 0.5
                or abs(shot.get("end", 0.0) - time) < 0.5
            ):
                scene_proximity = 1.0
                break

        # Weighted combination
        score = (
            self.WEIGHT_MOTION * motion_spike
            + self.WEIGHT_FACE * face_presence
            + self.WEIGHT_BEAT * beat_aligned
            + self.WEIGHT_SCENE * scene_proximity
        )

        # Boost for high-intensity moments
        intensity = moment.get("intensity", 0.0)
        if intensity > 0.8:
            score += 0.1

        return round(min(1.0, score), 4)

    def _deduplicate_moments(
        self, moments: List[Dict], min_gap: float = 1.5
    ) -> List[Dict]:
        """Remove duplicate moments that are too close together."""
        if not moments:
            return moments

        # Sort by score (highest first), then by time
        sorted_moments = sorted(
            moments, key=lambda x: (-x.get("score", 0.0), x.get("time", 0.0))
        )

        filtered = []
        used_times = []

        for moment in sorted_moments:
            time = moment.get("time", 0.0)

            # Check if this moment is far enough from already selected moments
            if all(abs(time - t) >= min_gap for t in used_times):
                filtered.append(moment)
                used_times.append(time)

        # Sort filtered by time for chronological view
        filtered.sort(key=lambda x: x.get("time", 0.0))

        return filtered

    def _limit_candidates(self, moments: List[Dict]) -> List[Dict]:
        """Limit candidates to target range (10-20)."""
        if len(moments) < MIN_CANDIDATES:
            logger.warning(
                f"[MOMENT_MINER] Low candidate count: {len(moments)} (target: {MIN_CANDIDATES}-{MAX_CANDIDATES})"
            )
            return moments

        if len(moments) > MAX_CANDIDATES:
            # Keep top N by score
            sorted_moments = sorted(
                moments, key=lambda x: x.get("score", 0.0), reverse=True
            )
            return sorted_moments[:MAX_CANDIDATES]

        return moments

    def _finalize_moment(self, moment: Dict) -> Dict:
        """Finalize moment format with all required fields."""
        moment_type = moment.get("type", "appearance")
        m_time = round(moment.get("time", 0.0), 3)
        m_motion = round(moment.get("intensity", 0.0), 3)

        # Faked emotion logic for stability
        pseudo_random = abs(math.sin(m_time * 1.7)) * 0.4
        emotion_proxy = round((0.5 * m_motion) + (0.3 * moment.get("retention", 0.0)) + pseudo_random, 3)

        return {
            "clip_id": moment.get("clip_id", 0),
            "time": m_time,
            "score": moment.get("score", 0.0),
            "type": moment_type,
            "duration_hint": DURATION_HINTS.get(moment_type, 2.0),
            "face_present": moment.get("face_present", False),
            "motion_intensity": m_motion,
            "beat_aligned": moment.get("beat_aligned", False),
            "emotion_proxy": emotion_proxy
        }

    def run(self) -> List[Dict]:
        """
        Main entry point: Extract candidate moments from all signal sources.

        Returns:
            List of candidate moments with scores and metadata.
        """
        logger.info("[MOMENT_MINER] Starting moment extraction...")

        all_moments = []

        # 1. Extract from motion peaks
        motion_peaks = self._detect_motion_peaks()
        logger.info(f"[MOMENT_MINER] Motion peaks detected: {len(motion_peaks)}")
        all_moments.extend(motion_peaks)

        # 2. Extract from face presence
        face_moments = self._detect_face_moments()
        logger.info(f"[MOMENT_MINER] Face moments detected: {len(face_moments)}")
        all_moments.extend(face_moments)

        # 3. Extract reactions
        reaction_moments = self._detect_reaction_moments()
        logger.info(
            f"[MOMENT_MINER] Reaction moments detected: {len(reaction_moments)}"
        )
        all_moments.extend(reaction_moments)

        # 4. Extract beat-aligned moments
        beat_moments = self._detect_beat_moments()
        logger.info(f"[MOMENT_MINER] Beat moments detected: {len(beat_moments)}")
        all_moments.extend(beat_moments)

        # 5. Extract from scene boundaries
        scene_moments = self._detect_scene_boundary_moments()
        logger.info(
            f"[MOMENT_MINER] Scene boundary moments detected: {len(scene_moments)}"
        )
        all_moments.extend(scene_moments)

        # Calculate scores for all moments
        for moment in all_moments:
            moment["score"] = self._calculate_moment_score(moment)
            moment.setdefault("clip_id", 0)
            logger.info(
                f"[MULTI_CLIP_MOMENT] clip={moment.get('clip_id',0)} time={round(moment.get('time',0.0),3)} strength={round(moment.get('score',0.0),3)}"
            )

        # Deduplicate
        deduplicated = self._deduplicate_moments(all_moments)
        logger.info(f"[MOMENT_MINER] After deduplication: {len(deduplicated)}")

        # Limit to target range
        final_candidates = self._limit_candidates(deduplicated)
        logger.info(f"[MOMENT_MINER] candidates_found={len(final_candidates)}")

        # Ensure unique timestamps (rounded) before logging/returning
        unique = {}
        for m in final_candidates:
            key = round(m.get("time", 0.0), 2)
            if key not in unique:
                unique[key] = m
        final_candidates = list(unique.values())
        logger.info(f"[MOMENT_MINER] unique_timestamps={len(final_candidates)}")

        # Finalize format
        return [self._finalize_moment(m) for m in final_candidates]


def export_moments_debug(moments: List[Dict], output_path: str) -> None:
    """Export candidate moments to JSON for debugging."""
    debug_data = {
        "export_timestamp": datetime.now().isoformat(),
        "candidate_count": len(moments),
        "candidates": moments,
        "summary": {"types": {}},
    }

    # Count by type
    for moment in moments:
        m_type = moment.get("type", "unknown")
        debug_data["summary"]["types"][m_type] = (
            debug_data["summary"]["types"].get(m_type, 0) + 1
        )

    with open(output_path, "w") as f:
        json.dump(debug_data, f, indent=2)

    logger.info(f"[MOMENT_MINER] Debug export: {output_path}")


# Convenience function for orchestrator integration
def run_moment_miner(
    profile_data: Dict[str, Any],
    job_dir: Optional[str] = None,
    clip_id: int = 0,
) -> List[Dict]:
    """
    Convenience function for pipeline integration.

    Args:
        profile_data: Pipeline profile data (motion_scores, subject_tracking,
                      beat_data, shots).  For multi-clip jobs each clip's
                      sub-profile is passed in turn by the orchestrator.
        job_dir:      Optional job directory for debug JSON export.
        clip_id:      Source clip index (0-based).  Every returned moment is
                      stamped with this value so downstream modules
                      (TimelineReconstructor, render engine) can route to the
                      correct input file.  Defaults to 0 — single-clip jobs
                      are unaffected.

    Returns:
        List of candidate moments, each carrying ``clip_id``.
    """
    miner = MomentMiner(profile_data)
    candidates = miner.run()

    # Stamp clip_id on every moment (MomentMiner.run() already sets
    # clip_id=0 via setdefault; override here for clips 1+).
    for m in candidates:
        m["clip_id"] = clip_id

    # Store in profile only for the primary clip (clip_id == 0) so that
    # multi-clip callers can accumulate across clips themselves.
    if clip_id == 0:
        profile_data["candidate_moments"] = candidates

    # Export debug file if job_dir provided
    if job_dir:
        suffix = f"_clip{clip_id}" if clip_id > 0 else ""
        debug_path = os.path.join(
            job_dir, f"candidate_moments{suffix}_debug.json"
        )
        export_moments_debug(candidates, debug_path)

    return candidates