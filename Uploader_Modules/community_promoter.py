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

STATE_FILE       = "The_json/community_promo_state.json"
LOS_POLLOS_FILE  = "The_json/los_pollos_links.json"
GEO_ANALYTICS_FILE = "The_json/geo_analytics.json"

# ── Country → Language mapping ────────────────────────────────────────────────
_COUNTRY_LANG: Dict[str, str] = {
    "India":                 "Hindi",
    "United States":         "English",
    "Pakistan":              "Urdu",
    "Bangladesh":            "Bengali",
    "United Kingdom":        "English",
    "Germany":               "German",
    "United Arab Emirates":  "Arabic",
    "Mexico":                "Spanish",
    "Malaysia":              "Malay",
    "Canada":                "English",
    "Saudi Arabia":          "Arabic",
    "France":                "French",
    "Australia":             "English",
    "Sri Lanka":             "Sinhala",
    "T\u00fcrkiye":           "Turkish",
    "Oman":                  "Arabic",
    "Spain":                 "Spanish",
    "Nepal":                 "Nepali",
    "Indonesia":             "Indonesian",
    "Brazil":                "Portuguese",
    "Italy":                 "Italian",
    "Japan":                 "Japanese",
    "South Korea":           "Korean",
    "Russia":                "Russian",
    "Netherlands":           "Dutch",
    "Sweden":                "Swedish",
    "Qatar":                 "Arabic",
    "Kuwait":                "Arabic",
    "Bahrain":               "Arabic",
    "Egypt":                 "Arabic",
}

