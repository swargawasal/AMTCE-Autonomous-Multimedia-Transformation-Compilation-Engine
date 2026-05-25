"""Persona rule engine for selecting editing styles.

This module defines lightweight, auditable persona objects that capture
editorial preferences (pacing, transitions, zoom frequency, captions) and
provides a selector that maps moment-level analytics to a persona choice.
"""

from dataclasses import dataclass
from typing import Dict, Mapping, Optional


@dataclass(frozen=True)
class Persona:
    """Represents a configurable editing persona.

    Attributes:
        name: Display name of the persona.
        max_shot_length: Maximum shot duration (seconds) before forcing a cut.
        transition_style: Preferred transition type (e.g., "cut", "whip").
        zoom_frequency: How often to introduce zooms (e.g., "high", "low").
        caption_style: Caption rendering style keyword.
        pacing: Qualitative pacing label (e.g., "fast", "medium", "slow").
    """

    name: str
    max_shot_length: float
    transition_style: str
    zoom_frequency: str
    caption_style: str
    pacing: str


def load_personas() -> Dict[str, Persona]:
    """Return the predefined persona catalog keyed by name."""

    return {
        "HYPE": Persona(
            name="HYPE",
            max_shot_length=1.8,
            transition_style="whip",
            zoom_frequency="high",
            caption_style="bold",
            pacing="fast",
        ),
        "AESTHETIC": Persona(
            name="AESTHETIC",
            max_shot_length=3.0,
            transition_style="smooth",
            zoom_frequency="low",
            caption_style="minimal",
            pacing="slow",
        ),
        "ANALYST": Persona(
            name="ANALYST",
            max_shot_length=4.0,
            transition_style="cut",
            zoom_frequency="none",
            caption_style="clean",
            pacing="medium",
        ),
    }


def _to_float(value: Optional[object], default: float = 0.5) -> float:
    """Best-effort conversion of numeric or categorical intensity to float.

    Accepts floats/ints in [0,1] or strings ("low"/"medium"/"high"). Values
    outside [0,1] are clipped. Unknown values fall back to the provided default.
    """

    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "low":
            return 0.2
        if lowered == "medium":
            return 0.5
        if lowered == "high":
            return 0.8

    return default


def select_persona(
    moment_analysis: Mapping[str, object],
    personas: Optional[Dict[str, Persona]] = None,
) -> Persona:
    """Select an editing persona based on moment analytics.

    Args:
        moment_analysis: Mapping with keys like ``energy_level``,
            ``motion_intensity``, and ``emotion_score``. Values can be floats
            in [0,1] or categorical strings ("low"/"medium"/"high"). Missing
            values default to neutral (0.5).
        personas: Optional preloaded persona dict; defaults to ``load_personas``.

    Returns:
        Persona: The chosen persona object.

    Selection heuristic (simple, auditable):
    - High energy or motion pushes toward HYPE.
    - Low energy + low motion favors AESTHETIC.
    - Neutral/analytical or low emotion with medium energy defaults to ANALYST.
    """

    catalog = personas or load_personas()

    energy = _to_float(moment_analysis.get("energy_level"))
    motion = _to_float(moment_analysis.get("motion_intensity"))
    emotion = _to_float(moment_analysis.get("emotion_score"))

    # Primary rules
    if energy >= 0.66 or motion >= 0.66:
        return catalog["HYPE"]

    if energy <= 0.35 and motion <= 0.35:
        return catalog["AESTHETIC"]

    # Analytical/neutral fallback: when emotion is muted or balanced.
    if emotion <= 0.4:
        return catalog["ANALYST"]

    # Default to balanced analyst persona to avoid overfitting.
    return catalog["ANALYST"]

