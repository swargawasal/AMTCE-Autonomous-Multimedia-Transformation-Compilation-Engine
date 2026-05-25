"""
ig_auto_dm_responder.py
────────────────────────
Automation 3: Respond to All DMs — powered by Gemini AI

When someone DMs the Instagram account, Gemini reads the conversation
history and responds exactly like the account owner would:
  • Natural, human-style language — no robotic templates
  • Full product + niche knowledge injected via system prompt
  • Affiliate link weaved in naturally when buying intent is detected
  • Remembers previous turns in the conversation (multi-turn context)
  • Never double-replies to the same message

Knowledge passed to Gemini:
  1. Owner personas / brand voice (Credentials/.env  →  IG_OWNER_NAME, IG_OWNER_BIO)
  2. All affiliate links from Monetization_Metrics/Amazon_affliate_link.json
  3. Recent post captions from Monetization_Metrics/ig_dm_map.json (context)
  4. Configurable niche persona from Credentials/social_media/*/dm_templates.json

State: Monetization_Metrics/ig_dm_replied_conversations.json
       (tracks last-replied message ID per conversation)

Controlled via .env:
  DM_AUTO_RESPONDER_ENABLED=yes  (default: no  — must opt-in)
  IG_DM_POLL_INTERVAL=300        (default: 5 minutes)
  IG_OWNER_NAME                  (name Gemini uses when identifying itself)
  IG_OWNER_BIO                   (1-2 line bio about the account owner)
  IG_MAX_CONVERSATIONS=20        (max conversations to process per cycle)
"""

import os
import time
import json
import asyncio
import logging
import threading
import aiohttp
from typing import Dict, List, Optional, Set

from Uploader_Modules.meta_uploader import AsyncMetaUploader

logger = logging.getLogger("ig_auto_dm_responder")

GRAPH_API_URL = "https://graph.facebook.com/v19.0"


# ─────────────────────────────────────────────────────────────────────────────
# GEMINI PERSONA ENGINE
# Generates human-like, context-aware Instagram DM replies.
# ─────────────────────────────────────────────────────────────────────────────

