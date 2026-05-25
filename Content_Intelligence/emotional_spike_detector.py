"""
Content_Intelligence/emotional_spike_detector.py
------------------------------------------------
Emotional Spike Detector — Viral Moment Predictor.

Detects high-emotion moments that drive viewer engagement:
    - laughter
    - surprise
    - sudden motion
    - crowd reaction

Emotional Score Formula (per spec):
    E(t) = 0.4 * face_expression + 0.3 * motion_change + 0.3 * audio_spike

    face_expression  — rapid bbox changes (proxy for expression shift)
    motion_change    — sudden motion intensity delta
    audio_spike      — beat amplitude / loudness spike

Pipeline position: Step 1g (after Retention Engine, before Creative Director)

Inputs (read from profile_data):
    subject_tracking   — face tracking data (bbox per frame)
    motion_scores      — per-frame motion intensity list
    beat_data          — {"beats": [float, ...], "amplitudes": [float, ...]}
    duration           — float, seconds

Outputs:
    profile_data["emotional_spikes"]  — list of spike dicts
    profile_data["emotion_summary"]   — spike_count / strongest_spike / spike_times
    emotional_spikes_debug.json       — full debug export (written to job_dir)

Expected spike shape:
    {
        "time":              float,  # spike anchor timestamp
        "emotion_score":     float,  # 0.0-1.0 composite score
        "face_expression":   float,  # face component
        "motion_change":     float,  # motion component
        "audio_spike":       float,  # audio component
        "spike_type":        str,    # "surprise" | "laughter" | "motion" | "reaction"
        "intensity":         str     # "high" | "medium" | "low"
    }
"""

import json
import logging
import os
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("emotional_spike_detector")

# ── Formula weights (per spec) ─────────────────────────────────────────────────
WEIGHT_FACE_EXPRESSION = 0.4
WEIGHT_MOTION_CHANGE = 0.3
WEIGHT_AUDIO_SPIKE = 0.3

# ── Tuning constants ───────────────────────────────────────────────────────────
FACE_CHANGE_WINDOW = 0.3  # seconds — time window to measure bbox delta
MOTION_CHANGE_WINDOW = 0.4  # seconds — time window to measure motion delta
AUDIO_SPIKE_TOLERANCE = 0.2  # seconds — match tolerance for beat spikes
MIN_SPIKE_GAP = 1.2  # minimum seconds between reported spikes
SPIKE_THRESHOLD_MULT = 1.3  # spike_threshold = mean + 1.3 * std
MIN_EMOTION_SCORE = 0.35  # minimum composite score to qualify as a spike

# Spike type classification thresholds
HIGH_INTENSITY = 0.75
MEDIUM_INTENSITY = 0.50

# Safe empty returns
DEFAULT_SPIKES: List[Dict] = []
DEFAULT_RESULT: Dict = {
    "emotional_spikes": [],
    "emotion_summary": {"spike_count": 0, "strongest_spike": 0.0, "spike_times": []},
}


# ══════════════════════════════════════════════════════════════════════════════
#  Signal resolvers
# ══════════════════════════════════════════════════════════════════════════════


