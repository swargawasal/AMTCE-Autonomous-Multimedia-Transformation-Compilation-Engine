"""Meaning Engine: interpret perceptual events into viewer-relevant moments."""

from typing import Dict, List


class MeaningEngine:
    """Map low-level spikes into semantic moment labels."""

    def infer(self, perceived: List[Dict], temporal_stream: List[Dict]) -> List[Dict]:
        """Convert spikes into interpreted moments."""
        meanings: List[Dict] = []
        if not perceived:
            return meanings

        # Build a quick lookup of energy by time for contextual scoring.
        energy_lookup = {round(s.get("time", 0.0), 3): float(s.get("energy", 0.0)) for s in temporal_stream}

        for event in perceived:
            t = round(float(event.get("time", 0.0)), 3)
            energy = energy_lookup.get(t, 0.5)
            strength = float(event.get("strength", 0.0))

            # Simple heuristics: strong spikes -> surprise, medium -> confidence, else reaction.
            if strength >= 0.35:
                moment_type = "surprise"
            elif energy >= 0.6:
                moment_type = "confidence"
            else:
                moment_type = "reaction"

            viewer_interest = max(0.0, min(1.0, (energy + strength) / 2))

            meanings.append(
                {
                    "time": t,
                    "moment_type": moment_type,
                    "viewer_interest": round(viewer_interest, 3),
                    "energy": energy,
                }
            )
        return meanings

