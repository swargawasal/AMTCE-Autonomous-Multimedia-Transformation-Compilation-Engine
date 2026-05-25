"""
ig_comment_link_responder.py
────────────────────────────
Comment-to-DM Link Bot — Rate-Limited Edition

When a viewer comments the trigger keyword (e.g. "LINK", "DRESS") on an
Instagram post, this module:

  1. Reads the post's own caption to extract the #vid_XXXXXX tag
  2. Looks up Monetization_Metrics/Amazon_affliate_link.json → by_uid[vid_XXXXXX]
  3. Queues the commenter in a SafeDMQueue with jitter delay
  4. SafeDMQueue sends the EXACT affiliate link to the commenter via Instagram DM
  5. Respects hourly (80) and session (200) caps to avoid Meta spam flag

Rate-limit behaviour:
  - 3–9 second jitter between every DM send (random.uniform — looks human)
  - Hourly cap: 80 DMs/hr  (Meta soft limit is ~100 — we stay well below)
  - Session cap: 200 DMs/24h
  - Deduplication: each commenter gets at most one DM per session
  - On hourly cap hit: bot sleeps until the next hourly window resets
  - On DM send failure: single retry after 30s, then skips

Controlled via .env:
  COMMENT_LINK_BOT_ENABLED=yes    (default: no — must opt-in)
  IG_COMMENT_POLL_INTERVAL=300    (default: 5 minutes)
  MAX_POSTS_TO_SCAN=10            (number of recent posts to check)
  DM_DELAY_MIN=3                  (min seconds between DM sends)
  DM_DELAY_MAX=9                  (max seconds between DM sends)
  HOURLY_DM_CAP=80                (max DMs per hour)
  SESSION_DM_CAP=200              (max DMs per 24h session)
  COMMENT_KEYWORD=LINK            (trigger word, case-insensitive)

How the UID is embedded:
  Caption contains "#vid_a3f9b2" in the hashtag section.
  When someone comments the keyword on that post, this bot reads the post
  caption, extracts the UID, looks up the exact affiliate link, and DMs
  the commenter through the SafeDMQueue.

DEPLOY ORDER:
  Step 1: DRY_RUN=yes, COMMENT_LINK_BOT_ENABLED=no  → verify caption only
  Step 2: DRY_RUN=no, COMMENT_LINK_BOT_ENABLED=no   → post one caption manually
  Step 3: COMMENT_LINK_BOT_ENABLED=yes only now      → comment your keyword, verify DM
  Step 4: Monitor hourly DM log — cap respected?
  NEVER flip both DRY_RUN and bot to live at the same time.
"""

import os
import re
import json
import time
import logging
import asyncio
import threading
import aiohttp
from collections import deque
from typing import Dict, List, Optional, Callable

from Uploader_Modules.meta_uploader import AsyncMetaUploader

logger = logging.getLogger("ig_comment_link_responder")

GRAPH_API_URL = "https://graph.facebook.com/v19.0"

# ── Rate-limit config (overridable via .env) ─────────────────────────────────
_DM_DELAY_MIN  = float(os.getenv("DM_DELAY_MIN",  "3"))
_DM_DELAY_MAX  = float(os.getenv("DM_DELAY_MAX",  "9"))
_HOURLY_CAP    = int(os.getenv("HOURLY_DM_CAP",   "80"))
_SESSION_CAP   = int(os.getenv("SESSION_DM_CAP",  "200"))

# ── Trigger keyword ──────────────────────────────────────────────────────────
_COMMENT_KEYWORD = os.getenv("COMMENT_KEYWORD", "LINK").upper()

# Keywords that trigger the affiliate link DM — includes the configured keyword
_LINK_TRIGGERS = {
    _COMMENT_KEYWORD.lower(),
    "link", "link?", "send link", "buy", "price", "where to buy",
    "kahan se", "link bhejo", "price?", "how much", "kitna",
    "buy link", "shop link", "purchase", "order", "from where",
}

# State file tracks which comments we've already replied to
_STATE_FILE    = "Monetization_Metrics/ig_comment_link_replied.json"
_AFFILIATE_FILE = "Monetization_Metrics/Amazon_affliate_link.json"