def _compute_face_expression_score(
    t: float,
    face_data: List[Dict],
) -> float:
    """
    Compute face expression change [0.0-1.0] at time t.

    Proxy for expression change: rapid bbox position/size delta.
    Measures the distance between bbox centers at t and t-FACE_CHANGE_WINDOW.

    Large movement = surprise/reaction; size change = emotional expression shift.
    """
    if not face_data or len(face_data) < 2:
        return 0.0

    # Find frames near t and t-WINDOW
    current_candidates = [
        f
        for f in face_data
        if isinstance(f, dict) and abs(f.get("time", -9999.0) - t) <= 0.15
    ]
    previous_candidates = [
        f
        for f in face_data
        if isinstance(f, dict)
        and abs(f.get("time", -9999.0) - (t - FACE_CHANGE_WINDOW)) <= 0.15
    ]

    if not current_candidates or not previous_candidates:
        return 0.0

    curr = min(current_candidates, key=lambda f: abs(f.get("time", 0.0) - t))
    prev = min(
        previous_candidates,
        key=lambda f: abs(f.get("time", 0.0) - (t - FACE_CHANGE_WINDOW)),
    )

    curr_bbox = curr.get("bbox", [])
    prev_bbox = prev.get("bbox", [])

    if not (isinstance(curr_bbox, (list, tuple)) and len(curr_bbox) >= 4):
        return 0.0
    if not (isinstance(prev_bbox, (list, tuple)) and len(prev_bbox) >= 4):
        return 0.0

    try:
        # bbox format: [x, y, w, h]
        curr_cx = float(curr_bbox[0]) + float(curr_bbox[2]) / 2.0
        curr_cy = float(curr_bbox[1]) + float(curr_bbox[3]) / 2.0
        curr_area = float(curr_bbox[2]) * float(curr_bbox[3])

        prev_cx = float(prev_bbox[0]) + float(prev_bbox[2]) / 2.0
        prev_cy = float(prev_bbox[1]) + float(prev_bbox[3]) / 2.0
        prev_area = float(prev_bbox[2]) * float(prev_bbox[3])

        # Position delta (Euclidean distance)
        position_delta = ((curr_cx - prev_cx) ** 2 + (curr_cy - prev_cy) ** 2) ** 0.5

        # Size delta (relative change)
        if prev_area > 0:
            size_delta = abs(curr_area - prev_area) / prev_area
        else:
            size_delta = 0.0

        # Normalize position delta (assume 1080x1920 frame)
        # Large movement: 100+ pixels → score ~0.5; 200+ pixels → 1.0
        position_score = min(1.0, position_delta / 200.0)

        # Size change: 20%+ change → score ~0.5; 40%+ → 1.0
        size_score = min(1.0, size_delta / 0.4)

        # Combine (position weighted higher for reaction detection)
        expression_score = 0.7 * position_score + 0.3 * size_score

        return round(min(1.0, expression_score), 4)

    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _compute_motion_change_score(
    t: float,
    motion_scores: List[Dict],
) -> float:
    """
    Compute sudden motion change [0.0-1.0] at time t.

    Measures the delta between motion intensity at t and t-MOTION_CHANGE_WINDOW.
    Large spike = sudden action/reaction.
    """
    if not motion_scores or len(motion_scores) < 2:
        return 0.0

    # Find motion entries near t and t-WINDOW
    current_candidates = [
        m
        for m in motion_scores
        if isinstance(m, dict) and abs(m.get("time", -9999.0) - t) <= 0.2
    ]
    previous_candidates = [
        m
        for m in motion_scores
        if isinstance(m, dict)
        and abs(m.get("time", -9999.0) - (t - MOTION_CHANGE_WINDOW)) <= 0.2
    ]

    if not current_candidates or not previous_candidates:
        return 0.0

    curr = min(current_candidates, key=lambda m: abs(m.get("time", 0.0) - t))
    prev = min(
        previous_candidates,
        key=lambda m: abs(m.get("time", 0.0) - (t - MOTION_CHANGE_WINDOW)),
    )

    # Extract motion scores (supports both numeric "score" and string "strength")
    def _get_motion_value(entry: Dict) -> float:
        raw = entry.get("score")
        if raw is not None:
            try:
                return float(min(1.0, max(0.0, raw)))
            except (TypeError, ValueError):
                pass
        # Fallback to strength string
        strength_map = {"large": 1.0, "medium": 0.6, "small": 0.3}
        return strength_map.get(str(entry.get("strength", "")).lower(), 0.0)

    curr_motion = _get_motion_value(curr)
    prev_motion = _get_motion_value(prev)

    # Measure delta (absolute change)
    motion_delta = abs(curr_motion - prev_motion)

    # Normalize: 0.3+ delta → 0.5; 0.6+ → 1.0
    change_score = min(1.0, motion_delta / 0.6)

    return round(change_score, 4)


