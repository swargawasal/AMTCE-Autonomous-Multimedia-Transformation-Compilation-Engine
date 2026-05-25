"""
ig_story_dm_listener.py
───────────────────────
Automation 2: Instant DM from Stories

Polls the Instagram Graph API for:
  • Story REPLIES  — users who directly replied to an active story
  • Story REACTIONS — users who reacted (emoji) to an active story

When detected, sends a private DM with the relevant affiliate link.

State is persisted to Monetization_Metrics/ig_story_replied.json to prevent
duplicate messages.

Controlled by:
  IG_STORY_POLL_INTERVAL   (env) — poll frequency in seconds (default 180s)
  ENABLE_META_UPLOAD=yes   (env) — master gate (shared with uploader)
"""

import os
import time
import json
import asyncio
import logging
import threading
import aiohttp
from typing import Dict, Optional, Set

from Uploader_Modules.meta_uploader import AsyncMetaUploader

logger = logging.getLogger("ig_story_dm_listener")

GRAPH_API_URL = "https://graph.facebook.com/v19.0"

# ── Default DM text when someone replies/reacts to a story ──────────────────
_STORY_DM_TEMPLATE = (
    "Hey! 👋 Thanks for watching and reacting!\n\n"
    "You asked for it — here's the shopping link:\n"
    "🔗 {link}\n\n"
    "🛍️ Limited stock — grab it while it lasts!\n"
    "DM anytime if you need sizing help. 💬"
)


