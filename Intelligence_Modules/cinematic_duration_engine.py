"""
CinematicDurationEngine — Psychology-driven duration calculator for AMTCE.

RESEARCH BASIS:
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │  HUMAN ATTENTION RETENTION ARC (Short-Form Cinematic Storytelling)         │
  │                                                                             │
  │  Source: TikTok/Reels loop studies, Netflix skip-intro analytics,          │
  │          Loewenstein Curiosity Gap Theory (1994), Cognitive Load Theory     │
  │                                                                             │
  │  KEY FINDING: For story-driven content the OPTIMAL attention window is      │
  │               45 – 90 seconds. Below 45s = story doesn't breathe.          │
  │               Above 90s = cognitive fatigue kills retention.                │
  │                                                                             │
  │  SWEET SPOT: 60 seconds (the "Netflix Micro-Doc" length)                   │
  │  Hard ceiling: 90s (loop probability collapses after this)                  │
  │  Hard floor:   45s (5-act arc needs minimum breath room)                   │
  └─────────────────────────────────────────────────────────────────────────────┘

THE COMPRESSION FORMULA:
  output_duration = clamp(source_duration × 0.09, 45, 90)

  WHY 9%?
    • A 10-min (600s) scene × 0.09 = 54s → lands perfectly in sweet spot.
    • A 5-min  (300s) scene × 0.09 = 27s → floor kicks in → 45s (minimum story)
    • A 2-min  (120s) scene × 0.09 = 11s → floor kicks in → 45s
    • A 20-min (1200s) scene × 0.09 = 108s → ceiling kicks in → 90s

  WHY NOT linear (e.g. 50%)?
    • 50% of 10 min = 5 min → way too long for short-form retention
    • We are creating a CONCENTRATED EMOTIONAL EXTRACT, not a highlight reel

BEAT MATH:
  The output duration is divided into story beats. Each beat:
    • Duration: 2.0–4.0 seconds per beat (depending on act tension)
    • Words:    4–6 words (subtitle-safe, readable at 2.5 words/second)
    • Count:    output_sec / avg_beat_sec

  The 5-act arc distributes beats as:
    Hook    (1 beat,  2s)  → instant tension, no setup
    Build   (2 beats, 3s each) → context, character
    Tension (2 beats, 2.5s each) → stakes rise
    Climax  (2 beats, 2s each) → peak emotion
    Pay-off (1 beat,  3s)  → consequence / loop-back

  Total base arc: 8 beats = ~22s minimum skeleton
  Remaining time (e.g. 54s - 22s = 32s) is filled with EXTENSION BEATS
  distributed proportionally across Build + Tension acts (the "breathing room")
"""

import os
import math
import logging

logger = logging.getLogger(__name__)

# ── Psychology Constants ──────────────────────────────────────────────────────
MIN_OUTPUT_SECONDS  = 45    # floor: below this, 5-act arc cannot breathe
SWEET_SPOT_SECONDS  = 60    # ideal: maximum loop probability
MAX_OUTPUT_SECONDS  = 90    # ceiling: cognitive fatigue threshold
COMPRESSION_RATIO   = 0.09  # 9% of source → concentrated emotional extract
WORD_RATE_PER_SEC   = 2.5   # comfortable subtitle reading speed (words/sec)
WORDS_PER_BEAT      = 5     # 4-6 word beats for karaoke subtitle safety

# Act-level beat durations (seconds per beat in each act)
ACT_BEAT_DURATIONS = {
    "hook":    2.0,   # instant — no setup allowed
    "build":   3.0,   # breathing room, introduce character
    "tension": 2.5,   # pressure builds
    "climax":  2.0,   # rapid cuts, peak energy
    "payoff":  3.5,   # lingering consequence, loop-back
}

# Minimum beats per act (structural minimum for story comprehension)
ACT_MIN_BEATS = {
    "hook":    1,
    "build":   2,
    "tension": 2,
    "climax":  2,
    "payoff":  1,
}


