"""
Content_Intelligence/retention_curve_engine.py
-----------------------------------------------
Retention Curve Engine — Virality Predictor.

Predicts viewer attention peaks across the video timeline so the editor can
prioritize moments with high engagement probability.

Retention Formula (per spec):
    R(t) = 0.35 * M + 0.25 * F + 0.20 * B + 0.20 * D

    M = motion intensity
    F = face reaction score
    B = beat alignment
    D = dialogue / emotion signal  (optional — gracefully zeroed when absent)

Pipeline position: Step 1f (after Moment Miner, before Creative Director)

Inputs (read from profile_data):
    candidate_moments  — MomentMiner output
    motion_scores      — per-frame motion intensity list
    subject_tracking   — face tracking data (bbox per frame)
    beat_data          — {"beats": [float, ...]} from BeatEngine
    narrative_data     — optional; Gemini-derived dialogue / emotion signals

Outputs:
    profile_data["retention_peaks"]    — list of peak dicts
    profile_data["retention_curve"]    — full smoothed curve (lean format)
    profile_data["retention_summary"]  — peak_count / strongest_peak / peak_times

    retention_curve_debug.json         — full debug export (written to job_dir)

Expected peak summary shape:
    {
        "peak_count":     6,
        "strongest_peak": 0.93,
        "peak_times":     [2.3, 5.1, 9.8, ...]
    }
"""

import json
import logging
import os
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("retention_curve_engine")

# ── Formula weights (per spec) ─────────────────────────────────────────────────
WEIGHT_MOTION = 0.35
WEIGHT_FACE = 0.25
WEIGHT_BEAT = 0.20
WEIGHT_DIALOGUE = 0.20

# ── NICHE-SPECIFIC WEIGHT MATRIX (Upgraded R1) ──────────────────────────────
# Different niches require different visual stimuli to maintain retention.
# Fashion = Face/Detail focus. Fitness = Motion/Intensity focus.
NICHE_WEIGHT_MATRIX = {
    "FASHION":    {"motion": 0.2, "face": 0.5, "object": 0.3, "pacing_bias": 1.0},
    "FITNESS":    {"motion": 0.6, "face": 0.1, "object": 0.3, "pacing_bias": 1.5},
    "STREETWEAR": {"motion": 0.4, "face": 0.2, "object": 0.4, "pacing_bias": 1.2},
    "MINIMALIST": {"motion": 0.1, "face": 0.3, "object": 0.6, "pacing_bias": 0.8},
    "BOLLYWOOD":  {"motion": 0.3, "face": 0.6, "object": 0.1, "pacing_bias": 1.1},
    "GLOBAL":     {"motion": 0.33, "face": 0.33, "object": 0.33, "pacing_bias": 1.0}
}

# ── Tuning constants ───────────────────────────────────────────────────────────
BEAT_TOLERANCE = 0.35  # seconds — linear decay window around each beat
MOTION_WINDOW = 0.40  # seconds — search radius for motion signal lookup
FACE_WINDOW = 0.40  # seconds — search radius for face signal lookup
DIALOGUE_WINDOW = 0.50  # seconds — search radius for dialogue signal lookup
SAMPLE_INTERVAL = 0.50  # seconds — filler sample spacing (curve density)
SMOOTH_WINDOW = 5  # moving-average window size (per spec)
PEAK_SIGMA_MULT = 1.2  # peak_threshold = mean + 1.2 * std (per spec)
MIN_PEAK_GAP = 1.0  # minimum seconds between reported peaks

# Standard short-video frame area (1080 × 1920) used for face-ratio normalisation
FRAME_AREA = 1080 * 1920

# Safe empty returns
DEFAULT_PEAKS: List[Dict] = []
DEFAULT_RESULT: Dict = {
    "retention_peaks": [],
    "retention_curve": [],
    "retention_summary": {"peak_count": 0, "strongest_peak": 0.0, "peak_times": []},
}