class GeminiDMPersona:
    """
    Uses GeminiGovernor to generate natural, human-style DM replies.
    Receives full conversation context + product knowledge.
    """

    # Hard character cap to keep replies concise (Instagram DM feel)
    MAX_REPLY_CHARS = 320

    def __init__(self):
        self._governor = None
        self._affiliate_data: Dict = {}
        self._dm_map: Dict = {}
        self._load_knowledge()

    def _load_knowledge(self):
        """Pre-loads product + affiliate data into memory."""
        # Affiliate links
        path = "Monetization_Metrics/Amazon_affliate_link.json"
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._affiliate_data = json.load(f)
                logger.info(f"📚 [DM_AI] Loaded affiliate data from {path}")
            except Exception as e:
                logger.warning(f"⚠️ [DM_AI] Could not load affiliate data: {e}")

        # Recent post mappings (using unified source)
        if hasattr(self, "_affiliate_data") and "by_ig_id" in self._affiliate_data:
            self._dm_map = self._affiliate_data["by_ig_id"]
        else:
            self._dm_map = {}

    def _get_governor(self):
        """Lazy-load governor (avoids circular import at module level)."""
        if self._governor is None:
            try:
                from Intelligence_Modules.gemini_governor import GeminiGovernor
                self._governor = GeminiGovernor()
            except Exception as e:
                logger.error(f"❌ [DM_AI] Could not load GeminiGovernor: {e}")
        return self._governor

    def _build_system_prompt(self) -> str:
        owner_name = os.getenv("IG_OWNER_NAME", "").strip()
        owner_bio  = os.getenv("IG_OWNER_BIO", "").strip()
        niche      = os.getenv("DEFAULT_NICHE", "").strip()

        # Warn if critical persona vars are missing so the user knows to set them
        if not owner_name:
            logger.warning(
                "⚠️ [DM_AI] IG_OWNER_NAME is not set in .env. "
                "Gemini will reply without knowing your name. "
                "Add: IG_OWNER_NAME=YourInstagramHandle"
            )
            owner_name = "the account owner"   # safe neutral fallback
        if not owner_bio:
            logger.warning(
                "⚠️ [DM_AI] IG_OWNER_BIO is not set in .env. "
                "Add a short bio for better Gemini persona accuracy: "
                "IG_OWNER_BIO=Your short brand description here"
            )
            owner_bio = "A content creator."   # safe neutral fallback
        if not niche:
            niche = "content creation"          # safe neutral fallback

        # Build product knowledge block
        product_lines = []
        for category, products in self._affiliate_data.items():
            if isinstance(products, dict):
                for product_name, link in products.items():
                    if link:
                        product_lines.append(f"  • {product_name}: {link}")
            elif isinstance(products, list):
                for link in products:
                    if isinstance(link, str) and link.startswith("http"):
                        product_lines.append(f"  • General link: {link}")

        product_block = (
            "\n".join(product_lines)
            if product_lines
            else "  • No specific products. Recommend browsing the page."
        )

        # Recent posts for context
        recent_posts = []
        current_time = time.time()
        for _, data in self._dm_map.items():
            link = data.get("link", "")
            caption_snip = str(data.get("caption", ""))[:80]
            ts = data.get("timestamp", 0)
            if link and (current_time - ts) < (72 * 3600):
                recent_posts.append(f"  • \"{caption_snip}...\" → {link}")
        recent_block = "\n".join(recent_posts) if recent_posts else "  • No recent posts."

        return f"""You are replying to Instagram DMs on behalf of {owner_name}.

ABOUT YOU:
{owner_bio}

YOUR NICHE: {niche}

YOUR PRODUCTS & AFFILIATE LINKS:
{product_block}

RECENT POSTS (caption snippet → shopping link):
{recent_block}

HOW TO REPLY:
- Write EXACTLY like a real person texting on Instagram — casual, warm, friendly
- Keep it SHORT (1-3 sentences max). No essays. No bullet points. No emojis overload.
- Use 1-2 emojis max per reply, naturally placed
- If they ask "price?", "where to buy?", "link?", "how much?" → weave the link naturally into the reply
- If they compliment → say thanks genuinely, maybe ask about their style or keep the convo going
- If they ask a question you don't know → be honest: "not sure about that one, DM me more details! 😊"
- NEVER say you are an AI, bot, or automated system
- NEVER use generic phrases like "Hello! How can I help you today?"
- Sound like you just saw the message on your phone and replied
- Match their energy — if they're excited, be excited; if they're casual, be casual
- If they use regional greetings or slang, mirror it naturally
"""

    def generate_reply(self, conversation_history: List[Dict], incoming_message: str) -> Optional[str]:
        """
        Given the conversation history and the latest incoming message,
        generates a natural human-style reply using Gemini.

        conversation_history: [{"role": "user"|"assistant", "text": "..."}, ...]
        incoming_message: the latest message text from the customer
        """
        governor = self._get_governor()
        if not governor:
            logger.error("❌ [DM_AI] Governor unavailable. Cannot generate reply.")
            return None

        system_prompt = self._build_system_prompt()

        # Build conversation thread string
        history_str = ""
        for turn in conversation_history[-6:]:  # last 3 exchanges max
            role_label = "Customer" if turn["role"] == "user" else "You"
            history_str += f"{role_label}: {turn['text']}\n"

        # Full prompt
        prompt = (
            f"{system_prompt}\n\n"
            f"--- CONVERSATION SO FAR ---\n{history_str}"
            f"Customer: {incoming_message}\n\n"
            f"Reply as yourself in 1-3 casual sentences (Instagram DM style). "
            f"Do NOT start with 'Hi' or 'Hello'. Be direct and natural:"
        )

        try:
            reply = governor.generate(
                task_type="creative",
                prompt=prompt,
                module_name="ig_auto_dm_responder",
                gen_config={"max_output_tokens": 150, "temperature": 0.85},
            )

            if not reply:
                logger.warning("⚠️ [DM_AI] Gemini returned empty reply.")
                return None

            # Clean up any AI artifacts
            reply = reply.strip()
            # Remove any "You:" prefix the model might add
            if reply.lower().startswith("you:"):
                reply = reply[4:].strip()
            # Enforce character cap
            if len(reply) > self.MAX_REPLY_CHARS:
                # Truncate at last sentence boundary
                cutoff = reply.rfind(".", 0, self.MAX_REPLY_CHARS)
                if cutoff > 50:
                    reply = reply[:cutoff + 1]
                else:
                    reply = reply[:self.MAX_REPLY_CHARS].rstrip() + "..."

            logger.info(f"🤖 [DM_AI] Generated reply ({len(reply)} chars): {reply[:80]}...")
            return reply

        except Exception as e:
            logger.error(f"❌ [DM_AI] Gemini generation failed: {e}")
            return None


# ─────────────────────────────────────────────────────────────────────────────
# CORE RESPONDER
# ─────────────────────────────────────────────────────────────────────────────

