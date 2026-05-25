"""
Reaction_Engine/reaction_script_generator.py
---------------------------------------------
Generates a timed reaction script from the emotion analysis of a source video.

Reads from profile_data:
    - fused_moments      (SignalFusionEngine output — primary signal source)
    - emotional_spikes   (EmotionalSpikeDetector output — fallback)
    - expression_moments (ExpressionChangeDetector output — optional enrichment)
    - video_path         (used for Gemini label only)

Returns:
    List of ReactionLine dicts:
    [
        {
            "ts":       4.2,        # timestamp in source video (seconds)
            "text":     "No way!",  # what the reactor says
            "emotion":  "shocked",  # reactor expression to use
            "duration": 1.8,        # how long this reaction lasts (seconds)
            "trigger":  "emotion_anchor"  # why this moment was chosen
        },
        ...
    ]

Integration:
    Called by ReactionEngine.run() after SignalFusionEngine completes.
    Uses existing Gemini router (gemini_governor) if available, falls back to
    rule-based templates if Gemini is unavailable.
"""

import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("reaction_script_generator")

# ── Editorial Context Extraction ──────────────────────────────────────────────

_EDITORIAL_EMOTION_MAP = {
    # Fashion / luxury keywords → impressed
    "couture":      "impressed", "designer": "impressed", "luxury":    "impressed",
    "bespoke":      "impressed", "silk":      "impressed", "heritage":  "impressed",
    "rare":         "impressed", "limited":   "impressed", "artisan":   "impressed",
    # Energy / action keywords → hype
    "bold":         "hype",      "power":    "hype",      "stunning":  "hype",
    "iconic":       "hype",      "statement":"hype",       "hit":       "hype",
    # Surprise / unusual → shocked
    "unexpected":   "shocked",   "wait":     "shocked",   "suddenly":  "shocked",
    "unbelievable": "shocked",   "rare":     "shocked",
}

def _extract_editorial_chunks(profile_data: Dict[str, Any]) -> List[str]:
    """
    Pulls the editorial_script from profile_data and splits it into
    short, speakable phrases. Returns empty list if nothing found.
    NO API calls made.
    """
    script = (
        profile_data.get("monetization_data", {}).get("editorial_script", "")
        or profile_data.get("editorial_script", "")
    ).strip()

    if not script or len(script) < 10:
        return []

    # Split by sentence boundary (. ! ?)
    import re
    sentences = [s.strip() for s in re.split(r"[.!?]", script) if len(s.strip()) > 5]
    # Keep max 2 sentences, max 12 words each
    chunks = []
    for s in sentences[:2]:
        words = s.split()
        if len(words) > 12:
            s = " ".join(words[:10]) + "..."
        chunks.append(s)
    return chunks


def _editorial_chunk_to_emotion(chunk: str) -> str:
    """Map an editorial phrase to a reactor emotion. Falls back to 'impressed'."""
    lower = chunk.lower()
    for keyword, emotion in _EDITORIAL_EMOTION_MAP.items():
        if keyword in lower:
            return emotion
    return "impressed"  # safe default for editorial content


# ── Emotion spike type → reactor emotion mapping ──────────────────────────────
# Maps signal sources to reactor expressions and template lines.
_EMOTION_MAP = {
    "laughter":     ("laughing",  [
        "I can't— this is actually too good",
        "No way, I'm screaming right now",
        "Okay that got me 💀",
        "Dead. Absolutely deceased.",
    ]),
    "surprise":     ("shocked",   [
        "Wait— WHAT? No way that just happened",
        "Hold on, rewind that",
        "I did NOT see that coming",
        "Bro my jaw is on the floor right now",
    ]),
    "motion":       ("hype",      [
        "BRO THE ENERGY right now",
        "Did you see that?! Insane",
        "Okay okay okay — that was CLEAN",
        "The audacity. The AUDACITY.",
    ]),
    "reaction":     ("impressed", [
        "Okay I see you, this is smooth",
        "That's actually impressive",
        "Giving what it needs to give",
        "Understated excellence right there",
    ]),
    "expression":   ("impressed", [
        "That look said everything",
        "No words needed honestly",
        "I felt that",
    ]),
    "emotion_anchor": ("shocked",  [
        "I have no words right now",
        "peak content, peak content",
        "This is why I'm always online",
    ]),
    "hero_moment":  ("hype",      [
        "That right there is the moment",
        "THAT'S the shot. That's IT.",
        "Cinema. Pure cinema.",
    ]),
    "attention_shift": ("confused", [
        "Wait wait wait— what changed?",
        "Did anyone else clock that?",
        "Okay something just shifted",
    ]),
    "default":      ("neutral",   [
        "Okay...",
        "Hmm, interesting",
        "I'm watching this very closely",
        "Noted.",
    ]),
}

