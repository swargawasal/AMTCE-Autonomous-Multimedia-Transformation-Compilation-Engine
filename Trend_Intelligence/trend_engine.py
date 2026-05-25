"""
Trend_Intelligence/trend_engine.py
------------------------------------
Trend Intelligence Engine — Zero Extra API Calls.

Combines multiple trend signal sources to produce a structured `trend_context`:
  1. User-provided trend hints (from trend_context/user_trend_input.json)
  2. Visual entities already detected by forensic_analyzer / content_director
  3. Optional external signals (Google Trends, YouTube, Reddit) — best-effort only

Design principles:
  - If external sources fail, user trend hints are still used.
  - All failures are non-critical — returns empty defaults.
  - No Gemini calls here; trend context is passed to Universal Content Director
    which interprets it using the already-existing forensic Gemini response.

Output:
  {
    "trend_context": {
      "topics":         [],   # Trend topic strings
      "entities":       [],   # Entities matching trend topics
      "keywords":       [],   # Extracted trend keywords
      "trend_strength": 0.0  # 0.0–1.0 aggregate signal strength
    }
  }
"""

import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional

logger = logging.getLogger("trend_engine")

# ── Constants ──────────────────────────────────────────────────────────────────
USER_TREND_FILE = os.path.join("trend_context", "user_trend_input.json")
TREND_MAX_AGE_DAYS = 40  # Entries older than this are expired and removed


# ── Default return ─────────────────────────────────────────────────────────────
DEFAULT_TREND_CONTEXT = {
    "trend_context": {
        "topics":         [],
        "entities":       [],
        "keywords":       [],
        "trend_strength": 0.0,
    }
}


# ── User Trend Storage ─────────────────────────────────────────────────────────