# ══════════════════════════════════════════════════════════════════════════════
#  Signal resolvers — each returns a float in [0.0, 1.0]
# ══════════════════════════════════════════════════════════════════════════════


def _resolve_motion(t: float, motion_scores: List[Dict]) -> float:
    """
    Return normalised motion intensity [0.0-1.0] at time t.

    Looks for the nearest motion_scores entry within MOTION_WINDOW.
    Supports both numeric "score" fields and "strength" string keys
    (large / medium / small) produced by the existing pipeline.
    """
    if not motion_scores:
        return 0.0

    candidates = [
        m
        for m in motion_scores
        if isinstance(m, dict) and abs(m.get("time", -9999.0) - t) <= MOTION_WINDOW
    ]
    if not candidates:
        return 0.0

    nearest = min(candidates, key=lambda m: abs(m.get("time", 0.0) - t))

    # ── Numeric score (preferred) ──────────────────────────────────────────
    raw = nearest.get("score")
    if raw is not None:
        try:
            return float(min(1.0, max(0.0, raw)))
        except (TypeError, ValueError):
            pass

    # ── Strength string fallback ───────────────────────────────────────────
    strength_map = {"large": 1.0, "medium": 0.6, "small": 0.3}
    return strength_map.get(str(nearest.get("strength", "")).lower(), 0.0)


def _resolve_face(t: float, face_data: List[Dict]) -> float:
    """
    Return face reaction score [0.0-1.0] at time t.

    Scores based on face-area ratio relative to the standard frame area.
    A face covering ~5 % of the frame → 0.5; ~10 %+ → 1.0.
    Falls back to a binary presence score (0.3) when bbox data is absent.
    """
    if not face_data:
        return 0.0

    candidates = [
        f
        for f in face_data
        if isinstance(f, dict) and abs(f.get("time", -9999.0) - t) <= FACE_WINDOW
    ]
    if not candidates:
        return 0.0

    best = 0.0
    for entry in candidates:
        bbox = entry.get("bbox", [])
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                # bbox format: [x, y, w, h]  OR  [x1, y1, x2, y2]
                # MomentMiner uses [x, y, w, h] — compute area accordingly
                w = float(bbox[2])
                h = float(bbox[3])
                face_area = w * h
                ratio = face_area / FRAME_AREA
                # Scale: 5 % → 0.5, 10 %+ → 1.0  (linear up to cap)
                score = min(1.0, ratio * 10.0)
                best = max(best, score)
            except (TypeError, ValueError, ZeroDivisionError):
                # Malformed bbox — treat as binary presence
                best = max(best, 0.3)
        else:
            # Record present but no bbox → binary presence
            best = max(best, 0.3)

    return round(best, 4)


def _resolve_beat(t: float, beats: List[float]) -> float:
    """
    Return beat alignment score [0.0-1.0] at time t.

    Score decays linearly from 1.0 (exactly on the beat) to 0.0
    at BEAT_TOLERANCE seconds away.
    """
    if not beats:
        return 0.0

    nearest_beat = min(beats, key=lambda b: abs((b.get("time", 0.0) if isinstance(b, dict) else b) - t))
    b_time = nearest_beat.get("time", 0.0) if isinstance(nearest_beat, dict) else nearest_beat
    b_energy = nearest_beat.get("energy", 1.0) if isinstance(nearest_beat, dict) else 1.0

    dist = abs(b_time - t)

    if dist >= BEAT_TOLERANCE:
        return 0.0

    alignment = 1.0 - (dist / BEAT_TOLERANCE)
    return round(alignment * b_energy, 4)