# Minimum emotion score to trigger a reaction line (below this = silent neutral)
_MIN_SCORE_FOR_SPEECH = 0.40

# Natural human reaction lag (seconds after the spike that the reactor starts speaking)
_REACTION_LAG = 0.6  # seconds

# Min gap between two reaction lines (seconds) to avoid overlapping
_MIN_LINE_GAP = 3.0

# Max number of reaction lines per video
_MAX_LINES = 12


def _pick_line(emotion_type: str) -> tuple:
    """
    Returns (reactor_expression, reaction_text) for a given emotion_type.
    Falls back to 'default' if not found.
    """
    entry = _EMOTION_MAP.get(emotion_type) or _EMOTION_MAP.get("default")
    expression, lines = entry
    return expression, random.choice(lines)


def _estimate_duration(text: str) -> float:
    """
    Estimate how long it takes to say a piece of text (seconds).
    Rough average: 120 words/minute → 2 words/second.
    Clamp to [1.0, 3.5].
    """
    words = len(text.split())
    duration = words / 2.0
    return round(max(1.0, min(3.5, duration)), 2)


def generate_reaction_script(
    profile_data: Dict[str, Any],
    max_lines: int = _MAX_LINES,
) -> List[Dict]:
    """
    Generate a timed reaction script from profile_data.

    Args:
        profile_data:  Pipeline profile dict. Reads fused_moments + emotional_spikes.
        max_lines:     Maximum number of reaction lines to generate.

    Returns:
        List of ReactionLine dicts sorted chronologically.
    """
    # ── 1. Collect trigger moments ────────────────────────────────────────────
    trigger_moments: List[Dict] = []

    # Primary: fused_moments (richest signal)
    fused = profile_data.get("fused_moments", [])
    for m in fused:
        if not isinstance(m, dict):
            continue
        score = float(m.get("fusion_score", m.get("score", 0.0)))
        if score >= _MIN_SCORE_FOR_SPEECH:
            trigger_moments.append({
                "time":    float(m.get("time", 0.0)),
                "score":   score,
                "trigger": m.get("editor_tag", m.get("source", "default")),
                "emotion": float(m.get("emotion", 0.0)),
                "face":    float(m.get("face", 0.0)),
            })

    # Fallback: raw emotional_spikes
    if not trigger_moments:
        for spike in profile_data.get("emotional_spikes", []):
            if not isinstance(spike, dict):
                continue
            score = float(spike.get("emotion_score", spike.get("score", 0.0)))
            if score >= _MIN_SCORE_FOR_SPEECH:
                trigger_moments.append({
                    "time":    float(spike.get("time", 0.0)),
                    "score":   score,
                    "trigger": spike.get("spike_type", "default"),
                    "emotion": score,
                    "face":    0.0,
                })

    if not trigger_moments:
        logger.info(
            "[REACTION_SCRIPT] No high-score moments found. "
            "Empty script — reactor will be silent."
        )
        return []

    # ── 0. INJECT EDITORIAL CONTEXT (zero new calls) ────────────────────────────
    editorial_chunks = _extract_editorial_chunks(profile_data)
    editorial_lines_injected = []
    
    inject_editorial = os.getenv("REACTION_EDITORIAL_INJECT", "yes").lower() in ("yes", "true", "1")

    if inject_editorial and editorial_chunks and trigger_moments:
        # Slot editorial chunk at the FIRST real emotional spike
        first_ts = sorted(trigger_moments, key=lambda m: m["time"])[0]["time"]
        for i, chunk in enumerate(editorial_chunks[:1]):  # Max 1 editorial line at start
            emotion = _editorial_chunk_to_emotion(chunk)
            editorial_lines_injected.append({
                "ts":       round(max(0.3, first_ts - 0.5), 3),  # just before first spike
                "text":     chunk,
                "emotion":  emotion,
                "duration": _estimate_duration(chunk),
                "trigger":  "editorial_context",
                "score":    0.95,  # high score = won't be overridden
            })
            logger.info(
                f"[REACTION_SCRIPT] 📝 Editorial context injected at "
                f"ts={first_ts:.2f}s | emotion={emotion} | text='{chunk}'"
            )

    # ── 2. Sort by time, apply min-gap filter ─────────────────────────────────
    trigger_moments.sort(key=lambda m: m["time"])
    filtered: List[Dict] = []
    last_ts = -999.0
    for m in trigger_moments:
        if m["time"] - last_ts >= _MIN_LINE_GAP:
            filtered.append(m)
            last_ts = m["time"]

    # ── 3. Cap to max_lines ────────────────────────────────────────────────────
    filtered = filtered[:max_lines]

    # ── 4. Generate reaction lines ─────────────────────────────────────────────
    reaction_lines: List[Dict] = []
    
    # Pre-fetch available emotions from the library so Gemini only picks real folders
    available_emotions = []
    try:
        from Reaction_Engine.reactor_library_manager import ReactorLibraryManager
        lib = ReactorLibraryManager()
        available_emotions = lib.list_available_emotions()
    except Exception as e:
        logger.warning(f"[REACTION_SCRIPT] Failed to load library emotions: {e}")
        
    if not available_emotions:
        available_emotions = ["shocked", "laughing", "impressed", "confused", "hype", "neutral"]

    # Gather visual context for Gemini
    editorial_text = " ".join(editorial_chunks) if editorial_chunks else profile_data.get("editorial_script", "")

    for i, m in enumerate(filtered):
        trigger_key = m["trigger"]
        score = m["score"]

        # Default rule-based selection
        expression, text = _pick_line(trigger_key)
        if score < 0.55:
            expression, text = _pick_line("default")
        if score >= 0.85:
            expression, text = _pick_line("emotion_anchor")

        # Intelligent Gemini Selection disabled to save API calls.
        # Deterministic rule-based selection `_pick_line` provides excellent accuracy at 0 API cost.

        # Duration: if using original clip audio (TTS off), use the spike's real duration.
        # Otherwise estimate from text length (used when TTS is active).
        use_clip_audio = os.getenv("REACTION_USE_CLIP_AUDIO", "no").lower() in ("yes", "true", "1")
        use_tts = os.getenv("REACTION_USE_TTS", "yes").lower() in ("yes", "true", "1")
        if use_clip_audio and not use_tts and m.get("duration", 0) > 0:
            duration = float(m["duration"])
        else:
            duration = _estimate_duration(text)

        reaction_lines.append({
            "ts":       round(m["time"] + _REACTION_LAG, 3),  # offset by lag
            "text":     text,
            "emotion":  expression,
            "duration": duration,
            "trigger":  trigger_key,
            "score":    round(m["score"], 4),
        })

        logger.info(
            f"[REACTION_SCRIPT] t={m['time']:.2f}s → +{_REACTION_LAG}s lag → "
            f"ts={m['time'] + _REACTION_LAG:.2f}s | "
            f"emotion={expression} | score={m['score']:.2f} | "
            f"text='{text}'"
        )

    # Prepend editorial context lines before template lines
    final_lines = editorial_lines_injected + reaction_lines

    logger.info(
        f"✅ [REACTION_SCRIPT] Generated {len(final_lines)} reaction lines "
        f"from {len(trigger_moments)} trigger moments."
    )
    return final_lines