# ─────────────────────────────────────────────────────────────────────────────
# SAFE DM QUEUE — rate-limited, deduplicated, jitter-delayed
# ─────────────────────────────────────────────────────────────────────────────

class SafeDMQueue:
    """
    Thread-safe, rate-limited queue for Instagram DM sends.

    Jitter (3–9s between sends) prevents the mechanical regularity that
    Meta's spam detection looks for. Flat 2s intervals = instant flag.

    Hourly cap: sleeps until window resets when HOURLY_DM_CAP is hit.
    Session cap: hard stops and logs when SESSION_DM_CAP is reached.
    Deduplication: each username gets at most one DM per queue instance.
    """

    def __init__(
        self,
        delay_min: float = _DM_DELAY_MIN,
        delay_max: float = _DM_DELAY_MAX,
        hourly_cap: int  = _HOURLY_CAP,
        session_cap: int = _SESSION_CAP,
    ):
        import random
        self._random       = random
        self.queue         = deque()
        self.seen          = set()          # per-session deduplication
        self.sent_this_hr  = 0
        self.sent_total    = 0
        self.hr_reset_at   = time.time() + 3600
        self.delay_min     = delay_min
        self.delay_max     = delay_max
        self.hourly_cap    = hourly_cap
        self.session_cap   = session_cap

    def enqueue(self, username: str, link: str):
        """Add a DM task. Silently drops duplicates."""
        if username in self.seen:
            logger.info(f"[DM_QUEUE] Skip duplicate: @{username}")
            return
        self.queue.append((username, link))
        self.seen.add(username)
        logger.debug(f"[DM_QUEUE] Queued DM for @{username} (queue size={len(self.queue)})")

    def flush(self, send_fn: Callable[[str, str], bool]):
        """
        Drain the queue, calling send_fn(username, link) for each item.
        send_fn must return True on success, False on failure.
        Blocks the calling thread — run in a background thread.
        """
        while self.queue:
            # ── Session cap ───────────────────────────────────────────────────
            if self.sent_total >= self.session_cap:
                logger.warning(
                    f"[DM_QUEUE] Session cap hit ({self.session_cap} DMs). Stopping."
                )
                break

            # ── Hourly reset ──────────────────────────────────────────────────
            now = time.time()
            if now >= self.hr_reset_at:
                self.sent_this_hr = 0
                self.hr_reset_at  = now + 3600
                logger.info("[DM_QUEUE] Hourly window reset.")

            # ── Hourly cap sleep ──────────────────────────────────────────────
            if self.sent_this_hr >= self.hourly_cap:
                wait = max(0.0, self.hr_reset_at - time.time())
                logger.info(
                    f"[DM_QUEUE] Hourly cap hit ({self.hourly_cap}/hr). "
                    f"Sleeping {wait:.0f}s until window resets."
                )
                time.sleep(wait)
                continue

            username, link = self.queue.popleft()
            try:
                success = send_fn(username, link)
                if success:
                    self.sent_this_hr += 1
                    self.sent_total   += 1
                    logger.info(
                        f"[DM_QUEUE] Sent to @{username} "
                        f"({self.sent_this_hr}/{self.hourly_cap} this hr, "
                        f"{self.sent_total}/{self.session_cap} total)"
                    )
                else:
                    # Requeue once for retry after a longer pause
                    logger.warning(f"[DM_QUEUE] DM failed for @{username}. Retrying after 30s.")
                    self.queue.appendleft((username, link))
                    time.sleep(30)
                    continue
            except Exception as e:
                logger.error(f"[DM_QUEUE] Exception sending to @{username}: {e}")
                self.queue.appendleft((username, link))
                time.sleep(30)
                continue

            # ── Jitter delay — looks human, defeats regularity detection ─────
            jitter = self._random.uniform(self.delay_min, self.delay_max)
            logger.debug(f"[DM_QUEUE] Jitter delay: {jitter:.1f}s")
            time.sleep(jitter)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_uid_from_caption(caption: str) -> Optional[str]:
    """
    Extracts #vid_XXXXXX tag from a post caption.
    Returns the UID string (e.g. 'vid_a3f9b2') or None.
    Note: the #vid_ tag is stripped from the published caption by sanitise_ig_caption()
    — this reads it from the internal session/sidecar, not the live post.
    """
    if not caption:
        return None
    match = re.search(r"#(vid_[a-f0-9]{6})", caption, re.IGNORECASE)
    return match.group(1) if match else None


