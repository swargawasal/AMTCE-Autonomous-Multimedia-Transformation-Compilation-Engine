"""Narrative Coherence Engine

Validates and lightly corrects the segment sequence to follow a coherent arc:
1) Arc order validation via Kendall tau similarity
2) Energy progression toward the arc climax
3) Segment role consistency

Outputs a coherence report without altering external response schemas.
"""

from __future__ import annotations

import math
from typing import Dict, List

# Canonical role order per arc
_ARC_CANONICAL_ORDER = {
    "reveal_arc": ["hook", "build", "reveal", "reaction"],
    "reaction_arc": ["hook", "build", "reaction"],
    "confidence_arc": ["hook", "build", "confidence"],
    "tension_arc": ["hook", "build", "release"],
    "humor_arc": ["hook", "setup", "punchline", "reaction"],
}

# Climax role per arc
_ARC_CLIMAX = {
    "reveal_arc": "reveal",
    "reaction_arc": "reaction",
    "confidence_arc": "confidence",
    "tension_arc": "release",
    "humor_arc": "punchline",
}

MIN_ENERGY_SAMPLES = 2


class NarrativeCoherenceEngine:
    """Ensures timeline coherence without breaking existing outputs."""

    def validate(self, segments: List[Dict], temporal_stream: List[Dict], arc_type: str) -> Dict:
        report = {
            "valid": True,
            "segments": segments,
            "coherence_score": 1.0,
            "issues": [],
        }

        if not segments:
            report.update({"valid": False, "coherence_score": 0.0, "issues": ["no_segments"]})
            return report

        canonical = _ARC_CANONICAL_ORDER.get(arc_type, [])
        order_score, relabeled_segments, order_issue = self._validate_order(segments, canonical)
        energy_score, energy_issue = self._validate_energy(relabeled_segments, temporal_stream, arc_type)

        coherence_score = (order_score + energy_score) / 2

        issues = []
        if order_issue:
            issues.append(order_issue)
        if energy_issue:
            issues.append(energy_issue)

        report.update(
            {
                "segments": relabeled_segments,
                "coherence_score": round(coherence_score, 3),
                "issues": issues,
                "valid": True,
            }
        )
        return report

    # ---- helpers ----
    def _validate_order(self, segments: List[Dict], canonical: List[str]):
        if not canonical or len(segments) < 2:
            return 1.0, segments, None

        roles = [seg.get("role") or seg.get("segment_role") or "build" for seg in segments]

        # Map roles to canonical indices; unknown roles sent to end
        role_to_idx = {r: i for i, r in enumerate(canonical)}
        seq = [role_to_idx.get(r, len(canonical)) for r in roles]

        if len(set(seq)) <= 1:
            tau = 0.0
        else:
            tau = self._kendall_tau(seq)
        order_score = (tau + 1) / 2  # map [-1,1] -> [0,1]

        relabeled_segments = segments
        issue = None

        if order_score < 0.5:
            # Relabel roles following canonical order while preserving time order
            relabeled_segments = sorted(segments, key=lambda s: s.get("start", 0.0))
            for i, seg in enumerate(relabeled_segments):
                if i < len(canonical):
                    seg["role"] = canonical[i]
                else:
                    seg["role"] = canonical[-1]
            issue = "arc_order_adjusted"
            order_score = 0.5  # after adjustment, set minimal acceptable score

        return order_score, relabeled_segments, issue

    def _kendall_tau(self, seq: List[int]) -> float:
        """Compute Kendall tau-b for a list of ranks."""
        n = len(seq)
        if n < 2:
            return 1.0
        concordant = discordant = 0
        for i in range(n):
            for j in range(i + 1, n):
                if seq[i] == seq[j]:
                    continue
                sign = (seq[i] - seq[j]) * -1  # smaller rank should come first
                if sign > 0:
                    concordant += 1
                elif sign < 0:
                    discordant += 1
        total = concordant + discordant
        if total == 0:
            return 0.0
        return (concordant - discordant) / total

    def _validate_energy(self, segments: List[Dict], temporal_stream: List[Dict], arc_type: str):
        if len(segments) < 2 or not temporal_stream:
            return 0.5, None  # neutral when not enough data

        climax_role = _ARC_CLIMAX.get(arc_type)
        energies = []
        for seg in segments:
            start = seg.get("start", 0.0)
            end = seg.get("end", start)
            energies.append(self._avg_energy_between(temporal_stream, start, end))

        # Use segments up to climax if present
        if climax_role:
            try:
                climax_idx = next(i for i, s in enumerate(segments) if s.get("role") == climax_role or s.get("segment_role") == climax_role)
                energies = energies[: climax_idx + 1]
            except StopIteration:
                pass

        if len(energies) < MIN_ENERGY_SAMPLES:
            return 0.5, "insufficient_energy_samples"

        # Pearson correlation between index and energy
        indices = list(range(len(energies)))
        corr = self._pearson(indices, energies)
        energy_score = (corr + 1) / 2  # map [-1,1] -> [0,1]
        energy_score = max(0.0, min(1.0, energy_score))

        issue = None
        if energy_score < 0.5:
            issue = "energy_progression_low"
        return energy_score, issue

    def _avg_energy_between(self, stream: List[Dict], start: float, end: float) -> float:
        values = [p.get("energy", 0.0) for p in stream if start <= p.get("time", 0.0) <= end]
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _pearson(self, xs: List[float], ys: List[float]) -> float:
        n = len(xs)
        if n == 0 or len(ys) != n:
            return 0.0
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
        den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
        if den_x == 0 or den_y == 0:
            return 0.0
        return num / (den_x * den_y)