class IGAutoDMResponder:
    """
    Monitors the Instagram inbox and auto-replies to new customer messages
    using a Gemini-powered human persona.
    """

    STATE_FILE = "Monetization_Metrics/ig_dm_replied_conversations.json"

    def __init__(self, test_mode: bool = False):
        self.test_mode = test_mode
        self.state = self._load_state()
        self.persona = GeminiDMPersona()
        self._max_conversations = int(os.getenv("IG_MAX_CONVERSATIONS", 20))

    # ── State persistence ────────────────────────────────────────────────────

    def _load_state(self) -> Dict:
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {"last_replied_msg_id": {}}   # { conversation_id: last_msg_id_we_replied_to }

    def _save_state(self):
        os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
        with open(self.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=4)

    # ── Credential resolution ────────────────────────────────────────────────

    def _get_creds(self) -> Dict:
        return AsyncMetaUploader._resolve_meta_config(os.getenv("DEFAULT_NICHE", "General_Fallback"))

    # ── Send reply DM ────────────────────────────────────────────────────────

    async def _send_reply(
        self,
        session: aiohttp.ClientSession,
        page_id: str,
        page_token: str,
        recipient_igsid: str,
        reply_text: str,
    ) -> bool:
        if self.test_mode:
            logger.info(
                f"[TEST MODE] Would send DM to IGSID={recipient_igsid}:\n  → {reply_text}"
            )
            return True

        url = f"{GRAPH_API_URL}/{page_id}/messages"
        payload = {
            "recipient": {"id": recipient_igsid},
            "message": {"text": reply_text},
        }
        params = {"access_token": page_token}

        try:
            async with session.post(url, json=payload, params=params) as resp:
                data = await resp.json()
                if resp.status == 200 and "message_id" in data:
                    logger.info(f"✅ [DM_AUTO] Replied to IGSID={recipient_igsid}")
                    return True
                else:
                    err = data.get("error", {})
                    err_msg = err.get("message", "Unknown Error")
                    # Soft fail: message-level errors (e.g. user turned off DMs)
                    logger.warning(
                        f"⚠️ [DM_AUTO] Could not reply to {recipient_igsid}: {err_msg}"
                    )
                    return False
        except Exception as e:
            logger.error(f"❌ [DM_AUTO] Error sending reply: {e}")
            return False

    # ── Fetch conversation messages ──────────────────────────────────────────

    async def _fetch_messages(
        self,
        session: aiohttp.ClientSession,
        conversation_id: str,
        access_token: str,
        limit: int = 10,
    ) -> List[Dict]:
        """
        Fetches recent messages in a conversation, newest first.
        Returns list of dicts: {"from": {"id": igsid}, "message": str, "id": str}
        """
        url = f"{GRAPH_API_URL}/{conversation_id}/messages"
        params = {
            "access_token": access_token,
            "fields": "id,message,from,created_time",
            "limit": limit,
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("data", [])
        except Exception as e:
            logger.error(f"❌ [DM_AUTO] Error fetching messages: {e}")
            return []

    # ── Core poll ─────────────────────────────────────────────────────────────

    async def poll_and_respond(self):
        """
        Main polling loop: fetches inbox conversations, detects new customer
        messages, generates AI replies, and sends them.
        """
        logger.info("📬 [DM_AUTO] Polling Instagram inbox...")

        creds = self._get_creds()
        page_id = creds.get("META_PAGE_ID", "")
        page_token = creds.get("META_PAGE_TOKEN", "")
        ig_id = creds.get("IG_BUSINESS_ID", "")

        if not page_id or not page_token:
            logger.warning("⚠️ [DM_AUTO] Missing page credentials. Skipping.")
            return

        last_replied = self.state.get("last_replied_msg_id", {})
        updated = False

        async with aiohttp.ClientSession() as session:
            # 1. List all conversations (Removed platform filter for broader search)
            conv_url = f"{GRAPH_API_URL}/{page_id}/conversations"
            conv_params = {
                "access_token": page_token,
                "fields": "id,participants,updated_time",
                "limit": self._max_conversations,
            }

            try:
                async with session.get(conv_url, params=conv_params) as resp:
                    resp_data = await resp.json()
                    if resp.status != 200:
                        logger.warning(f"⚠️ [DM_AUTO] Conversations fetch failed: {resp_data}")
                        return
                    conversations = resp_data.get("data", [])
            except Exception as e:
                logger.error(f"❌ [DM_AUTO] Error fetching conversations: {e}")
                return

            if not conversations:
                logger.info(f"📬 [DM_AUTO] No active conversations. Diagnostic: {resp_data}")
                return

            logger.info(f"📬 [DM_AUTO] Found {len(conversations)} conversation(s).")

            # 2. Process each conversation
            for conv in conversations:
                conv_id = conv.get("id")
                if not conv_id:
                    continue

                # Get the customer IGSID (participant who is NOT our IG account)
                participants = conv.get("participants", {}).get("data", [])
                customer_igsid = None
                for p in participants:
                    # Filter out both our IG Business ID and Page ID
                    if str(p.get("id")) not in [str(ig_id), str(page_id)]:
                        customer_igsid = str(p.get("id"))
                        break

                if not customer_igsid:
                    continue

                # 3. Fetch recent messages in this conversation
                messages = await self._fetch_messages(
                    session, conv_id, page_token, limit=12
                )
                if not messages:
                    continue

                # Messages come newest-first from the API
                # Reverse to get chronological order for context building
                messages_chrono = list(reversed(messages))
                latest_msg = messages_chrono[-1]  # newest = last after reversal

                latest_msg_id = latest_msg.get("id", "")
                latest_sender_id = str(latest_msg.get("from", {}).get("id", ""))
                latest_text = (latest_msg.get("message") or "").strip()

                # 4. Skip if: we already replied to this message, or we sent it
                if not latest_text:
                    continue

                already_replied = last_replied.get(conv_id) == latest_msg_id
                sent_by_page = latest_sender_id == str(page_id)

                if already_replied or sent_by_page:
                    continue

                logger.info(
                    f"💬 [DM_AUTO] New message in conv {conv_id}: \"{latest_text[:60]}...\""
                )

                # 5. Build conversation history for Gemini context
                history = []
                for msg in messages_chrono[:-1]:  # exclude the latest (current)
                    sender_id = str(msg.get("from", {}).get("id", ""))
                    role = "user" if sender_id != str(page_id) else "assistant"
                    text = (msg.get("message") or "").strip()
                    if text:
                        history.append({"role": role, "text": text})

                # 6. Generate AI reply
                reply = self.persona.generate_reply(history, latest_text)
                if not reply:
                    logger.warning(
                        f"⚠️ [DM_AUTO] Could not generate reply for conv {conv_id}. Skipping."
                    )
                    continue

                # 7. Send the reply
                success = await self._send_reply(
                    session, page_id, page_token, customer_igsid, reply
                )

                if success:
                    last_replied[conv_id] = latest_msg_id
                    updated = True
                    # Polite pacing — avoid rate limits
                    await asyncio.sleep(3)

        # Persist state if anything changed
        if updated:
            self.state["last_replied_msg_id"] = last_replied
            self._save_state()

        logger.info("✅ [DM_AUTO] Inbox poll complete.")

    # ── Background Thread ────────────────────────────────────────────────────

    @classmethod
    def start_background_polling(cls, interval_seconds: int = None):
        """
        Launches a daemon thread that polls and auto-responds to DMs in a loop.

        REQUIRES: DM_AUTO_RESPONDER_ENABLED=yes in .env  (DEFAULT: OFF)
        Interval:  IG_DM_POLL_INTERVAL env var (default 300s / 5 minutes)
        """
        if os.getenv("DM_AUTO_RESPONDER_ENABLED", "no").lower() not in ["yes", "true", "on"]:
            logger.info(
                "🚫 [DM_AUTO] DM_AUTO_RESPONDER_ENABLED is not set. "
                "Set it to 'yes' in .env to activate Gemini DM replies."
            )
            return

        if os.getenv("ENABLE_META_UPLOAD", "no").lower() not in ["yes", "true", "on"]:
            logger.info("🚫 [DM_AUTO] Meta upload disabled — skipping auto DM responder.")
            return

        interval = interval_seconds or int(os.getenv("IG_DM_POLL_INTERVAL", 300))

        def _loop():
            logger.info(
                f"🤖 [DM_AUTO] Gemini DM Auto-Responder started. Poll interval: {interval}s"
            )
            while True:
                try:
                    responder = cls()
                    asyncio.run(responder.poll_and_respond())
                except Exception as e:
                    logger.error(f"❌ [DM_AUTO] Polling error: {e}")
                finally:
                    time.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True, name="ig_auto_dm_responder")
        t.start()
        logger.info(
            "✅ [DM_AUTO] Gemini-powered DM auto-responder thread started.\n"
            "   Gemini will reply naturally as you to all incoming Instagram DMs."
        )


# Standalone execution / testing
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO)
    responder = IGAutoDMResponder(test_mode=True)  # test_mode=True = no actual sending
    asyncio.run(responder.poll_and_respond())
