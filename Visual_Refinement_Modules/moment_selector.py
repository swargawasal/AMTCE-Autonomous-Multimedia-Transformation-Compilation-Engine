"""Moment selector module (extracted from SmartSceneEditor)."""

from typing import Any, Dict, List, Mapping, Optional, Sequence


class MomentSelector:
    """Ranks candidate moments for downstream timeline building."""

    def select_moments(
        self,
        moment_candidates: Optional[Sequence[Mapping[str, Any]]] = None,
        fallback_segments: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Sort candidates by importance/score descending."""
        if not moment_candidates and fallback_segments:
            return list(fallback_segments)

        def score(m: Mapping[str, Any]) -> float:
            if m.get("importance") is not None:
                return float(m["importance"])
            if m.get("score") is not None:
                return float(m["score"])
            return 0.0

        return sorted(moment_candidates or [], key=score, reverse=True)

