"""
pattern_extractor.py
Maps retention analysis events to the editing decisions that caused them.

This is the critical attribution step: given "viewers rewatched at t=8.4s" and
the edit plan that placed a reveal segment at t=8.0s–11.5s, we attribute the
rewatch to (arc=reveal_arc, persona=HYPE, segment_role=reveal, transition=zoom_in).

Output schema (editing_pattern):
{
  "video_id":      str,
  "arc_type":      str,
  "persona":       str,
  "segment_role":  str,
  "transition":    str,
  "hook_offset_s": float,   # seconds between top moment and hook start
  "avg_energy":    float,
  "event_type":    str,     # rewatch_peak | drop_cliff | flat_zone | recovery
  "magnitude":     float,
  "engagement_score": float,
  "signal":        str,     # "positive" | "negative" | "neutral"
  "weight":        float,   # per-pattern learning weight [0,1]
}
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Attribution window: retention event at time T is attributed to the segment
# that contains T ± ATTRIBUTION_WINDOW_S
_ATTRIBUTION_WINDOW_S = 1.5

# Signal classification thresholds
_POSITIVE_EVENTS = {"rewatch_peak", "flat_zone", "recovery"}
_NEGATIVE_EVENTS = {"drop_cliff"}

# How much learning weight to assign per event type and magnitude
def _learning_weight(event_type: str, magnitude: float, engagement_score: float) -> float:
    """
    Weight reflects how trustworthy this single pattern observation is.
    High-magnitude events on high-engagement videos get more weight.
    """
    base = magnitude * 0.6 + engagement_score * 0.4
    # Drop cliffs are noisier than rewatch peaks (can be caused by distraction)
    if event_type == "drop_cliff":
        base *= 0.7
    return round(min(1.0, max(0.05, base)), 4)


def _find_segment_at(segments: List[Dict], t: float, window: float = _ATTRIBUTION_WINDOW_S) -> Optional[Dict]:
    """Return the first segment containing time t ± window, or None."""
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        if (start - window) <= t <= (end + window):
            return seg
    return None


class PatternExtractor:
    """
    Attributes retention events to editing decisions.

    Inputs:
        retention_result:  output of RetentionAnalyzer.analyze()
        log_entry:         output of VideoLog.read()

    Output:
        List[Dict] — one editing_pattern per attributed event
    """

    def extract(self, retention_result: Dict, log_entry: Dict) -> List[Dict]:
        """
        Returns a list of editing_pattern dicts, one per attributed event.
        Events that cannot be attributed to a segment are dropped.
        """
        if not retention_result or not log_entry:
            return []

        events: List[Dict] = retention_result.get("events", [])
        segments: List[Dict] = log_entry.get("segments", [])
        arc_type = log_entry.get("arc_type", "")
        persona = log_entry.get("persona", "")
        avg_energy = log_entry.get("avg_energy", 0.5)
        hook_time_s = log_entry.get("hook_time_s", 0.0)
        top_moment_time_s = log_entry.get("top_moment_time_s", 0.0)
        coherence_score = log_entry.get("coherence_score", 1.0)
        engagement_score = retention_result.get("engagement_score", 0.5)
        video_id = log_entry.get("video_id", "")
        # Hook time derived from first segment start if not provided
        if not hook_time_s and segments:
            hook_time_s = segments[0].get("start", 0.0)

        patterns: List[Dict] = []

        for event in events:
            t = event.get("t", 0.0)
            event_type = event.get("type", "")
            magnitude = event.get("magnitude", 0.5)

            # Try to attribute to a segment
            seg = _find_segment_at(segments, t)
            if seg is None:
                logger.debug(
                    "PatternExtractor: event at t=%.1f not attributable to any segment in %s",
                    t, video_id,
                )
                continue

            signal = (
                "positive" if event_type in _POSITIVE_EVENTS
                else "negative" if event_type in _NEGATIVE_EVENTS
                else "neutral"
            )

            weight = _learning_weight(event_type, magnitude, engagement_score)

            # hook_offset: distance between hook start and top-interest moment
            hook_offset_s = round(abs(top_moment_time_s - hook_time_s), 3)

            seg_start = seg.get("start", 0.0)
            seg_end = seg.get("end", seg_start)
            cut_offset = round(top_moment_time_s - seg_start, 3)
            segment_duration = round(max(0.0, seg_end - seg_start), 3)

            # Reaction offset is aligned with cut timing for now
            reaction_offset = cut_offset

            rewatch_weight = 1.25 if event_type == "rewatch_peak" else 1.0

            patterns.append({
                "video_id":         video_id,
                "arc_type":         arc_type,
                "persona":          persona,
                "segment_role":     seg.get("role", ""),
                "transition":       seg.get("transition", ""),
                "hook_offset_s":    hook_offset_s,
                "hook_time":        hook_time_s,
                "cut_offset":       cut_offset,
                "reaction_offset":  reaction_offset,
                "segment_duration": segment_duration,
                "avg_energy":       avg_energy,
                "event_type":       event_type,
                "magnitude":        magnitude,
                "engagement_score": engagement_score,
                "signal":           signal,
                "weight":           weight,
                "rewatch_weight":   rewatch_weight,
                "coherence_score":  coherence_score,
            })

        logger.info(
            "PatternExtractor: %d events → %d attributed patterns for %s",
            len(events), len(patterns), video_id,
        )
        return patterns