def _resolve_dialogue(
    t: float,
    candidate_moments: List[Dict],
    narrative_data: Optional[Dict],
) -> float:
    """
    Return dialogue / emotion signal [0.0-1.0] at time t.

    Source priority:
      1. candidate_moments of type "dialogue" within DIALOGUE_WINDOW
      2. narrative_data["emotion_timeline"] entries
      3. 0.0  (signal absent — valid for optional input per spec)
    """
    # ── Priority 1: dialogue candidate moments ────────────────────────────
    dialogue_moments = [
        m
        for m in candidate_moments
        if isinstance(m, dict)
        and m.get("type") == "dialogue"
        and abs(m.get("time", -9999.0) - t) <= DIALOGUE_WINDOW
    ]
    if dialogue_moments:
        nearest = min(dialogue_moments, key=lambda m: abs(m.get("time", 0.0) - t))
        return float(min(1.0, max(0.0, nearest.get("score", 0.0))))

    # ── Priority 2: narrative_data emotion timeline ───────────────────────
    if narrative_data and isinstance(narrative_data, dict):
        emotion_tl = narrative_data.get("emotion_timeline", [])
        if isinstance(emotion_tl, list):
            nearby = [
                e
                for e in emotion_tl
                if isinstance(e, dict)
                and abs(e.get("time", -9999.0) - t) <= DIALOGUE_WINDOW
            ]
            if nearby:
                nearest = min(nearby, key=lambda e: abs(e.get("time", 0.0) - t))
                return float(min(1.0, max(0.0, nearest.get("score", 0.0))))

        # Flat sentiment_score field (some Gemini responses)
        sentiment = narrative_data.get("sentiment_score")
        if sentiment is not None:
            try:
                return float(min(1.0, max(0.0, sentiment)))
            except (TypeError, ValueError):
                pass

    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  Timeline construction
# ══════════════════════════════════════════════════════════════════════════════


def _build_timeline_timestamps(
    candidate_moments: List[Dict],
    motion_scores: List[Dict],
    face_data: List[Dict],
    beats: List[Any],
    duration: float,
) -> List[float]:
    """
    Collect all unique signal timestamps across every data source,
    then fill any sparse gaps with SAMPLE_INTERVAL-spaced samples to
    guarantee smooth curve coverage across the full video duration.

    Returns a sorted list of timestamps.
    """
    times: set = set()

    # Candidate moment timestamps
    for m in candidate_moments:
        t = m.get("time")
        if t is not None:
            try:
                times.add(round(float(t), 3))
            except (TypeError, ValueError):
                pass

    # Motion signal timestamps
    for m in motion_scores:
        t = m.get("time")
        if t is not None:
            try:
                times.add(round(float(t), 3))
            except (TypeError, ValueError):
                pass

    # Face tracking timestamps
    for f in face_data:
        t = f.get("time")
        if t is not None:
            try:
                times.add(round(float(t), 3))
            except (TypeError, ValueError):
                pass

    # Beat timestamps
    for b in beats:
        try:
            t = b.get("time") if isinstance(b, dict) else b
            times.add(round(float(t), 3))
        except (TypeError, ValueError):
            pass

    # Dense filler samples so the curve has no large gaps
    import random
    if duration > 0:
        sample_t = 0.0
        while sample_t <= duration + 1e-9:
            # Jitter the sample points slightly to break perfect periodicity
            # which can cause "harmonic" alignment with regular beats.
            jitter = (random.random() - 0.5) * 0.1 * SAMPLE_INTERVAL
            times.add(round(max(0, min(duration, sample_t + jitter)), 3))
            sample_t = round(sample_t + SAMPLE_INTERVAL, 3)

    sorted_times = sorted(times)
    deduped = []
    for t in sorted_times:
        if not deduped or (t - deduped[-1]) > 0.02:
            deduped.append(t)
    return deduped


# ══════════════════════════════════════════════════════════════════════════════
#  Curve computation
# ══════════════════════════════════════════════════════════════════════════════


