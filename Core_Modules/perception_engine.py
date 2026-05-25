"""Perception Engine: detects salient events from temporal signals."""

from typing import Dict, List


class PerceptionEngine:
    """Detect spikes, pauses, reaction timing, motion bursts."""

    def __init__(self, spike_threshold: float = 0.15):
        self.spike_threshold = spike_threshold

    def detect(self, temporal_stream: List[Dict]) -> List[Dict]:
        """Detect energy spikes based on delta between consecutive samples."""
        events: List[Dict] = []
        if not temporal_stream or len(temporal_stream) < 2:
            return events

        prev = temporal_stream[0]
        for current in temporal_stream[1:]:
            delta = float(current.get("energy", 0.0)) - float(prev.get("energy", 0.0))
            if delta >= self.spike_threshold:
                events.append(
                    {
                        "time": current.get("time", 0.0),
                        "type": "spike",
                        "strength": round(delta, 3),
                    }
                )
            prev = current
        return events