class IGStoryDMListener:
    """
    Polls Instagram Story replies and reactions and sends DMs with affiliate links.
    State is tracked in Monetization_Metrics/ig_story_replied.json.
    """

    STATE_FILE = "Monetization_Metrics/ig_story_replied.json"
    DM_MAP_FILE = "Monetization_Metrics/Amazon_affliate_link.json"

    def __init__(self, test_mode: bool = False):
        self.test_mode = test_mode
        self.state = self._load_state()

    # ── State persistence ────────────────────────────────────────────────────

    def _load_state(self) -> Dict:
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {"replied_users": []}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
        with open(self.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=4)

    # ── Credential resolution ────────────────────────────────────────────────

    def _get_creds(self) -> Dict:
        return AsyncMetaUploader._resolve_meta_config(os.getenv("DEFAULT_NICHE", "General_Fallback"))

    # ── Affiliate link lookup ────────────────────────────────────────────────

    def _get_affiliate_link(self) -> str:
        """
        Returns the most relevant affiliate link. Falls back to a generic link
        if no niche-specific link is configured.
        """
        # Try ig_dm_map for active reels first to get a link
        if os.path.exists(self.DM_MAP_FILE):
            try:
                with open(self.DM_MAP_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Check for the new unified database format
                dm_map = data.get("by_ig_id", {}) if "by_ig_id" in data else data
                
                current_time = time.time()
                for _, mapping in dm_map.items():
                    # Handle both new dict format and old string format
                    if isinstance(mapping, dict):
                        link = mapping.get("link", "")
                        ts = mapping.get("timestamp", 0)
                    else:
                        link = mapping
                        ts = current_time # Assume active if it's just a string link
                        
                    if link and (current_time - ts) < (72 * 3600):
                        return link
            except Exception:
                pass

        # Amazon affiliate fallback
        affiliate_path = "Monetization_Metrics/Amazon_affliate_link.json"
        if os.path.exists(affiliate_path):
            try:
                with open(affiliate_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for _, products in data.items():
                    for _, link in products.items():
                        if link:
                            return link
            except Exception:
                pass

        return os.getenv("DEFAULT_AFFILIATE_LINK", "")

    # ── Send DM ──────────────────────────────────────────────────────────────

    async def _send_dm(
        self,
        session: aiohttp.ClientSession,
        page_id: str,
        page_token: str,
        recipient_igsid: str,
        link: str,
    ) -> bool:
        """
        Sends a private DM using the Messenger API.
        recipient_igsid: the Instagram Scoped User ID of the target.
        """
        if self.test_mode:
            logger.info(
                f"[TEST MODE] Would send story DM to IGSID={recipient_igsid} | link={link}"
            )
            return True

        url = f"{GRAPH_API_URL}/{page_id}/messages"
        payload = {
            "recipient": {"id": recipient_igsid},
            "message": {
                "text": _STORY_DM_TEMPLATE.format(link=link)
            },
        }
        params = {"access_token": page_token}

        try:
            async with session.post(url, json=payload, params=params) as resp:
                data = await resp.json()
                if resp.status == 200 and "message_id" in data:
                    logger.info(f"✅ [STORY_DM] DM sent to IGSID={recipient_igsid}")
                    return True
                else:
                    err = data.get("error", {})
                    logger.error(
                        f"❌ [STORY_DM] Failed to send DM to {recipient_igsid}: "
                        f"{err.get('message', 'Unknown Error')}"
                    )
                    return False
        except Exception as e:
            logger.error(f"❌ [STORY_DM] Exception during DM POST: {e}")
            return False

    # ── Core poll ────────────────────────────────────────────────────────────

    async def poll_stories(self):
        logger.info("📖 [STORY_DM] Polling active Instagram stories...")

        creds = self._get_creds()
        ig_token = creds.get("IG_BUSINESS_TOKEN", "")
        ig_id = creds.get("IG_BUSINESS_ID", "")
        page_id = creds.get("META_PAGE_ID", "")
        page_token = creds.get("META_PAGE_TOKEN", "")

        if not ig_token or not ig_id or not page_id or not page_token:
            logger.warning("⚠️ [STORY_DM] Missing Meta credentials. Skipping.")
            return

        link = self._get_affiliate_link()
        if not link:
            logger.warning("⚠️ [STORY_DM] No affiliate link found. Skipping.")
            return

        replied: Set[str] = set(self.state.get("replied_users", []))

        async with aiohttp.ClientSession() as session:
            # 1. Fetch active stories for this IG business account
            stories_url = f"{GRAPH_API_URL}/{ig_id}/stories"
            stories_params = {
                "access_token": ig_token,
                "fields": "id,timestamp",
            }

            try:
                async with session.get(stories_url, params=stories_params) as resp:
                    if resp.status != 200:
                        err = await resp.json()
                        logger.warning(f"⚠️ [STORY_DM] Could not fetch stories: {err}")
                        return

                    stories_data = await resp.json()
                    stories = stories_data.get("data", [])

                    if not stories:
                        logger.info("📖 [STORY_DM] No active stories found.")
                        return

                    logger.info(f"📖 [STORY_DM] Found {len(stories)} active stories.")

            except Exception as e:
                logger.error(f"❌ [STORY_DM] Error fetching stories: {e}")
                return

            # 2. For each story, check replies and reactions
            for story in stories:
                story_id = story.get("id")
                if not story_id:
                    continue

                # ── Story Replies ──
                await self._process_story_replies(
                    session, story_id, ig_token, page_id, page_token, link, replied
                )

                # ── Story Reactions ──
                await self._process_story_reactions(
                    session, story_id, ig_token, page_id, page_token, link, replied
                )

        # Persist dedup state
        self.state["replied_users"] = list(replied)
        self._save_state()
        logger.info("✅ [STORY_DM] Story poll complete.")

    async def _process_story_replies(
        self,
        session,
        story_id: str,
        ig_token: str,
        page_id: str,
        page_token: str,
        link: str,
        replied: Set[str],
    ):
        """Fetches and processes direct text replies to a story."""
        url = f"{GRAPH_API_URL}/{story_id}/replies"
        params = {
            "access_token": ig_token,
            "fields": "id,from,text,timestamp",
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                replies = data.get("data", [])

                for reply in replies:
                    sender = reply.get("from", {})
                    user_igsid = sender.get("id", "")
                    if not user_igsid or user_igsid in replied:
                        continue

                    logger.info(
                        f"💬 [STORY_DM] Story reply from IGSID={user_igsid}"
                    )
                    success = await self._send_dm(
                        session, page_id, page_token, user_igsid, link
                    )
                    if success:
                        replied.add(user_igsid)
                        await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"❌ [STORY_DM] Error fetching story replies: {e}")

    async def _process_story_reactions(
        self,
        session,
        story_id: str,
        ig_token: str,
        page_id: str,
        page_token: str,
        link: str,
        replied: Set[str],
    ):
        """Fetches and processes emoji reactions to a story."""
        url = f"{GRAPH_API_URL}/{story_id}/reactions"
        params = {
            "access_token": ig_token,
            "fields": "id,type,from",
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                reactions = data.get("data", [])

                for reaction in reactions:
                    user = reaction.get("from", {})
                    user_igsid = user.get("id", "")
                    if not user_igsid or user_igsid in replied:
                        continue

                    emoji = reaction.get("type", "❤️")
                    logger.info(
                        f"😍 [STORY_DM] Story reaction ({emoji}) from IGSID={user_igsid}"
                    )
                    success = await self._send_dm(
                        session, page_id, page_token, user_igsid, link
                    )
                    if success:
                        replied.add(user_igsid)
                        await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"❌ [STORY_DM] Error fetching story reactions: {e}")

    # ── Background Thread ────────────────────────────────────────────────────

    @classmethod
    def start_background_polling(cls, interval_seconds: int = None):
        """
        Launches a daemon thread that polls story interactions in a continuous loop.
        Controlled by IG_STORY_POLL_INTERVAL env var (default 180s).
        Gate: ENABLE_META_UPLOAD must be yes/true/on.
        """
        if os.getenv("ENABLE_STORY_DM_LISTENER", "yes").lower() not in ["yes", "true", "on"]:
            logger.info("🚫 [STORY_DM] Disabled via ENABLE_STORY_DM_LISTENER=no")
            return
        if os.getenv("ENABLE_META_UPLOAD", "no").lower() not in ["yes", "true", "on"]:
            logger.info("🚫 [STORY_DM] Meta upload disabled — skipping story DM listener.")
            return

        interval = interval_seconds or int(os.getenv("IG_STORY_POLL_INTERVAL", 180))

        def _loop():
            logger.info(
                f"🔁 [STORY_DM] Background story listener started. Poll interval: {interval}s"
            )
            while True:
                try:
                    listener = cls()
                    asyncio.run(listener.poll_stories())
                except Exception as e:
                    logger.error(f"❌ [STORY_DM] Polling error: {e}")
                finally:
                    time.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True, name="ig_story_dm_listener")
        t.start()
        logger.info("✅ [STORY_DM] Background story DM listener thread started.")


# Standalone execution
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    listener = IGStoryDMListener(test_mode=True)
    asyncio.run(listener.poll_stories())
