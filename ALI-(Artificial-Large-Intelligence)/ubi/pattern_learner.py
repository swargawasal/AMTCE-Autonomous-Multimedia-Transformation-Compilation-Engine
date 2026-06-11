"""
UBI — Pattern Learner
=====================
Computes kurtosis of a user's topic distribution to classify
their behaviour profile.

Kurtosis classification (Fisher normalised, scipy convention):
  k > 3  → FOCUSED   — expert, deep technical topics, narrow domain
  1–3    → MODERATE  — balanced learner
  k < 1  → RANDOM    — broad explorer, general friendly answers

Also learns session timing patterns (which hours the user is most active)
and persists everything to ruflow_brain/user_profiles.json.

All public functions are async-compatible (called with await).
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import numpy as np
from scipy.stats import kurtosis as scipy_kurtosis

_BRAIN_DIR    = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ruflow_brain")
_PROFILE_PATH = os.path.join(_BRAIN_DIR, "user_profiles.json")
_WRITE_LOCK   = threading.Lock()

# Minimum interactions before we trust the kurtosis value
MIN_INTERACTIONS_FOR_KURTOSIS = 5


# ── I/O helpers ───────────────────────────────────────────────────────────────
def _load_profiles() -> Dict[str, Any]:
    os.makedirs(_BRAIN_DIR, exist_ok=True)
    if not os.path.exists(_PROFILE_PATH):
        return {"version": "1.0", "users": {}}
    with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_profiles(data: Dict[str, Any]) -> None:
    os.makedirs(_BRAIN_DIR, exist_ok=True)
    tmp = _PROFILE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _PROFILE_PATH)


def _get_user(profiles: Dict[str, Any], uid: str) -> Dict[str, Any]:
    users = profiles.setdefault("users", {})
    if uid not in users:
        users[uid] = {
            "user_id":            uid,
            "kurtosis_score":     0.0,
            "kurtosis_type":      "MODERATE",
            "top_topics":         [],
            "topic_distribution": {},
            "mse_current":        1.0,
            "mse_history":        [],
            "error_history":      [],
            "prediction_accuracy": 0.0,
            "mse_trend":          "STABLE",
            "confidence_multiplier": 1.0,
            "session_times":      [],
            "total_interactions": 0,
            "last_seen":          datetime.now(timezone.utc).isoformat(),
        }
    return users[uid]


# ── Kurtosis Classifier ───────────────────────────────────────────────────────
def _classify_kurtosis(k: float, n_interactions: int) -> str:
    """Return FOCUSED / MODERATE / RANDOM based on kurtosis and interaction count."""
    if n_interactions < MIN_INTERACTIONS_FOR_KURTOSIS:
        return "MODERATE"   # not enough data yet — default to middle
    if k > 3.0:
        return "FOCUSED"
    if k >= 1.0:
        return "MODERATE"
    return "RANDOM"


# ── Topic Distribution Update ─────────────────────────────────────────────────
async def update_pattern(
    user_id: str,
    detected_topic: str,
    session_hour: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Record a new topic interaction and recompute kurtosis + timing patterns.

    Args:
        user_id:       Hashed session identifier (never raw PII).
        detected_topic: The classified topic string (e.g. "machine learning").
        session_hour:  Current UTC hour (0–23). If None, auto-derived.

    Returns:
        {
          kurtosis_score: float,
          kurtosis_type:  "FOCUSED" | "MODERATE" | "RANDOM",
          top_topics:     List[str],
          total_interactions: int
        }
    """
    if session_hour is None:
        session_hour = datetime.now(timezone.utc).hour

    with _WRITE_LOCK:
        profiles = _load_profiles()
        user     = _get_user(profiles, user_id)

        # ── Update topic distribution ────────────────────────────────────────
        dist: Dict[str, int] = user.setdefault("topic_distribution", {})
        dist[detected_topic] = dist.get(detected_topic, 0) + 1
        user["total_interactions"] = user.get("total_interactions", 0) + 1

        # ── Top topics ───────────────────────────────────────────────────────
        sorted_topics = sorted(dist.items(), key=lambda x: x[1], reverse=True)
        user["top_topics"] = [t for t, _ in sorted_topics[:5]]

        # ── Kurtosis ─────────────────────────────────────────────────────────
        values = list(dist.values())
        n_int  = user["total_interactions"]

        if len(values) >= 2:
            # Fisher-normalised kurtosis (scipy default) — normal dist = 0
            # We use excess kurtosis: k>0 is leptokurtic (peaked = FOCUSED)
            k_val = scipy_kurtosis(values, fisher=True)
            k_raw = 0.0 if np.isnan(k_val) or not np.isfinite(k_val) else float(k_val)
        else:
            k_raw = 0.0

        kurtosis_type = _classify_kurtosis(k_raw, n_int)
        user["kurtosis_score"] = round(k_raw, 4)
        user["kurtosis_type"]  = kurtosis_type

        # ── Session timing ───────────────────────────────────────────────────
        times: List[int] = user.setdefault("session_times", [])
        times.append(session_hour)
        if len(times) > 100:
            user["session_times"] = times[-100:]

        user["last_seen"] = datetime.now(timezone.utc).isoformat()
        _save_profiles(profiles)

    return {
        "kurtosis_score":    k_raw,
        "kurtosis_type":     kurtosis_type,
        "top_topics":        user["top_topics"],
        "total_interactions": n_int,
    }


async def get_user_profile(user_id: str) -> Dict[str, Any]:
    """Read the current profile for a user (no write)."""
    profiles = _load_profiles()
    user     = _get_user(profiles, user_id)
    return dict(user)


async def get_peak_hours(user_id: str) -> List[int]:
    """Return the user's top-3 most active UTC hours."""
    profiles = _load_profiles()
    user     = _get_user(profiles, user_id)
    times    = user.get("session_times", [])
    if not times:
        return []
    counts   = {}
    for h in times:
        counts[h] = counts.get(h, 0) + 1
    sorted_h = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [h for h, _ in sorted_h[:3]]
