"""Timeline builder module (extracted from SmartSceneEditor)."""

from typing import Any, Dict, List, Optional, Sequence


class TimelineBuilder:
    """Generates an edit timeline from scenes and ranked moments."""

    def _moment_to_scene(self, moment: Dict) -> Dict:
        """Converts a candidate moment into a scene segment centered on time."""
        time = float(moment.get("time", 0.0))
        duration = float(moment.get("duration", 1.5))

        # 🔥 KEY FIX: center the moment instead of anchoring at 0
        start = moment.get("start")
        end = moment.get("end")

        if start is None:
            start = max(0.0, time - (duration / 2.0))

        if end is None:
            end = start + duration

        return {
            "start": round(start, 3),
            "end": round(end, 3),
            "importance": moment.get("composite_score", moment.get("score", 0.5)),
            "source_time": time,
        }

    def build_timeline(
        self,
        scenes: Sequence[Dict[str, Any]],
        moments: Sequence[Dict[str, Any]],
        editing_plan: Optional[Dict[str, Any]] = None,
        reconstructed_timeline: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Priority: reconstructed_timeline > editing_plan.segments > scenes > moments.
        Returns dict: {"scenes": [...], "moment_driven": bool}
        """
        # Priority 1: reconstructed timeline
        if reconstructed_timeline:
            return {"scenes": list(reconstructed_timeline), "moment_driven": True}

        # Priority 2: Gemini editing plan
        if isinstance(editing_plan, dict) and editing_plan.get("segments"):
            return {"scenes": list(editing_plan["segments"]), "moment_driven": False}

        # Priority 3: detected scenes enriched by moments
        if scenes:
            return {"scenes": list(scenes), "moment_driven": bool(moments)}

        # Fallback: convert moments into scenes (fixed centered duration)
        fallback_scenes = [self._moment_to_scene(m) for m in moments]
        return {"scenes": fallback_scenes, "moment_driven": True}

