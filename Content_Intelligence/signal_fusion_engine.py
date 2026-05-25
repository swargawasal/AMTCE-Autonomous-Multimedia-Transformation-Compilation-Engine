"""
Content_Intelligence/signal_fusion_engine.py
---------------------------------------------
Signal Fusion Engine — Unified Moment Importance Scoring.

Merges all intelligence signals into a single authoritative moment score:
    - Retention peaks      (viewer engagement prediction)
    - Emotional spikes     (viral moment detection)
    - Motion intensity     (action/energy)
    - Beat alignment       (musical synchronization)
    - Face reactions       (human presence/expression)

Fusion Formula (per spec):
    S(t) = 0.25 * R + 0.25 * E + 0.20 * M + 0.15 * B + 0.15 * F

    R = retention score     (from RetentionCurveEngine)
    E = emotional spike     (from EmotionalSpikeDetector)
    M = motion energy       (from motion_scores)
    B = beat alignment      (from beat_data)
    F = face reaction       (from subject_tracking)

Pipeline position: Step 1h (after all signal sources, before Creative Director)

Inputs (read from profile_data):
    retention_peaks        — RetentionCurveEngine output
    emotional_spikes       — EmotionalSpikeDetector output
    candidate_moments      — MomentMiner output (provides timeline anchors)
    motion_scores          — per-frame motion intensity
    subject_tracking       — face tracking data
    beat_data              — {"beats": [float, ...]}

Outputs:
    profile_data["fused_moments"]     — unified importance-ranked moments
    profile_data["fusion_summary"]    — moment_count / strongest_moment / moment_times
    signal_fusion_debug.json          — full debug export (written to job_dir)

Expected output shape:
    {
        "fused_moments": [
            {
                "time": 5.3,
                "fusion_score": 0.92,
                "retention": 0.85,
                "emotion": 0.78,
                "motion": 0.90,
                "beat": 1.0,
                "face": 0.65,
                "source": "emotional_spike"  # dominant signal
            },
            ...
        ],
        "fusion_summary": {
            "moment_count": 10,
            "strongest_moment": 0.92,
            "moment_times": [2.8, 5.3, 9.7, ...]
        }
    }
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("signal_fusion_engine")

# ── Fusion weights (per spec) ──────────────────────────────────────────────────
WEIGHT_EMOTION = 0.40
WEIGHT_FACE = 0.30     # Semantic subject focus
WEIGHT_MOTION = 0.15
WEIGHT_RETENTION = 0.10
WEIGHT_BEAT = 0.05

# ── Tuning constants ───────────────────────────────────────────────────────────
MATCH_WINDOW = 0.8  # seconds — signal correlation window
MIN_FUSION_SCORE = 0.35  # minimum composite score to qualify as final moment
MIN_MOMENT_GAP = 1.5  # seconds — redundancy filter (suppress similar moments)
TARGET_MOMENT_COUNT = 10  # ideal number of final moments
MAX_MOMENT_COUNT = 12  # hard cap on output moments
SCORE_THRESHOLD_MULT = 1.1  # optional threshold = mean + 1.1 * std

# Safe empty returns
DEFAULT_MOMENTS: List[Dict] = []
DEFAULT_RESULT: Dict = {
    "fused_moments": [],
    "fusion_summary": {"moment_count": 0, "strongest_moment": 0.0, "moment_times": []},
}


# ══════════════════════════════════════════════════════════════════════════════
#  Signal resolvers — match each signal source to a given timestamp
# ══════════════════════════════════════════════════════════════════════════════


def _resolve_retention(t: float, retention_peaks: List[Dict]) -> float:
    """
    Return retention score [0.0-1.0] at time t.
    Matches the nearest retention peak within MATCH_WINDOW.
    """
    if not retention_peaks:
        return 0.0

    candidates = [
        p
        for p in retention_peaks
        if isinstance(p, dict) and abs(p.get("time", -9999.0) - t) <= MATCH_WINDOW
    ]

    if not candidates:
        return 0.0

    nearest = min(candidates, key=lambda p: abs(p.get("time", 0.0) - t))
    score = nearest.get("score", 0.0)

    try:
        return float(min(1.0, max(0.0, score)))
    except (TypeError, ValueError):
        return 0.0


def _resolve_emotion(t: float, emotional_spikes: List[Dict]) -> float:
    """
    Return emotional spike score [0.0-1.0] at time t.
    Matches the nearest emotional spike within MATCH_WINDOW.
    """
    if not emotional_spikes:
        return 0.0

    candidates = [
        e
        for e in emotional_spikes
        if isinstance(e, dict) and abs(e.get("time", -9999.0) - t) <= MATCH_WINDOW
    ]

    if not candidates:
        return 0.0

    nearest = min(candidates, key=lambda e: abs(e.get("time", 0.0) - t))
    score = nearest.get("emotion_score", 0.0)

    try:
        return float(min(1.0, max(0.0, score)))
    except (TypeError, ValueError):
        return 0.0


def _resolve_motion(t: float, motion_scores: List[Dict]) -> float:
    """
    Return motion intensity [0.0-1.0] at time t.
    Matches the nearest motion entry within MATCH_WINDOW.
    """
    if not motion_scores:
        return 0.0

    candidates = [
        m
        for m in motion_scores
        if isinstance(m, dict) and abs(m.get("time", -9999.0) - t) <= MATCH_WINDOW
    ]

    if not candidates:
        return 0.0

    nearest = min(candidates, key=lambda m: abs(m.get("time", 0.0) - t))

    # Numeric score (preferred)
    raw = nearest.get("score")
    if raw is not None:
        try:
            return float(min(1.0, max(0.0, raw)))
        except (TypeError, ValueError):
            pass

    # Strength string fallback
    strength_map = {"large": 1.0, "medium": 0.6, "small": 0.3}
    return strength_map.get(str(nearest.get("strength", "")).lower(), 0.0)


def _resolve_beat(t: float, beat_data: Dict) -> float:
    """
    Return beat alignment score [0.0-1.0] at time t.
    Linear decay from 1.0 (exactly on beat) to 0.0 at MATCH_WINDOW.
    """
    if not beat_data or not isinstance(beat_data, dict):
        return 0.0

    beats = beat_data.get("beats", [])
    if not beats:
        return 0.0

    # Find nearest beat
    try:
        nearest_beat = min(beats, key=lambda b: abs(float(b) - t))
        dist = abs(float(nearest_beat) - t)

        if dist >= MATCH_WINDOW:
            return 0.0

        # Linear decay
        return round(1.0 - (dist / MATCH_WINDOW), 4)

    except (TypeError, ValueError, AttributeError):
        return 0.0


def _resolve_face(t: float, face_data: List[Dict]) -> tuple[float, bool]:
    """
    Return semantic subject focus score [0.0-1.0] and change_event boolean at time t.
    Reads focus_strength from Gemini semantic tracking.
    Falls back to legacy bbox area ratio if semantic data is missing.
    """
    if not face_data:
        return 0.0, False

    candidates = [
        f
        for f in face_data
        if isinstance(f, dict) and abs(f.get("time", f.get("timestamp", -9999.0)) - t) <= MATCH_WINDOW
    ]

    if not candidates:
        return 0.0, False

    best = 0.0
    has_change_event = False
    FRAME_AREA = 1080 * 1920  # standard short-video frame

    for entry in candidates:
        # Check semantic focus strength
        if "focus_strength" in entry:
            try:
                score = float(entry["focus_strength"])
                if entry.get("change_event"):
                    has_change_event = True
                best = max(best, score)
                continue
            except (TypeError, ValueError):
                pass

        # Legacy bounding box calculation
        bbox = entry.get("bbox", [])
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                w = float(bbox[2])
                h = float(bbox[3])
                face_area = w * h
                ratio = face_area / FRAME_AREA
                score = min(1.0, ratio * 10.0)
                best = max(best, score)
            except (TypeError, ValueError, ZeroDivisionError):
                best = max(best, 0.3)
        else:
            if entry.get("face_present") or entry.get("focus"):
                best = max(best, 0.3)

    return round(best, 4), has_change_event


def _determine_dominant_source(
    retention: float,
    emotion: float,
    motion: float,
    beat: float,
    face: float,
) -> str:
    """
    Identify which signal contributed most to the fusion score.
    Returns: "retention" | "emotion" | "motion" | "beat" | "face"
    """
    signals = {
        "retention": retention * WEIGHT_RETENTION,
        "emotion": emotion * WEIGHT_EMOTION,
        "motion": motion * WEIGHT_MOTION,
        "beat": beat * WEIGHT_BEAT,
        "face": face * WEIGHT_FACE,
    }

    return max(signals, key=lambda k: signals[k])


# ══════════════════════════════════════════════════════════════════════════════
#  Timeline construction
# ══════════════════════════════════════════════════════════════════════════════


def _build_fusion_timeline(
    candidate_moments: List[Dict],
    retention_peaks: List[Dict],
    emotional_spikes: List[Dict],
    motion_scores: List[Dict],
    face_data: List[Dict],
    beat_data: Dict,
) -> List[Dict]:
    """
    Build unified timeline with fusion scores at all candidate moment timestamps.

    Uses candidate_moments as anchor points (provides temporal coverage).
    Computes S(t) = 0.25R + 0.25E + 0.20M + 0.15B + 0.15F at each anchor.

    Returns list of {"time": float, "fusion_score": float, "retention": float, ...}
    """
    fusion_timeline: List[Dict] = []

    if not candidate_moments:
        # Fallback: use retention peaks + emotional spikes as anchors
        anchor_points = set()

        for p in retention_peaks:
            if isinstance(p, dict) and "time" in p:
                anchor_points.add(round(float(p["time"]), 2))

        for e in emotional_spikes:
            if isinstance(e, dict) and "time" in e:
                anchor_points.add(round(float(e["time"]), 2))

        candidate_moments = [{"time": t} for t in sorted(anchor_points)]

    for moment in candidate_moments:
        if not isinstance(moment, dict):
            continue

        t = moment.get("time")
        if t is None:
            continue

        try:
            t = float(t)
        except (TypeError, ValueError):
            continue

        # Resolve each signal at time t
        retention = _resolve_retention(t, retention_peaks)
        emotion = _resolve_emotion(t, emotional_spikes)
        motion = _resolve_motion(t, motion_scores)
        beat = _resolve_beat(t, beat_data)
        face, tag_change_event = _resolve_face(t, face_data)

        # 🧠 LAYER 1 — WEIGHTED BASE SCORE
        base_score = (
            WEIGHT_RETENTION * retention
            + WEIGHT_EMOTION * emotion
            + WEIGHT_MOTION * motion
            + WEIGHT_BEAT * beat
            + WEIGHT_FACE * face
        )

        # 🧠 LAYER 2 — HUMAN EDITOR OVERRIDES
        tag = "baseline"

        # 🔥 RULE 1 — EMOTION DOMINANCE (HARD OVERRIDE)
        if emotion >= 0.75:
            base_score *= 1.5
            tag = "emotion_anchor"
            
        # 🔥 RULE 2 — ATTENTION SHIFT (VERY IMPORTANT)
        elif tag_change_event:
            base_score *= 1.3
            tag = "attention_shift"
            
        # 🔥 RULE 3 — DOUBLE SIGNAL SYNERGY
        elif emotion >= 0.6 and face >= 0.6:
            base_score *= 1.4
            tag = "hero_moment"
            
        # 🔥 RULE 4 — DEAD MOMENT KILLER
        elif motion < 0.2 and emotion < 0.3 and face < 0.3:
            base_score *= 0.3
            tag = "dead_zone"
            
        # 🔥 RULE 5 — ANTI-RETENTION BIAS
        elif retention > 0.7 and emotion < 0.4:
            base_score *= 0.7
            tag = "algorithm_trap"

        # 🧠 FINAL SCORE
        fusion_score = min(base_score, 1.0)
        
        # Determine dominant signal strictly for standard logging
        dominant_source = _determine_dominant_source(
            retention, emotion, motion, beat, face
        )
        
        # Override source with our editor tag if a rule fired
        if tag != "baseline":
            dominant_source = tag

        # Add explicit debug logging for human editor verification
        logger.debug(
            f"[FUSION_DEBUG] t={t:.2f} | E={emotion:.2f} S={face:.2f} M={motion:.2f} R={retention:.2f} "
            f"→ {fusion_score:.2f} ({tag})"
        )

        fusion_timeline.append(
            {
                "time": round(t, 3),
                "clip_id": moment.get("clip_id", 0),  # [MULTI_CLIP FIX] preserve source clip
                "fusion_score": round(fusion_score, 4),
                "retention": round(retention, 4),
                "emotion": round(emotion, 4),
                "motion": round(motion, 4),
                "beat": round(beat, 4),
                "face": round(face, 4),
                "source": dominant_source,
                "editor_tag": tag,
            }
        )

    return fusion_timeline


# ══════════════════════════════════════════════════════════════════════════════
#  Redundancy filter + moment selection
# ══════════════════════════════════════════════════════════════════════════════


def _apply_redundancy_filter(
    fusion_timeline: List[Dict],
    min_gap: float = MIN_MOMENT_GAP,
) -> List[Dict]:
    """
    Suppress redundant moments that are too close together.

    Algorithm:
      1. Sort by fusion_score descending
      2. Greedily select moments ensuring min_gap separation
      3. Return top moments up to TARGET_MOMENT_COUNT

    This prevents selecting multiple nearly-identical segments (e.g., 8.2s, 8.6s, 9.1s spin).
    """
    if not fusion_timeline:
        return []

    # Sort by score descending
    sorted_timeline = sorted(
        fusion_timeline, key=lambda m: m["fusion_score"], reverse=True
    )

    # Apply minimum score threshold
    filtered = [m for m in sorted_timeline if m["fusion_score"] >= MIN_FUSION_SCORE]

    # [MULTI_CLIP FIX] Greedy selection with clip-aware gap constraint.
    # Moments from different clips are independent shots and must NEVER
    # suppress each other — only apply the min_gap rule within the same clip.
    selected: List[Dict] = []
    used_times_per_clip: dict = {}  # clip_id -> [accepted times]

    for moment in filtered:
        t = moment["time"]
        cid = moment.get("clip_id", 0)

        clip_used = used_times_per_clip.get(cid, [])
        if all(abs(t - used_t) >= min_gap for used_t in clip_used):
            selected.append(moment)
            clip_used.append(t)
            used_times_per_clip[cid] = clip_used

            # Stop at target count
            if len(selected) >= TARGET_MOMENT_COUNT:
                break

    # Hard cap at MAX_MOMENT_COUNT
    selected = selected[:MAX_MOMENT_COUNT]

    # Sort selected moments by clip_id then time (chronological per-clip output)
    selected.sort(key=lambda m: (m.get("clip_id", 0), m["time"]))

    return selected


# ══════════════════════════════════════════════════════════════════════════════
#  Main engine class
# ══════════════════════════════════════════════════════════════════════════════


class SignalFusionEngine:
    """
    Merges all intelligence signals into unified moment importance scores.

    Formula:
        S(t) = 0.25 * retention + 0.25 * emotion + 0.20 * motion
             + 0.15 * beat + 0.15 * face

    Includes redundancy suppression to prevent selecting similar segments.
    """

    def fuse_signals(
        self,
        profile_data: Dict[str, Any],
        job_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Public entry point. Never raises — returns DEFAULT_RESULT on any error.

        Args:
            profile_data:  Pipeline profile dict. Reads:
                             - candidate_moments   (MomentMiner)
                             - retention_peaks     (RetentionCurveEngine)
                             - emotional_spikes    (EmotionalSpikeDetector)
                             - motion_scores       (motion analysis)
                             - subject_tracking    (face tracking)
                             - beat_data           (BeatEngine)
            job_dir:       Optional path where debug JSON will be written.

        Returns:
            {
                "fused_moments":   list[dict],   # unified importance-ranked moments
                "fusion_summary":  dict          # moment_count / strongest_moment / moment_times
            }
        """
        try:
            return self._run(profile_data, job_dir)
        except Exception as exc:
            logger.warning(
                f"🔀 [SIGNAL_FUSION] fuse_signals() failed unexpectedly: {exc}. "
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

        # ── 0. Pull all signal sources from profile_data ───────────────────
        candidate_moments: List[Dict] = profile_data.get("candidate_moments", [])
        retention_peaks: List[Dict] = profile_data.get("retention_peaks", [])
        emotional_spikes: List[Dict] = profile_data.get("emotional_spikes", [])
        motion_scores: List[Dict] = profile_data.get("motion_scores", [])
        face_data: List[Dict] = profile_data.get("subject_tracking", [])
        beat_data: Dict = profile_data.get("beat_data", {})

        logger.info(
            f"🔀 [SIGNAL_FUSION] Starting fusion — "
            f"anchors={len(candidate_moments)} | "
            f"retention_peaks={len(retention_peaks)} | "
            f"emotional_spikes={len(emotional_spikes)} | "
            f"motion_pts={len(motion_scores)} | "
            f"face_pts={len(face_data)} | "
            f"beats={len(beat_data.get('beats', []))}"
        )

        # ── 1. Build fusion timeline ───────────────────────────────────────
        fusion_timeline = _build_fusion_timeline(
            candidate_moments,
            retention_peaks,
            emotional_spikes,
            motion_scores,
            face_data,
            beat_data,
        )

        logger.info(
            f"🔀 [SIGNAL_FUSION] Timeline: {len(fusion_timeline)} sample points"
        )

        if not fusion_timeline:
            logger.warning(
                "🔀 [SIGNAL_FUSION] Empty fusion timeline — returning safe defaults."
            )
            return DEFAULT_RESULT.copy()

        # ── 2. Apply redundancy filter + moment selection ──────────────────
        fused_moments = _apply_redundancy_filter(
            fusion_timeline, min_gap=MIN_MOMENT_GAP
        )

        # ── 2b. Fallback if fusion rejected all signals ────────────────────
        if len(fused_moments) == 0 and candidate_moments:
            # Convert candidate_moments to fused format
            # [MULTI_CLIP FIX] take top 5 per unique clip so all clips are represented
            _seen_clips: dict = {}
            _fallback_pool = sorted(candidate_moments, key=lambda m: m.get("score", 0.0), reverse=True)
            for m in _fallback_pool:
                if not (isinstance(m, dict) and "time" in m):
                    continue
                _cid = m.get("clip_id", 0)
                if _seen_clips.get(_cid, 0) < 2:  # up to 2 moments per clip
                    _seen_clips[_cid] = _seen_clips.get(_cid, 0) + 1
                    fused_moments.append({
                        "time": round(float(m["time"]), 3),
                        "clip_id": _cid,  # [MULTI_CLIP FIX] preserve source clip
                        "fusion_score": round(m.get("score", 0.5), 4),
                        "retention": 0.0,
                        "emotion": 0.0,
                        "motion": round(m.get("motion_intensity", 0.0), 4),
                        "beat": 1.0 if m.get("beat_aligned") else 0.0,
                        "face": 1.0 if m.get("face_present") else 0.0,
                        "source": "fallback_candidate_moment",
                    })
            fused_moments.sort(key=lambda m: m["time"])
            logger.info("[SIGNAL_FUSION] fallback_to_candidate_moments=True")

        # ── 3. Build summary ───────────────────────────────────────────────
        fusion_summary = {
            "moment_count": len(fused_moments),
            "strongest_moment": round(
                max((m["fusion_score"] for m in fused_moments), default=0.0), 4
            ),
            "moment_times": [round(m["time"], 2) for m in fused_moments],
        }

        logger.info(
            f"✅ [SIGNAL_FUSION] final_moments={fusion_summary['moment_count']} | "
            f"strongest={fusion_summary['strongest_moment']:.4f}"
        )

        if fused_moments:
            logger.info(
                f"🔀 [SIGNAL_FUSION] Moment times: {fusion_summary['moment_times']}"
            )

            # Log dominant source distribution
            source_counts = {}
            for m in fused_moments:
                src = m["source"]
                source_counts[src] = source_counts.get(src, 0) + 1
            logger.info(f"🔀 [SIGNAL_FUSION] Dominant sources: {source_counts}")

        # ── 4. Export debug file ───────────────────────────────────────────
        self._export_debug(
            fusion_timeline=fusion_timeline,
            fused_moments=fused_moments,
            fusion_summary=fusion_summary,
            job_dir=job_dir,
        )

        # ── 5. Write back to profile_data ──────────────────────────────────
        profile_data["fused_moments"] = fused_moments
        profile_data["fusion_summary"] = fusion_summary

        return {
            "fused_moments": fused_moments,
            "fusion_summary": fusion_summary,
        }

    def _export_debug(
        self,
        fusion_timeline: List[Dict],
        fused_moments: List[Dict],
        fusion_summary: Dict,
        job_dir: Optional[str],
    ) -> None:
        """Export full debug data to JSON."""
        if not job_dir:
            return

        try:
            debug_data = {
                "export_timestamp": datetime.now().isoformat(),
                "moment_count": len(fused_moments),
                "fusion_summary": fusion_summary,
                "fused_moments": fused_moments,
                "fusion_timeline": fusion_timeline[:50],  # First 50 points for brevity
                "formula": {
                    "weight_retention": WEIGHT_RETENTION,
                    "weight_emotion": WEIGHT_EMOTION,
                    "weight_motion": WEIGHT_MOTION,
                    "weight_beat": WEIGHT_BEAT,
                    "weight_face": WEIGHT_FACE,
                },
                "filters": {
                    "min_fusion_score": MIN_FUSION_SCORE,
                    "min_moment_gap": MIN_MOMENT_GAP,
                    "target_moment_count": TARGET_MOMENT_COUNT,
                },
            }

            debug_path = os.path.join(job_dir, "signal_fusion_debug.json")
            with open(debug_path, "w") as f:
                json.dump(debug_data, f, indent=2)

            logger.info(f"🔀 [SIGNAL_FUSION] Debug export: {debug_path}")

        except Exception as e:
            logger.warning(f"🔀 [SIGNAL_FUSION] Debug export failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  Module-level convenience functions
# ══════════════════════════════════════════════════════════════════════════════

_engine_instance = None


def get_engine() -> SignalFusionEngine:
    """Singleton accessor for the engine."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = SignalFusionEngine()
    return _engine_instance


def fuse_signals(
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
            "fused_moments": list[dict],
            "fusion_summary": dict
        }
    """
    return get_engine().fuse_signals(profile_data, job_dir)