def _compute_audio_spike_score(
    t: float,
    beat_data: Dict,
) -> float:
    """
    Compute audio spike [0.0-1.0] at time t.

    Detects amplitude spikes in beat_data (loud moments = emotional reactions).
    Falls back to binary beat presence when amplitude data is unavailable.
    """
    if not beat_data or not isinstance(beat_data, dict):
        return 0.0

    beats = beat_data.get("beats", [])
    amplitudes = beat_data.get("amplitudes", [])

    if not beats:
        return 0.0

    # Find nearest beat within tolerance
    clean_beats = [
        float(b.get("time", 0.0)) if isinstance(b, dict) else float(b)
        for b in beats
    ]
    nearby_beats = [
        (i, b) for i, b in enumerate(clean_beats) if abs(b - t) <= AUDIO_SPIKE_TOLERANCE
    ]

    if not nearby_beats:
        return 0.0

    nearest_idx, nearest_beat = min(nearby_beats, key=lambda x: abs(x[1] - t))

    # If amplitude data is available, use it
    if amplitudes and isinstance(amplitudes, list) and nearest_idx < len(amplitudes):
        try:
            amp = float(amplitudes[nearest_idx])
            # Normalize amplitude (assume range 0.0-1.0; some pipelines use dB)
            # If amplitude > 1.0, it's likely dB scale — clamp it
            if amp > 1.0:
                # Convert dB to normalized score (rough approximation)
                # -60 dB → 0.0, -6 dB → 1.0
                amp = max(0.0, min(1.0, (amp + 60.0) / 54.0))
            return round(min(1.0, max(0.0, amp)), 4)
        except (TypeError, ValueError):
            pass

    # Fallback: binary presence (beat exists = moderate audio spike)
    return 0.4


def _classify_spike_type(
    face_score: float,
    motion_score: float,
    audio_score: float,
) -> str:
    """
    Classify spike type based on dominant component.

    Returns: "surprise" | "laughter" | "motion" | "reaction"
    """
    # High face expression + high audio = laughter/vocal reaction
    if face_score >= 0.6 and audio_score >= 0.5:
        return "laughter"

    # High face expression + moderate motion = surprise/reaction
    if face_score >= 0.7:
        return "surprise"

    # High motion change = sudden action/movement
    if motion_score >= 0.7:
        return "motion"

    # Mixed signals = general reaction
    return "reaction"


def _determine_intensity(emotion_score: float) -> str:
    """Classify intensity level based on composite score."""
    if emotion_score >= HIGH_INTENSITY:
        return "high"
    elif emotion_score >= MEDIUM_INTENSITY:
        return "medium"
    else:
        return "low"


# ══════════════════════════════════════════════════════════════════════════════
#  Timeline construction
# ══════════════════════════════════════════════════════════════════════════════


def _build_emotion_timeline(
    duration: float,
    face_data: List[Dict],
    motion_scores: List[Dict],
    beat_data: Dict,
) -> List[Dict]:
    """
    Build a time-indexed emotion score timeline.

    Samples at every face frame, motion frame, and beat timestamp.
    Computes E(t) = 0.4 * face_expression + 0.3 * motion_change + 0.3 * audio_spike.

    Returns list of {"time": float, "emotion_score": float, "face_expression": float, ...}
    """
    timeline: List[Dict] = []
    timestamps_set = set()

    # Collect all timestamps
    for f in face_data:
        if isinstance(f, dict) and "time" in f:
            timestamps_set.add(round(float(f["time"]), 2))

    for m in motion_scores:
        if isinstance(m, dict) and "time" in m:
            timestamps_set.add(round(float(m["time"]), 2))

    beats = beat_data.get("beats", []) if isinstance(beat_data, dict) else []
    for b in beats:
        try:
            val = float(b.get("time", 0.0)) if isinstance(b, dict) else float(b)
            timestamps_set.add(round(val, 2))
        except (TypeError, ValueError):
            pass

    # Sort timestamps
    timestamps = sorted(list(timestamps_set))

    # Compute emotion score at each timestamp
    for t in timestamps:
        if t < 0.0 or t > duration:
            continue

        face_expr = _compute_face_expression_score(t, face_data)
        motion_change = _compute_motion_change_score(t, motion_scores)
        audio_spike = _compute_audio_spike_score(t, beat_data)

        # Weighted composite
        emotion_score = (
            WEIGHT_FACE_EXPRESSION * face_expr
            + WEIGHT_MOTION_CHANGE * motion_change
            + WEIGHT_AUDIO_SPIKE * audio_spike
        )

        timeline.append(
            {
                "time": round(t, 3),
                "emotion_score": round(emotion_score, 4),
                "face_expression": round(face_expr, 4),
                "motion_change": round(motion_change, 4),
                "audio_spike": round(audio_spike, 4),
            }
        )

    return timeline


# ══════════════════════════════════════════════════════════════════════════════
#  Spike detection
# ══════════════════════════════════════════════════════════════════════════════


