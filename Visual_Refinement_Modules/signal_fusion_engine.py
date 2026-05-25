"""
Signal Fusion Engine 2.0
------------------------
Fuses multiple streams of content intelligence (Emotion, Subject, Motion, Retention, Beat)
into a single composite score and applies heuristic overrides for human-like judgment.

This is the SINGLE SOURCE OF TRUTH for moment scoring.
timeline_reconstructor's fallback path uses these same weights.

Signal priority (viral hierarchy):
    1. Emotion (dominant)  — how the viewer feels
    2. Subject             — subject focus and presence
    3. Motion              — visual energy
    4. Retention           — past-performance signal (least trusted)
    5. Beat                — rhythmic alignment

Layer 2 overrides use ADDITIVE BONUSES (not multiplicative stacking).
Multiplicative rules compound unpredictably and push borderline moments
past legitimate high-signal ones. Additive bonuses are capped at +0.25
so the score remains interpretable.
"""

import logging
from typing import Any, Dict, List, Optional
import math

logger = logging.getLogger("signal_fusion_engine")

# --- SIGNAL WEIGHTS (Viral Hierarchy) ---
# These weights are the canonical source. timeline_reconstructor fallback
# path must mirror these — do not change one without changing the other.
WEIGHT_EMOTION   = 0.40  # dominant: viewer emotional response
WEIGHT_SUBJECT   = 0.25  # subject focus / face presence
WEIGHT_MOTION    = 0.20  # visual energy
WEIGHT_RETENTION = 0.10  # algorithmic past-performance (least trusted)
WEIGHT_BEAT      = 0.05  # rhythmic alignment

# Layer 2 additive bonuses — capped at BONUS_CEILING total
BONUS_EMOTION_ANCHOR  = 0.18  # strong emotion (≥0.75) — viewer locked in
BONUS_ATTENTION_SHIFT = 0.12  # subject change event — cuts feel alive
BONUS_HERO_MOMENT     = 0.10  # emotion + subject synergy — peak human moment
BONUS_CEILING         = 0.25  # total bonus cannot exceed this

# Dead zone penalty — no signal at all
DEAD_ZONE_PENALTY = 0.40  # multiply base_score by this


def fuse_signals(moment: Dict[str, Any], profile_data: Dict[str, Any]) -> float:
    """
    Computes a composite score (0.0–1.0) by fusing intelligence signals
    and applying Layer 2 heuristic overrides.

    Architecture note: this is an executor for signal math, not an editor.
    Gemini is the editorial brain. These scores inform Gemini's context
    and power the Python fallback path when Gemini is absent.
    """
    m_time = moment.get("time", 0.0)

    # ── LAYER 1: Base signal extraction ──────────────────────────────────────

    # Emotion: Gemini Vision spike preferred, fallback to moment_score
    emotion = float(moment.get("emotional_spike_score", moment.get("score", 0.0)))

    # Subject: focus_strength from tracking; fallback to face/motion inference
    subject = 0.0
    subject_shift = False
    for s in profile_data.get("subject_tracking", []):
        if abs(s.get("time", 0.0) - m_time) < 0.5:
            subject = float(s.get("focus_strength", 0.5 if moment.get("face_present") else 0.2))
            subject_shift = s.get("change_event", False)
            break

    # Motion: direct intensity
    motion = float(moment.get("motion_intensity", 0.0))

    # Retention: algorithmic curve signal
    retention = float(moment.get("retention_score", 0.0))

    # Beat: rhythmic alignment
    beat = 1.0 if moment.get("beat_aligned") else 0.0

    # Weighted base score
    base_score = (
        emotion   * WEIGHT_EMOTION +
        subject   * WEIGHT_SUBJECT +
        motion    * WEIGHT_MOTION +
        retention * WEIGHT_RETENTION +
        beat      * WEIGHT_BEAT
    )

    # ── LAYER 2: Additive heuristic bonuses ──────────────────────────────────
    # Bonuses are ADDITIVE, not multiplicative. Stacking multipliers
    # (e.g. 1.5 × 1.25 × 1.3 = 2.4375) makes borderline moments
    # outrank legitimate peaks. Additive bonuses are transparent and capped.

    bonus = 0.0
    tags = []

    # RULE 1: EMOTION ANCHOR — viewer locked in
    if emotion >= 0.75:
        bonus += BONUS_EMOTION_ANCHOR
        tags.append("emotion_anchor")

    # RULE 2: ATTENTION SHIFT — subject change wakes viewers up
    if subject_shift:
        bonus += BONUS_ATTENTION_SHIFT
        tags.append("attention_shift")

    # RULE 3: HERO MOMENT — emotion + subject synergy
    if emotion >= 0.6 and subject >= 0.6:
        bonus += BONUS_HERO_MOMENT
        tags.append("hero_moment")

    # Cap total bonus
    bonus = min(bonus, BONUS_CEILING)

    # DEAD ZONE: no signal at all — penalise before adding bonus
    # (a dead moment with a subject_shift is still a weak moment)
    if emotion < 0.2 and motion < 0.2 and subject < 0.2:
        base_score *= DEAD_ZONE_PENALTY
        tags.append("dead_zone")

    # NOTE: pseudo-random noise (math.sin time-based offset) was removed.
    # It added timestamp-position bias, not variance — moments at certain
    # timestamps got free score boosts unrelated to any signal.

    final_score = min(1.0, max(0.0, base_score + bonus))

    if tags:
        logger.debug(
            f"[FUSION] t={m_time:.2f}s | base={base_score:.3f} bonus={bonus:.3f} "
            f"final={final_score:.3f} | tags={tags}"
        )
    else:
        logger.debug(
            f"[FUSION] t={m_time:.2f}s | base={base_score:.3f} final={final_score:.3f}"
        )

    return final_score

def resolve_subject_focus(moment: Dict[str, Any], profile_data: Dict[str, Any]) -> str:
    """Detects the semantic focus type for a moment."""
    for s in profile_data.get("subject_tracking", []):
        if abs(s.get("time", 0.0) - moment.get("time", 0.0)) < 0.5:
            return s.get("subject", "environment")
    
    if moment.get("face_present"):
        return "face"
    if moment.get("motion_intensity", 0) > 0.5:
        return "movement"
    return "scene"