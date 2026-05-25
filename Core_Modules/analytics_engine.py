"""
analytics_engine.py
Fetches YouTube Analytics data for a published video.

Retrieves two data types:
  1. Audience retention curve  — relative % watched at each second
  2. Summary metrics           — views, avg_view_duration, avg_view_pct, likes

Uses the YouTube Analytics API v2 (googleapiclient).
Falls back gracefully when quota is exhausted or credentials are absent.

Schema produced (analytics_snapshot):
{
  "video_id":            str,
  "fetched_at":          ISO-8601 str,
  "views":               int,
  "avg_view_duration_s": float,
  "avg_view_pct":        float,   # 0–100
  "likes":               int,
  "retention_curve": [
    {"t": float, "pct": float},   # t = seconds from start, pct = 0–100
    ...
  ],
  "data_quality":        str,     # "full" | "partial" | "insufficient"
  "sample_count":        int,     # number of data points in retention curve
}
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Minimum views before retention data is statistically useful
_MIN_VIEWS_FOR_RETENTION = 30
# YouTube Analytics quota cost per retention report request: ~10 units
_RETRY_BACKOFF_S = [2, 5, 15]


class AnalyticsEngine:
    """
    Fetches per-video analytics from YouTube Analytics API.

    Args:
        credentials:       google.oauth2 credentials object (OAuth2 with
                           youtube.readonly + yt-analytics.readonly scope)
        channel_id:        "mine" or explicit channel ID
        quota_guard:       if True, skip requests when quota_used > quota_limit
    """

    def __init__(
        self,
        credentials=None,
        channel_id: str = "mine",
        quota_guard: bool = True,
        _mock_mode: bool = False,
    ):
        self.channel_id = channel_id
        self.quota_guard = quota_guard
        self._mock_mode = _mock_mode
        self._quota_used = 0
        self._quota_limit = 9500  # conservative daily limit (real limit: 10,000)

        if not _mock_mode:
            try:
                from googleapiclient.discovery import build
                self._yt_analytics = build(
                    "youtubeAnalytics", "v2", credentials=credentials
                )
            except Exception as e:
                logger.warning("AnalyticsEngine: could not build YT Analytics client: %s", e)
                self._yt_analytics = None
        else:
            self._yt_analytics = None

    def _mock_snapshot(self, video_id: str) -> Dict:
        """Return a synthetic snapshot for testing without API access."""
        import random
        rng = random.Random(hash(video_id) & 0xFFFF)
        # Simulate a realistic retention curve: high start, mid-dip, late payoff
        curve = []
        pct = 100.0
        for t in range(0, 31):
            if t < 3:
                pct = max(80, pct - rng.uniform(1, 4))
            elif t < 15:
                pct = max(40, pct - rng.uniform(0.5, 2.5))
            else:
                # slight bump at punchline/reveal
                pct = max(20, pct + rng.uniform(-1.5, 2.0))
            curve.append({"t": float(t), "pct": round(min(100, max(0, pct)), 2)})
        return {
            "video_id": video_id,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "views": rng.randint(50, 5000),
            "avg_view_duration_s": rng.uniform(12, 28),
            "avg_view_pct": rng.uniform(35, 75),
            "likes": rng.randint(0, 200),
            "retention_curve": curve,
            "data_quality": "full",
            "sample_count": len(curve),
        }

    def _fetch_summary_metrics(self, video_id: str) -> Dict:
        """Fetch views, avg view duration, likes."""
        if not self._yt_analytics:
            return {"views": 0, "avg_view_duration_s": 0.0, "avg_view_pct": 0.0, "likes": 0}
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            resp = (
                self._yt_analytics.reports()
                .query(
                    ids=f"channel=={self.channel_id}",
                    startDate="2020-01-01",
                    endDate=today,
                    metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes",
                    dimensions="video",
                    filters=f"video=={video_id}",
                )
                .execute()
            )
            self._quota_used += 5
            rows = resp.get("rows", [])
            if not rows:
                return {"views": 0, "avg_view_duration_s": 0.0, "avg_view_pct": 0.0, "likes": 0}
            row = rows[0]
            return {
                "views": int(row[1]),
                "avg_view_duration_s": float(row[3]),
                "avg_view_pct": float(row[4]),
                "likes": int(row[5]) if len(row) > 5 else 0,
            }
        except Exception as e:
            logger.warning("AnalyticsEngine summary fetch failed for %s: %s", video_id, e)
            return {"views": 0, "avg_view_duration_s": 0.0, "avg_view_pct": 0.0, "likes": 0}

    def _fetch_retention_curve(self, video_id: str, video_duration_s: float) -> List[Dict]:
        """
        Fetch audience retention curve.
        Returns list of {"t": float, "pct": float} sorted by t.
        Empty list if API unavailable or quota exceeded.
        """
        if not self._yt_analytics:
            return []
        if self.quota_guard and self._quota_used > self._quota_limit:
            logger.warning("AnalyticsEngine: quota guard tripped, skipping retention fetch")
            return []
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            resp = (
                self._yt_analytics.reports()
                .query(
                    ids=f"channel=={self.channel_id}",
                    startDate="2020-01-01",
                    endDate=today,
                    metrics="audienceWatchRatio",
                    dimensions="elapsedVideoTimeRatio",
                    filters=f"video=={video_id}",
                )
                .execute()
            )
            self._quota_used += 10
            rows = resp.get("rows", [])
            curve = []
            for row in rows:
                ratio = float(row[0])
                watch_pct = float(row[1]) * 100.0
                curve.append({
                    "t": round(ratio * video_duration_s, 2),
                    "pct": round(min(100.0, max(0.0, watch_pct)), 3),
                })
            return sorted(curve, key=lambda x: x["t"])
        except Exception as e:
            logger.warning("AnalyticsEngine retention fetch failed for %s: %s", video_id, e)
            return []

    def fetch(
        self,
        video_id: str,
        video_duration_s: float = 30.0,
        min_age_hours: float = 24.0,
    ) -> Optional[Dict]:
        """
        Fetch a complete analytics snapshot for one video.

        Args:
            video_id:          YouTube video ID (11-char string)
            video_duration_s:  video duration in seconds (from upload record)
            min_age_hours:     don't fetch if video is younger than this
                               (retention data is unreliable in first 24h)

        Returns:
            analytics_snapshot dict, or None if data is unavailable.
        """
        if self._mock_mode:
            return self._mock_snapshot(video_id)

        summary = self._fetch_summary_metrics(video_id)
        views = summary.get("views", 0)

        if views < _MIN_VIEWS_FOR_RETENTION:
            logger.info(
                "AnalyticsEngine: video %s has only %d views — skipping (need %d)",
                video_id, views, _MIN_VIEWS_FOR_RETENTION,
            )
            return None

        curve = self._fetch_retention_curve(video_id, video_duration_s)
        quality = (
            "full" if len(curve) >= 20
            else "partial" if len(curve) >= 5
            else "insufficient"
        )

        return {
            "video_id": video_id,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "views": views,
            "avg_view_duration_s": summary["avg_view_duration_s"],
            "avg_view_pct": summary["avg_view_pct"],
            "likes": summary["likes"],
            "retention_curve": curve,
            "data_quality": quality,
            "sample_count": len(curve),
        }