def _compute_retention_curve(
    timestamps: List[float],
    motion_scores: List[Dict],
    face_data: List[Dict],
    beats: List[Any],
    candidate_moments: List[Dict],
    narrative_data: Optional[Dict],
) -> List[Dict]:
    """
    Evaluate R(t) = 0.35*M + 0.25*F + 0.20*B + 0.20*D at every timestamp.

    Returns a list of entries:
        {
            "time":       float,
            "score":      float,          # raw R(t) before smoothing
            "components": {               # per-signal breakdown for debug
                "motion":   float,
                "face":     float,
                "beat":     float,
                "dialogue": float,
            }
        }
    """
    curve = []
    for t in timestamps:
        M = _resolve_motion(t, motion_scores)
        F = _resolve_face(t, face_data)
        B = _resolve_beat(t, beats)
        D = _resolve_dialogue(t, candidate_moments, narrative_data)

        score = (
            WEIGHT_MOTION * M + WEIGHT_FACE * F + WEIGHT_BEAT * B + WEIGHT_DIALOGUE * D
        )
        score = round(min(1.0, max(0.0, score)), 4)

        curve.append(
            {
                "time": round(t, 3),
                "score": score,
                "components": {
                    "motion": round(M, 4),
                    "face": round(F, 4),
                    "beat": round(B, 4),
                    "dialogue": round(D, 4),
                },
            }
        )

    return curve


# ══════════════════════════════════════════════════════════════════════════════
#  Smoothing
# ══════════════════════════════════════════════════════════════════════════════


def _smooth_curve(curve: List[Dict], window: int = SMOOTH_WINDOW) -> List[Dict]:
    """
    Apply a centred moving-average to the retention scores (window = 5 per spec).

    Edge points use whatever neighbours are available — no zero-padding.
    The component breakdown is preserved unchanged (only .score is smoothed).
    """
    n = len(curve)
    if n == 0:
        return curve

    scores = [c["score"] for c in curve]
    half = window // 2
    smoothed = []

    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window_scores = scores[lo:hi]
        avg = sum(window_scores) / len(window_scores)

        entry = dict(curve[i])
        entry["score"] = round(avg, 4)
        smoothed.append(entry)

    return smoothed


# ══════════════════════════════════════════════════════════════════════════════
#  Peak detection
# ══════════════════════════════════════════════════════════════════════════════


def _detect_peaks(
    curve: List[Dict],
    min_gap: float = MIN_PEAK_GAP,
) -> Tuple[List[Dict], float]:
    """
    Detect retention peaks above the adaptive threshold:
        peak_threshold = mean + 1.2 * std  (per spec)

    Algorithm:
      1. Compute threshold using population std-dev (deterministic).
      2. Find every local maximum that exceeds the threshold.
      3. Deduplicate clusters: within MIN_PEAK_GAP seconds, keep the highest.

    Returns:
        peaks     — list of {"time": float, "score": float}, sorted by time
        threshold — the computed threshold value (for debug export)
    """
    if not curve:
        return [], 0.0

    scores = [c["score"] for c in curve]

    if len(scores) < 2:
        threshold = scores[0] if scores else 0.0
        return (
            [{"time": curve[0]["time"], "score": curve[0]["score"]}] if scores else [],
            round(threshold, 4),
        )

    mean_s = statistics.mean(scores)
    # Population std-dev — consistent regardless of sample size
    std_s = statistics.pstdev(scores)
    threshold = mean_s + PEAK_SIGMA_MULT * std_s

    # If the curve is extremely flat, there are no meaningful peaks.
    if std_s < 0.005:
        best = max(curve, key=lambda c: c["score"])
        if best["score"] > 0.05:
            return [{"time": best["time"], "score": round(best["score"], 4)}], round(threshold, 4)
        return [], round(threshold, 4)

    # ── Pass 1: collect local maxima above threshold ───────────────────────
    n = len(curve)
    raw_peaks: List[Dict] = []

    for i in range(n):
        s = curve[i]["score"]
        if s < threshold or s <= 0.001:
            continue

        # Local maximum: score >= both immediate neighbours
        left = curve[i - 1]["score"] if i > 0 else s
        right = curve[i + 1]["score"] if i < n - 1 else s

        if s >= left and s >= right and (s > left or s > right):
            # Add a tiny amount of score-based jitter (or "peakiness" tie-break)
            # to prevent perfectly equal scores from being picked at uniform gaps.
            # Local max quality: how much it stands out from neighbors
            peakiness = (s - left) + (s - right)
            raw_peaks.append(
                {
                    "time": curve[i]["time"],
                    "score": round(s, 4),
                    "_peakiness": peakiness
                }
            )

    if not raw_peaks:
        # Threshold was set too high (flat curve) — return the single global max
        best = max(curve, key=lambda c: c["score"])
        if best["score"] > 0.0:
            return [{"time": best["time"], "score": round(best["score"], 4)}], round(
                threshold, 4
            )
        return [], round(threshold, 4)

    # ── Pass 2: deduplicate clusters (greedy, highest-score first) ────────
    final_peaks: List[Dict] = []

    import random
    # Tie-break with peakiness if scores are exactly equal. Add random jitter
    # to avoid chronological uniformity when scores and peakiness are identical.
    for peak in sorted(raw_peaks, key=lambda p: (-p["score"], -p.get("_peakiness", 0), random.random())):
        # Accept if it is at least MIN_PEAK_GAP away from every accepted peak
        if all(abs(peak["time"] - kept["time"]) >= min_gap for kept in final_peaks):
            final_peaks.append({k: v for k, v in peak.items() if not k.startswith("_")})

    # Return chronological order
    final_peaks.sort(key=lambda p: p["time"])
    return final_peaks, round(threshold, 4)


