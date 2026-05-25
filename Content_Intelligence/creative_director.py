"""Creative Director: choose persona and arc with memory bias."""

from typing import Dict, List

from Content_Intelligence.persona_engine import select_persona
from Intelligence_Modules.editor_memory import find_similar_pattern


class CreativeDirector:
    """Select persona, arc, and priority moments."""

    ARC_MAP = {
        "humor": "humor_arc",
        "confidence": "confidence_arc",
        "surprise": "reveal_arc",
        "reaction": "reaction_arc",
        "awkwardness": "reaction_arc",
    }

    def __init__(self, profile_data: Dict = None):
        self.profile = profile_data or {}
        # Support both 'meanings' and 'candidate_moments' as source of truth
        self.meanings = self.profile.get("meanings") or self.profile.get("candidate_moments") or []
        self.temporal_stream = self.profile.get("temporal_stream", [])

    def build_strategy(self) -> Dict:
        """Stateful version of strategy selection for orchestrator.py."""
        # Ensure meanings is always a list — guards against None injected
        # via profile_data after construction (e.g. late-populated candidate_moments).
        meanings = (
            self.profile.get("meanings")
            or self.profile.get("candidate_moments")
            or self.meanings
            or []
        )
        temporal_stream = self.profile.get("temporal_stream", self.temporal_stream)
        return self.choose_strategy(meanings, temporal_stream)

    def build_narrative_story_map(self, candidate_moments: List[Dict]) -> Dict:
        """Map candidate moments to a basic story arc structure."""
        if not candidate_moments:
            return {"story_type": "none", "narrative_moments": []}
            
        # Enrich moments with role if missing
        processed = []
        for m in candidate_moments:
            m_copy = dict(m)
            if "role" not in m_copy:
                m_copy["role"] = m_copy.get("moment_type") or "build"
            processed.append(m_copy)
            
        return {
            "story_type": "narrative_arc",
            "narrative_moments": processed,
            "total_weight": sum(m.get("viewer_interest", 0.5) for m in processed)
        }

    def choose_strategy(self, meanings: List[Dict], temporal_stream: List[Dict]) -> Dict:
        """Functional version of strategy selection (Legacy/Internal)."""
        if not meanings:
            persona = select_persona({"moment_type": "neutral"})
            return {"arc_type": "reaction_arc", "priority_moments": [], "persona": persona.name}

        # Use top-interest moment as signature for memory lookups
        top = max(meanings, key=lambda m: m.get("viewer_interest", 0))
        moment_type = top.get("moment_type", "neutral")
        signature = f"{moment_type}+spike"

        memory_hit = None
        try:
            memory_hit = find_similar_pattern(signature)
        except Exception:
            pass  # editor_memory not ready yet — fall through to default persona
        if memory_hit:
            persona_name = memory_hit.get("preferred_persona") or "HYPE"
            arc = memory_hit.get("preferred_arc") or self._arc_for_moment(moment_type)
        else:
            persona = select_persona(top)
            persona_name = persona.name
            arc = self._arc_for_moment(moment_type)

        priorities = [i for i, _ in enumerate(meanings)][:5]
        return {"arc_type": arc, "priority_moments": priorities, "persona": persona_name}

    def optimize_hook(
        self,
        fused_moments: List[Dict] = None,
        candidate_moments: List[Dict] = None,
    ) -> Dict:
        """
        Selects the best hook moment from fused or candidate moments.
        Returns a dict the orchestrator stores in profile_data['hook_optimization'].
        """
        moments = fused_moments or candidate_moments or []
        if not moments:
            return {"hook_enabled": False, "hook_time": 0.0, "hook_score": 0.0}

        # Pick the moment with highest viewer_interest / strength near the start
        # Prefer moments in the first 3 seconds for maximum hook impact
        early = [m for m in moments if float(m.get("time", m.get("t", 0))) <= 3.0]
        pool = early if early else moments

        best = max(
            pool,
            key=lambda m: float(m.get("viewer_interest", m.get("strength", 0.0))),
        )
        hook_time = float(best.get("time", best.get("t", 0.0)))
        hook_score = float(best.get("viewer_interest", best.get("strength", 0.0)))

        return {
            "hook_enabled": hook_score > 0.0,
            "hook_time": round(hook_time, 3),
            "hook_score": round(hook_score, 4),
            "hook_type": best.get("moment_type", best.get("type", "visual_attention")),
        }

    def _arc_for_moment(self, moment_type: str) -> str:
        return self.ARC_MAP.get(moment_type, "inspiration_arc")