def _detect_emotional_spikes(
    emotion_timeline: List[Dict],
    min_gap: float = MIN_SPIKE_GAP,
) -> Tuple[List[Dict], float]:
    """
    Detect emotional spikes above threshold.

    Threshold = mean + SPIKE_THRESHOLD_MULT * std.
    Filters spikes by min_gap to prevent cluster duplicates.

    Returns: (spikes, threshold)
    """
    if not emotion_timeline:
        return [], 0.0

    scores = [e["emotion_score"] for e in emotion_timeline]

    if not scores:
        return [], 0.0

    mean_score = statistics.mean(scores)
    std_score = statistics.stdev(scores) if len(scores) > 1 else 0.0
    threshold = mean_score + (SPIKE_THRESHOLD_MULT * std_score)

    # Also enforce minimum absolute threshold
    threshold = max(threshold, MIN_EMOTION_SCORE)

    # Collect all points above threshold
    candidates = [e for e in emotion_timeline if e["emotion_score"] >= threshold]

    # Sort by score descending
    candidates.sort(key=lambda e: e["emotion_score"], reverse=True)

    # Deduplicate by min_gap
    spikes: List[Dict] = []
    used_times: List[float] = []

    for cand in candidates:
        t = cand["time"]
        if all(abs(t - used_t) >= min_gap for used_t in used_times):
            # Classify spike type
            spike_type = _classify_spike_type(
                cand["face_expression"],
                cand["motion_change"],
                cand["audio_spike"],
            )

            intensity = _determine_intensity(cand["emotion_score"])

            spikes.append(
                {
                    "time": round(t, 3),
                    "emotion_score": round(cand["emotion_score"], 4),
                    "face_expression": round(cand["face_expression"], 4),
                    "motion_change": round(cand["motion_change"], 4),
                    "audio_spike": round(cand["audio_spike"], 4),
                    "spike_type": spike_type,
                    "intensity": intensity,
                }
            )
            used_times.append(t)

    # Sort spikes by time for chronological output
    spikes.sort(key=lambda s: s["time"])

    return spikes, threshold


# ══════════════════════════════════════════════════════════════════════════════
#  Gemini Vision Integration
# ══════════════════════════════════════════════════════════════════════════════

