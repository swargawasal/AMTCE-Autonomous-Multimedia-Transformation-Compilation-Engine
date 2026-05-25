"""Style validation to keep edits aligned with persona rules before render."""

from typing import Dict, List, Mapping, Sequence

from Content_Intelligence.persona_engine import Persona


class StyleValidator:
    """Validates an edit decision list (EDL) against a persona's style rules."""

    def __init__(self):
        # Thresholds can be tuned without changing function logic.
        self.zoom_thresholds = {
            "high": (0.5, 1.0),   # expected zoom ratio range
            "low": (0.0, 0.25),
            "none": (0.0, 0.0),
        }
        self.caption_thresholds = {
            "bold": 0.75,
            "clean": 0.55,
            "minimal": 0.35,
        }

    def _segments(self, edl: Mapping) -> List[Mapping]:
        """Normalize EDL structure to a list of segment dicts."""
        if isinstance(edl, Mapping):
            if "segments" in edl and isinstance(edl["segments"], Sequence):
                return list(edl["segments"])
            if "edits" in edl and isinstance(edl["edits"], Sequence):
                return list(edl["edits"])
        if isinstance(edl, Sequence):
            return list(edl)
        return []

    def validate_pacing(self, edl: Mapping, persona: Persona) -> List[str]:
        """Check that segment durations respect persona.max_shot_length."""
        issues: List[str] = []
        for idx, seg in enumerate(self._segments(edl)):
            duration = seg.get("duration")
            if duration is None and "start" in seg and "end" in seg:
                try:
                    duration = float(seg["end"]) - float(seg["start"])
                except (TypeError, ValueError):
                    duration = None
            if duration is None:
                continue
            if duration > persona.max_shot_length * 1.05:  # 5% tolerance
                issues.append(
                    f"Segment {idx} duration {duration:.2f}s exceeds "
                    f"max_shot_length {persona.max_shot_length:.2f}s for {persona.name}"
                )
        return issues

    def validate_transitions(self, edl: Mapping, persona: Persona) -> List[str]:
        """Ensure transitions match the persona's preferred transition style."""
        issues: List[str] = []
        expected = persona.transition_style
        for idx, seg in enumerate(self._segments(edl)):
            transition = seg.get("transition") or seg.get("transition_style")
            if transition is None:
                continue  # unknown is tolerated; upstream can fill defaults
            if str(transition).lower() != str(expected).lower():
                issues.append(
                    f"Segment {idx} transition '{transition}' != '{expected}' for {persona.name}"
                )
        return issues

    def validate_zoom_behavior(self, edl: Mapping, persona: Persona) -> List[str]:
        """Validate zoom frequency against persona.zoom_frequency."""
        issues: List[str] = []
        segments = self._segments(edl)
        if not segments:
            return issues

        zoom_segments = 0
        for seg in segments:
            # Accept several possible encodings for zoom.
            zoom_flag = seg.get("zoom") or seg.get("zoom_effect")
            effects = seg.get("effects") or seg.get("fx") or []
            has_zoom = bool(zoom_flag) or ("zoom" in effects if isinstance(effects, Sequence) else False)
            zoom_segments += 1 if has_zoom else 0

        ratio = zoom_segments / len(segments)
        low, high = self.zoom_thresholds.get(persona.zoom_frequency, (0.0, 1.0))
        if not (low <= ratio <= high):
            issues.append(
                f"Zoom ratio {ratio:.2f} outside expected [{low:.2f}, {high:.2f}] for {persona.zoom_frequency}"
            )
        return issues

    def validate_caption_density(self, edl: Mapping, persona: Persona) -> List[str]:
        """Prevent caption overload based on persona.caption_style."""
        issues: List[str] = []
        segments = self._segments(edl)
        if not segments:
            return issues

        caption_segments = 0
        for seg in segments:
            captions = seg.get("captions") or seg.get("caption") or seg.get("text")
            if captions:
                caption_segments += 1

        ratio = caption_segments / len(segments)
        max_ratio = self.caption_thresholds.get(persona.caption_style, 0.6)
        if ratio > max_ratio:
            issues.append(
                f"Caption density {ratio:.2f} exceeds {max_ratio:.2f} allowed for style {persona.caption_style}"
            )
        return issues

    def validate_all(self, edl: Mapping, persona: Persona) -> Dict[str, object]:
        """Run all validators and aggregate results."""
        issues: List[str] = []
        issues += self.validate_pacing(edl, persona)
        issues += self.validate_transitions(edl, persona)
        issues += self.validate_zoom_behavior(edl, persona)
        issues += self.validate_caption_density(edl, persona)

        score = max(0.0, 1.0 - 0.1 * len(issues))  # simple penalty-based score
        return {
            "valid": len(issues) == 0,
            "score": round(score, 3),
            "issues": issues,
        }