class CommunityPromoter:
    """
    Handles 'Community Post' promotion via Channel Comments (commentThreads).
    - Rate Limited (6h)
    - Gemini-generated multilingual hooks (language selection driven by geo_analytics.json)
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

    @staticmethod
    def _get_top_languages(max_langs: int = 3) -> list:
        """
        Reads The_json/geo_analytics.json, maps country → language,
        aggregates viewer % per language, and returns the top `max_langs`
        languages sorted by audience share.

        English is ALWAYS first (widest reach regardless of %).
        The list is deduplicated (e.g. UAE + Saudi + Oman all map to Arabic once).

        Update geo_analytics.json from YouTube Analytics → Geography tab to
        keep the language selection current.
        """
        try:
            with open(GEO_ANALYTICS_FILE, "r", encoding="utf-8") as fh:
                geo = json.load(fh)
            countries = geo.get("countries", [])
        except Exception as _e:
            logger.warning(f"⚠️ [GEO] Could not read geo_analytics.json: {_e}")
            return ["English", "Hindi", "Urdu"]   # safe hard-coded fallback

        # Aggregate % per language
        lang_pct: Dict[str, float] = {}
        for row in countries:
            country = row.get("country", "")
            pct     = float(row.get("pct", 0))
            lang    = _COUNTRY_LANG.get(country, None)
            if not lang:
                continue
            lang_pct[lang] = lang_pct.get(lang, 0.0) + pct

        # Sort descending, deduplicated by language name
        sorted_langs = sorted(lang_pct.items(), key=lambda x: x[1], reverse=True)

        # Always lead with English; then fill remaining slots with top non-English
        result = ["English"]
        for lang, pct in sorted_langs:
            if lang == "English":
                continue
            if lang not in result:
                result.append(lang)
            if len(result) >= max_langs:
                break

        logger.info(f"🌐 [GEO] Selected languages for this post: {result} (from {len(countries)} countries)")
        return result

    def _generate_gemini_hook(self, is_short: bool, fashion_data: Optional[Dict], tg_display: str, actress_name: str = "") -> Optional[str]:
        """Uses Gemini to generate a unique multilingual dual-CTA hook.
        Languages are chosen automatically from geo_analytics.json viewer %.
        Cache is intentionally DISABLED so every post is unique.
        """
        # ── Cache intentionally SKIPPED for uniqueness ────────────────────────
        # (Pipeline cache caused repeated identical posts — removed)

        # ── Gemini Call with uniqueness seed ──────────────────────────────────
        try:
            from Intelligence_Modules.gemini_governor import gemini_router
            if not gemini_router:
                return None

            # Read customisable button labels from .env (fallback to defaults)
            from dotenv import load_dotenv
            import datetime
            load_dotenv("Credentials/.env")
            partner_label = os.getenv("TG_BTN_PARTNER_LABEL", "Find Your Match").replace("🔥 ", "").strip()
            corn_label    = os.getenv("TG_BTN_CORN_LABEL",   "Watch Full Clips").replace("🎬 ", "").strip()

            # Build name anchor — prefer actress name, fallback to neutral
            name_anchor = actress_name.strip() if actress_name else "this creator"

            # ── Uniqueness seed: random style + timestamp so Gemini never repeats ──
            _styles = [
                "mysterious and FOMO-driven",
                "urgent and conspiratorial",
                "casual and direct like a friend tipping you off",
                "excited and slightly disbelieving",
                "dry and factual but intriguing",
                "teasing and playful",
                "short sharp punchy — one-word sentences style",
            ]
            _style_seed = random.choice(_styles)
            _ts = datetime.datetime.now().strftime("%H%M%S")  # injects uniqueness into context

            # ── Language strategy: read from geo analytics ────────────────────
            # _get_top_languages() reads The_json/geo_analytics.json and picks
            # the top languages by real viewer %. Always English first.
            # Current top (from analytics): English, Hindi, Urdu, Bengali, Arabic…
            _langs = CommunityPromoter._get_top_languages(max_langs=3)

            # Build dynamic language block instructions for Gemini
            _lang_blocks = []
            for i, lang in enumerate(_langs, 1):
                _lang_blocks += [
                    f"BLOCK {i} — {lang.upper()} (Style: {_style_seed if i == 1 else 'match tone'}):",
                    f"  Write 2 mini-hooks about {name_anchor}. Each hook = 1 teaser line + its arrow label.",
                    f"  Hook A ends with: → {partner_label}",
                    f"  Hook B ends with: → {corn_label}",
                    f"  Use {name_anchor}'s name. Keep it YouTube-safe.",
                    "",
                ]

            prompt = [
                "SYSTEM ROLE:",
                "You are a growth hacker who specializes in building Telegram groups via YouTube comments.",
                "Your audience is viewers from multiple countries watching actress / celebrity content on YouTube.",
                f"Your goal: write ONE YouTube comment with the hook written in {len(_langs)} LANGUAGES so viewers from all top countries can read it.",
                "The comment MUST NOT contain adult links, explicit words, or gender-specific sexual language.",
                "CRITICAL: Do NOT use words like 'girl', 'she', 'her', 'raw footage', 'censorship', or '🔞'.",
                f"CRITICAL: Use the NAME '{name_anchor}' instead of any gender pronoun in ALL languages.",
                "Use curiosity gaps, name-based exclusivity, and 'members-only hidden content' framing. Keep it fully YouTube-safe.",
                f"WRITING STYLE THIS TIME: {_style_seed}",
                f"SESSION: {_ts}",   # forces Gemini to generate fresh output every call
                "",
                f"TARGET LINK: {tg_display}",
                f"ACTRESS / CREATOR NAME: {name_anchor}",
                f"LANGUAGES TO USE (in this order): {', '.join(_langs)}",
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
                prompt.append(f"- Focus: Exclusive members-only extended cuts of {name_anchor}'s content that aren't publicly available.")

            prompt += [
                "",
                "─────────────────────────────────────────",
                f"OUTPUT STRUCTURE — {len(_langs)} LANGUAGE BLOCKS:",
                "─────────────────────────────────────────",
                "",
            ] + _lang_blocks + [
                "─────────────────────────────────────────",
                "RULES:",
                f"1. Use '{name_anchor}' — never use 'she', 'her', 'girl' in any language.",
                "2. No explicit words, no 🔞, no censorship references. Keep all blocks YouTube-safe.",
                "3. Keep each block punchy: max 4 lines per block.",
                "4. Separate blocks with a single blank line.",
                "5. The VERY LAST LINE of the entire comment must be: 👉 [TARGET LINK]",
                "6. Output ONLY the raw comment text. No block headers, no markdown, no labels.",
                "",
                "EXAMPLE STRUCTURE (tone and format only — write completely differently each time):",
                f"{name_anchor}'s extended cut didn't survive YouTube's filter. 👀",
                f"→ {partner_label}",
                "",
                f"The version they flagged and removed? Still live for members.",
                f"→ {corn_label}",
                "",
                f"{name_anchor} ka full version yahan se hata diya gaya. Group mein available hai.",
                f"→ {partner_label}",
                "",
                f"Jo clip delete hui, wo members ke liye abhi bhi live hai.",
                f"→ {corn_label}",
                "",
                f"{name_anchor} er full clip delete kora hoyeche. Members ra ekhono dekhte parchen.",
                f"→ {partner_label}",
                "",
                f"YouTube theke hatiye dewa video group e achhe. Members only.",
                f"→ {corn_label}",
                f"👉 {tg_display}",
            ]

            res = gemini_router.generate(
                task_type="copywriter",
                prompt="\n".join(prompt),
                module_name="community_promoter",
                metadata={"type": "actress_funnel_comment_multilang", "fashion": bool(fashion_data), "langs": _langs, "seed": _style_seed}

            )

            if res and tg_display in res:
                return res.strip()
            elif res:
                # Gemini returned something but forgot to add the link — append it
                return res.strip() + f"\n👉 {tg_display}"

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

    def _get_template(self, clip_count: int, promo_url: str, is_short: bool = True, custom_text: Optional[str] = None, fashion_data: Optional[Dict] = None, actress_name: str = "") -> str:
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

        # Extract actress name from custom_text (the monetization_cta field = video title)
        # custom_text is typically the video title like "Yesha Sagar: Hot Outfit"
        # We extract the first part before ":" or "-" as the actress name
        _actress = actress_name.strip()
        if not _actress and custom_text:
            _raw = str(custom_text).split(":")[0].split("-")[0].strip()
            # Only use it if it looks like a name (not a long sentence)
            if _raw and len(_raw.split()) <= 4:
                _actress = _raw
        if not _actress:
            _actress = "this creator"

        # 1. Try Gemini Primary Hook
        gemini_hook = self._generate_gemini_hook(is_short, fashion_data, tg_display, actress_name=_actress)
        if gemini_hook:
            logger.info(f"✨ Using Gemini-generated hook for '{_actress}'.")
            return gemini_hook

        # 2. Secondary Fallback Hooks — Actress-Funnel Oriented (YouTube-Safe)
        from dotenv import load_dotenv
        load_dotenv("Credentials/.env")
        partner_label = os.getenv("TG_BTN_PARTNER_LABEL", "🔥 Find Your Match").strip()
        corn_label    = os.getenv("TG_BTN_CORN_LABEL",   "🎬 Watch Full Clips").strip()

        if is_short:
            # Dual-hook fallback pool — name-based, YouTube-safe curiosity hooks
            teasers = [
                # Set 1 — Extended cut + members-only
                f"{_actress}'s extended cut didn't make it past YouTube's filter. 👀\n"
                f"→ {corn_label}\n"
                f"\n"
                f"Members get behind-the-scenes content {_actress} posted before it got taken down.\n"
                f"→ {partner_label}\n"
                f"👉 {tg_display}",

                # Set 2 — Deleted version
                f"YouTube flagged and removed {_actress}'s original upload. The full version is inside.\n"
                f"→ {corn_label}\n"
                f"\n"
                f"Members-only access. Not public anywhere else.\n"
                f"→ {partner_label}\n"
                f"👉 {tg_display}",

                # Set 3 — FOMO / secret cut
                f"This is the edited version. {_actress}'s full cut is inside the group. 🔑\n"
                f"→ {corn_label}\n"
                f"\n"
                f"Group members get exclusive content that doesn't go public.\n"
                f"→ {partner_label}\n"
                f"👉 {tg_display}",

                # Set 4 — Hinglish variant (India audience) — name-based
                f"{_actress} ka full version yahan nahi daal sakte. Group mein available hai. 👀\n"
                f"→ {corn_label}\n"
                f"\n"
                f"Members ko exclusive content milta hai jo publicly nahi hai.\n"
                f"→ {partner_label}\n"
                f"👉 {tg_display}",

                # Set 5 — Platform removed it
                f"The platform keeps removing {_actress}'s best clips. We archive them inside.\n"
                f"→ {corn_label}\n"
                f"\n"
                f"Join to get notified every time new content drops. Members only.\n"
                f"→ {partner_label}\n"
                f"👉 {tg_display}",

                # Set 6 — Behind the scenes
                f"{_actress}'s behind-the-scenes clips never go on the main channel. 🔑\n"
                f"→ {corn_label}\n"
                f"\n"
                f"Every upload — exclusive to group members before it's anywhere else.\n"
                f"→ {partner_label}\n"
                f"👉 {tg_display}",
            ]
            return random.choice(teasers)
        else:
            # Long-form / compilation — name-based, YouTube-safe
            return (
                f"{_actress}'s extended compilation — the version YouTube keeps flagging is inside the group.\n"
                f"→ {corn_label}\n"
                f"\n"
                f"Members get early access to every upload before it goes public. No waiting.\n"
                f"→ {partner_label}\n"
                f"👉 {tg_display}"
            )

    def get_instagram_fanpage_caption(
        self,
        base_caption: str,
        actress_name: str = "",
        affiliate_link: str = None,
        item_name: str = None,
    ) -> str:
        """
        Generates a fan-page style caption for Instagram.

        ── WITH affiliate_link (manual Fashion & Style route) ──────────────────
        Fires a military-grade 3-beat Gemini copywriting call:
          BEAT 1 — Pattern-Interrupt : Disrupt the scroll with a specific visual detail
          BEAT 2 — Identity Gap      : Make the viewer feel the desire
          BEAT 3 — Zero-Friction CTA : Single clear action (Comment LINK → ManyChat DM)
        The affiliate link is NOT pasted in the caption (Instagram removes it). Instead
        it is injected downstream by the ManyChat bot reply.

        ── WITHOUT affiliate_link (General_Fallback or no manual input) ────────
        Falls back to the existing editorial fan-page templates (no Gemini call).
        """
        tg_link = self._get_telegram_link()
        clean_handle = tg_link.replace("https://t.me/", "@") if tg_link else os.getenv("BRAND_NAME", "")
        name_part = actress_name.strip() if actress_name else "her"

        # ── MILITARY-GRADE PATH: Affiliate link present ──────────────────────────
        if affiliate_link:
            try:
                from Intelligence_Modules.gemini_governor import gemini_router
                if gemini_router:
                    _product_hint = item_name or base_caption[:80] or "this exact look"
                    _mg_prompt = "\n".join([
                        "SYSTEM ROLE: You are a military-grade Instagram copywriter.",
                        "Your ONLY mission: Convert scrolling viewers into affiliate buyers via ManyChat.",
                        "Architecture = 3 beats. Zero fluff. Zero filler. Zero hashtags in body.",
                        "",
                        f"ACTRESS: {name_part}",
                        f"PRODUCT: {_product_hint}",
                        "",
                        "BEAT 1 — PATTERN-INTERRUPT (1 sentence, max 12 words):",
                        "  Hyper-specific visual detail that stops the scroll cold.",
                        "  Start with a noun or action. No 'I', no 'we'. No generic openers.",
                        "  Examples: 'That drape placement is doing heavy work.'",
                        "           'The cut was engineered for exactly this body type.'",
                        "",
                        "BEAT 2 — IDENTITY GAP (1–2 sentences, max 20 words):",
                        "  Make the viewer feel the desire or gap. Not the actress, the viewer.",
                        "  Examples: 'You already know this is your style — you\\'ve just been missing the piece.'",
                        "           'The look isn\\'t the outfit. It\\'s the decision to wear it right.'",
                        "",
                        "BEAT 3 — ZERO-FRICTION CLOSE (exactly 1 line):",
                        "  Drive the ManyChat funnel. The bot DMs the link. DO NOT paste the link.",
                        "  Format EXACTLY: Comment \"LINK\" below and I\\'ll send the shop link to your DMs 📩",
                        "  (or variation using 'drop LINK in comments' or 'Reply LINK for the direct link')",
                        "",
                        "RULES:",
                        "1. 3 beats total. No more, no less.",
                        "2. Max 4 lines of body text (before hashtags).",
                        "3. NEVER paste the actual URL in the caption.",
                        "4. End with 5–8 high-reach fashion hashtags on a new line.",
                        "5. Output ONLY raw caption text. No labels. No markdown. No explanations.",
                        "",
                        "OUTPUT FORMAT:",
                        "[Beat 1 sentence]",
                        "",
                        "[Beat 2 sentence(s)]",
                        "",
                        "[Beat 3 CTA]",
                        "",
                        "#hashtag1 #hashtag2 ...",
                    ])
                    _mg_res = gemini_router.generate(
                        task_type="copywriter",
                        prompt=_mg_prompt,
                        module_name="community_promoter",
                        metadata={"type": "ig_affiliate_caption", "actress": name_part}
                    )
                    if _mg_res and len(_mg_res.strip()) > 30:
                        logger.info("💪 [MG_COPY] Military-grade affiliate caption generated for @%s", name_part)
                        return _mg_res.strip()
            except Exception as _mg_e:
                logger.warning("⚠️ [MG_COPY] Military-grade caption failed (falling back): %s", _mg_e)

        # ── FALLBACK PATH: No affiliate link — editorial fan-page templates ────────
        captions = [
            # Elite Editorial tone
            f"Analyzing {name_part}'s Archive 📂\n\n"
            f"The silhouette, the architectural weight, and the silent cues — "
            f"this is how high-tier selection looks in motion.\n\n"
            f"Access the Full Dossier \u2192 Telegram (link in bio) {clean_handle}\n"
            f"#elitefashion #fashionintelligence #bollywood #dossier #reels",

            # Insider Specialist tone
            f"Selection Status: Verified. 🔐\n\n"
            f"{name_part} has mastered the pattern — "
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
            f"The details in {name_part}'s choice here are intentional — "
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
            # Extract actress name from custom_text (typically the video title e.g. "Yesha Sagar")
            _actress_name = ""
            if custom_text:
                _raw_name = str(custom_text).split(":")[0].split("-")[0].strip()
                if _raw_name and len(_raw_name.split()) <= 4:
                    _actress_name = _raw_name
            text = self._get_template(clip_count, promo_link, is_short=is_short, custom_text=custom_text, fashion_data=fashion_data, actress_name=_actress_name)
            
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