def _detect_emotional_spikes_with_gemini(
    frame_paths: List[str], duration: float
) -> Optional[List[Dict]]:
    """Invoke the professional human editor prompt via Gemini Vision on sampled frames."""
    try:
        from Intelligence_Modules.gemini_governor import gemini_router
        if not gemini_router:
            return None
    except ImportError:
        return None

    if not frame_paths:
        return None

    # Frame Sampling target (8-16 frames MAX)
    step = max(1, len(frame_paths) // 12)
    sampled_frame_paths = frame_paths[::step][:16]  # ensure hard limit
    
    prompt_text = f"""SYSTEM ROLE:
You are a professional short-form video editor and visual emotion analyst.

You do NOT detect basic emotions like "happy" or "sad".
You detect MOMENTS that feel impactful to a viewer.

Your job is to identify frames that a human editor would KEEP because they FEEL strong.

---

INPUT:
You are given a sequence of frames from a video.
Each frame has a timestamp.

Frames are ordered chronologically.
Video duration: {duration:.2f}s

---

YOUR TASK:
Identify timestamps where emotional or visual impact peaks.

Focus on:

1. IMPACT MOMENTS
* sudden movement
* strong action (jump, hit, turn, expression change)

2. PRESENCE MOMENTS
* eye contact with camera
* strong pose or body language
* subject dominance in frame

3. TENSION / RELEASE
* buildup → action
* action → reaction

4. VISUAL STRIKING FRAMES
* strong lighting
* clean composition
* aesthetic or cinematic feel

---

STRICT RULES:
* DO NOT select more than 6 moments
* DO NOT select weak or repetitive frames
* IGNORE static or low-change frames
* ONLY pick moments that feel EDIT-WORTHY

---

SCORING:
For each moment:
* strength (0.0 → 1.0)
* type: ["impact", "presence", "tension", "release", "aesthetic"]

---

OUTPUT FORMAT (STRICT JSON):
{{
"emotional_moments": [
{{
"timestamp": 2.4,
"strength": 0.85,
"type": "impact",
"reason": "sudden explosive movement with strong visual contrast"
}}
]
}}

---

CRITICAL:
Think like a viral video editor.

If this moment was removed, the video would feel weaker.
Only select those moments.
"""

    prompt_list = [prompt_text]
    import PIL.Image
    
    actual_sampled = 0
    for path in sampled_frame_paths:
        if os.path.exists(path):
            try:
                img = PIL.Image.open(path)
                prompt_list.append(img)
                actual_sampled += 1
            except Exception:
                pass

    if actual_sampled == 0:
        return None

    logger.info(f"🧠 [EMOTIONAL_SPIKE] Requesting Gemini Vision analysis with {actual_sampled} frames...")
    res_text = gemini_router.generate(
        task_type="vision",
        prompt=prompt_list,
        module_name="emotional_spike_detector"
    )

    if not res_text:
        return None

    try:
        import re
        match = re.search(r"\{.*\}", res_text, re.DOTALL)
        data = json.loads(match.group(0) if match else res_text)
        
        moments = data.get("emotional_moments", [])
        spikes = []
        for m in moments:
            strength = float(m.get("strength", 0.0))
            spikes.append({
                "time": float(m.get("timestamp", 0.0)),
                "emotion_score": strength,
                "face_expression": 1.0 if m.get("type") in ["presence", "aesthetic"] else 0.5,
                "motion_change": 1.0 if m.get("type") in ["impact", "release", "tension"] else 0.5,
                "audio_spike": 0.5,
                "spike_type": m.get("type", "impact"),
                "intensity": _determine_intensity(strength),
                "reason": m.get("reason", "")
            })
            
        return spikes
    except Exception as e:
        logger.warning(f"⚠️ [EMOTIONAL_SPIKE] Failed parsing Gemini JSON: {e}")
        return None
#  Main engine class
# ══════════════════════════════════════════════════════════════════════════════


class EmotionalSpikeDetector:
    """
    Detects high-emotion moments using composite formula.

    Formula:
        E(t) = 0.4 * face_expression + 0.3 * motion_change + 0.3 * audio_spike
    """

    def analyse(
        self,
        profile_data: Dict[str, Any],
        job_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Public entry point. Never raises — returns DEFAULT_RESULT on any error.

        Args:
            profile_data:  Pipeline profile dict. Reads:
                             - subject_tracking  (face tracking)
                             - motion_scores     (motion analysis)
                             - beat_data         (BeatEngine)
                             - duration          (float, seconds)
                             - shots             (shot boundary list)
            job_dir:       Optional path where debug JSON will be written.

        Returns:
            {
                "emotional_spikes":  list[dict],  # spike moments
                "emotion_summary":   dict         # spike_count / strongest_spike / spike_times
            }
        """
        try:
            return self._run(profile_data, job_dir)
        except Exception as exc:
            logger.warning(
                f"😮 [EMOTIONAL_SPIKE] analyse() failed unexpectedly: {exc}. "
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

        # ── 0. Pull signals from profile_data ──────────────────────────────
        face_data: List[Dict] = profile_data.get("subject_tracking", [])
        motion_scores: List[Dict] = profile_data.get("motion_scores", [])
        beat_data: Dict = profile_data.get("beat_data", {})

        # ── 1. Estimate duration ───────────────────────────────────────────
        duration = profile_data.get("duration", 0.0)
        if not duration or duration <= 0:
            shots = profile_data.get("shots", [])
            if shots:
                duration = max(s.get("end", 0.0) for s in shots)
            elif motion_scores:
                duration = max(m.get("time", 0.0) for m in motion_scores)
            elif face_data:
                duration = max(f.get("time", 0.0) for f in face_data)
            else:
                duration = 30.0  # fallback

        logger.info(
            f"😮 [EMOTIONAL_SPIKE] Starting analysis — "
            f"duration={duration:.1f}s | "
            f"face_pts={len(face_data)} | "
            f"motion_pts={len(motion_scores)} | "
            f"beats={len(beat_data.get('beats', []))}"
        )

        if duration <= 0:
            logger.warning(
                "😮 [EMOTIONAL_SPIKE] Zero duration — returning safe defaults."
            )
            return DEFAULT_RESULT.copy()

        # ── 2. Build emotion timeline ──────────────────────────────────────
        emotion_timeline = _build_emotion_timeline(
            duration, face_data, motion_scores, beat_data
        )
        logger.info(
            f"😮 [EMOTIONAL_SPIKE] Timeline: {len(emotion_timeline)} sample points"
        )

        if not emotion_timeline:
            logger.warning(
                "😮 [EMOTIONAL_SPIKE] Empty timeline — returning safe defaults."
            )
            return DEFAULT_RESULT.copy()

        # ── 3. Detect spikes ───────────────────────────────────────────────
        spikes = None
        threshold = 0.5
        
        frame_paths = profile_data.get("frame_paths", [])
        if frame_paths:
            gemini_spikes = _detect_emotional_spikes_with_gemini(frame_paths, duration)
            if gemini_spikes:
                spikes = gemini_spikes
                spikes.sort(key=lambda s: s["time"])
                if spikes:
                    scores = [s.get("emotion_score", 0.0) for s in spikes]
                    threshold = sum(scores) / len(scores)

        if not spikes:
            logger.info("🎬 [EMOTIONAL_SPIKE] fallback → mathematical algorithm")
            spikes, threshold = _detect_emotional_spikes(
                emotion_timeline, min_gap=MIN_SPIKE_GAP
            )

        # ── 4. Build summary ───────────────────────────────────────────────
        emotion_summary = {
            "spike_count": len(spikes),
            "strongest_spike": round(
                max((s["emotion_score"] for s in spikes), default=0.0), 4
            ),
            "spike_times": [round(s["time"], 2) for s in spikes],
        }

        logger.info(
            f"✅ [EMOTIONAL_SPIKE] spikes_detected={emotion_summary['spike_count']} | "
            f"threshold={threshold:.4f} | "
            f"strongest={emotion_summary['strongest_spike']:.4f}"
        )

        if spikes:
            logger.info(
                f"😮 [EMOTIONAL_SPIKE] Spike times: {emotion_summary['spike_times']}"
            )
            # Log spike type distribution
            type_counts = {}
            for s in spikes:
                stype = s["spike_type"]
                type_counts[stype] = type_counts.get(stype, 0) + 1
            logger.info(f"😮 [EMOTIONAL_SPIKE] Spike types: {type_counts}")

        # ── 5. Export debug file ───────────────────────────────────────────
        self._export_debug(
            emotion_timeline=emotion_timeline,
            spikes=spikes,
            threshold=threshold,
            emotion_summary=emotion_summary,
            duration=duration,
            job_dir=job_dir,
        )

        # ── 6. Write back to profile_data ──────────────────────────────────
        profile_data["emotional_spikes"] = spikes
        profile_data["emotion_summary"] = emotion_summary

        return {
            "emotional_spikes": spikes,
            "emotion_summary": emotion_summary,
        }

    def _export_debug(
        self,
        emotion_timeline: List[Dict],
        spikes: List[Dict],
        threshold: float,
        emotion_summary: Dict,
        duration: float,
        job_dir: Optional[str],
    ) -> None:
        """Export full debug data to JSON."""
        if not job_dir:
            return

        try:
            debug_data = {
                "export_timestamp": datetime.now().isoformat(),
                "duration": round(duration, 2),
                "threshold": round(threshold, 4),
                "spike_count": len(spikes),
                "emotion_summary": emotion_summary,
                "spikes": spikes,
                "emotion_timeline": emotion_timeline[
                    :100
                ],  # First 100 points for brevity
                "formula": {
                    "weight_face_expression": WEIGHT_FACE_EXPRESSION,
                    "weight_motion_change": WEIGHT_MOTION_CHANGE,
                    "weight_audio_spike": WEIGHT_AUDIO_SPIKE,
                },
            }

            debug_path = os.path.join(job_dir, "emotional_spikes_debug.json")
            with open(debug_path, "w") as f:
                json.dump(debug_data, f, indent=2)

            logger.info(f"😮 [EMOTIONAL_SPIKE] Debug export: {debug_path}")

        except Exception as e:
            logger.warning(f"😮 [EMOTIONAL_SPIKE] Debug export failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  Module-level convenience functions
# ══════════════════════════════════════════════════════════════════════════════

_engine_instance = None


def get_engine() -> EmotionalSpikeDetector:
    """Singleton accessor for the engine."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = EmotionalSpikeDetector()
    return _engine_instance


def analyse_emotional_spikes(
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
            "emotional_spikes": list[dict],
            "emotion_summary": dict
        }
    """
    return get_engine().analyse(profile_data, job_dir)
