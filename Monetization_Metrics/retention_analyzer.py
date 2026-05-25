"""
retention_analyzer.py
Analyzes a YouTube audience retention curve to find engagement events.

Detects:
  - rewatch_peak:    local maximum above average (viewers seeking back)
  - drop_cliff:      sudden fall > cliff_threshold in one step (disengagement)
  - flat_zone:       extended stable section (sustained attention)
  - recovery:        rise after a cliff (viewers returned)

Output schema (retention_peaks):
{
  "video_id": str,
  "avg_retention_pct": float,
  "peak_retention_pct": float,
  "final_retention_pct": float,
  "events": [
    {
      "t":          float,    # seconds from video start
      "type":       str,      # rewatch_peak | drop_cliff | flat_zone | recovery
      "magnitude":  float,    # size of the event [0,1] normalised
      "pct_at_t":   float,    # raw retention % at this timestamp
    },
    ...
  ],
  "engagement_score": float,  # composite [0,1] — higher = better retention
}
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional


def _smooth(curve: List[Dict], window: int = 3) -> List[float]:
    """Simple moving average over pct values."""
    pcts = [p["pct"] for p in curve]
    smoothed = []
    half = window // 2
    for i in range(len(pcts)):
        lo = max(0, i - half)
        hi = min(len(pcts), i + half + 1)
        smoothed.append(sum(pcts[lo:hi]) / (hi - lo))
    return smoothed


class RetentionAnalyzer:
    """
    Identifies meaningful engagement events from a retention curve.

    Args:
        cliff_threshold:   pct-point drop per step to count as cliff (default 8.0)
        peak_min_above:    how many pct above rolling avg a local max must be (default 5.0)
        flat_min_duration: min seconds of stable retention to count as flat zone (default 3.0)
        flat_tolerance:    max pct-point variation within a flat zone (default 3.0)
    """

    def __init__(
        self,
        cliff_threshold: float = 8.0,
        peak_min_above: float = 5.0,
        flat_min_duration: float = 3.0,
        flat_tolerance: float = 3.0,
    ):
        self.cliff_threshold = cliff_threshold
        self.peak_min_above = peak_min_above
        self.flat_min_duration = flat_min_duration
        self.flat_tolerance = flat_tolerance

    def _engagement_score(
        self,
        avg_pct: float,
        peak_pct: float,
        final_pct: float,
        n_cliffs: int,
        n_peaks: int,
    ) -> float:
        """
        Composite engagement score [0,1].

        Formula:
          base = avg_pct / 100
          peak_bonus   = (peak_pct - avg_pct) / 100 * 0.20
          final_bonus  = final_pct / 100 * 0.15
          cliff_penalty = n_cliffs * 0.05
          peak_reward  = n_peaks  * 0.03
        """
        base = avg_pct / 100.0
        peak_bonus = ((peak_pct - avg_pct) / 100.0) * 0.20
        final_bonus = (final_pct / 100.0) * 0.15
        cliff_penalty = n_cliffs * 0.05
        peak_reward = min(0.10, n_peaks * 0.03)
        raw = base + peak_bonus + final_bonus - cliff_penalty + peak_reward
        return round(min(1.0, max(0.0, raw)), 4)

    def analyze(self, snapshot: Dict) -> Optional[Dict]:
        """
        Analyze one analytics_snapshot and return retention_peaks.

        Returns None if curve has fewer than 5 data points.
        """
        curve: List[Dict] = snapshot.get("retention_curve", [])
        if len(curve) < 5:
            return None

        curve = sorted(curve, key=lambda x: x["t"])
        smoothed = _smooth(curve, window=3)
        avg_pct = sum(p["pct"] for p in curve) / len(curve)
        peak_pct = max(p["pct"] for p in curve)
        final_pct = curve[-1]["pct"]

        events: List[Dict] = []

        # ── Hook failure detection (early drop) ───────────────────────────
        early_points = [p for p in curve if p["t"] <= 1.5]
        if early_points:
            first_pt = min(early_points, key=lambda x: x["t"])
            if first_pt["pct"] < 45.0:
                severity = round(min(1.0, (45.0 - first_pt["pct"]) / 45.0), 4)
                events.append({
                    "t": first_pt["t"],
                    "type": "hook_failure",
                    "magnitude": severity,
                    "pct_at_t": first_pt["pct"],
                })

        # ── Cliff detection ───────────────────────────────────────────────
        for i in range(1, len(curve)):
            drop = smoothed[i - 1] - smoothed[i]
            if drop >= self.cliff_threshold:
                magnitude = min(1.0, drop / 30.0)  # 30 pct-point drop = magnitude 1.0
                events.append({
                    "t": curve[i]["t"],
                    "type": "drop_cliff",
                    "magnitude": round(magnitude, 4),
                    "pct_at_t": curve[i]["pct"],
                })

        # ── Rewatch peak detection (local max above rolling avg) ──────────
        for i in range(1, len(curve) - 1):
            if (smoothed[i] > smoothed[i - 1]
                    and smoothed[i] > smoothed[i + 1]
                    and smoothed[i] > avg_pct + self.peak_min_above):
                magnitude = min(1.0, (smoothed[i] - avg_pct) / 30.0)
                events.append({
                    "t": curve[i]["t"],
                    "type": "rewatch_peak",
                    "magnitude": round(magnitude, 4),
                    "pct_at_t": curve[i]["pct"],
                })

        # ── Recovery detection (rise after a cliff) ───────────────────────
        cliff_times = {e["t"] for e in events if e["type"] == "drop_cliff"}
        for i in range(2, len(curve)):
            rise = smoothed[i] - smoothed[i - 2]
            if rise >= 4.0 and curve[i - 1]["t"] in cliff_times:
                events.append({
                    "t": curve[i]["t"],
                    "type": "recovery",
                    "magnitude": round(min(1.0, rise / 15.0), 4),
                    "pct_at_t": curve[i]["pct"],
                })

        # ── Flat zone detection ───────────────────────────────────────────
        zone_start = None
        zone_vals: List[float] = []
        step = curve[1]["t"] - curve[0]["t"] if len(curve) > 1 else 1.0

        for i, item in enumerate(curve):
            if zone_start is None:
                zone_start = item["t"]
                zone_vals = [item["pct"]]
            else:
                span = max(zone_vals) - min(zone_vals)
                if span <= self.flat_tolerance:
                    zone_vals.append(item["pct"])
                else:
                    duration = curve[i - 1]["t"] - zone_start
                    if duration >= self.flat_min_duration:
                        magnitude = min(1.0, (sum(zone_vals) / len(zone_vals)) / 80.0)
                        events.append({
                            "t": zone_start,
                            "type": "flat_zone",
                            "magnitude": round(magnitude, 4),
                            "pct_at_t": round(sum(zone_vals) / len(zone_vals), 2),
                            "duration_s": round(duration, 2),
                        })
                    zone_start = item["t"]
                    zone_vals = [item["pct"]]

        events.sort(key=lambda e: e["t"])

        n_cliffs = sum(1 for e in events if e["type"] == "drop_cliff")
        n_peaks = sum(1 for e in events if e["type"] == "rewatch_peak")

        return {
            "video_id": snapshot.get("video_id", ""),
            "avg_retention_pct": round(avg_pct, 3),
            "peak_retention_pct": round(peak_pct, 3),
            "final_retention_pct": round(final_pct, 3),
            "events": events,
            "engagement_score": self._engagement_score(
                avg_pct, peak_pct, final_pct, n_cliffs, n_peaks
            ),
        }
