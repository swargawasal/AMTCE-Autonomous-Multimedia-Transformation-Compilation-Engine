"""Reward Scorer: compute confidence for an edit plan."""

from typing import Dict, List


class RewardScorer:
    """Blend hook timing, moment strength, arc completeness, and coherence."""

    def score(self, plan: Dict, meanings: List[Dict], coherence_score: float = 1.0) -> float:
        segments = plan.get("segments", [])
        if not segments:
            return 0.0

        # Hook timing: prefer first segment near t<=2s.
        first_start = segments[0].get("start", 0.0)
        hook_score = max(0.0, 1.0 - (first_start / 5.0))  # decays after 5s

        # Moment strength: average viewer_interest.
        if meanings:
            strength = sum(m.get("viewer_interest", 0.0) for m in meanings) / len(meanings)
        else:
            strength = 0.5

        # Arc completeness: simple presence of >=2 segments.
        arc_score = 1.0 if len(segments) >= 2 else 0.6

        # Weighted blend
        total = 0.45 * hook_score + 0.35 * strength + 0.20 * arc_score

        # Adjust by coherence (penalize up to 20% if coherence is low)
        total = total * (1 - 0.20 * (1 - coherence_score))

        return max(0.0, min(1.0, round(total, 3)))
