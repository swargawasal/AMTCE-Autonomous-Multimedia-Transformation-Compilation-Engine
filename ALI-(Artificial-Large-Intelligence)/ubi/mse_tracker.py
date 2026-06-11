"""
UBI — MSE Tracker
=================
Stores per-user MSE history in ruflow_brain/user_profiles.json.
Detects MSE trend (RISING / FALLING / STABLE) and resets or boosts
the predictor confidence accordingly.

MSE = Mean Square Error between the sentence-embedding distance of
      (predicted_topic, actual_topic) across all historical predictions.

MSE rising  → predictions getting worse  → reset topic model
MSE falling → predictions improving       → raise confidence multiplier
MSE stable  → steady state               → no change
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import numpy as np

# ── Brain path ────────────────────────────────────────────────────────────────
_BRAIN_DIR   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ruflow_brain")
_PROFILE_PATH = os.path.join(_BRAIN_DIR, "user_profiles.json")
_WRITE_LOCK  = threading.Lock()

MSE_WINDOW   = 10   # number of recent errors to compute MSE over
RISE_THRESH  = 0.05 # MSE increased by more than this → RISING
FALL_THRESH  = 0.05 # MSE decreased by more than this → FALLING


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
            "error_history":      [],          # raw per-prediction distances
            "prediction_accuracy": 0.0,
            "mse_trend":          "STABLE",
            "confidence_multiplier": 1.0,
            "session_times":      [],
            "total_interactions": 0,
            "last_seen":          datetime.now(timezone.utc).isoformat(),
        }
    return users[uid]


# ── Core API ──────────────────────────────────────────────────────────────────
async def record_prediction_error(
    user_id: str,
    embedding_distance: float,
) -> Dict[str, Any]:
    """
    Called after every interaction to record the embedding distance between
    the predicted topic and the actual topic.

    Returns:
        {
          mse_current:         float,
          mse_trend:           "RISING" | "FALLING" | "STABLE",
          confidence_multiplier: float,
          action:              "RESET" | "BOOST" | "NONE"
        }
    """
    with _WRITE_LOCK:
        profiles = _load_profiles()
        user     = _get_user(profiles, user_id)

        # Append new error and keep a rolling window
        errors: List[float] = user.setdefault("error_history", [])
        errors.append(float(embedding_distance))
        if len(errors) > MSE_WINDOW * 3:           # cap total history at 30
            user["error_history"] = errors[-MSE_WINDOW * 3:]

        # Compute MSE over last MSE_WINDOW errors
        window = errors[-MSE_WINDOW:]
        mse_new = float(np.mean(np.array(window) ** 2))

        # Detect trend
        prev_mse = user.get("mse_current", 1.0)
        delta    = mse_new - prev_mse
        if delta > RISE_THRESH:
            trend  = "RISING"
        elif delta < -FALL_THRESH:
            trend  = "FALLING"
        else:
            trend  = "STABLE"

        # Decide action and update confidence multiplier
        action = "NONE"
        confidence = user.get("confidence_multiplier", 1.0)
        if trend == "RISING" and mse_new > 0.5:
            action     = "RESET"
            confidence = max(0.3, confidence * 0.7)    # cut confidence
        elif trend == "FALLING" and mse_new < 0.3:
            action     = "BOOST"
            confidence = min(2.0, confidence * 1.1)    # raise confidence

        # Append to mse_history (keep last 20)
        mse_hist: List[float] = user.setdefault("mse_history", [])
        mse_hist.append(round(mse_new, 4))
        if len(mse_hist) > 20:
            user["mse_history"] = mse_hist[-20:]

        # Compute overall prediction accuracy (% of errors < 0.3 threshold)
        good = sum(1 for e in errors if e < 0.3)
        user["prediction_accuracy"]   = round(good / max(len(errors), 1), 4)
        user["mse_current"]           = round(mse_new, 4)
        user["mse_trend"]             = trend
        user["confidence_multiplier"] = round(confidence, 4)
        user["last_seen"]             = datetime.now(timezone.utc).isoformat()

        _save_profiles(profiles)

    return {
        "mse_current":           mse_new,
        "mse_trend":             trend,
        "confidence_multiplier": confidence,
        "action":                action,
    }


async def get_mse_stats(user_id: str) -> Dict[str, Any]:
    """Return current MSE stats for a user (read-only)."""
    profiles = _load_profiles()
    user     = _get_user(profiles, user_id)
    return {
        "mse_current":           user.get("mse_current", 1.0),
        "mse_history":           user.get("mse_history", []),
        "mse_trend":             user.get("mse_trend", "STABLE"),
        "prediction_accuracy":   user.get("prediction_accuracy", 0.0),
        "confidence_multiplier": user.get("confidence_multiplier", 1.0),
    }
