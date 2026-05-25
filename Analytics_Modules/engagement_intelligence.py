"""
Analytics_Modules/engagement_intelligence.py
---------------------------------------------
Engagement Intelligence — Strategy Feedback Loop.

Tracks per-video performance metrics after publishing, computes an
engagement_score, and surfaces historical "what worked best" signals
that bias (but never override) the Content Director's Gemini decisions.

Dataset: analytics/engagement_dataset.json
  [
    {
      "video_id": "...",
      "narrative": "...",
      "editing_style": "...",
      "tone": "...",
      "feature_flags": {...},
      "engagement_score": 0.0,
      "metrics": { "views": 0, "watch_time": 0, "completion_rate": 0, ... },
      "timestamp": "..."
    },
    ...
  ]

Engagement Score Formula:
  engagement_score =
      0.40 * completion_rate
    + 0.20 * likes_ratio          (likes / views, capped 1.0)
    + 0.20 * shares_ratio         (shares / views, capped 1.0)
    + 0.20 * watch_time_ratio     (avg_watch_time / video_duration, capped 1.0)

Strategy Signals (returned to Content Director):
  preferred_style   — editing style key with highest avg score (last 30 entries)
  confidence_boost  — score delta above the overall mean (0.0–1.0)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import threading

logger = logging.getLogger("engagement_intelligence")
_DATASET_LOCK = threading.Lock()

DATASET_PATH = os.path.join("analytics", "engagement_dataset.json")
MAX_HISTORY  = 500   # cap to avoid unbounded growth


def _load_dataset() -> List[dict]:
    """Load the engagement dataset from disk. Returns [] on any error."""
    try:
        if os.path.exists(DATASET_PATH):
            with open(DATASET_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        logger.warning(f"📊 [EI] Failed to load dataset: {e}")
    return []


def _save_dataset(dataset: List[dict]) -> None:
    """Atomically save the dataset. Silently skips on error."""
    try:
        os.makedirs(os.path.dirname(DATASET_PATH), exist_ok=True)
        tmp_path = DATASET_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(dataset, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, DATASET_PATH)
    except Exception as e:
        logger.warning(f"📊 [EI] Failed to save dataset: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def compute_engagement_score(metrics: dict) -> float:
    """
    Compute a 0.0–1.0 engagement score from raw metrics.

    Expected metric keys (all optional, default 0):
      views, watch_time (secs), completion_rate (0-1),
      likes, shares, comments, video_duration (secs)
    """
    try:
        views           = max(1, int(metrics.get("views", 1)))
        completion_rate = max(0.0, min(1.0, float(metrics.get("completion_rate", 0.0))))
        likes           = max(0, int(metrics.get("likes", 0)))
        shares          = max(0, int(metrics.get("shares", 0)))
        watch_time      = max(0.0, float(metrics.get("watch_time", 0.0)))
        duration        = max(1.0, float(metrics.get("video_duration", 30.0)))

        likes_ratio      = min(1.0, likes  / views)
        shares_ratio     = min(1.0, shares / views)
        watch_time_ratio = min(1.0, (watch_time / views) / duration)

        score = (
            0.40 * completion_rate
          + 0.20 * likes_ratio
          + 0.20 * shares_ratio
          + 0.20 * watch_time_ratio
        )
        return round(score, 4)
    except Exception as e:
        logger.warning(f"📊 [EI] compute_engagement_score error: {e}")
        return 0.0


def record_video_result(
    video_id:     str,
    metadata:     dict,
    metrics:      dict,
) -> float:
    """
    Record a video's strategy metadata alongside its engagement metrics.

    Args:
        video_id:   Unique identifier (uuid or filename).
        metadata:   Dict with keys: narrative, editing_style, tone, feature_flags
        metrics:    Dict with keys: views, watch_time, completion_rate, likes,
                    shares, comments, video_duration

    Returns:
        The computed engagement_score.
    """
    try:
        score = compute_engagement_score(metrics)

        entry = {
            "video_id":      video_id,
            "narrative":     str(metadata.get("narrative",     "")),
            "editing_style": str(metadata.get("editing_style", "")),
            "tone":          str(metadata.get("tone",          "")),
            "feature_flags": dict(metadata.get("feature_flags", {})),
            "engagement_score": score,
            "metrics":       metrics,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
        }

        with _DATASET_LOCK:
            dataset = _load_dataset()
            dataset.append(entry)
    
            # Trim to MAX_HISTORY (keep most recent)
            if len(dataset) > MAX_HISTORY:
                dataset = dataset[-MAX_HISTORY:]
    
            _save_dataset(dataset)

        logger.info(
            f"📊 [EI] Recorded: video={video_id} style={entry['editing_style']} "
            f"narrative={entry['narrative']} score={score:.3f}"
        )
        return score

    except Exception as e:
        logger.warning(f"📊 [EI] record_video_result error: {e}")
        return 0.0


def get_strategy_signals(top_n: int = 30) -> dict:
    """
    Analyse the engagement dataset and return signals to bias the Content Director.

    Args:
        top_n: Number of most-recent records to consider.

    Returns:
        {
          "preferred_style":    str   — editing_style key with highest avg score,
          "preferred_narrative": str  — narrative with highest avg score,
          "confidence_boost":   float — score delta above mean (capped 0-1),
          "style_scores":       dict  — {style: avg_score}, for logging,
        }
        or {} on any error / empty dataset.
    """
    try:
        dataset = _load_dataset()
        if not dataset:
            return {}

        recent = dataset[-top_n:] if len(dataset) >= top_n else dataset

        # Aggregate by editing_style
        style_totals:     Dict[str, List[float]] = {}
        narrative_totals: Dict[str, List[float]] = {}

        for entry in recent:
            style = entry.get("editing_style", "").strip()
            narr  = entry.get("narrative",     "").strip()
            score = float(entry.get("engagement_score", 0.0))

            if style:
                style_totals.setdefault(style, []).append(score)
            if narr:
                narrative_totals.setdefault(narr, []).append(score)

        if not style_totals:
            return {}

        style_avgs     = {s: sum(v) / len(v) for s, v in style_totals.items()}
        narrative_avgs = {n: sum(v) / len(v) for n, v in narrative_totals.items()}
        overall_mean   = sum(style_avgs.values()) / len(style_avgs)

        best_style     = max(style_avgs,     key=style_avgs.get)
        best_narrative = max(narrative_avgs, key=narrative_avgs.get) if narrative_avgs else ""
        confidence_boost = min(1.0, max(0.0, style_avgs[best_style] - overall_mean))

        signals = {
            "preferred_style":     best_style,
            "preferred_narrative": best_narrative,
            "confidence_boost":    round(confidence_boost, 3),
            "style_scores":        {s: round(v, 3) for s, v in style_avgs.items()},
        }

        logger.info(
            f"📊 [EI] Strategy signals: preferred_style={best_style} "
            f"preferred_narrative={best_narrative} "
            f"confidence_boost={confidence_boost:.3f}"
        )
        return signals

    except Exception as e:
        logger.warning(f"📊 [EI] get_strategy_signals error: {e}")
        return {}