def load_user_trends() -> List[dict]:
    """
    Load user-provided trend entries from disk.
    Automatically removes entries older than TREND_MAX_AGE_DAYS.
    Returns list of valid (non-expired) trend entry dicts.
    """
    try:
        if not os.path.exists(USER_TREND_FILE):
            return []
        with open(USER_TREND_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=TREND_MAX_AGE_DAYS)
        valid = []
        removed = 0
        for entry in data:
            try:
                ts = datetime.fromisoformat(entry.get("timestamp", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    valid.append(entry)
                else:
                    removed += 1
            except Exception:
                valid.append(entry)  # Keep entries with bad timestamps (be lenient)

        if removed:
            logger.info(f"🌊 [Trend] Expired {removed} old trend entries (>{TREND_MAX_AGE_DAYS}d)")
            save_user_trends(valid)

        return valid
    except Exception as e:
        logger.warning(f"🌊 [Trend] Failed to load user trends: {e}")
        return []


def save_user_trends(entries: List[dict]) -> None:
    """Persist the trend entries list to disk. Silently skips on error."""
    try:
        os.makedirs(os.path.dirname(USER_TREND_FILE), exist_ok=True)
        tmp = USER_TREND_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        os.replace(tmp, USER_TREND_FILE)
    except Exception as e:
        logger.warning(f"🌊 [Trend] Failed to save user trends: {e}")


def add_user_trend(raw_input: str) -> None:
    """
    Store a single user trend input with an expiry timestamp.
    Called from main.py or the optional trend input prompt.
    """
    if not raw_input or not raw_input.strip():
        return
    entries = load_user_trends()
    entries.append({
        "input":     raw_input.strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    save_user_trends(entries)
    logger.info(f"🌊 [Trend] Stored user trend: '{raw_input.strip()[:80]}'")


def collect_trend_input_interactive() -> Optional[str]:
    """
    Optionally prompt the user for trending topic hints (interactive mode only).
    Returns the raw input string or None if skipped.
    Call this from main.py before the pipeline starts.
    """
    try:
        print("\n💡 Optional: Enter any current trending topics, slang, gossip, memes, or")
        print("   discussions you are aware of. Press ENTER to skip.")
        raw = input("   Trend hints → ").strip()
        if raw:
            add_user_trend(raw)
            return raw
        return None
    except (EOFError, KeyboardInterrupt):
        return None  # Non-interactive environment — skip silently


# ── External Trend Sources (best-effort) ──────────────────────────────────────

def _fetch_google_trends(keywords: List[str]) -> List[str]:
    """Try Google Trends via pytrends. Returns list of related topic strings."""
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=330, timeout=(5, 15))
        pt.build_payload(keywords[:5], timeframe="now 7-d")
        related = pt.related_queries()
        topics = []
        for kw, results in related.items():
            top = results.get("top")
            if top is not None and not top.empty:
                topics.extend(top["query"].head(3).tolist())
        return topics[:10]
    except Exception as e:
        logger.debug(f"🌊 [Trend] Google Trends unavailable: {e}")
        return []


def _fetch_youtube_trending() -> List[str]:
    """Try YouTube Data API for trending video titles. Requires YT_API_KEY env."""
    try:
        import urllib.request
        api_key = os.getenv("YOUTUBE_API_KEY", "")
        if not api_key:
            return []
        url = (
            f"https://www.googleapis.com/youtube/v3/videos"
            f"?part=snippet&chart=mostPopular&maxResults=10&videoCategoryId=24"
            f"&key={api_key}"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        titles = [item["snippet"]["title"] for item in data.get("items", [])]
        return titles[:5]
    except Exception as e:
        logger.debug(f"🌊 [Trend] YouTube Trends unavailable: {e}")
        return []


# ── Keyword extraction ─────────────────────────────────────────────────────────

def _extract_keywords(text: str) -> List[str]:
    """Extract meaningful words from free-form trend text (no NLP dependency)."""
    # Remove punctuation, lowercase, split
    words = re.findall(r"[a-zA-Z\u0900-\u097F\u0B80-\u0BFF]+", text.lower())
    # Simple stop-word filter
    stop = {
        "the", "a", "an", "in", "on", "at", "of", "and", "or", "is",
        "are", "was", "were", "to", "for", "with", "this", "that",
    }
    return [w for w in words if w not in stop and len(w) > 2]


# ── Main Engine ────────────────────────────────────────────────────────────────

class TrendEngine:
    """
    Aggregates trend signals from user input, external APIs, and visual entities.
    Produces a structured `trend_context` dict for the Universal Content Director.
    """

    def get_trend_context(
        self,
        visual_entities: Optional[List[str]] = None,
    ) -> dict:
        """
        Build the full trend context by combining all available signals.

        Args:
            visual_entities: List of entity strings already detected by forensic
                             analyzer / content_director (e.g. ["person:male"]).

        Returns:
            Dict with key "trend_context" (always returns; never raises).
        """
        try:
            all_topics:   List[str] = []
            all_keywords: List[str] = []
            sources_used: List[str] = []

            # ── Source 1: User trend hints ─────────────────────────────────────
            user_entries = load_user_trends()
            for entry in user_entries:
                raw = entry.get("input", "")
                if raw:
                    all_topics.append(raw)
                    all_keywords.extend(_extract_keywords(raw))
                    sources_used.append("user_input")

            # ── Source 2: External trends (best-effort) ────────────────────────
            if all_keywords:
                gt_topics = _fetch_google_trends(all_keywords[:3])
                if gt_topics:
                    all_topics.extend(gt_topics)
                    sources_used.append("google_trends")

                yt_topics = _fetch_youtube_trending()
                if yt_topics:
                    all_topics.extend(yt_topics)
                    sources_used.append("youtube_trending")

            # ── Source 3: Visual entities as context ───────────────────────────
            entities = visual_entities or []

            # ── Aggregate ─────────────────────────────────────────────────────
            # De-duplicate while preserving order
            seen: set = set()
            topics_dedup: List[str] = []
            for t in all_topics:
                key = t.lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    topics_dedup.append(t)

            keywords_dedup = list(dict.fromkeys(all_keywords))

            # Trend strength: 0.0 if no signals, scales with source count
            strength = min(1.0, len(sources_used) * 0.3 + (0.1 if topics_dedup else 0.0))

            result = {
                "trend_context": {
                    "topics":         topics_dedup[:20],
                    "entities":       entities[:10],
                    "keywords":       keywords_dedup[:20],
                    "trend_strength": round(strength, 3),
                }
            }

            if topics_dedup:
                logger.info(
                    f"🌊 [Trend] Context built: {len(topics_dedup)} topics, "
                    f"{len(keywords_dedup)} keywords, "
                    f"strength={strength:.2f}, sources={list(set(sources_used))}"
                )
            else:
                logger.info("🌊 [Trend] No active trend signals found — empty context")

            return result

        except Exception as e:
            logger.warning(f"🌊 [Trend] get_trend_context error: {e}. Returning defaults.")
            return DEFAULT_TREND_CONTEXT.copy()


# ── Module-level singleton + convenience ──────────────────────────────────────

_engine: Optional[TrendEngine] = None


def get_engine() -> TrendEngine:
    global _engine
    if _engine is None:
        _engine = TrendEngine()
    return _engine


def get_trend_context(visual_entities: Optional[List[str]] = None) -> dict:
    """Convenience function. Never raises."""
    return get_engine().get_trend_context(visual_entities=visual_entities)
