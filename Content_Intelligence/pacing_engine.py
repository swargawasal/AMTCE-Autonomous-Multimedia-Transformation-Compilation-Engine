"""Pacing Engine: detect emotional energy waves."""

from typing import Dict, List, Optional


class PacingEngine:
    """Identify rising/peak/falling energy regions in temporal stream."""

    def detect(self, temporal_stream: List[Dict]) -> Optional[Dict]:
        if not temporal_stream:
            return None

        energies = [float(s.get("energy", 0.0)) for s in temporal_stream]
        times = [float(s.get("time", 0.0)) for s in temporal_stream]

        peak_idx = max(range(len(energies)), key=lambda i: energies[i])
        peak_time = times[peak_idx]

        # Find start where energy begins rising towards peak.
        start_idx = max(0, peak_idx - 1)
        while start_idx > 0 and energies[start_idx - 1] < energies[start_idx]:
            start_idx -= 1

        end_idx = min(len(energies) - 1, peak_idx + 1)
        while end_idx < len(energies) - 1 and energies[end_idx + 1] <= energies[end_idx]:
            end_idx += 1

        return {
            "wave_start": times[start_idx],
            "wave_peak": peak_time,
            "wave_end": times[end_idx],
        }

