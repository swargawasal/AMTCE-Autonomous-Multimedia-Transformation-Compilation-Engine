import os
import json
import time
import hashlib
import random
import logging
import asyncio
from typing import Optional, Dict
from datetime import datetime

logger = logging.getLogger("community_promoter")
logger.setLevel(logging.INFO)

STATE_FILE = "The_json/community_promo_state.json"
LOS_POLLOS_FILE = "The_json/los_pollos_links.json"

class CommunityPromoter:
    """
    Handles 'Community Post' promotion via Channel Comments (commentThreads).
    - Rate Limited (6h)
    - Deterministic Content (No Gemini)
    - Silent Failures
    """
    
    def __init__(self):
        self.state = self._load_state()
        
    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # Migration: Single URL -> Pool
                    if "last_compilation_url" in data and "promo_pool" not in data:
                        data["promo_pool"] = [
                            {"url": data["last_compilation_url"], "ts": data.get("last_compilation_time", time.time())}
                        ]
                        
                    return data
            except Exception:
                pass
        return {"last_run": 0, "posted_hashes": [], "promo_pool": [], "last_rotation_idx": -1}

    def _save_state(self):
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.state, f)
        except Exception as e:
            logger.error(f"❌ Failed to save promoter state: {e}")

    def _get_telegram_link(self) -> str:
        """Reads the Telegram link from config or .env."""
        try:
            from dotenv import load_dotenv
            load_dotenv("Credentials/.env")
            env_link = os.getenv("TELEGRAM_GROUP_ID")
            if env_link:
                return env_link.strip()
                
            with open("Credentials/telegram_config.json", "r") as f:
                data = json.load(f)
                return data.get("telegram_link", "")
        except:
            return ""

    def _generate_gemini_hook(self, is_short: bool, fashion_data: Optional[Dict], tg_display: str) -> Optional[str]:
        """Uses Gemini to generate a dual-CTA psychological hook (Partner + Clips)."""
        # ── Tier 0: Try to read from the pipeline cache first ─────────────────
        try:
            from Text_Modules.smart_price_tag import _read_from_pipeline_cache
            cached = _read_from_pipeline_cache("community_comment_hook")
            if cached and tg_display:
                if tg_display not in cached:
                    cached = cached.rstrip() + f"\n👉 {tg_display}"
                logger.info("✨ Using cached master community hook — no extra Gemini call.")
                return cached
        except Exception as _e:
            logger.debug(f"[HOOK_CACHE] Failed to read community hook from cache: {_e}")

        # ── Tier 1: Fallback Gemini Call ───────────────────────────────────────
        try:
            from Intelligence_Modules.gemini_governor import gemini_router
            if not gemini_router:
                return None

            # Read customisable button labels from .env (fallback to defaults)
            from dotenv import load_dotenv
            load_dotenv("Credentials/.env")
            partner_label = os.getenv("TG_BTN_PARTNER_LABEL", "Find Your Match").replace("🔥 ", "").strip()
            corn_label    = os.getenv("TG_BTN_CORN_LABEL",   "Watch Full Clips").replace("🎬 ", "").strip()

            prompt = [
                "SYSTEM ROLE:",
                "You are a growth hacker who specializes in building Telegram groups via YouTube comments.",
                "Your audience is mostly male viewers who are watching actress / celebrity content on YouTube.",
                "Your goal: write a YouTube comment that makes them WANT to join the Telegram group.",
                "The comment MUST NOT contain adult links directly. It ONLY points to the Telegram link provided.",
                "Use curiosity gaps, exclusivity, and 'members-only hidden content' framing. Keep it YouTube-safe.",
                "",
                f"TARGET LINK: {tg_display}",
                "",
                "CONTEXT:",
            ]

            if fashion_data:
                item  = fashion_data.get("outfit_type", "this exact look")
                brand = fashion_data.get("designer_or_brand", "the designer")
                prompt.append(f"- Fashion Item: {item}")
                prompt.append(f"- Designer/Brand: {brand}")
                if "educational_fact" in fashion_data:
                    prompt.append(f"- Secret Detail: {fashion_data['educational_fact']}")
            else:
                prompt.append("- Focus: Exclusive hidden actress content and rare unfiltered clips only inside the group.")

            prompt += [
                "",
                "STRATEGY: Write a comment with EXACTLY TWO mini-hooks back-to-back:",
                f"  HOOK 1 — 'Partner Hook' (label: '{partner_label}'):",
                "           Tease finding someone exactly like this actress / meeting someone this attractive.",
                "           Use phrases like: 'meet someone exactly like her', 'your type is waiting',",
                "           'she has a twin in the group', 'the kind of girl you've been looking for'.",
                f"           End hook 1 with the arrow line: '→ {partner_label}'",
                "",
                f"  HOOK 2 — 'Content Hook' (label: '{corn_label}'):",
                "           Tease exclusive uncut actress clips — stuff YouTube removes / won't show.",
                "           Use phrases like: 'the full version they deleted', 'what YouTube hides from you',",
                "           'raw footage only group members can see', 'the clip they reported and removed'.",
                f"           End hook 2 with the arrow line: '→ {corn_label}'",
                "",
                "RULES:",
                "1. Keep total comment " + ("short & punchy (4-6 lines)." if is_short else "engaging (5-7 lines)."),
                "2. Each hook = 1 teaser sentence + its '→ Label' line.",
                "3. Separate the two hooks with a blank line.",
                "4. MUST end with a standalone line: 👉 [TARGET LINK]",
                "5. Use max 1-2 emojis total. No hashtags. No explicit words.",
                "6. Output ONLY the raw comment text. No labels, no markdown.",
                "",
                "EXAMPLE OUTPUT:",
                "She has a twin in the group who's looking for exactly your type. 👀",
                f"→ {partner_label}",
                "",
                "The full version they took down is still live inside. YouTube can't touch it.",
                f"→ {corn_label}",
                f"👉 {tg_display}",
            ]

            res = gemini_router.generate(
                task_type="copywriter",
                prompt="\n".join(prompt),
                module_name="community_promoter",
                metadata={"type": "actress_funnel_comment", "fashion": bool(fashion_data)}
            )

            if res and tg_display in res:
                return res.strip()

        except Exception as e:
            logger.warning(f"⚠️ Gemini hook generation failed: {e}")

        return None

    def _generate_telegram_group_hook(self, partner_label: str, corn_label: str, fashion_data: Optional[Dict] = None) -> Optional[str]:
        """Uses Gemini to generate a dual-CTA psychological hook specifically for the Telegram group post."""
        try:
            from Intelligence_Modules.gemini_governor import gemini_router
            if not gemini_router:
                return None

            prompt = [
                "SYSTEM ROLE:",
                "You are a ruthless, top-tier affiliate marketer and copywriter whose SOLE PURPOSE is to maximize CTR (Click-Through Rate) and skyrocket business profits.",
                "You are writing a caption for a video posted directly in a Telegram group. The audience consists of highly visual male viewers looking for exclusive celebrity/actress content.",
                "Your goal is to relentlessly squeeze every single viewer into tapping the two CTA buttons below the video. You must use dark psychology, extreme curiosity gaps, FOMO, and raw desire to guarantee they click.",
                "",
                "CONTEXT:",
            ]

            if fashion_data:
                item  = fashion_data.get("outfit_type", "this exact look")
                brand = fashion_data.get("designer_or_brand", "the designer")
                prompt.append(f"- Visual Anchor: {item}")
                if "educational_fact" in fashion_data:
                    prompt.append(f"- Secret Detail: {fashion_data['educational_fact']}")
            else:
                prompt.append("- Focus: Extremely rare, unfiltered, and 'banned' actress content.")

            prompt += [
                "",
                "STRATEGY: Write a devastatingly effective caption with EXACTLY TWO bullet points (mini-hooks).",
                f"  HOOK 1 — 'Partner Hook' (Button Label: '{partner_label}'):",
                "           Exploit the viewer's loneliness and desire. Tell them a girl who looks EXACTLY like the one in the video is waiting for them right now. Frame it as an immediate, effortless reality.",
                f"           End hook 1 with the arrow line: '➡️ {partner_label}'",
                "",
                f"  HOOK 2 — 'Content Hook' (Button Label: '{corn_label}'):",
                "           Exploit the forbidden/taboo angle. Tease that this video is just the censored preview, and the 'uncut', 'deleted', or 'too hot for mainstream' version is hiding behind this button.",
                f"           End hook 2 with the arrow line: '➡️ {corn_label}'",
                "",
                "RULES:",
                "1. Keep the total caption ruthlessly punchy (3-4 lines maximum). Short sentences only.",
                "2. Each hook = 1 visceral, high-converting teaser sentence + its '➡️ Label' line.",
                "3. Separate the two hooks with a single blank line.",
                "4. Use exactly 1 or 2 emojis to draw the eye, no more. No hashtags. Keep it clean enough to avoid auto-bans, but suggestive enough to guarantee clicks.",
                "5. Output ONLY the raw caption text. No markdown, no labels, no meta-text. Do NOT include actual links (buttons are injected automatically).",
                "",
                "EXAMPLE OUTPUT (Tone and Structure):",
                "🔹 The exact girl from this video is bored and waiting to talk to you tonight.",
                f"➡️ {partner_label}",
                "",
                "🔹 The platform banned the rest of this clip. Watch the full uncut footage here before it's gone.",
                f"➡️ {corn_label}",
            ]

            res = gemini_router.generate(
                task_type="copywriter",
                prompt="\n".join(prompt),
                module_name="community_promoter",
                metadata={"type": "telegram_group_post_hook", "fashion": bool(fashion_data)}
            )

            if res:
                return res.strip()

        except Exception as e:
            logger.warning(f"⚠️ Gemini TG group hook generation failed: {e}")

        return None

    def _get_template(self, clip_count: int, promo_url: str, is_short: bool = True, custom_text: Optional[str] = None, fashion_data: Optional[Dict] = None) -> str:
        """
        YouTube Comment Strategy: ONLY drive Telegram GROUP JOINS.
        - No CPA links in YouTube comments (protects channel).
        - CPA links (Los Pollos) are posted inside Telegram group automatically.
        - Goal: maximise curiosity gap so actress fans tap the Telegram link.
        - Audience: mostly male voyeurs / actress fans who want to see more.
        """
        tg_link = self._get_telegram_link()
        clean_handle = tg_link.replace("https://t.me/", "@") if tg_link else os.getenv("BRAND_NAME", "")
        tg_display = tg_link if tg_link else clean_handle

        # 1. Try Gemini Primary Hook
        gemini_hook = self._generate_gemini_hook(is_short, fashion_data, tg_display)
        if gemini_hook:
            logger.info("✨ Using Gemini-generated actress funnel hook.")
            return gemini_hook

        # 2. Secondary Fallback Hooks — Actress-Funnel Oriented (YouTube-Safe)
        from dotenv import load_dotenv
        load_dotenv("Credentials/.env")
        partner_label = os.getenv("TG_BTN_PARTNER_LABEL", "🔥 Find Your Match").strip()
        corn_label    = os.getenv("TG_BTN_CORN_LABEL",   "🎬 Watch Full Clips").strip()

        if is_short:
            # Dual-hook fallback pool — actress fan funnel + exclusive content hooks
            teasers = [
                # Set 1 — Twin / look-alike + deleted clip
                f"She has a twin in the group who's looking for exactly your type. 👀\n"
                f"→ {partner_label}\n"
                f"\n"
                f"The full version they reported and deleted is still up inside.\n"
                f"→ {corn_label}\n"
                f"👉 {tg_display}",

                # Set 2 — Find her type + what YouTube hides
                f"If she's your type, someone exactly like her is already in the group.\n"
                f"→ {partner_label}\n"
                f"\n"
                f"YouTube took down the original. The uncut version is inside. Members only.\n"
                f"→ {corn_label}\n"
                f"👉 {tg_display}",

                # Set 3 — Real connection + raw footage
                f"The kind of girl you've been scrolling for — she's already waiting inside.\n"
                f"→ {partner_label}\n"
                f"\n"
                f"Raw footage they can't post here. No censorship inside the group 🔞\n"
                f"→ {corn_label}\n"
                f"👉 {tg_display}",

                # Set 4 — Hinglish variant (India audience)
                f"Iska jaisi hi ladki group mein hai — seriously, apna wala type dhundh.\n"
                f"→ {partner_label}\n"
                f"\n"
                f"Jo yahan nahi dikhate, wo sab andar hai. Members only 🔞\n"
                f"→ {corn_label}\n"
                f"👉 {tg_display}",

                # Set 5 — Exclusive members club framing
                f"The group has girls exactly like her looking for someone. I'm not kidding.\n"
                f"→ {partner_label}\n"
                f"\n"
                f"Full uncut clips. The ones the platform keeps removing. All inside.\n"
                f"→ {corn_label}\n"
                f"👉 {tg_display}",

                # Set 6 — Secret content + type match
                f"They keep deleting her best clips here. We saved them. Join to see.\n"
                f"→ {corn_label}\n"
                f"\n"
                f"Also — someone exactly your type is already in the group. Not clickbait.\n"
                f"→ {partner_label}\n"
                f"👉 {tg_display}",
            ]
            return random.choice(teasers)
        else:
            # Long-form / compilation — actress fan dual hook
            return (
                f"If she's exactly your type, someone like her is already inside this group.\n"
                f"→ {partner_label}\n"
                f"\n"
                f"And the uncut compilations they keep removing from YouTube? All inside. No censorship.\n"
                f"→ {corn_label}\n"
                f"👉 {tg_display}"
            )

    def get_instagram_fanpage_caption(self, base_caption: str, actress_name: str = "") -> str:
        """
        Generates a fan-page style caption for Instagram.
        Tone: fan community, not editorial. Drives Telegram joins via bio link.
        """
        tg_link = self._get_telegram_link()
        clean_handle = tg_link.replace("https://t.me/", "@") if tg_link else os.getenv("BRAND_NAME", "")

        name_part = actress_name.strip() if actress_name else "her"

        captions = [
            # Elite Editorial tone
            f"Analyzing {name_part}'s Archive 📂\n\n"
            f"The silhouette, the architectural weight, and the silent cues \u2014 "
            f"this is how high-tier selection looks in motion.\n\n"
            f"Access the Full Dossier \u2192 Telegram (link in bio) {clean_handle}\n"
            f"#elitefashion #fashionintelligence #bollywood #dossier #reels",

            # Insider Specialist tone
            f"Selection Status: Verified. 🔐\n\n"
            f"{name_part} has mastered the pattern \u2014 "
            f"cut, proportion, and texture aligned with precision. "
            f"This is the blueprint for 2026.\n\n"
            f"📌 Elite Archive \u2192 Link in bio\n"
            f"#fashionblueprint #styleinsider #bollywood #archive #reels",

            # Trend Prediction tone
            f"The {name_part} Trajectory 📊\n\n"
            f"{base_caption}\n\n"
            f"This look is the catalyst for next season. Full Dossier on Telegram \u2192 link in bio {clean_handle}\n"
            f"#trajectory #fashionintelligence #bollywood #styleanalysis #reels",

            # The Secret/Mystery tone
            f"Intelligence Note 📝\n\n"
            f"The details in {name_part}'s choice here are intentional \u2014 "
            f"there's a reason for every seam. Full 'Secret' breakdown on Telegram.\n\n"
            f"🔗 Access the Vault \u2192 {clean_handle}\n"
            f"#insidernotes #fashionintelligence #bollywood #secretvault #outfitanalysis",
        ]
        return random.choice(captions)

    def _get_next_los_pollos_link(self) -> Optional[str]:
        """
        Loads links from los_pollos_links.json and rotates through them.
        """
        try:
            if not os.path.exists(LOS_POLLOS_FILE):
                return None
                
            with open(LOS_POLLOS_FILE, "r", encoding="utf-8") as f:
                links = json.load(f)
                
            if not links or not isinstance(links, list):
                return None
                
            idx = self.state.get("last_rotation_idx", -1)
            next_idx = (idx + 1) % len(links)
            
            self.state["last_rotation_idx"] = next_idx
            self._save_state()
            
            return links[next_idx]
        except Exception as e:
            logger.error(f"❌ Failed to rotate Los Pollos links: {e}")
            return None

    def register_compilation_url(self, url: str):
        """
        Adds compilation URL to the rotating pool (Max 10).
        """
        pool = self.state.get("promo_pool", [])
        
        # Deduplicate
        pool = [x for x in pool if x["url"] != url]
        
        # Add new
        pool.append({"url": url, "ts": time.time()})
        
        # Cap size (Keep recent 10)
        if len(pool) > 10:
            pool = pool[-10:]
            
        self.state["promo_pool"] = pool
        self._save_state()
        logger.info(f"💾 Registered Compilation URL to Pool (Total: {len(pool)}): {url}")

    def _get_rotation_url(self) -> Optional[str]:
        """
        Picks a URL from the pool (Random Rotation).
        """
        pool = self.state.get("promo_pool", [])
        if not pool:
            return None
        return random.choice(pool)["url"]

    def _can_run(self, content_hash: str) -> bool:
        """
        Checks rate limit (1m) and duplication.
        """
        now = time.time()
        
        # 1. Rate Limit (1 Minute Safe Guard)
        last_run = self.state.get("last_run", 0)
        
        if now - last_run < 60: 
            logger.info(f"⏳ Community Promotion skipped (Rate Limit: {int(60 - (now-last_run))}s remaining)")
            return False
            
        # 2. Duplicate Guard
        if content_hash in self.state.get("posted_hashes", []):
            logger.info("♻️ Community Promotion skipped (Duplicate content)")
            return False
            
        return True

    def _register_success(self, content_hash: str):
        self.state["last_run"] = time.time()
        
        # Keep hash history manageable (last 50)
        hashes = self.state.get("posted_hashes", [])
        hashes.append(content_hash)
        if len(hashes) > 50:
            hashes = hashes[-50:]
        self.state["posted_hashes"] = hashes
        
        self._save_state()

    async def promote_on_short_async(self, service, short_video_url: str, is_short: bool = True, delay_seconds: int = 20, custom_text: Optional[str] = None, fashion_data: Optional[Dict] = None):
        """
        Promotes a ROTATING Compilation on the provided Video (Short or Long).
        """
        comp_url = self._get_rotation_url()
        
        if not comp_url:
            logger.warning("⚠️ No Compilation URL in Pool. Skipping promotion.")
            return

        logger.info(f"⏲️ Scheduling Community Promotion in {delay_seconds}s (Link: {short_video_url})...")
        await asyncio.sleep(delay_seconds)
        
        # We need to run the blocking API call in a thread
        clip_count = 10 
        await asyncio.to_thread(self._promote_sync, service, short_video_url, comp_url, clip_count, is_short, custom_text, fashion_data)

    def _extract_video_id(self, url: str) -> Optional[str]:
        try:
            if "youtu.be" in url:
                return url.split("/")[-1].split("?")[0]
            if "v=" in url:
                return url.split("v=")[-1].split("&")[0]
            if "shorts" in url:
                 return url.split("shorts/")[-1].split("?")[0]
        except:
            pass
        return None

    def _promote_sync(self, service, target_video_url: str, promo_link: str, clip_count: int, is_short: bool = True, custom_text: Optional[str] = None, fashion_data: Optional[Dict] = None):
        try:
            # 1. Extract Video ID (Target Short) - Required for Unique Hashing
            video_id = self._extract_video_id(target_video_url)
            if not video_id:
                logger.warning(f"⚠️ Could not extract Video ID from {target_video_url}. Skipping.")
                return

            # 2. Generate Content
            text = self._get_template(clip_count, promo_link, is_short=is_short, custom_text=custom_text, fashion_data=fashion_data)
            
            # UNIQUE HASH: Include video_id so we can post the same text on DIFFERENT videos
            content_hash = hashlib.md5(f"{video_id}:{text}".encode()).hexdigest()
            
            # 3. Guard Checks
            if not self._can_run(content_hash):
                return

            # 4. Get Channel ID (Required for commentThreads)
            try:
                channels_response = service.channels().list(mine=True, part="id").execute()
                if not channels_response.get("items"):
                    logger.warning("⚠️ Could not resolve Channel ID. Skipping.")
                    return
                channel_id = channels_response["items"][0]["id"]
            except Exception as e:
                 logger.warning(f"⚠️ Channel ID fetch failed: {e}")
                 return

            # 4. Execute API Call (Best Effort)
            # Posting a TOP LEVEL COMMENT on the TARGET VIDEO
            body = {
                "snippet": {
                    "channelId": channel_id,
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": text
                        }
                    }
                }
            }
            
            service.commentThreads().insert(
                part="snippet",
                body=body
            ).execute()
            
            # 5. Success
            logger.info(f"📣 Community Promotion Posted on Short ({video_id}) -> Linking to Compilation!")
            self._register_success(content_hash)
            
        except Exception as e:
            # SILENT FAILURE
            logger.warning(f"ℹ️ Community Promotion skipped: {e}")

# Global Instance
promoter = CommunityPromoter()

if __name__ == "__main__":
    # Manual Test Mode
    logging.basicConfig(level=logging.INFO)
    print("📢 Community Promoter Manual Mode")
    
    try:
        from Uploader_Modules.uploader import get_authenticated_service
        service = get_authenticated_service()
        if not service:
            print("❌ Auth failed.")
            exit(1)
            
        url = input("Enter Video URL: ").strip()
        count = int(input("Enter Clip Count: ").strip())
        
        print("🚀 Promoting...")
        promoter._promote_sync(service, url, count)
        
    except ImportError:
        import traceback
        traceback.print_exc()
        print("❌ Could not import 'uploader.get_authenticated_service'. Check traceback above.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error: {e}")