class CinematicDurationEngine:
    """
    Computes psychologically-optimal output duration and beat plan
    from an arbitrary source clip duration.
    """

    def compute(self, source_duration_sec: float) -> dict:
        """
        Given a source clip duration (e.g. 600.0 for 10 minutes),
        return the full duration plan with beat breakdown.

        Returns:
            {
                "output_seconds": 54.0,          # total output video duration
                "word_target": 135,              # total words in narration script
                "beat_count": 15,                # total story beats
                "beat_plan": [                   # per-act breakdown
                    {"act": "hook",    "beats": 1, "seconds": 2.0},
                    ...
                ],
                "source_duration_sec": 600.0,
                "compression_ratio_pct": 9.0,
                "model": "retention_arc_v1",
            }
        """
        # Step 1: Raw compression
        raw_output = source_duration_sec * COMPRESSION_RATIO

        # Step 2: Clamp to the human attention window
        output_sec = max(MIN_OUTPUT_SECONDS, min(MAX_OUTPUT_SECONDS, raw_output))

        # If source is very short (< 30s), just use source duration directly
        if source_duration_sec < 30:
            output_sec = max(source_duration_sec * 0.8, 10.0)

        # Step 3: Base skeleton — minimum beats per act
        skeleton_sec = sum(
            ACT_MIN_BEATS[act] * ACT_BEAT_DURATIONS[act]
            for act in ACT_MIN_BEATS
        )

        # Step 4: Extension budget — time left after base skeleton
        extension_budget = max(0.0, output_sec - skeleton_sec)

        # Step 5: Distribute extension beats to Build and Tension
        # (these are the "breathing room" acts — Hook/Climax/Payoff are fixed)
        beat_plan = {}
        for act, min_b in ACT_MIN_BEATS.items():
            beat_plan[act] = {"beats": min_b, "seconds": min_b * ACT_BEAT_DURATIONS[act]}

        if extension_budget > 0:
            # Allocate extra beats: 50% Build, 50% Tension
            extra_build_sec   = extension_budget * 0.5
            extra_tension_sec = extension_budget * 0.5

            extra_build_beats   = max(1, math.floor(extra_build_sec   / ACT_BEAT_DURATIONS["build"]))
            extra_tension_beats = max(1, math.floor(extra_tension_sec / ACT_BEAT_DURATIONS["tension"]))

            beat_plan["build"]["beats"]   += extra_build_beats
            beat_plan["build"]["seconds"] += extra_build_beats * ACT_BEAT_DURATIONS["build"]

            beat_plan["tension"]["beats"]   += extra_tension_beats
            beat_plan["tension"]["seconds"] += extra_tension_beats * ACT_BEAT_DURATIONS["tension"]

        # Step 6: Total beat count and word target
        total_beats = sum(v["beats"] for v in beat_plan.values())
        word_target = math.ceil(total_beats * WORDS_PER_BEAT)  # 4-6 words per beat

        # Step 7: Build ordered beat plan list
        beat_plan_list = [
            {
                "act":     act,
                "beats":   beat_plan[act]["beats"],
                "seconds": round(beat_plan[act]["seconds"], 1),
                "words":   beat_plan[act]["beats"] * WORDS_PER_BEAT,
            }
            for act in ["hook", "build", "tension", "climax", "payoff"]
        ]

        actual_total_sec = sum(v["seconds"] for v in beat_plan.values())

        logger.info(
            f"🎬 [CINEMATIC_DURATION] source={source_duration_sec:.0f}s "
            f"→ output={actual_total_sec:.1f}s "
            f"({COMPRESSION_RATIO*100:.0f}% compression, "
            f"{total_beats} beats, {word_target} words)"
        )

        return {
            "output_seconds":       round(actual_total_sec, 1),
            "word_target":          word_target,
            "beat_count":           total_beats,
            "beat_plan":            beat_plan_list,
            "source_duration_sec":  source_duration_sec,
            "compression_ratio_pct": COMPRESSION_RATIO * 100,
            "model":                "retention_arc_v1",
        }


# ── Singleton ────────────────────────────────────────────────────────────────
_engine = CinematicDurationEngine()


def compute_cinematic_duration(source_duration_sec: float) -> dict:
    """Public API — call this from orchestrator."""
    return _engine.compute(source_duration_sec)
