"""Effect assignment module (extracted from SmartSceneEditor)."""

from typing import Any, Dict, List, Optional

from Content_Intelligence.persona_engine import Persona


class EffectAssigner:
    """Assigns transitions, zooms, and visual cues to timeline segments."""

    def assign_effects(
        self,
        timeline: Dict[str, Any],
        persona: Optional[Persona] = None,
        feature_flags: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Adds effect hints to each segment WITHOUT overwriting values already
        set by upstream modules (RhythmTimelineBuilder, CreativeEditorBridge,
        TimelineReconstructor).

        Rule: only set a field if it is absent or empty — never stomp existing values.
        This preserves the per-beat transition variety (punch_cut / whip_pan /
        blur_cut / flash) that creative modules carefully assign.
        """
        _ = feature_flags
        segments = timeline.get("scenes", [])
        for seg in segments:
            # Only apply persona transition as a FALLBACK — never overwrite
            if persona:
                if not seg.get("transition") and not seg.get("style"):
                    seg["transition"] = persona.transition_style
                if not seg.get("caption_style"):
                    seg["caption_style"] = persona.caption_style
                if persona.zoom_frequency != "none":
                    if "zoom" not in seg.get("effects", []):
                        seg.setdefault("effects", []).append("zoom")
            else:
                # Absolute fallback — only if nothing is set
                if not seg.get("transition") and not seg.get("style"):
                    seg["transition"] = "cut"
        return timeline