"""Temporal Signal Builder for EditorBrainV3."""

from typing import Dict, List, Sequence, Tuple


class TemporalSignalBuilder:
    """Resample detector outputs into a uniform timeline."""

    def build(
        self,
        emotion: Sequence[Dict],
        motion: Sequence[Dict],
        audio: Sequence[Dict],
        step: float = 0.5,
    ) -> List[Dict]:
        """Create a 0.5s-sampled signal stream with combined energy."""
        if step <= 0:
            step = 0.5

        def _bounds(seq: Sequence[Dict]) -> Tuple[float, float]:
            times = [float(s.get("time", 0.0)) for s in seq if "time" in s]
            if not times:
                return 0.0, 0.0
            return min(times), max(times)

        spans = [_bounds(emotion), _bounds(motion), _bounds(audio)]
        start = min(s[0] for s in spans)
        end = max(s[1] for s in spans)
        if end <= start:
            return []

        def _at_time(seq: Sequence[Dict], t: float) -> float:
            # Nearest-neighbor lookup; fall back to 0
            nearest = None
            best_dt = 1e9
            for s in seq:
                if "time" not in s:
                    continue
                dt = abs(float(s["time"]) - t)
                if dt < best_dt:
                    best_dt = dt
                    nearest = s
            if nearest is None:
                return 0.0
            val = (
                nearest.get("value")
                or nearest.get("emotion")
                or nearest.get("motion")
                or nearest.get("audio")
                or 0.0
            )
            try:
                return max(0.0, min(1.0, float(val)))
            except (TypeError, ValueError):
                return 0.0

        samples: List[Dict] = []
        t = start
        while t <= end + 1e-6:
            e = _at_time(emotion, t)
            m = _at_time(motion, t)
            a = _at_time(audio, t)
            energy = max(0.0, min(1.0, (e + m + a) / 3.0))
            samples.append(
                {"time": round(t, 3), "emotion": e, "motion": m, "audio": a, "energy": energy}
            )
            t += step

        return samples