# ══════════════════════════════════════════════════════════════════════════════
#  Duration inference
# ══════════════════════════════════════════════════════════════════════════════


def _estimate_duration(
    profile_data: Dict[str, Any],
    candidate_moments: List[Dict],
    motion_scores: List[Dict],
    face_data: List[Dict],
    beats: List[Any],
) -> float:
    """
    Infer video duration from the richest available source in profile_data.

    Priority order:
      1. Explicit "duration" field
      2. Shot list (shots[-1]["end"])
      3. Max candidate moment time  +3 s buffer
      4. Max motion timestamp       +2 s buffer
      5. Max face timestamp         +2 s buffer
      6. Max beat timestamp         +2 s buffer
      7. 30.0 s absolute fallback
    """
    dur = profile_data.get("duration", 0.0)
    if dur and isinstance(dur, (int, float)) and float(dur) > 0:
        return float(dur)

    shots = profile_data.get("shots", [])
    if shots and isinstance(shots, list):
        try:
            return max(s.get("end", 0.0) for s in shots if isinstance(s, dict))
        except (ValueError, TypeError):
            pass

    if candidate_moments:
        try:
            return max(m.get("time", 0.0) for m in candidate_moments) + 3.0
        except (ValueError, TypeError):
            pass

    if motion_scores:
        try:
            return max(m.get("time", 0.0) for m in motion_scores) + 2.0
        except (ValueError, TypeError):
            pass

    if face_data:
        try:
            return max(f.get("time", 0.0) for f in face_data) + 2.0
        except (ValueError, TypeError):
            pass

    if beats:
        try:
            return max(b.get("time", 0.0) if isinstance(b, dict) else b for b in beats) + 2.0
        except (ValueError, TypeError):
            pass

    return 30.0  # Absolute fallback


# ══════════════════════════════════════════════════════════════════════════════
#  Main engine class
# ══════════════════════════════════════════════════════════════════════════════


