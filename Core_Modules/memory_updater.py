"""
memory_updater.py
Writes extracted patterns into editor_memory using EWMA updates.

Applies one additional layer: a minimum-data guard that prevents the memory
from acting on patterns that have fewer than MIN_CONFIDENCE_SAMPLES observations.
This protects against the cold-start problem where 1 viral video causes the
system to over-commit to a single pattern.

Also handles the per-video engagement_score update to arc_scores / persona_scores.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from Core_Modules.editor_memory import EditorMemory, _pattern_key
from Core_Modules.learning_stability_gate import stabilize

logger = logging.getLogger(__name__)

# Patterns below this sample count get written to memory but not acted on
_MIN_ACTIONABLE_SAMPLES = 3


class MemoryUpdater:
    """
    Applies a list of editing_pattern observations to the EditorMemory store.

    Args:
        memory:  EditorMemory instance
    """

    def __init__(self, memory: EditorMemory):
        self.memory = memory

    def update(self, patterns: List[Dict], save: bool = True) -> Dict:
        """
        Upsert all patterns into memory and rebuild aggregate scores.

        Args:
            patterns: list of editing_pattern dicts from PatternExtractor
            save:     if True, persist to disk after updates

        Returns:
            summary dict:
            {
              "patterns_updated": int,
              "patterns_skipped": int,
              "new_patterns":     int,
              "actionable_now":   int,  # patterns with sample_count >= threshold
            }
        """
        updated = 0
        skipped = 0
        new_count = 0

        seen_video_ids = set()

        for p in patterns:
            p = stabilize(dict(p))
            arc = p.get("arc_type", "")
            persona = p.get("persona", "")
            role = p.get("segment_role", "")
            transition = p.get("transition", "")
            signal = p.get("signal", "neutral")
            score = p.get("engagement_score", 0.5) * float(p.get("weight", 1.0))
            video_id = p.get("video_id", "")

            if not arc or not persona:
                skipped += 1
                continue

            key = _pattern_key(arc, persona, role, transition)
            is_new = self.memory.get_pattern(key) is None

            self.memory.upsert_pattern(
                key=key,
                new_score=score,
                signal=signal,
                pattern_meta={
                    "arc_type": arc,
                    "persona": persona,
                    "segment_role": role,
                    "transition": transition,
                    "cut_offset": p.get("cut_offset"),
                    "reaction_offset": p.get("reaction_offset"),
                    "segment_duration": p.get("segment_duration"),
                    "hook_time": p.get("hook_time"),
                    "coherence_score": p.get("coherence_score", 1.0),
                    "rewatch_weight": p.get("rewatch_weight", 1.0),
                },
            )

            if is_new:
                new_count += 1
            updated += 1
            seen_video_ids.add(video_id)

        # Count each unique video toward the learned total
        for _ in seen_video_ids:
            self.memory.increment_video_count()

        # Rebuild arc/persona aggregate scores after all updates
        self.memory.rebuild_aggregate_scores()

        if save:
            self.memory.save()

        actionable = sum(
            1
            for p in self.memory.all_patterns().values()
            if p.get("sample_count", 0) >= _MIN_ACTIONABLE_SAMPLES
        )

        summary = {
            "patterns_updated": updated,
            "patterns_skipped": skipped,
            "new_patterns": new_count,
            "actionable_now": actionable,
            "total_in_memory": self.memory.total_patterns,
        }
        logger.info("MemoryUpdater: %s", summary)
        return summary