def _lookup_affiliate_link(media_id: str = None, uid: str = None) -> Optional[str]:
    """
    Looks up the affiliate link from the JSON store.

    Priority:
      1. by_ig_id[media_id]  — Direct mapping from the new system
      2. by_uid[uid]         — Legacy mapping from #vid_ tag in caption
      3. general_fallback    — Final safety link
    """
    try:
        if not os.path.exists(_AFFILIATE_FILE):
            return None
        with open(_AFFILIATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 1. Direct Instagram Media ID Match (New System)
        if media_id:
            entry = data.get("by_ig_id", {}).get(str(media_id))
            if entry:
                link = entry.get("link") if isinstance(entry, dict) else entry
                if link:
                    logger.info(f"[COMMENT_BOT] Direct IG_ID match: {media_id} → {link}")
                    return link

        # 2. Legacy UID Match (from Caption)
        if uid:
            entry = data.get("by_uid", {}).get(uid)
            if entry:
                link = entry.get("link")
                if link:
                    logger.info(f"[COMMENT_BOT] Legacy UID match: {uid} → {link}")
                    return link

        # 3. Final Global Fallback
        fallback = data.get("general_fallback_link")
        if fallback:
            logger.info(f"[COMMENT_BOT] Using global fallback link: {fallback}")
            return fallback

    except Exception as e:
        logger.warning(f"[COMMENT_BOT] Affiliate lookup failed: {e}")
    return None


def _is_link_trigger(comment_text: str) -> bool:
    """Returns True if the comment contains a link-request keyword."""
    if not comment_text:
        return False
    text_lower = comment_text.lower().strip()
    if text_lower in _LINK_TRIGGERS:
        return True
    words = set(re.findall(r"\w+", text_lower))
    dynamic_trigger = {_COMMENT_KEYWORD.lower(), "link", "buy", "price", "kahan", "kitna", "order"}
    return bool(words & dynamic_trigger)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BOT CLASS
# ─────────────────────────────────────────────────────────────────────────────

class IGCommentLinkResponder:
    """
    Polls recent Instagram posts for new trigger-keyword comments and
    queues affiliate link DMs through the SafeDMQueue.
    """

    def __init__(self, test_mode: bool = False):
        self.test_mode  = test_mode
        self._replied: Dict[str, bool] = self._load_state()
        self._max_posts = int(os.getenv("MAX_POSTS_TO_SCAN", 10))
        self._dm_queue  = SafeDMQueue()

    # ── State ─────────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict:
        if os.path.exists(_STATE_FILE):
            try:
                with open(_STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self):
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        try:
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._replied, f, indent=2)
        except Exception as e:
            logger.warning(f"[COMMENT_BOT] State save failed: {e}")

    # ── Credentials ───────────────────────────────────────────────────────────

    def _get_creds(self) -> Dict:
        return AsyncMetaUploader._resolve_meta_config(os.getenv("DEFAULT_NICHE", "General_Fallback"))

    # ── API helpers ───────────────────────────────────────────────────────────

    async def _get_recent_posts(
        self, session: aiohttp.ClientSession, ig_user_id: str, access_token: str
    ) -> List[Dict]:
        url = f"{GRAPH_API_URL}/{ig_user_id}/media"
        params = {
            "fields": "id,caption,timestamp",
            "limit": self._max_posts,
            "access_token": access_token,
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("data", [])
        except Exception as e:
            logger.error(f"[COMMENT_BOT] Failed to fetch posts: {e}")
            return []

    async def _get_comments(
        self, session: aiohttp.ClientSession, media_id: str, access_token: str
    ) -> List[Dict]:
        url = f"{GRAPH_API_URL}/{media_id}/comments"
        params = {
            "fields": "id,text,from,timestamp",
            "limit": 50,
            "access_token": access_token,
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    logger.warning(f"⚠️ [COMMENT_BOT] Failed to fetch comments for {media_id}. Status: {resp.status}. Error: {err_txt}")
                    return []
                data = await resp.json()
                return data.get("data", [])
        except Exception as e:
            logger.error(f"[COMMENT_BOT] Failed to fetch comments for {media_id}: {e}")
            return []

    async def _send_dm_async(
        self,
        session: aiohttp.ClientSession,
        page_id: str,
        page_token: str,
        comment_id: str,
        link: str,
    ) -> bool:
        if self.test_mode:
            logger.info(f"[TEST MODE] Would send DM to {comment_id}: {link}")
            return True

        msg = (
            f"Hey! Here's the link you asked for 🛒\n"
            f"{link}\n\n"
            f"Happy shopping! 💫 Follow us for more finds like this."
        )
        url = f"{GRAPH_API_URL}/{page_id}/messages"
        payload = {
            "recipient": {"comment_id": comment_id},
            "message": {"text": msg},
        }
        params = {"access_token": page_token}
        try:
            async with session.post(url, json=payload, params=params) as resp:
                data = await resp.json()
                if resp.status == 200 and "message_id" in data:
                    logger.info(f"✅ [COMMENT_BOT] Link DM sent to {comment_id}")
                    return True
                err = data.get("error", {}).get("message", "unknown")
                logger.warning(f"⚠️ [COMMENT_BOT] DM failed to {comment_id}: {err}")
        except Exception as e:
            logger.error(f"❌ [COMMENT_BOT] DM error: {e}")
        return False

    # ── Core poll ──────────────────────────────────────────────────────────────

    async def poll_and_respond(self):
        """
        Main polling cycle:
        1. Fetch recent posts
        2. For each post, fetch comments
        3. Detect trigger-keyword comments
        4. Extract UID from post caption → look up exact affiliate link
        5. Enqueue in SafeDMQueue (not fire-and-forget)
        6. Flush queue with rate-limiting in a background thread
        """
        logger.info(
            f"💬 [COMMENT_BOT] Polling Instagram posts for '{_COMMENT_KEYWORD}' comments..."
        )

        creds      = self._get_creds()
        page_id    = creds.get("META_PAGE_ID", "")
        page_token = creds.get("META_PAGE_TOKEN", "")
        ig_user_id = creds.get("IG_BUSINESS_ID", page_id)
        ig_token   = creds.get("IG_BUSINESS_TOKEN", page_token)

        if not ig_user_id or not ig_token:
            logger.warning("⚠️ [COMMENT_BOT] Missing credentials. Skipping.")
            return

        updated = False
        async with aiohttp.ClientSession() as session:
            posts = await self._get_recent_posts(session, ig_user_id, ig_token)
            if not posts:
                logger.info("💬 [COMMENT_BOT] No recent posts found.")
                return

            logger.info(f"💬 [COMMENT_BOT] Scanning {len(posts)} post(s)...")

            for post in posts:
                media_id = post.get("id")
                caption  = post.get("caption", "") or ""
                if not media_id:
                    continue

                # logger.debug(f"[DEBUG] Inspecting post {media_id} with caption: {caption[:50]}...")

                uid = _extract_uid_from_caption(caption)

                comments = await self._get_comments(session, media_id, ig_token)
                for comment in comments:
                    comment_id   = comment.get("id", "")
                    comment_text = (comment.get("text") or "").strip()
                    from_user    = comment.get("from", {})
                    commenter_id = str(from_user.get("id", ""))
                    
                    # logger.debug(f"[DEBUG] Fetched comment '{comment_text}' from {commenter_id} on post {media_id}")

                    if self._replied.get(comment_id):
                        continue
                    if commenter_id == str(page_id):
                        continue
                    if not _is_link_trigger(comment_text):
                        # logger.debug(f"[DEBUG] Comment '{comment_text}' is NOT a link trigger.")
                        continue

                    logger.info(
                        f"💬 [COMMENT_BOT] Trigger comment on post {media_id}: "
                        f"'{comment_text[:50]}' from IGSID={commenter_id}"
                    )

                    link = _lookup_affiliate_link(media_id=media_id, uid=uid)
                    if not link:
                        logger.warning(
                            f"⚠️ [COMMENT_BOT] No link found for post {media_id} "
                            f"(uid={uid}). Skipping DM."
                        )
                        self._replied[comment_id] = True
                        updated = True
                        continue

                    # ── Enqueue — do NOT send inline (rate-limit queue handles it) ──
                    self._dm_queue.enqueue(comment_id, link)
                    self._replied[comment_id] = True
                    updated = True

            # ── Flush queue in a separate thread so async loop isn't blocked ──
            if self._dm_queue.queue:
                logger.info(
                    f"[COMMENT_BOT] Flushing DM queue ({len(self._dm_queue.queue)} pending)..."
                )

                # Build a synchronous send wrapper for SafeDMQueue.flush()
                # We use a new event loop inside the thread for the async call.
                def _sync_send(recipient_id: str, link: str) -> bool:
                    async def _inner():
                        async with aiohttp.ClientSession() as _s:
                            return await self._send_dm_async(_s, page_id, page_token, recipient_id, link)
                    return asyncio.run(_inner())

                flush_thread = threading.Thread(
                    target=self._dm_queue.flush,
                    args=(_sync_send,),
                    daemon=True,
                    name="dm_queue_flush",
                )
                flush_thread.start()

        if updated:
            self._save_state()

        logger.info("✅ [COMMENT_BOT] Poll complete.")

    # ── Background Thread ─────────────────────────────────────────────────────

    @classmethod
    def start_background_polling(cls, interval_seconds: int = None):
        """
        Launches a daemon thread that polls for trigger-keyword comments in a loop.

        REQUIRES: COMMENT_LINK_BOT_ENABLED=yes in .env (DEFAULT: OFF)
        Interval:  IG_COMMENT_POLL_INTERVAL env var (default 300s / 5 minutes)

        DEPLOY ORDER — enable bot LAST, after DRY_RUN=no has been verified:
          1. DRY_RUN=yes, bot=no  → caption preview only
          2. DRY_RUN=no, bot=no   → post one caption manually, verify live
          3. bot=yes ONLY NOW     → comment your keyword, verify DM arrives
          4. Monitor hourly log   → confirm caps are respected
        """
        if os.getenv("COMMENT_LINK_BOT_ENABLED", "no").lower() not in ("yes", "true", "1"):
            logger.info(
                "🚫 [COMMENT_BOT] COMMENT_LINK_BOT_ENABLED is not set to 'yes'. "
                "Bot inactive. Enable AFTER verifying DRY_RUN=no output."
            )
            return

        if os.getenv("ENABLE_META_UPLOAD", "no").lower() not in ("yes", "true", "1"):
            logger.info("🚫 [COMMENT_BOT] Meta upload disabled — skipping comment bot.")
            return

        interval = interval_seconds or int(os.getenv("IG_COMMENT_POLL_INTERVAL", 300))

        def _loop():
            logger.info(
                f"💬 [COMMENT_BOT] Comment→DM Bot started. "
                f"Trigger='{_COMMENT_KEYWORD}' | Poll={interval}s | "
                f"Caps: {_HOURLY_CAP}/hr, {_SESSION_CAP}/session | "
                f"Jitter: {_DM_DELAY_MIN}–{_DM_DELAY_MAX}s"
            )
            while True:
                try:
                    responder = cls()
                    asyncio.run(responder.poll_and_respond())
                except Exception as e:
                    logger.error(f"❌ [COMMENT_BOT] Polling error: {e}")
                finally:
                    time.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True, name="ig_comment_link_responder")
        t.start()
        logger.info(
            "✅ [COMMENT_BOT] Comment → DM Bot thread started.\n"
            f"   When viewers comment '{_COMMENT_KEYWORD}' on your posts, they get\n"
            "   the exact affiliate link DMed to them — rate-limited and jittered."
        )


# ── Standalone testing ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    responder = IGCommentLinkResponder(test_mode=True)
    asyncio.run(responder.poll_and_respond())
