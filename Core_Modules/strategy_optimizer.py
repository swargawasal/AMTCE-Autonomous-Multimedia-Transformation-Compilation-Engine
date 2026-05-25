"""
strategy_optimizer.py
Reads editor_memory and produces optimization_signals — lightweight hints
that the CreativeDirector and RewardScorer apply at edit time.

Design principle: hints are SOFT suggestions, never hard overrides.
The brain's own confidence gate is the final arbiter.
If memory is cold (< MIN_MEMORY_VIDEOS), no hints are emitted.

Output schema (optimization_signals):
{
  "arc_rankings": [
    {"arc_type": str, "score": float, "sample_count": int},
    ...
  ],
  "persona_rankings": [
    {"persona": str, "score": float, "sample_count": int},
    ...
  ],
  "top_transitions": {
    "reveal_arc": "zoom_in",    # best transition per arc type
    ...
  },
  "reward_weight_overrides": {
    "has_dynamic_effect": float,   # override the +0.10 weight in RewardScorer
    "final_strong_role":  float,
    ...
  },
  "memory_cold":    bool,      # True = not enough data yet to trust hints
  "total_videos":   int,
}
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional

from Core_Modules.editor_memory import EditorMemory

logger = logging.getLogger(__name__)

# Don't emit meaningful hints until at least this many videos have been learned
_MIN_MEMORY_VIDEOS = 5
_MIN_PATTERN_SAMPLES = 3

# Reward weight range — hints can shift weights ±MAX_WEIGHT_DELTA from default
_MAX_WEIGHT_DELTA = 0.08


class StrategyOptimizer:
    """
    Builds optimization_signals from the current state of EditorMemory.

    Args:
        memory: EditorMemory instance
    """

    def __init__(self, memory: EditorMemory):
        self.memory = memory

    def _rank_arcs(self) -> List[Dict]:
        """Return arc types ranked by weighted EWMA score, descending."""
        arc_scores = self.memory._data.get("arc_scores", {})
        # Count samples per arc from pattern store
        arc_samples: Dict[str, int] = defaultdict(int)
        for p in self.memory.all_patterns().values():
            arc_samples[p["arc_type"]] += p.get("sample_count", 0)

        ranked = sorted(
            [
                {
                    "arc_type": arc,
                    "score": round(score, 4),
                    "sample_count": arc_samples.get(arc, 0),
                }
                for arc, score in arc_scores.items()
            ],
            key=lambda x: x["score"],
            reverse=True,
        )
        return ranked

    def _rank_personas(self) -> List[Dict]:
        """Return personas ranked by weighted EWMA score, descending."""
        persona_scores = self.memory._data.get("persona_scores", {})
        persona_samples: Dict[str, int] = defaultdict(int)
        for p in self.memory.all_patterns().values():
            persona_samples[p["persona"]] += p.get("sample_count", 0)

        ranked = sorted(
            [
                {
                    "persona": persona,
                    "score": round(score, 4),
                    "sample_count": persona_samples.get(persona, 0),
                }
                for persona, score in persona_scores.items()
            ],
            key=lambda x: x["score"],
            reverse=True,
        )
        return ranked

    def _best_transitions_per_arc(self) -> Dict[str, str]:
        """
        For each arc type, find the transition style with the highest
        mean EWMA score across patterns with sufficient samples.
        """
        arc_transition_scores: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for p in self.memory.all_patterns().values():
            if p.get("sample_count", 0) < _MIN_PATTERN_SAMPLES:
                continue
            arc = p["arc_type"]
            transition = p["transition"]
            if transition:
                arc_transition_scores[arc][transition].append(p["ewma_score"])

        best: Dict[str, str] = {}
        for arc, transitions in arc_transition_scores.items():
            ranked = sorted(
                transitions.items(),
                key=lambda kv: sum(kv[1]) / len(kv[1]),
                reverse=True,
            )
            if ranked:
                best[arc] = ranked[0][0]
        return best

    def _compute_reward_overrides(self) -> Dict[str, float]:
        """
        Derive soft weight overrides for RewardScorer based on what the data
        shows actually correlates with high engagement.

        Current RewardScorer defaults:
            has_dynamic_effect: +0.10
            final_strong_role:  +0.10
            arc_tension_shape:  +0.15
            hook_present:       +0.20

        We adjust each ±MAX_WEIGHT_DELTA based on observed correlation.
        """
        patterns = self.memory.all_patterns()
        if not patterns:
            return {}

        # Measure: do patterns with zoom/whip effects correlate with higher scores?
        with_effect = [
            p["ewma_score"]
            for p in patterns.values()
            if p.get("transition") in ("zoom_in", "whip", "smash_cut")
            and p.get("sample_count", 0) >= _MIN_PATTERN_SAMPLES
        ]
        without_effect = [
            p["ewma_score"]
            for p in patterns.values()
            if p.get("transition") in ("cut", "fade", "smooth")
            and p.get("sample_count", 0) >= _MIN_PATTERN_SAMPLES
        ]

        overrides = {}

        if len(with_effect) >= 3 and len(without_effect) >= 3:
            avg_with = sum(with_effect) / len(with_effect)
            avg_without = sum(without_effect) / len(without_effect)
            delta = (avg_with - avg_without) * 0.5  # scale down
            overrides["has_dynamic_effect"] = round(
                0.10 + max(-_MAX_WEIGHT_DELTA, min(_MAX_WEIGHT_DELTA, delta)), 4
            )

        # Measure: do strong closing roles (payoff/reveal/triumph) correlate?
        with_strong_close = [
            p["ewma_score"]
            for p in patterns.values()
            if p.get("segment_role") in ("payoff", "reveal", "triumph", "punchline")
            and p.get("sample_count", 0) >= _MIN_PATTERN_SAMPLES
        ]
        if len(with_strong_close) >= 3:
            avg_close = sum(with_strong_close) / len(with_strong_close)
            # Baseline is ~0.65 (assumed mid-performance)
            delta = (avg_close - 0.65) * 0.3
            overrides["final_strong_role"] = round(
                0.10 + max(-_MAX_WEIGHT_DELTA, min(_MAX_WEIGHT_DELTA, delta)), 4
            )

        return overrides

    def _learned_timings(self) -> Dict[str, float]:
        """Compute learned timing hints from memory fields."""
        patterns = [
            p
            for p in self.memory.all_patterns().values()
            if p.get("sample_count", 0) >= _MIN_PATTERN_SAMPLES
        ]
        if not patterns:
            return {}

        def _weighted_mean(field: str) -> Optional[float]:
            vals = [
                (p.get(field, 0.0), p.get("sample_count", 1))
                for p in patterns
                if p.get(field, None) not in (None, 0)
            ]
            if not vals:
                return None
            num = sum(v * w for v, w in vals)
            den = sum(w for _, w in vals)
            if den == 0:
                return None
            return round(num / den, 3)

        return {
            "optimal_cut_offset": _weighted_mean("cut_offset_avg"),
            "optimal_segment_duration": _weighted_mean("segment_duration_avg"),
            "optimal_hook_time": _weighted_mean("hook_time_avg"),
        }

    def build_signals(self) -> Dict:
        """
        Build the full optimization_signals payload.

        Returns a cold signal with memory_cold=True if there is
        insufficient data to make reliable suggestions.
        """
        total_videos = self.memory.total_videos_learned
        cold = total_videos < _MIN_MEMORY_VIDEOS

        if cold:
            logger.info(
                "StrategyOptimizer: memory cold (%d/%d videos), emitting null hints",
                total_videos,
                _MIN_MEMORY_VIDEOS,
            )
            return {
                "arc_rankings": [],
                "persona_rankings": [],
                "top_transitions": {},
                "reward_weight_overrides": {},
                "optimal_cut_offset": None,
                "optimal_segment_duration": None,
                "optimal_hook_time": None,
                "memory_cold": True,
                "total_videos": total_videos,
            }

        arc_rankings = self._rank_arcs()
        persona_rankings = self._rank_personas()
        top_transitions = self._best_transitions_per_arc()
        reward_overrides = self._compute_reward_overrides()
        timings = self._learned_timings()

        signals = {
            "arc_rankings": arc_rankings,
            "persona_rankings": persona_rankings,
            "top_transitions": top_transitions,
            "reward_weight_overrides": reward_overrides,
            "optimal_cut_offset": timings.get("optimal_cut_offset"),
            "optimal_segment_duration": timings.get("optimal_segment_duration"),
            "optimal_hook_time": timings.get("optimal_hook_time"),
            "memory_cold": False,
            "total_videos": total_videos,
        }
        logger.info(
            "StrategyOptimizer: built signals — top arc=%s top persona=%s",
            arc_rankings[0]["arc_type"] if arc_rankings else "none",
            persona_rankings[0]["persona"] if persona_rankings else "none",
        )
        return signals
