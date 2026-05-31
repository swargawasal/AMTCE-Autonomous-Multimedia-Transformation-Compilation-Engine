"""
telegram_router.py — Niche-based Telegram Group Router
=======================================================
Routes video uploads to the correct Telegram group based on content category:

  • fashion  → TELEGRAM_GROUP_ID_FASHION, TELEGRAM_GROUP_ID_FASHION_01, _02, ...
  • nsfw     → TELEGRAM_GROUP_ID_NSFW,    TELEGRAM_GROUP_ID_NSFW_01,    _02, ...
  • general  → TELEGRAM_GROUP_ID,          TELEGRAM_GROUP_ID_01,          _02, ...
              (paparazzi / mixed content — the default/original group)

Adding a new group is just adding a new secret to GitHub + `.env`:
  TELEGRAM_GROUP_ID_FASHION_03 = @your_new_fashion_group

Round-robin load balancing spreads posts across all groups in the same niche
so no single channel gets spammed. State is persisted in:
  The_json/telegram_router_state.json

Env var pattern (matches the IG/YT increment convention):
  Base:      TELEGRAM_GROUP_ID              / TELEGRAM_GROUP_ID_FASHION   / TELEGRAM_GROUP_ID_NSFW
  Index 01:  TELEGRAM_GROUP_ID_01           / TELEGRAM_GROUP_ID_FASHION_01/ TELEGRAM_GROUP_ID_NSFW_01
  Index 02:  TELEGRAM_GROUP_ID_02           / TELEGRAM_GROUP_ID_FASHION_02/ TELEGRAM_GROUP_ID_NSFW_02
  ...
"""

import json
import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

# ── State file for round-robin tracking ───────────────────────────────────────
_STATE_FILE = "The_json/telegram_router_state.json"
_state_lock = threading.Lock()

# ── Category → env-var prefix mapping ─────────────────────────────────────────
_CATEGORY_PREFIX = {
    "fashion": "TELEGRAM_GROUP_ID_FASHION",
    "nsfw":    "TELEGRAM_GROUP_ID_NSFW",
    "general": "TELEGRAM_GROUP_ID",         # base = original group
}

# ── State management ──────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, _STATE_FILE)
    except Exception as e:
        logger.warning("⚠️ [TG_ROUTER] Failed to save state: %s", e)


# ── Group pool discovery ──────────────────────────────────────────────────────

def _discover_groups(prefix: str) -> list[str]:
    """
    Discovers all configured Telegram group IDs for a given env-var prefix.

    For prefix="TELEGRAM_GROUP_ID_FASHION":
      Base:    TELEGRAM_GROUP_ID_FASHION          → index 0
      _01:     TELEGRAM_GROUP_ID_FASHION_01        → index 1
      _02:     TELEGRAM_GROUP_ID_FASHION_02        → index 2
      ...

    For prefix="TELEGRAM_GROUP_ID" (general):
      Base:    TELEGRAM_GROUP_ID                   → index 0
      _01:     TELEGRAM_GROUP_ID_01                → index 1
      _02:     TELEGRAM_GROUP_ID_02                → index 2
      ...
      NOTE: _FASHION and _NSFW variants are NOT matched here (exact suffix check).

    Returns a list of group IDs (strings like '@mychannel' or '-100xxxxx').
    Empty list if none are configured.
    """
    groups = []

    # ── Base (no number suffix) ───────────────────────────────────────────────
    base_val = os.getenv(prefix, "").strip()
    if base_val and "Your Telegram" not in base_val:
        groups.append(base_val)

    # ── Numbered suffixes: _01, _02, _03, ... ────────────────────────────────
    # Scan env for matching keys.  We check up to 20 slots to avoid scanning all env.
    for n in range(1, 21):
        key = f"{prefix}_{n:02d}"
        val = os.getenv(key, "").strip()
        if val and "Your Telegram" not in val:
            groups.append(val)
        elif n > 3 and not val:
            # Stop scanning after 3 consecutive misses past index 3
            # (avoids scanning _04–_20 if only _01–_02 exist)
            break

    return groups


# ── Public API ────────────────────────────────────────────────────────────────

def get_telegram_group_id(category: str = "general") -> str | None:
    """
    Returns the next Telegram group ID for the given content category,
    using round-robin across all configured groups in the niche.

    Args:
        category: "fashion" | "nsfw" | "general"

    Returns:
        A Telegram group ID/username string, or None if none are configured.

    Fallback chain:
        fashion → TELEGRAM_GROUP_ID_FASHION pools → TELEGRAM_GROUP_ID (general)
        nsfw    → TELEGRAM_GROUP_ID_NSFW pools    → TELEGRAM_GROUP_ID (general)
        general → TELEGRAM_GROUP_ID pools         (no further fallback)
    """
    cat = category.lower().strip()
    if cat not in _CATEGORY_PREFIX:
        cat = "general"

    prefix = _CATEGORY_PREFIX[cat]
    pool   = _discover_groups(prefix)

    if not pool:
        # ── Fallback for fashion/nsfw when no dedicated group is configured ──
        if cat in ("fashion", "nsfw"):
            logger.info(
                "📭 [TG_ROUTER] No %s groups configured — falling back to general TELEGRAM_GROUP_ID",
                cat.upper()
            )
            pool = _discover_groups(_CATEGORY_PREFIX["general"])
        if not pool:
            logger.warning("⚠️ [TG_ROUTER] No Telegram group configured for category=%s", cat)
            return None

    # ── Round-robin pick ──────────────────────────────────────────────────────
    with _state_lock:
        state = _load_state()
        idx   = state.get(cat, 0) % len(pool)
        chosen = pool[idx]
        state[cat] = (idx + 1) % len(pool)
        _save_state(state)

    # Normalise: ensure @username format for usernames, keep numeric IDs as-is
    if chosen and not chosen.lstrip("-").isdigit() and not chosen.startswith("@"):
        chosen = f"@{chosen}"

    logger.info(
        "📡 [TG_ROUTER] category=%-8s → group=%s  (pool size=%d, idx=%d)",
        cat, chosen, len(pool), idx
    )
    return chosen


def get_all_groups_for_category(category: str) -> list[str]:
    """
    Returns ALL configured group IDs for a category (without round-robin).
    Useful for broadcasting to every group in a niche simultaneously.
    """
    cat    = category.lower().strip()
    prefix = _CATEGORY_PREFIX.get(cat, _CATEGORY_PREFIX["general"])
    pool   = _discover_groups(prefix)
    return pool


# ── Config summary (for /tg_status admin command) ─────────────────────────────

def get_router_status() -> dict:
    """Returns a summary of all configured Telegram groups by category."""
    status = {}
    for cat, prefix in _CATEGORY_PREFIX.items():
        pool = _discover_groups(prefix)
        with _state_lock:
            state = _load_state()
            idx   = state.get(cat, 0) % len(pool) if pool else 0
        status[cat] = {
            "prefix":        prefix,
            "groups":        pool,
            "count":         len(pool),
            "next_index":    idx,
            "next_group":    pool[idx] if pool else None,
            "configured":    bool(pool),
        }
    return status
