"""
account_limiter.py — Per-Account Daily Post Limiter
====================================================
Tracks how many times each niche account has posted per platform per day.
Enforces daily caps to prevent bot-like posting patterns.

Limits (configurable via .env):
  IG_DAILY_LIMIT   = 3   (Instagram posts/day per account)
  YT_DAILY_LIMIT   = 2   (YouTube uploads/day per account)
  TG_DAILY_LIMIT   = 5   (Telegram posts/day per account)

State is persisted in Actress_Modules/account_daily_limits.json
and auto-resets when the date changes.
"""

import json
import logging
import os
import random
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
LIMITS_FILE = os.path.join("Actress_Modules", "account_daily_limits.json")

DAILY_CAPS = {
    "ig":       int(os.getenv("IG_DAILY_LIMIT",  "3")),
    "yt":       int(os.getenv("YT_DAILY_LIMIT",  "2")),
    "telegram": int(os.getenv("TG_DAILY_LIMIT",  "5")),
}

# Min/max random delay (seconds) between consecutive uploads to different accounts
MIN_STAGGER_SECS = int(os.getenv("ACCOUNT_STAGGER_MIN_SECS", "120"))   # 2 min
MAX_STAGGER_SECS = int(os.getenv("ACCOUNT_STAGGER_MAX_SECS", "900"))   # 15 min


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    """Load daily limits state from disk. Returns empty dict if missing."""
    if not os.path.exists(LIMITS_FILE):
        return {}
    try:
        with open(LIMITS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("⚠️ [LIMITER] Failed to load state: %s — starting fresh", exc)
        return {}


def _save_state(state: dict) -> None:
    """Persist daily limits state to disk."""
    os.makedirs(os.path.dirname(LIMITS_FILE), exist_ok=True)
    try:
        with open(LIMITS_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as exc:
        logger.warning("⚠️ [LIMITER] Failed to save state: %s", exc)


def _today() -> str:
    return date.today().isoformat()


def _get_account_entry(state: dict, niche: str) -> dict:
    """
    Get/init the entry for a niche. Auto-resets if date has changed.
    """
    today = _today()
    entry = state.get(niche, {})

    if entry.get("date") != today:
        # New day — reset all counters
        entry = {
            "date":     today,
            "ig":       0,
            "yt":       0,
            "telegram": 0,
        }

    return entry


# ── Public API ────────────────────────────────────────────────────────────────

def can_post(niche: str, platform: str) -> bool:
    """
    Returns True if the niche account is allowed to post on the given platform today.

    Args:
        niche:    Folder name, e.g. "General_Fallback", "NSFW", "Fashion_Style"
        platform: One of "ig", "yt", "telegram"
    """
    platform = platform.lower().strip()
    cap      = DAILY_CAPS.get(platform)
    if cap is None:
        logger.warning("⚠️ [LIMITER] Unknown platform '%s' — allowing post", platform)
        return True

    state = _load_state()
    entry = _get_account_entry(state, niche)
    count = entry.get(platform, 0)

    allowed = count < cap
    if not allowed:
        logger.info(
            "🚫 [LIMITER] %s/%s daily limit reached (%d/%d) — skipping",
            niche, platform, count, cap,
        )
    return allowed


def record_post(niche: str, platform: str) -> None:
    """
    Increment the post counter for a niche + platform.
    Call this AFTER a successful upload.
    """
    platform = platform.lower().strip()
    state    = _load_state()
    entry    = _get_account_entry(state, niche)
    entry[platform] = entry.get(platform, 0) + 1
    state[niche]    = entry
    _save_state(state)

    cap = DAILY_CAPS.get(platform, "?")
    logger.info(
        "✅ [LIMITER] Recorded %s/%s post — today: %d/%s",
        niche, platform, entry[platform], cap,
    )


def get_daily_summary() -> dict:
    """Returns a summary of today's post counts for all accounts."""
    state   = _load_state()
    today   = _today()
    summary = {}
    for niche, entry in state.items():
        if entry.get("date") == today:
            summary[niche] = {
                "ig":       f"{entry.get('ig', 0)}/{DAILY_CAPS['ig']}",
                "yt":       f"{entry.get('yt', 0)}/{DAILY_CAPS['yt']}",
                "telegram": f"{entry.get('telegram', 0)}/{DAILY_CAPS['telegram']}",
            }
    return summary


def pick_available_account(pool: list[str], platform: str) -> str | None:
    """
    Given a shuffled pool of niche folder names, returns the first one
    that still has capacity for today on the given platform.

    Args:
        pool:     e.g. ["Fashion_02", "Fashion_01", "General_Fallback"]  (pre-shuffled)
        platform: "ig", "yt", or "telegram"

    Returns:
        Niche folder name (str) or None if all accounts are at daily limit.

    Example usage:
        pool    = get_route_pool("fashion")   # shuffled automatically
        account = pick_available_account(pool, "ig")
        if account:
            record_post(account, "ig")
    """
    for niche in pool:
        if can_post(niche, platform):
            logger.info(
                "✅ [LIMITER] Selected account: %s for platform=%s",
                niche, platform,
            )
            return niche

    logger.warning(
        "🚫 [LIMITER] All accounts in pool exhausted for platform=%s: %s",
        platform, pool,
    )
    return None


def stagger_delay() -> None:
    """
    Sleep a random amount between MIN_STAGGER_SECS and MAX_STAGGER_SECS.
    Call between consecutive uploads to different accounts to avoid
    identical timing patterns.
    """
    delay = random.randint(MIN_STAGGER_SECS, MAX_STAGGER_SECS)
    logger.info("⏱️  [LIMITER] Anti-pattern stagger: sleeping %d seconds...", delay)
    time.sleep(delay)


def select_posting_style() -> dict:
    """
    Returns a randomised posting style profile to vary caption structure
    between accounts, making automation harder to fingerprint.

    Returns dict with keys:
        caption_order:  "hashtags_first" | "caption_first"
        caption_length: "short" | "medium" | "long"
        emoji_density:  "low" | "medium" | "high"
        cta_style:      "link_in_bio" | "comment_below" | "save_this" | "none"
    """
    return {
        "caption_order":  random.choice(["caption_first", "caption_first", "hashtags_first"]),
        "caption_length": random.choice(["short", "medium", "medium", "long"]),
        "emoji_density":  random.choice(["low", "medium", "medium", "high"]),
        "cta_style":      random.choice(["link_in_bio", "comment_below", "save_this", "link_in_bio"]),
    }
