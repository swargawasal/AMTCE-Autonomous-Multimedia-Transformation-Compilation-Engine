"""
pacing_architect.py
─────────────────────────────────────────────────────────────────────────────
ENERGY CURVE ENFORCEMENT

A human editor intentionally shapes the pacing arc:
  0–20%  of edit → slow/setup    (3–5s cuts)   Hook & establish
  20–60% of edit → build         (2–3s cuts)   Rising tension
  60–85% of edit → escalation    (1–2s cuts)   Fast momentum
  85–100% of edit → climax/loop  (variable)    Hit the peak, hard exit

RhythmTimelineBuilder already micro-splits segments; this module re-assigns
target durations per positional band AFTER the timeline is built, so the
energy curve is enforced without losing the creative selection.

Usage in orchestrator.py:
    from Intelligence_Modules.pacing_architect import PacingArchitect
    _pacing_architect = PacingArchitect()
    rhythm_timeline = _pacing_architect.shape(rhythm_timeline, pacing_style)
─────────────────────────────────────────────────────────────────────────────
"""

import logging
import random
from typing import Any, Dict, List, Optional

logger = logging.getLogger("pacing_architect")


# ── Pacing presets ─────────────────────────────────────────────────────────
# Each preset maps positional band → (min_dur, max_dur) in seconds.
# Bands: "setup" (0-20%), "build" (20-60%), "escalation" (60-85%), "climax" (85-100%)
_PRESETS: Dict[str, Dict[str, tuple]] = {
    "fast_cut": {
        "setup":       (1.2, 2.5),
        "build":       (0.8, 1.8),
        "escalation":  (0.5, 1.2),
        "climax":      (1.0, 2.5),  # brief hold then out
    },
    "slow_build": {
        "setup":       (3.5, 5.5),
        "build":       (2.5, 4.0),
        "escalation":  (1.5, 3.0),
        "climax":      (2.0, 5.0),
    },
    "rhythm_driven": {
        "setup":       (2.0, 3.5),
        "build":       (1.5, 2.8),
        "escalation":  (0.8, 2.0),
        "climax":      (1.5, 3.0),
    },
    "story_driven": {
        "setup":       (3.0, 5.0),
        "build":       (2.0, 4.0),
        "escalation":  (1.5, 3.0),
        "climax":      (2.0, 4.0),
    },
    "reaction_focused": {
        "setup":       (1.0, 2.5),
        "build":       (0.8, 2.0),
        "escalation":  (0.6, 1.5),
        "climax":      (1.5, 3.5),
    },
}
_DEFAULT_PRESET = _PRESETS["rhythm_driven"]


def _get_band(pos: float) -> str:
    """Map fractional position [0.0, 1.0] to energy band name."""
    if pos < 0.20:
        return "setup"
    elif pos < 0.60:
        return "build"
    elif pos < 0.85:
        return "escalation"
    else:
        return "climax"


class PacingArchitect:
    """
    Post-processes the rhythm_timeline to enforce a human-like energy curve.
    Adjusts segment end times to match the target duration for each band.
    Segments are trimmed (never extended) to keep start times valid.
    """

    def shape(
        self,
        timeline: List[Dict],
        pacing_style: Optional[str] = None,
        source_duration: float = 0.0,
    ) -> List[Dict]:
        """
        Shape a timeline's cut durations to match an energy curve.

        Args:
            timeline:        List of segment dicts with 'start', 'end', 'clip_id' etc.
            pacing_style:    One of the preset names. Defaults to 'rhythm_driven'.
            source_duration: Total source video duration. Used for band boundary.

        Returns Modified timeline (same structure, adjusted 'end' values only).
        """
        if not timeline:
            return timeline

        preset = _PRESETS.get(pacing_style or "", _DEFAULT_PRESET)
        total_segments = len(timeline)

        # Total edit duration (sum of segment durations)
        edit_dur = sum(
            max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
            for s in timeline
        )
        if edit_dur <= 0:
            return timeline

        shaped   = []
        acc_dur  = 0.0

        for i, seg in enumerate(timeline):
            seg = dict(seg)  # don't mutate in place
            start  = float(seg.get("start", 0.0))
            end    = float(seg.get("end",   0.0))
            orig_dur = max(0.1, end - start)

            # Position in edit [0,1]
            pos = acc_dur / edit_dur
            band = _get_band(pos)
            min_d, max_d = preset[band]

            # Target duration: clip original dur toward the band range
            if orig_dur > max_d:
                target_dur = random.uniform(max(max_d - 0.4, min_d), max_d)
            else:
                target_dur = orig_dur  # already within band, or too short (cannot extend)

            # Only TRIM (never extend beyond what's available in the source)
            new_end = start + target_dur
            seg["end"] = round(new_end, 3)

            # Safety: ensure start < end with 0.3s minimum
            if seg["end"] - start < 0.3:
                seg["end"] = round(start + 0.3, 3)

            # Annotate so downstream modules know which band this is
            seg["_pacing_band"]  = band
            seg["_pacing_style"] = pacing_style or "rhythm_driven"

            shaped.append(seg)
            acc_dur += max(0.0, seg["end"] - start)

        shaped_dur = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in shaped)
        logger.info(
            f"[PacingArchitect] ✅ Shaped {len(shaped)} segments | "
            f"preset={pacing_style or 'rhythm_driven'} | "
            f"before={edit_dur:.1f}s → after={shaped_dur:.1f}s | "
            f"bands=setup/build/escalation/climax"
        )
        return shaped
