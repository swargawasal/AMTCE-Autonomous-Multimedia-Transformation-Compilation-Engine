"""Story Builder: generate clip segments around detected moments with reaction offsets and pacing."""

from typing import Dict, List, Sequence, Optional


class StoryBuilder:
    """Convert semantic moments into non-overlapping segments."""

    REACTION_OFFSETS = {
        "HYPE": 0.25,
        "AESTHETIC": 0.55,
        "ANALYST": 0.40,
    }

    def __init__(self, default_span: float = 2.5, pre_roll: float = 0.5, post_roll: float = 2.0):
        self.default_span = default_span
        self.pre_roll = pre_roll
        self.post_roll = post_roll

    def build(
        self,
        arc: str,
        meanings: Sequence[Dict],
        persona_name: str,
        pacing_hint: Optional[Dict] = None,
    ) -> Dict:
        """Build a simple hook→build→payoff timeline."""
        if not meanings:
            return {"segments": [], "arc": arc, "persona": persona_name}

        segments: List[Dict] = []
        current_end = -1.0
        reaction_offset = self.REACTION_OFFSETS.get(persona_name, 0.4)

        wave_peak = pacing_hint.get("wave_peak") if pacing_hint else None

        for m in sorted(meanings, key=lambda x: x.get("viewer_interest", 0), reverse=True):
            center = float(m.get("time", 0.0)) + reaction_offset

            # If pacing hint exists, bias placement toward wave peak.
            if wave_peak is not None:
                center = (center * 0.6) + (wave_peak * 0.4)

            start = max(0.0, center - self.pre_roll)
            end = center + self.post_roll

            # Avoid overlaps by pushing forward.
            if start < current_end:
                shift = current_end - start + 0.1
                start += shift
                end += shift

            # Clamp segment length to 2–3 seconds.
            duration = end - start
            if duration < 2.0:
                end = start + 2.0
            elif duration > 3.0:
                end = start + 3.0

            segments.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "moment_type": m.get("moment_type"),
                }
            )
            # Ensure role field exists for coherence engine
            if "role" not in segments[-1]:
                segments[-1]["role"] = m.get("moment_type") or "build"
            current_end = end

        return {"segments": segments, "arc": arc, "persona": persona_name}