class RetentionCurveEngine:
    """
    Builds a time-indexed retention curve and identifies high-engagement peaks
    so the editor can prioritize moments with the highest virality probability.

    Usage (direct):
        engine  = RetentionCurveEngine()
        result  = engine.analyse(profile_data, job_dir="/path/to/job")
        peaks   = result["retention_peaks"]
        summary = result["retention_summary"]

    Usage (convenience):
        from Content_Intelligence.retention_curve_engine import analyse_retention
        result = analyse_retention(profile_data, job_dir=job_dir)

    Writes to profile_data:
        profile_data["retention_peaks"]   — list of peak dicts
        profile_data["retention_curve"]   — full smoothed time-series
        profile_data["retention_summary"] — peak_count / strongest_peak / peak_times
    """

    # ------------------------------------------------------------------
    def analyse(
        self,
        profile_data: Dict[str, Any],
        job_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Public entry point.  Never raises — returns DEFAULT_RESULT on any error.

        Args:
            profile_data:  Pipeline profile dict.  Reads:
                             - candidate_moments   (from MomentMiner)
                             - motion_scores       (from motion analysis)
                             - subject_tracking    (face tracking data)
                             - beat_data           (from BeatEngine)
                             - narrative_data      (optional Gemini output)
                             - duration            (float, seconds)
                             - shots               (shot boundary list)
            job_dir:       Optional path where debug JSON will be written.

        Returns:
            {
                "retention_peaks":   list[dict],   # [{"time": float, "score": float}, ...]
                "retention_curve":   list[dict],   # full smoothed time-series
                "retention_summary": dict          # peak_count / strongest_peak / peak_times
            }
        """
        try:
            return self._run(profile_data, job_dir)
        except Exception as exc:
            logger.warning(
                f"📈 [Retention] analyse() failed unexpectedly: {exc}. "
                "Returning safe defaults."
            )
            import traceback

            logger.debug(traceback.format_exc())
            return {
                "retention_peaks": DEFAULT_PEAKS.copy(),
                "retention_curve": [],
                "retention_summary": {
                    "peak_count": 0,
                    "strongest_peak": 0.0,
                    "peak_times": [],
                },
            }

    # ------------------------------------------------------------------
    def _run(
        self,
        profile_data: Dict[str, Any],
        job_dir: Optional[str],
    ) -> Dict[str, Any]:

        # ── 0. Pull all signal sources from profile_data ───────────────────
        candidate_moments: List[Dict] = profile_data.get("candidate_moments", [])
        motion_scores: List[Dict] = profile_data.get("motion_scores", [])
        face_data: List[Dict] = profile_data.get("subject_tracking", [])
        narrative_data: Optional[Dict] = profile_data.get("narrative_data")

        # Normalise beat_data — pipeline stores it as {"beats": [...]} or list
        beat_raw = profile_data.get("beat_data", {})
        beats: List[Any] = []
        if isinstance(beat_raw, dict):
            for b in beat_raw.get("beats", []):
                if isinstance(b, dict):
                    beats.append(b)
                else:
                    try:
                        beats.append(float(b))
                    except (TypeError, ValueError):
                        pass
        elif isinstance(beat_raw, list):
            for b in beat_raw:
                if isinstance(b, dict):
                    beats.append(b)
                else:
                    try:
                        beats.append(float(b))
                    except (TypeError, ValueError):
                        pass

        # ── 1. Estimate duration ───────────────────────────────────────────
        duration = _estimate_duration(
            profile_data, candidate_moments, motion_scores, face_data, beats
        )

        logger.info(
            f"📈 [Retention] Starting analysis — "
            f"duration={duration:.1f}s | "
            f"moments={len(candidate_moments)} | "
            f"motion_pts={len(motion_scores)} | "
            f"face_pts={len(face_data)} | "
            f"beats={len(beats)}"
        )

        if duration <= 0:
            logger.warning("📈 [Retention] Zero duration — returning safe defaults.")
            return {
                "retention_peaks": DEFAULT_PEAKS.copy(),
                "retention_curve": [],
                "retention_summary": {
                    "peak_count": 0,
                    "strongest_peak": 0.0,
                    "peak_times": [],
                },
            }

        # ── 2. Build unified time-indexed timeline ─────────────────────────
        timestamps = _build_timeline_timestamps(
            candidate_moments, motion_scores, face_data, beats, duration
        )
        logger.info(f"📈 [Retention] Timeline: {len(timestamps)} sample points")

        # ── 3. Compute raw R(t) at every timestamp ─────────────────────────
        raw_curve = _compute_retention_curve(
            timestamps,
            motion_scores,
            face_data,
            beats,
            candidate_moments,
            narrative_data,
        )

        # ── 4. Smooth with moving average (window = 5) ─────────────────────
        smooth_curve = _smooth_curve(raw_curve, window=SMOOTH_WINDOW)
        logger.info(
            f"📈 [Retention] Smoothing complete — "
            f"window={SMOOTH_WINDOW} | points={len(smooth_curve)}"
        )

        # Public lean curve: time + score only (omit component breakdown)
        retention_curve = [
            {"time": c["time"], "score": c["score"]} for c in smooth_curve
        ]

        # ── 5. Detect peaks (mean + 1.2 * std) ────────────────────────────
        peaks, threshold = _detect_peaks(smooth_curve, min_gap=MIN_PEAK_GAP)

        # ── 6. Build summary ───────────────────────────────────────────────
        peak_summary = self._build_peak_summary(peaks)

        logger.info(
            f"📈 [Retention] Peaks detected: {peak_summary['peak_count']} | "
            f"threshold={threshold:.4f} | "
            f"strongest={peak_summary['strongest_peak']:.4f}"
        )
        if peaks:
            logger.info(
                f"📈 [Retention] Peak times: {[round(p['time'], 2) for p in peaks]}"
            )

        # ── 7. Export debug file ───────────────────────────────────────────
        self._export_debug(
            raw_curve=raw_curve,
            smooth_curve=smooth_curve,
            peaks=peaks,
            threshold=threshold,
            peak_summary=peak_summary,
            duration=duration,
            job_dir=job_dir,
        )

        # ── 8. Write back to profile_data ──────────────────────────────────
        profile_data["retention_peaks"] = peaks
        profile_data["retention_curve"] = retention_curve
        profile_data["retention_summary"] = peak_summary

        return {
            "retention_peaks": peaks,
            "retention_curve": retention_curve,
            "retention_summary": peak_summary,
        }

    # ------------------------------------------------------------------
    def _compute_retention_curve(self, segments: List[Dict], niche: str = "GLOBAL") -> List[float]:
        """
        Computes a second-by-second retention probability score.
        Incorporates Niche Weights (R1), Hook Multipliers (R2), and Cliff Detection (R3).
        """
        weights = NICHE_WEIGHT_MATRIX.get(niche.upper(), NICHE_WEIGHT_MATRIX["GLOBAL"])
        curve = []
        
        for i, seg in enumerate(segments):
            # Base Score from Visual Intelligence
            base_score = (
                seg.get("motion_score", 0) * weights["motion"] +
                seg.get("face_score", 0) * weights["face"] +
                seg.get("object_score", 0) * weights["object"]
            )
            
            # --- HOOK WINDOW MULTIPLIER (R2) ---
            # The first 3 seconds are 5x more important for overall video survival.
            timestamp = seg.get("start_time", 0)
            if timestamp <= 3.0:
                base_score *= 1.5 # 50% boost to retention score in hook window
            
            # --- CLIFF / DEATH-ZONE DETECTION (R3) ---
            # If a segment has < 0.2 visual score and lasts > 2s, it's a "Cliff".
            duration = seg.get("end_time", 0) - seg.get("start_time", 0)
            if base_score < 0.2 and duration > 2.0:
                seg["is_cliff"] = True
                seg["retention_risk"] = "high"
                base_score *= 0.5 # Penalty for boring long shots
            else:
                seg["is_cliff"] = False
            
            curve.append(round(base_score, 4))
            
        return curve

    def get_virality_score(self, retention_curve: List[float]) -> float:
        """
        Predicts potential virality (0-100) based on curve stability.
        Upgraded R4: Penalizes 'spiky' curves with deep valleys.
        """
        if not retention_curve: return 0.0
        avg = sum(retention_curve) / len(retention_curve)
        
        # Stability check: How often does the score drop below 0.3?
        valleys = [v for v in retention_curve if v < 0.3]
        valley_penalty = len(valleys) * 5
        
        score = (avg * 100) - valley_penalty
        return max(0.0, min(100.0, score))

    # ------------------------------------------------------------------
    def _build_peak_summary(self, peaks: List[Dict]) -> Dict:
        """
        Build the standardised peak summary block.

        Shape:
            {
                "peak_count":     int,
                "strongest_peak": float,
                "peak_times":     list[float]
            }
        """
        if not peaks:
            return {
                "peak_count": 0,
                "strongest_peak": 0.0,
                "peak_times": [],
            }

        return {
            "peak_count": len(peaks),
            "strongest_peak": round(max(p["score"] for p in peaks), 4),
            "peak_times": [p["time"] for p in peaks],
        }

    # ------------------------------------------------------------------
    def _export_debug(
        self,
        raw_curve: List[Dict],
        smooth_curve: List[Dict],
        peaks: List[Dict],
        threshold: float,
        peak_summary: Dict,
        duration: float,
        job_dir: Optional[str],
    ) -> None:
        """
        Write retention_curve_debug.json to job_dir (or cwd as fallback).

        File structure:
            {
                export_timestamp,
                duration_analysed,
                formula,
                weights,
                smoothing,
                peak_detection,
                summary,               ← peak_count / strongest_peak / peak_times
                peaks,                 ← [{time, score}, ...]
                retention_curve,       ← lean [{time, score}, ...] (smoothed)
                retention_curve_raw,   ← [{time, score, components}, ...] (raw)
                retention_curve_debug  ← [{time, score, components}, ...] (smoothed, full)
            }
        """
        debug_payload = {
            "export_timestamp": datetime.now().isoformat(),
            "duration_analysed": round(duration, 3),
            "formula": "R(t) = 0.35 * M + 0.25 * F + 0.20 * B + 0.20 * D",
            "weights": {
                "motion": WEIGHT_MOTION,
                "face": WEIGHT_FACE,
                "beat": WEIGHT_BEAT,
                "dialogue": WEIGHT_DIALOGUE,
            },
            "smoothing": {
                "method": "centred_moving_average",
                "window": SMOOTH_WINDOW,
            },
            "peak_detection": {
                "method": "mean + 1.2 * std (population)",
                "threshold": threshold,
                "min_gap_s": MIN_PEAK_GAP,
            },
            "summary": peak_summary,
            "peaks": peaks,
            "retention_curve": [
                {"time": c["time"], "score": c["score"]} for c in smooth_curve
            ],
            "retention_curve_raw": raw_curve,
            "retention_curve_debug": smooth_curve,
        }

        out_dir = job_dir if (job_dir and os.path.isdir(job_dir)) else "."
        out_path = os.path.join(out_dir, "retention_curve_debug.json")

        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(debug_payload, fh, indent=2)
            logger.info(f"📈 [Retention] Debug export → {out_path}")
        except OSError as exc:
            logger.warning(f"📈 [Retention] Could not write debug file: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  Module-level singleton + convenience function
# ══════════════════════════════════════════════════════════════════════════════

_engine: Optional[RetentionCurveEngine] = None


def get_engine() -> RetentionCurveEngine:
    """Return the module-level singleton engine (lazy-initialised)."""
    global _engine
    if _engine is None:
        _engine = RetentionCurveEngine()
    return _engine


def analyse_retention(
    profile_data: Dict[str, Any],
    job_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function for orchestrator.py integration.

    Builds the full retention curve, detects engagement peaks, writes
    profile_data["retention_peaks"], profile_data["retention_curve"], and
    profile_data["retention_summary"] in-place, then returns the same dict.

    Never raises — safe defaults are returned on any failure.

    Args:
        profile_data:  Pipeline profile dict (reads + writes in-place).
        job_dir:       Optional job directory for debug JSON export.

    Returns:
        {
            "retention_peaks":   list[dict],   # [{"time": float, "score": float}, ...]
            "retention_curve":   list[dict],   # full smoothed time-series
            "retention_summary": dict          # peak_count / strongest_peak / peak_times
        }
    """
    return get_engine().analyse(profile_data, job_dir)
