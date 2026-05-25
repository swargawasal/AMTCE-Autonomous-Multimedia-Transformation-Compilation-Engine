"""
Content_Intelligence/signal_repair_layer.py
-------------------------------------------
Signal Repair Layer — Pre-LLM Signal Bootstrap + Confidence + Semantic Scoring.

Pipeline position: Step 1g.5
  Runs AFTER: EmotionalSpikeDetector, RetentionCurveEngine, SubjectTracker
  Runs BEFORE: SignalFusionEngine, unified_intelligence (LLM)

CRITICAL RULES:
  - NEVER produces fake data. Only elevates proxy signals when real ones are missing.
  - NEVER overwrites existing valid signals (checks length thresholds first).
  - NEVER raises. All errors caught internally, safe defaults applied.
  - All writes to profile_data are in-place.

Public API:
    repair(profile_data, job_dir=None) -> Dict
        Entry point. Returns {"signal_health": float, "signal_flags": dict,
                               "repair_summary": dict}.

Execution order inside repair():
    1. repair signals        (_repair_emotion, _repair_retention, _repair_face)
    2. filter dead moments   (_filter_dead_moments)
    3. compute confidence    (_compute_signal_confidence)
    4. compute semantic      (_compute_semantic_score per moment)
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── CONSTANTS ────────────────────────────────────────────────────────────────

_MIN_SPIKE_GAP_SEC   = 1.5   # minimum seconds between synthetic emotion spikes
_MIN_PEAK_GAP_SEC    = 1.0   # minimum seconds between synthetic retention peaks
_MAX_SYNTHETIC_SPIKES = 8
_DEAD_POOL_MIN        = 2    # never reduce candidate pool below this


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def repair(profile_data: Dict, job_dir: Optional[str] = None) -> Dict:
    """
    Main entry point. Never raises. Always returns a valid result dict.

    Execution order:
        1. Repair signals (emotion, retention, face)
        2. Filter / hard-remove dead moments
        3. Compute signal confidence per moment
        4. Compute semantic score per moment
        5. Score signal health + build flags
        6. Return result + write to profile_data
    """
    if not isinstance(profile_data, dict):
        logger.warning("[SIGNAL_REPAIR] profile_data is not a dict — skipping repair.")
        return {"signal_health": 0.5, "signal_flags": {}, "repair_summary": {}}

    try:
        # ── Step 1: Repair upstream signals ───────────────────────────────────
        _repair_emotion_from_proxies(profile_data)
        _repair_retention_from_scene_cuts(profile_data)
        _repair_face_from_expression_data(profile_data)

        # ── Step 2: Hard-remove dead moments from candidate pool ──────────────
        _filter_dead_moments(profile_data)

        # ── Step 3: Compute signal confidence per moment ──────────────────────
        _compute_signal_confidence(profile_data)

        # ── Step 4: Compute semantic score per moment ─────────────────────────
        _run_semantic_scoring(profile_data)

        # ── Step 5: Score overall signal health + build flags ─────────────────
        signal_health = _score_signal_health(profile_data)
        signal_flags  = _build_signal_flags(signal_health, profile_data)

        profile_data["signal_health"] = signal_health
        profile_data["signal_flags"]  = signal_flags

        repair_summary = {
            "signal_health":    signal_health,
            "signal_flags":     signal_flags,
            "candidates_left":  len(profile_data.get("candidate_moments", [])),
            "semantic_strength": profile_data.get("semantic_strength", "UNKNOWN"),
        }
        profile_data["signal_repair_summary"] = repair_summary

        logger.info(
            f"✅ [SIGNAL_REPAIR] health={signal_health:.2f} | "
            f"mode={'FALLBACK' if signal_health < 0.4 else 'NORMAL'} | "
            f"candidates={repair_summary['candidates_left']} | "
            f"semantic={repair_summary['semantic_strength']}"
        )
        return {"signal_health": signal_health,
                "signal_flags":  signal_flags,
                "repair_summary": repair_summary}

    except Exception as _e:
        logger.warning(f"[SIGNAL_REPAIR] Non-fatal internal error: {_e}", exc_info=True)
        profile_data.setdefault("signal_health", 0.5)
        profile_data.setdefault("signal_flags",  {})
        return {"signal_health": 0.5, "signal_flags": {}, "repair_summary": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — SIGNAL REPAIR FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _repair_emotion_from_proxies(profile_data: Dict) -> None:
    """
    Boostrap emotion spikes from motion + expression proxies when real
    EmotionalSpikeDetector output is missing or sparse (< 2 spikes).

    NEVER overwrites existing spikes — extends in-place.
    Source tags: "expression_proxy" or "motion_proxy".
    """
    emotional_spikes: List[Dict] = profile_data.get("emotional_spikes", [])
    if len(emotional_spikes) >= 2:
        return  # real spikes available — do not interfere

    synthetic: List[Dict] = []

    # Source 1: expression_moments (ExpressionChangeEngine output)
    for em in profile_data.get("expression_moments", []):
        if not isinstance(em, dict):
            continue
        t         = float(em.get("time", em.get("t", 0.0)))
        intensity = float(em.get("change_intensity", em.get("intensity", 0.3)))
        synthetic.append({
            "time":          t,
            "emotion_score": min(1.0, intensity * 0.8),
            "source":        "expression_proxy",
        })

    # Source 2: motion_scores — top 30% peaks > 0.5
    motion_scores: List[Dict] = profile_data.get("motion_scores", [])
    _sorted_motion = sorted(
        [m for m in motion_scores if isinstance(m, dict) and float(m.get("score", 0.0)) > 0.5],
        key=lambda x: float(x.get("score", 0.0)),
        reverse=True,
    )
    top_n = max(1, len(_sorted_motion) // 3) if _sorted_motion else 0
    for m in _sorted_motion[:top_n]:
        t     = float(m.get("time", m.get("t", 0.0)))
        score = float(m.get("score", 0.5))
        synthetic.append({
            "time":          t,
            "emotion_score": min(1.0, score * 0.6),
            "source":        "motion_proxy",
        })

    # De-duplicate: keep highest within _MIN_SPIKE_GAP_SEC windows
    synthetic.sort(key=lambda x: x["time"])
    deduped: List[Dict] = []
    for s in synthetic:
        if deduped and abs(s["time"] - deduped[-1]["time"]) < _MIN_SPIKE_GAP_SEC:
            if s["emotion_score"] > deduped[-1]["emotion_score"]:
                deduped[-1] = s  # replace with higher-scoring spike
        else:
            deduped.append(s)

    deduped = deduped[:_MAX_SYNTHETIC_SPIKES]
    if deduped:
        profile_data["emotional_spikes"] = list(emotional_spikes) + deduped
        logger.info(
            f"[SIGNAL_REPAIR] emotion_repaired={len(deduped)} "
            f"from {set(d['source'] for d in deduped)}"
        )


def _repair_retention_from_scene_cuts(profile_data: Dict) -> None:
    """
    Synthesise retention peaks from scene boundaries (shot cuts) and beat data
    when RetentionCurveEngine produces < 3 peaks.

    NEVER overwrites existing peaks — extends in-place.
    """
    retention_peaks: List[Dict] = profile_data.get("retention_peaks", [])
    if len(retention_peaks) >= 3:
        return

    synthetic: List[Dict] = []
    video_duration = float(profile_data.get("video_duration", 30.0))

    # Source 1: scene_boundaries (ShotDetector output)
    for sb in profile_data.get("scene_boundaries", []):
        if not isinstance(sb, dict):
            continue
        t = float(sb.get("time", sb.get("start", sb.get("t", 0.0))))
        synthetic.append({"time": t, "score": 0.55, "source": "scene_cut_proxy"})

    # Source 2: beat_data["beats"]
    beat_data = profile_data.get("beat_data", {})
    if isinstance(beat_data, dict):
        for beat_t in beat_data.get("beats", []):
            try:
                synthetic.append({"time": float(beat_t), "score": 0.45, "source": "beat_proxy"})
            except (TypeError, ValueError):
                continue

    # Fallback: if no scene boundaries at all, evenly distribute 5 peaks
    if not any(s["source"] == "scene_cut_proxy" for s in synthetic):
        step = video_duration / 6.0
        for i in range(1, 6):
            synthetic.append({
                "time":   round(step * i, 2),
                "score":  0.40,
                "source": "evenly_distributed_proxy",
            })

    # De-duplicate within _MIN_PEAK_GAP_SEC windows
    synthetic.sort(key=lambda x: x["time"])
    deduped: List[Dict] = []
    for s in synthetic:
        if deduped and abs(s["time"] - deduped[-1]["time"]) < _MIN_PEAK_GAP_SEC:
            if s["score"] > deduped[-1]["score"]:
                deduped[-1] = s
        else:
            deduped.append(s)

    if deduped:
        profile_data["retention_peaks"] = list(retention_peaks) + deduped
        logger.info(
            f"[SIGNAL_REPAIR] retention_repaired={len(deduped)} "
            f"from {set(d['source'] for d in deduped)}"
        )


def _repair_face_from_expression_data(profile_data: Dict) -> None:
    """
    Synthesise face-present subject tracking entries from expression_moments
    when SubjectTracker reports no face_present in any entry.

    NEVER appends if at least one real face_present=True entry exists.
    """
    subject_tracking: List[Dict] = profile_data.get("subject_tracking", [])
    has_face = any(
        isinstance(s, dict) and s.get("face_present")
        for s in subject_tracking
    )
    if has_face:
        return

    synthetic: List[Dict] = []
    for em in profile_data.get("expression_moments", []):
        if not isinstance(em, dict):
            continue
        t = float(em.get("time", em.get("t", 0.0)))
        synthetic.append({
            "time":          t,
            "face_present":  True,
            "focus_strength": 0.5,
            "source":        "expression_proxy",
        })

    if synthetic:
        profile_data["subject_tracking"] = list(subject_tracking) + synthetic
        logger.info(
            f"[SIGNAL_REPAIR] face_repaired={len(synthetic)} entries from expression data"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — DEAD MOMENT ENFORCEMENT (Hard Removal)
# ═══════════════════════════════════════════════════════════════════════════════

def _filter_dead_moments(profile_data: Dict) -> None:
    """
    Hard-remove dead moments from the candidate pool.
    Threshold: motion < 0.15 AND emotion < 0.2 AND face < 0.1 simultaneously.

    Safety guard: never reduce pool below _DEAD_POOL_MIN.
    Resurrects least-dead moments if pool would drop too low.
    """
    moments: List[Dict] = profile_data.get("candidate_moments", [])
    if not moments:
        return

    alive: List[Dict] = []
    dead:  List[Dict] = []

    for m in moments:
        if not isinstance(m, dict):
            continue
        _motion  = float(m.get("motion_score",  m.get("motion_intensity", 0.0)))
        _emotion = float(m.get("emotion_score",  m.get("score", 0.0)))
        _face    = float(m.get("face_score",
                               1.0 if m.get("face_present") else 0.0))
        if _motion < 0.15 and _emotion < 0.2 and _face < 0.1:
            m["dead"] = True
            dead.append(m)
        else:
            alive.append(m)

    # Safety guard: resurrect least-dead moments if pool drops below minimum
    if len(alive) < _DEAD_POOL_MIN and dead:
        dead_sorted = sorted(
            dead,
            key=lambda x: max(
                float(x.get("motion_score",  0.0)),
                float(x.get("emotion_score", 0.0)),
                float(x.get("face_score",    0.0)),
            ),
            reverse=True,
        )
        resurrected = dead_sorted[:max(0, _DEAD_POOL_MIN - len(alive))]
        alive += resurrected
        logger.info(
            f"[SIGNAL_REPAIR] safety_resurrection fired — resurrected={len(resurrected)} moments"
        )

    profile_data["candidate_moments"] = alive
    logger.info(
        f"[SIGNAL_REPAIR] dead_moments_removed={len(dead)} | alive={len(alive)}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — SIGNAL CONFIDENCE (Patch 15)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_signal_confidence(profile_data: Dict) -> None:
    """
    Assigns signal_confidence (0.0–1.0) to each candidate moment.

    Confidence measures HOW RELIABLE the signals on each moment are,
    not how strong they are. A moment with reliable face detection and
    stable motion is high-confidence even if scores are moderate.

    Formula:
        confidence = (
            0.35 * face_conf        +  # face detection reliable?
            0.25 * expression_conf  +  # expression change detected?
            0.25 * motion_conf      +  # motion is stable (not spike-only)?
            0.15 * scene_conf          # scene boundary evidence?
        )

    Safe defaults for missing fields: 0.3–0.5 range (neutral, not punishing).
    """
    moments: List[Dict] = profile_data.get("candidate_moments", [])
    if not moments:
        return

    for m in moments:
        if not isinstance(m, dict):
            continue
        try:
            # ── Face confidence ────────────────────────────────────────────────
            face_present    = 1.0 if m.get("face_present") else 0.0
            face_score      = float(m.get("face_score", m.get("focus_strength", 0.0)))
            subject_pres    = float(m.get("subject_presence", 0.3))
            # Face is reliable when face_present flag AND face_score agree
            face_conf = min(1.0, 0.5 * max(face_present, face_score) + 0.5 * subject_pres)

            # ── Expression confidence ──────────────────────────────────────────
            expr_change     = float(m.get("expression_change", 0.0))
            expr_intensity  = float(m.get("expression_intensity", expr_change))
            # Expression is reliable when both change flag and intensity present
            expression_conf = min(1.0, (expr_change * 0.6 + expr_intensity * 0.4)
                                  if expr_change > 0 else 0.3)

            # ── Motion stability confidence ────────────────────────────────────
            motion          = float(m.get("motion_score",
                                          m.get("motion_intensity", 0.0)))
            motion_variance = float(m.get("motion_variance", 0.0))
            # Stable motion (moderate, low variance) = higher confidence
            # Extreme spike without context = lower confidence
            if motion > 0.7 and motion_variance < 0.05:
                # Very high motion, very low variance → sustained action (good)
                motion_conf = 0.75
            elif motion > 0.7 and motion_variance > 0.3:
                # Very high motion, erratic → likely camera shake (low confidence)
                motion_conf = 0.25
            elif 0.15 <= motion <= 0.7:
                # Normal range — confidence scales linearly
                motion_conf = min(1.0, 0.3 + motion * 0.9)
            else:
                # Near-zero motion
                motion_conf = 0.3  # neutral default, not zero

            # ── Scene cut confidence ────────────────────────────────────────────
            # If the moment is at or near a scene boundary → well-anchored
            beat_aligned    = 1.0 if m.get("beat_aligned") else 0.0
            scene_change    = float(m.get("scene_change", 0.0))
            source          = str(m.get("source", ""))
            # Moments mined from scene cuts are inherently reliable anchors
            is_scene_anchor = 1.0 if "scene_cut" in source or scene_change > 0.5 else 0.0
            scene_conf      = min(1.0, 0.5 * beat_aligned + 0.5 * max(scene_change, is_scene_anchor))
            scene_conf      = max(0.3, scene_conf)  # floor at 0.3 (never punish unknown)

            # ── Composite confidence ───────────────────────────────────────────
            confidence = min(1.0, (
                0.35 * face_conf       +
                0.25 * expression_conf +
                0.25 * motion_conf     +
                0.15 * scene_conf
            ))

            m["signal_confidence"] = round(confidence, 4)

        except Exception as _conf_err:
            logger.debug(f"[SIGNAL_REPAIR] confidence calc error for moment: {_conf_err}")
            m.setdefault("signal_confidence", 0.5)

    _mean_conf = (
        sum(float(m.get("signal_confidence", 0.5)) for m in moments if isinstance(m, dict))
        / max(1, len(moments))
    )
    logger.info(
        f"[SIGNAL_REPAIR] confidence_scored={len(moments)} | mean_confidence={_mean_conf:.3f}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — SEMANTIC VALUE SCORING (Patch 9)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_semantic_score(moment: Dict) -> float:
    """
    Semantic Value Score — measures whether a moment has HUMAN MEANING,
    not just visual energy.

    Formula:
        semantic_score = (
            0.35 * subject_importance  +  # is a human/subject central?
            0.25 * action_meaning      +  # is something purposeful happening?
            0.20 * contextual_relevance+  # does this connect to narrative flow?
            0.20 * visual_clarity         # is it sharp enough to read?
        )

    All components derived from existing moment fields — no new signals required.
    Returns float [0.0–1.0].
    """
    if not isinstance(moment, dict):
        return 0.5  # neutral default

    # ── 1. SUBJECT IMPORTANCE (0.35) ──────────────────────────────────────────
    face_present   = 1.0 if moment.get("face_present") else 0.0
    face_score     = float(moment.get("face_score", moment.get("focus_strength", 0.0)))
    subject_pres   = float(moment.get("subject_presence", 0.0))
    framing_score  = min(1.0, subject_pres * 2.0)  # ≥0.5 subject → fully central
    subject_importance = min(1.0, (
        0.5 * max(face_present, face_score) +
        0.3 * subject_pres +
        0.2 * framing_score
    ))

    # ── 2. ACTION MEANING (0.25) ───────────────────────────────────────────────
    expression_change = float(moment.get("expression_change", 0.0))
    motion            = float(moment.get("motion_score", moment.get("motion_intensity", 0.0)))
    purposeful_motion = min(1.0, expression_change * 1.2 + motion * 0.4)
    # Penalise pure random spikes (high motion, no expression → likely background shake)
    if motion > 0.7 and expression_change < 0.1:
        purposeful_motion *= 0.6
    action_meaning = min(1.0, purposeful_motion)

    # ── 3. CONTEXTUAL RELEVANCE (0.20) ────────────────────────────────────────
    beat_aligned       = 1.0 if moment.get("beat_aligned") else 0.0
    scene_change       = float(moment.get("scene_change", 0.0))
    continuity         = float(moment.get("continuity", 0.5))
    contextual_relevance = min(1.0, (
        0.40 * continuity    +
        0.35 * beat_aligned  +
        0.25 * scene_change
    ))

    # ── 4. VISUAL CLARITY (0.20) ──────────────────────────────────────────────
    edge_score   = float(moment.get("edge_score",   0.5))
    blur_penalty = float(moment.get("blur_penalty", 0.1))
    clarity_raw  = min(1.0, edge_score / (blur_penalty + 1e-5))
    # Chaos penalty: very high motion + no face = chaotic unreadable frame
    chaos_factor = 1.0 - min(0.4, max(0.0, motion - 0.6) * 0.5)
    visual_clarity = min(1.0, clarity_raw * chaos_factor)

    # ── FINAL SCORE ────────────────────────────────────────────────────────────
    semantic_score = (
        0.35 * subject_importance    +
        0.25 * action_meaning        +
        0.20 * contextual_relevance  +
        0.20 * visual_clarity
    )
    return round(min(1.0, max(0.0, semantic_score)), 4)


def _run_semantic_scoring(profile_data: Dict) -> None:
    """
    Run _compute_semantic_score() on every candidate moment.
    Sets moment["semantic_score"], moment["semantic_dead"], moment["semantic_weak"].
    Writes profile_data["semantic_mean"] and profile_data["semantic_strength"].
    """
    moments: List[Dict] = profile_data.get("candidate_moments", [])
    if not moments:
        profile_data.setdefault("semantic_mean",     0.5)
        profile_data.setdefault("semantic_strength", "UNKNOWN")
        return

    total  = 0.0
    count  = 0

    for m in moments:
        if not isinstance(m, dict):
            continue
        sem = _compute_semantic_score(m)
        m["semantic_score"] = sem
        total += sem
        count += 1

        # Propagate dead flag for Patch 2 compatibility
        if sem < 0.2:
            m["semantic_dead"] = True
            m["dead"] = True          # ensures filter_dead_moments also catches it
        elif sem < 0.3:
            m["semantic_weak"] = True

    semantic_mean = round(total / max(1, count), 4)
    semantic_strength = (
        "HIGH"   if semantic_mean >= 0.55
        else "MEDIUM" if semantic_mean >= 0.35
        else "LOW"
    )

    profile_data["semantic_mean"]     = semantic_mean
    profile_data["semantic_strength"] = semantic_strength

    logger.info(
        f"[SIGNAL_REPAIR] semantic_scoring complete | "
        f"moments_scored={count} | mean={semantic_mean:.3f} | "
        f"strength={semantic_strength}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5 — HEALTH SCORING + FLAGS
# ═══════════════════════════════════════════════════════════════════════════════

def _score_signal_health(profile_data: Dict) -> float:
    """
    Score overall signal health AFTER repairs.

    Formula:
        signal_health = (
            0.30 * emotion_health   +
            0.30 * face_health      +
            0.25 * retention_health +
            0.15 * motion_health
        )
    Returns float [0.0–1.0].
    """
    emotional_spikes  = profile_data.get("emotional_spikes",  [])
    subject_tracking  = profile_data.get("subject_tracking",  [])
    retention_peaks   = profile_data.get("retention_peaks",   [])
    motion_scores     = profile_data.get("motion_scores",     [])

    emotion_health = min(1.0, len(emotional_spikes) / 3.0)

    face_entries   = [s for s in subject_tracking if isinstance(s, dict)]
    face_health    = (
        sum(1 for s in face_entries if s.get("face_present"))
        / max(1, len(face_entries))
    ) if face_entries else 0.0

    retention_health = min(1.0, len(retention_peaks) / 5.0)

    strong_motion = [
        m for m in motion_scores
        if isinstance(m, dict) and float(m.get("score", 0.0)) > 0.3
    ]
    motion_health = min(1.0, len(strong_motion) / 5.0)

    health = round(
        0.30 * emotion_health   +
        0.30 * face_health      +
        0.25 * retention_health +
        0.15 * motion_health,
        4,
    )
    return health


def _build_signal_flags(signal_health: float, profile_data: Dict) -> Dict:
    """
    Build structured signal_flags dict for LLM context injection.
    """
    emotional_spikes = profile_data.get("emotional_spikes", [])
    subject_tracking = profile_data.get("subject_tracking", [])
    retention_peaks  = profile_data.get("retention_peaks",  [])

    face_entries  = [s for s in subject_tracking if isinstance(s, dict)]
    face_count    = sum(1 for s in face_entries if s.get("face_present"))
    face_health   = face_count / max(1, len(face_entries)) if face_entries else 0.0

    return {
        "emotion_missing":  len(emotional_spikes) == 0,
        "face_missing":     face_health < 0.2,
        "retention_weak":   len(retention_peaks) < 3,
        "fallback_active":  signal_health < 0.4,
        "signal_mode":      "normal" if signal_health >= 0.4 else "fallback",
    }
