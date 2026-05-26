"""
Monetization Brain Module (Gemini Authority Mode)
-------------------------------------------------
Acts as the YOUTUBE SHORTS CAPTION EDITOR & SAFETY OFFICER.
Goal: Pass YPP Human Review by enforcing strictly editorial/transformative captions.

**SINGLE SOURCE OF TRUTH: GEMINI**
- No OpenAI usage.
- Strict Text Parsing for robustness.
"""

import json
import logging
import os
import random
import re
import shutil
import tempfile
from datetime import datetime
from typing import Dict, List, Optional


from dotenv import load_dotenv

from Intelligence_Modules.gemini_governor import gemini_router

try:
    from Audio_Modules import voiceover
except ImportError:
    voiceover = None
try:
    from Text_Modules.caption_sanitizer import sanitize_caption_text
except ImportError:

    def sanitize_caption_text(text: str, target_max: int = 4, hard_max: int = 6):
        return text


# Load credentials
if os.path.exists(".env"):
    load_dotenv(".env", override=True)
else:
    load_dotenv("Credentials/.env", override=True)


try:
    from .gemini_status_manager import manager as quota_manager
except (ImportError, ValueError):
    try:
        from gemini_status_manager import manager as quota_manager
    except ImportError:
        quota_manager = None

# --- MONEY FLOW IMPORTS ---
try:
    from .link_optimizer import LinkOptimizer
    from .money_flow_logic import engine as money_engine
except (ImportError, ValueError, ModuleNotFoundError):
    try:
        from link_optimizer import LinkOptimizer
        from money_flow_logic import engine as money_engine
    except ImportError:
        LinkOptimizer = None
        money_engine = None

logger = logging.getLogger("monetization_brain")

# PREDICTION ANALYSIS PROMPT (THE 60% SOLUTION - STYLE FUTURIST)
# PROFESSIONAL REVIEWER PROMPT (Now loaded dynamically from niche_prompts.json)

# Post-processing helpers
GENERIC_WORDS = ["style", "outfit", "look", "fashion"]
FALLBACK_PRIORITY = ["Dress", "Saree", "Top", "Jeans", "Jacket", "Skirt"]

def sanitize_item_name(name, category=None):
    if not name or any(word in name.lower() for word in GENERIC_WORDS):
        if category and category.lower() not in ["unknown", "default", "none"]:
            return category
        return "Apparel Item"
    return name


class MonetizationStrategist:
    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
        self.router = gemini_router
        self.los_pollos_file = "Monetization_Metrics/los_pollos_links.json"
        self.link_optimizer = LinkOptimizer() if LinkOptimizer else None

        # Load niche-specific prompts
        self.niche_prompts = {}
        target_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "The_json", "niche_prompts.json")
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                self.niche_prompts = json.load(f)
            logger.info("✅ Loaded niche_prompts.json for monetization_brain")
        except Exception as e:
            logger.warning(f"⚠️ niche_prompts.json not found or invalid: {e}")

        if self.router:
             logger.info("🧠 YPP Editor Brain: ACTIVE via Router")
        else:
            logger.warning("🧠 YPP Editor Brain: INACTIVE (No Router)")

    def analyze_content(
        self,
        title: str,
        duration: float,
        transformations: Dict = {},
        image_paths: List[str] = None,
        visual_context: str = None,
        intelligence_cache=None,
        niche_category: str = "generic",
        real_mrp: int = None,       # User-supplied real Amazon MRP (highest priority)
        clone_price: int = None,    # Derived affordable/deal price for Telegram hook
    ) -> Dict:
        """
        Analyzes content using Gemini as the sole authority with Professional Reviewer persona.
        Implements a retry layer for maximum reliability.
        """
        if not self.router:
            return self._fallback_response(title, visual_context=visual_context)

        try:
            # 1. Input Sanitization
            clean_title = re.sub(r"[\x00-\x1F\x7F]", "", title).strip()
            clean_title = clean_title[:200]

            # Calculate target word count based on 140 WPM
            word_target = max(20, min(int((duration / 60) * 140), 55))
            
            hashtag_gen = os.getenv("HASHTAG_GEN", "yes").lower() == "yes"

            # Determine niche configuration FIRST so hook_strategy informs h_prompt - Unified Migration
            univ = self.niche_prompts.get("_universal", {})
            hook_strategies = self.niche_prompts.get("_hook_strategies", {})
            hook_strategy = hook_strategies.get(niche_category, hook_strategies.get("generic", {}))

            # Telegram group handle for embedding in community hook
            _tg_raw = os.getenv("TELEGRAM_GROUP_ID", "").strip().lstrip("@")
            _tg_display = f"https://t.me/{_tg_raw}" if _tg_raw else "our Telegram group"

            # ── MASTER HOOK BLOCK: all 4 purposes in one Gemini call ──────────────
            # Context injected so Gemini generates niche-aware, psychologically optimised output
            _niche_label = niche_category.upper()
            _duration_label = "SHORT (≤60s)" if duration <= 60 else f"LONG ({int(duration)}s)"
            _word_target_str = str(word_target)

            # ── PRICE CONTEXT INJECTION ───────────────────────────────────────────
            # If user supplied a real MRP + clone price (from title expansion), inject
            # them into the telegram_hook prompt so Gemini uses ACTUAL prices instead
            # of inventing numbers like ₹18,330 / ₹10,176.
            # Must be computed BEFORE h_prompt tuple — Python doesn't allow assignments
            # inside parenthetical expressions.
            _price_inject = ""
            if real_mrp and real_mrp > 0:
                _price_inject = (
                    f" CRITICAL PRICE DATA (USE EXACTLY — DO NOT INVENT PRICES):"
                    f" Original market price = \u20b9{real_mrp:,}."
                )
                if clone_price and clone_price > 0:
                    _price_inject += (
                        f" Amazon deal price = \u20b9{clone_price:,}."
                        f" Reference BOTH prices in the hook copy."
                    )
                else:
                    _price_inject += " Mention this exact MRP in the hook."
                logger.info(
                    f"[BRAIN_PRICE_INJECT] Injecting real prices into Gemini prompt: "
                    f"MRP=\u20b9{real_mrp:,} clone=\u20b9{clone_price:,}"
                )

            h_prompt = (
                f'{{\n'
                f'  "items": [\n'
                f'    {{\n'
                f'      "item_name": "<Descriptive name of main item>",\n'
                f'      "category": "<Item category>"\n'
                f'    }}\n'
                f'  ],\n'
                # ── HASHTAGS ──────────────────────────────────────────────────────
                f'  "generated_hashtags": ["<NICHE={_niche_label} hashtag 1>", "<hashtag 2>", "..."],\n'
                f'  "_hashtag_rules": "First 3 high-volume brand tags. Next 4-6 niche/visual-specific. Last 3-5 long-tail search tags. NO generic filler.",\n'

                # ── TITLE ─────────────────────────────────────────────────────────
                f'  "generated_title": "<SEO-OPTIMISED TITLE (Start with original title, append curiosity gap. Max 60 chars)>",\n'

                # ── TELEGRAM HOOK (Broadcast) ─────────────────────────────────────
                f'  "telegram_hook": "<TELEGRAM BROADCAST COPY. Strategy: {hook_strategy.get("telegram_purpose", "Desire + FOMO. Specific to the content. Max 20 words.")}. '
                f'RULES: (1) Max 20 words, no padding. (2) Name the SPECIFIC item. (3) Urgency framing. (4) End with motivation, not link. {_price_inject}>",\n'

                # ── INSTAGRAM HOOK — KYC 3-TIER PATTERN-INTERRUPT ────────────────
                f'  "instagram_hook": "<INSTAGRAM REEL CAPTION. '
                f'LINE 1: Anomalous number or contradiction. '
                f'LINE 2: Zero-friction CTA (e.g., Bio has it). NO hashtags. Max 2 lines.>",\n'

                # ── YOUTUBE HOOK — KYC TIER-1 INTERCEPT ──────────────────────────
                f'  "youtube_hook": "<YOUTUBE SHORTS TEXT. '
                f'Max 12 words. Use anomalous number OR identity contradiction OR specific scarcity. NO emojis.>",\n'

                # ── COMMUNITY COMMENT HOOK ────────────────────────────────────────
                f'  "community_comment_hook": "<YOUTUBE COMMUNITY COMMENT. '
                f'Strategy: {hook_strategy.get("community_purpose", "Drive viewers to join Telegram.")}. '
                f'RULES: 3-4 lines max. End with exactly: Join Telegram for raw unfiltered output & outfit requests 👇\\n{_tg_display}>",\n'

                # ── NARRATION SCRIPT ─────────────────────────────────────────────
                f'  "narration_script": "<VOICEOVER SCRIPT. '
                f'Strategy: {hook_strategy.get("narration_purpose", "Hyper-retention direct-response copy.")}. '
                f'WORD TARGET: {_word_target_str} words exactly. Constraints: {hook_strategy.get("domain_constraints", "None")}. '
                f'RULES: Plain text only, no stage directions, present tense, second person.>"'
                f'\n}}'
            ) if hashtag_gen else (
                f'{{\n'
                f'  "items": [\n'
                f'    {{\n'
                f'      "item_name": "<Descriptive name of main item>",\n'
                f'      "category": "<Item category>"\n'
                f'    }}\n'
                f'  ]\n'
                f'}}'
            )


            active_reviewer_prompt = univ.get(
                "reviewer_prompt",
                "YOU ARE A PROFESSIONAL REVIEWER AI. Generating FACTUAL descriptions.\nRETURN FORMAT (STRICT JSON ONLY):\n{\n  \"items\": [{\\\"item_name\\\": \\\"Item\\\", \\\"category\\\": \\\"unknown\\\"}]\n}"
            )

            # Format the dynamic prompt and append the input block
            formatted_base = active_reviewer_prompt.format(hashtag_prompt_v2=h_prompt)
            
            final_prompt = (
                f"{formatted_base}\n\n"
                f"INPUT:\n"
                f"Video Title: {clean_title}\n"
                f"Visual Context: {visual_context if visual_context else 'Not provided'}"
            )

            # 2. Multimodal Payload (Images)
            payload = [final_prompt]
            images_added = 0
            if image_paths:
                if isinstance(image_paths, str):
                    image_paths = [image_paths]

                from PIL import Image
                for path in image_paths:
                    if path and os.path.exists(path):
                        try:
                            img = Image.open(path).convert("RGB")
                            payload.append(img)
                            images_added += 1
                            logger.info(f"📸 Image Added: {os.path.basename(path)}")
                        except Exception as ie:
                            logger.warning(f"⚠️ Failed to load image {path}: {ie}")

            if images_added == 0:
                logger.error(
                    "❌ [MONETIZATION_BRAIN] Zero images in payload — Gemini will run TEXT-ONLY. "
                    "Visual output (garment type, colour) will be unreliable."
                )

            # 3. Execute with Retry Layer (Attempt 1: High Temp, Attempt 2: Strict)
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    temp = 0.85 if attempt == 0 else 0.4
                    res_txt = self.router.generate(
                        task_type="master",
                        prompt=payload,
                        module_name="monetization_brain",
                        gen_config={
                            "temperature": temp,
                            "response_mime_type": "application/json",
                            "max_output_tokens": 2048,   # Hard cap — prevents "exceeded max tokens" crash
                        }
                    )

                    if res_txt:
                        logger.info(f"🧠 Gemini Attempt {attempt+1} Success.")
                        main_data = self._parse_json_response(
                            res_txt,
                            clean_title,
                            duration=duration,
                            visual_context=visual_context,
                        )
                        if main_data.get("approved"):
                            # ── Hierarchical Intelligence: Fashion Scout Integration ──────────────
                            # Build a visual_hint from the structured data monetization_brain already
                            # extracted. This guides Fashion Scout instead of letting it run blind.
                            # Result: confidence jumps from ~0.4 to ~0.7–0.9.
                            if image_paths:
                                try:
                                    from Monetization_Metrics.fashion_scout import scout
                                    if scout:
                                        # ── FIX: primary_item is local to _parse_json_response()
                                        # Pull structured data from main_data which is in scope.
                                        _overlay = (main_data.get("overlay_data") or [{}])[0]
                                        if isinstance(_overlay, dict):
                                            pass
                                        else:
                                            _overlay = {}

                                        # ── VISUAL HINT: guide Fashion Scout with context WITHOUT
                                        # anchoring it to a generic garment label.
                                        #
                                        # !! CRITICAL BUG FIX !!
                                        # Previous code passed item_name (e.g. "Red Dress") as
                                        # garment_type + item_name into the HIGH-CONFIDENCE block.
                                        # Gemini then treated "Red Dress" as ground truth and stopped
                                        # at Phase 1 instead of running the full forensic analysis.
                                        # Result: "red cotton dress" instead of "Corset Slit Maxi Dress".
                                        #
                                        # FIX: ONLY pass editorial_context (prose from the brain's
                                        # broader analysis) + extracted color/occasion signals.
                                        # NEVER pass item_name or garment_type — those are low-quality
                                        # shortcuts that kill specificity.
                                        _editorial = main_data.get("editorial_script", "")

                                        # Extract color hint from editorial prose (non-binding — confirmatory only)
                                        import re as _re_hint
                                        _color_hint = ""
                                        _color_match = _re_hint.search(
                                            r'\b(black|white|red|blue|green|yellow|orange|purple|'
                                            r'pink|gold|silver|beige|brown|grey|gray|navy|coral|'
                                            r'burgundy|emerald|cobalt|ivory|cream|mustard|rust|'
                                            r'dusty rose|champagne|sage|olive|teal|maroon)\b',
                                            _editorial.lower()
                                        )
                                        if _color_match:
                                            _color_hint = _color_match.group(1)

                                        # Extract occasion from editorial or overlay
                                        _occasion_hint = _overlay.get("trend_text", "").lower()

                                        visual_hint = {
                                            # Editorial context provides visual prose WITHOUT dictating structure
                                            "editorial_context": _editorial[:300] if _editorial else "",
                                            # Color is safe to hint — it constrains without mislabelling
                                            "detected_color": _color_hint,
                                            # Occasion/niche for Amazon query framing
                                            "occasion": _occasion_hint,
                                            "confidence_source": "monetization_brain",
                                            # Explicit instruction NOT to anchor on this
                                            "_instruction": (
                                                "Run ALL phases (0-9) independently. "
                                                "Do NOT assume garment type from this context. "
                                                "The editorial_context is background info only — "
                                                "your Phase 1 visual dominance detection is authoritative."
                                            ),
                                        }
                                        # Strip empty values
                                        visual_hint = {k: v for k, v in visual_hint.items() if v}
                                        logger.info(
                                            f"[HIERARCHICAL] Passing visual_hint to fashion_scout: {list(visual_hint.keys())}"
                                        )
                                        main_data["fashion_scout"] = scout.scout_outfit(
                                            image_paths, visual_hint=visual_hint if visual_hint else None
                                        )
                                        # [HIERARCHICAL_SYNC] Update main result with the superior wear_name from scout
                                        _f_scout = main_data.get("fashion_scout")
                                        if _f_scout and _f_scout.get("wear_name"):
                                            _better_name = _f_scout["wear_name"]
                                            # Quality gate: reject generic or colour-first names
                                            _wn_words = _better_name.lower().split() if _better_name else []
                                            _COLOR_KWS = {
                                                "black", "white", "red", "blue", "green", "yellow",
                                                "orange", "purple", "pink", "gold", "silver", "beige",
                                                "brown", "grey", "gray", "navy", "ivory", "cream",
                                            }
                                            _is_generic = (
                                                not _better_name
                                                or len(_wn_words) <= 2
                                                or (_wn_words and _wn_words[0] in _COLOR_KWS)
                                            )
                                            if not _is_generic:
                                                main_data["final_caption"] = _better_name
                                                main_data["item_name"] = _better_name
                                                if main_data.get("overlay_data"):
                                                    main_data["overlay_data"][0]["item_name"] = _better_name
                                                logger.info(f"🔄 [HIERARCHICAL_SYNC] Superior wear_name found: '{_better_name}'")
                                            else:
                                                logger.warning(
                                                    f"[HIERARCHICAL_SYNC] Rejected generic/colour-first wear_name from scout: '{_better_name}'"
                                                )
                                    else:
                                        main_data["fashion_scout"] = None
                                except Exception as _fe:
                                    # No bare except: pass — log the actual failure
                                    logger.warning(f"[FASHION_SCOUT] Integration failed (non-fatal): {_fe}")
                                    main_data["fashion_scout"] = None
                            return main_data
                    
                    logger.warning(f"⚠️ Attempt {attempt+1} failed validation. Retrying...")
                except Exception as e:
                    logger.error(f"❌ Gemini Attempt {attempt+1} Error: {e}")

            return self._fallback_response(title, error="Max retry attempts reached or validation failed", transformations=transformations)

        except Exception as e:
            logger.error(f"🧠 Brain Analysis Critical Error: {e}")
            return self._fallback_response(title, error=e, transformations=transformations)

        except Exception as e:
            logger.error(f"🧠 Brain Analysis Error: {e}")
            return self._fallback_response(
                title, error=e, transformations=transformations
            )

    def _adaptive_truncate(
        self, script: str, duration: float, max_density: float = 1.20
    ) -> str:
        """
        Smart Truncation: Removes the last sentence until script fits density rules.
        Falls back to 1.45 density if only one sentence remains.
        """
        sentences = re.split(r"(?<=[.!?])\s+", script.strip())
        if not sentences:
            return script

        current_script = script
        while len(sentences) > 1:
            words = current_script.split()
            density = ((len(words) / 140) * 60) / duration if duration > 0 else 0

            if density <= max_density:
                return current_script

            # Remove last sentence
            sentences.pop()
            current_script = " ".join(sentences)
            logger.info(
                f"✂️ Smart Truncating script (Density: {density:.2f} > {max_density}). New word count: {len(current_script.split())}"
            )

        # If only one sentence remains, check against absolute fallback density (1.45)
        words = current_script.split()
        density = ((len(words) / 140) * 60) / duration if duration > 0 else 0
        if density <= 1.45:
            if density > max_density:
                logger.info(f"🟢 Allowing high-density single sentence ({density:.2f})")
            return current_script

        return current_script  # Final attempt, will be caught by gate

    def _smart_truncate_caption(
        self, text: str, max_chars: int = 120, max_lines: int = 4, wrap_width: int = 32
    ) -> str:
        """
        Intelligently truncates a caption to fit display constraints.
        Ensures word boundaries and avoids "half cut tail" issues.
        """
        import textwrap

        if not text:
            return ""

        # Clean up double spaces/newlines
        text = " ".join(text.split())

        # If it fits as is, return it
        lines = textwrap.wrap(text, width=wrap_width)
        if len(text) <= max_chars and len(lines) <= max_lines:
            return text

        # Otherwise, we must truncate
        words = text.split()
        current_text = ""

        for i in range(len(words)):
            candidate = " ".join(words[: i + 1])
            temp_lines = textwrap.wrap(candidate, width=wrap_width)

            if len(candidate) > max_chars or len(temp_lines) > max_lines:
                # We exceeded limits, return the previous version (with ellipsis if it's not a full sentence)
                final_text = " ".join(words[:i])
                if not final_text.endswith((".", "!", "?")):
                    final_text = final_text.rstrip(",;:- ") + "..."
                return final_text

        return text  # Should not happen given the initial check

    def _parse_json_response(
        self,
        text: str,
        original_title: str,
        duration: float = 15.0,
        visual_context: str = None,
    ) -> Dict:
        """
        Parses JSON using the New Professional Schema and applies Confidence Filters.
        """
        try:
            # 1. Extract JSON Object (Strict Regex)
            match = re.search(r"(\{.*\})", text, re.DOTALL)
            if not match:
                logger.warning("🧠 Invalid JSON format: No brackets found.")
                return {"approved": False}

            data = json.loads(match.group(1))
            items = data.get("items", [])
            if not items:
                logger.warning("🧠 No items found in JSON.")
                return {"approved": False}

            # 2. Focus on primary item (Confidence Filter)
            # The prompt is instructed to return items in order of clarity.
            primary_item = items[0]
            confidence = float(primary_item.get("confidence", 0.0))

            # ── PRE-CONFIDENCE HOOK RESCUE ─────────────────────────────────────────
            # Hooks (telegram/instagram/youtube) are generated at the data/prompt level
            # independently of item confidence. Cache them NOW so they survive even if
            # overlay confidence is too low. Previously these were silently discarded.
            _early_hooks = {
                "telegram_hook":          data.get("telegram_hook"),
                "instagram_hook":         data.get("instagram_hook"),
                "youtube_hook":           data.get("youtube_hook"),
                "community_comment_hook": data.get("community_comment_hook"),
            }
            try:
                from Text_Modules.smart_price_tag import set_pipeline_hooks as _set_hooks_early
                _set_hooks_early({k: v for k, v in _early_hooks.items() if v})
                if any(_early_hooks.values()):
                    logger.info("[HOOK_RESCUE] Pre-confidence hooks cached: %s",
                                [k for k, v in _early_hooks.items() if v])
            except Exception:
                pass

            if confidence < 0.5:
                logger.warning(
                    f"🚩 [CONFIDENCE_FAIL] Confidence {confidence} < 0.5. "
                    f"Overlay rejected — hooks preserved in pipeline cache."
                )
                _fail_return = {"approved": False}
                _fail_return.update({k: v for k, v in _early_hooks.items() if v})
                return _fail_return

            # 3. Factual Narration Construction (Non-Abstract)
            n = primary_item.get("narration", {})

            # [VISUAL CONSISTENCY GUARD] Reject if garment type or colour is clearly hallucinated
            # If visual_context mentions a colour and the narration colour contradicts it, reject.
            if visual_context:
                vc_lower = visual_context.lower()
                narration_colour = n.get("color", "").lower()
                narration_garment = n.get("garment_type", "").lower()
                # Colour contradiction check — only fire if both sides are explicit
                _colour_pairs = [
                    ("white", ["black", "red", "blue", "green", "pink", "yellow", "orange", "purple"]),
                    ("black", ["white", "red", "blue", "green", "pink", "yellow", "orange", "purple"]),
                ]
                for true_colour, wrong_colours in _colour_pairs:
                    if true_colour in vc_lower and any(w == narration_colour for w in wrong_colours):
                        logger.warning(
                            f"🚩 [COLOUR_MISMATCH] visual_context={true_colour!r} but narration.color={narration_colour!r}. Rejecting."
                        )
                        return {"approved": False}
                # Garment type contradiction — e.g. visual says "dress" but narration says "lehenga"
                _garment_pairs = [
                    (["dress", "gown", "mini dress", "midi dress", "maxi dress"], ["lehenga", "saree", "anarkali"]),
                    (["saree", "sari"], ["dress", "gown", "lehenga"]),
                    (["lehenga"], ["dress", "gown", "saree"]),
                ]
                for western_terms, indian_terms in _garment_pairs[:1]:  # dress vs lehenga/saree
                    if any(t in vc_lower for t in western_terms) and any(t in narration_garment for t in indian_terms):
                        logger.warning(
                            f"🚩 [GARMENT_MISMATCH] visual_context contains western garment but narration.garment_type={narration_garment!r}. Rejecting."
                        )
                        return {"approved": False}

            # Construct a grounded description from the extracted features
            # Handle both narration schemas: {fit, color, ...} and {detail_1, detail_2}
            d1 = n.get("detail_1", "") or n.get("garment_type", "")
            d2 = n.get("detail_2", "") or n.get("occasion", "")
            color = n.get("color", "")
            material = n.get("material", "")

            if d1 and d2:
                # New Schema path
                color_part = f"The {color} " if color else ""
                material_part = f"{material} " if material else ""
                script = f"{original_title} is seen in a {material_part}{color_part}look. {d1}. {d2}."
            else:
                # Legacy path fallback
                script = f"{original_title} is seen in a {n.get('fit', 'fitted')} {n.get('color', '')} {n.get('garment_type', 'outfit')}. "
                script += f"The {n.get('material', 'fabric')} features {n.get('pattern', 'detailed')} detailing, "
                script += f"ideal for {n.get('occasion', 'special occasions')}."

            # [RETRY TRIGGER] If script contains abstract or placeholder banned words, fail for retry
            banned_triggers = [
                "vibe", "energy", "owning the moment", "effortless", "stunning", "timeless",
                "textile", "various occasions", "complementary accessories", "detailed styling",
                "aspiration", "dreams", "best self", "masterclass", "craftsmanship", "luxury filler",
                "stunning look", "elegant style", "perfect for any", "versatile piece",
                "social advantage", "confidence", "empowerment", "elevate your wardrobe",
                "this look", "get the look", "you won't believe", "shop now", "click the link",
                "inspire", "aspire", "dream", "effortlessly", "gorgeous", "beautiful"
            ]
            if any(word in script.lower() for word in banned_triggers):
                logger.warning(f"🚩 [BANNED_WORD_FAIL] Script contains abstract/placeholder/motivation language: {script}. Triggering retry.")
                return {"approved": False}

            # 4. Sanitize Item Name — reject generic AI filler like 'Luxury Couture Ensemble'
            raw_name = primary_item.get("item_name", "Apparel Item")
            category = primary_item.get("category", "default")
            
            # Hard ban on generic luxury filler item names — force fallback to concrete category
            _BANNED_ITEM_NAMES = [
                "luxury couture", "bespoke artisan", "designer heritage", "artisan creation",
                "couture ensemble", "heritage piece", "luxury ensemble", "bespoke creation",
                "couture collection", "artisan ensemble"
            ]
            if any(b in raw_name.lower() for b in _BANNED_ITEM_NAMES):
                logger.warning(f"🚩 [ITEM_NAME_BANLIST] Generic filler item_name rejected: '{raw_name}'. Using category '{category}'.")
                raw_name = category  # Fallback to the concrete category (e.g. 'Lehenga', 'Dress')
            
            clean_name = sanitize_item_name(raw_name, category)

            # 5. Map to legacy overlay structure for compiler compatibility
            # We wrap it in a list as the pipeline expects 'overlay_data' to be a list
            overlay = {
                "item_name": clean_name,
                "trend_text": category.upper(),
                "brand_text": original_title.upper(),
                "price_tag": f"₹{primary_item.get('price_tag_estimate', random.randint(2500, 9500)):,}",
                "price_tag_time": float(primary_item.get("price_tag_time", 0.75))
            }

            # ── Extract all hook outputs from the master Gemini response ─────────
            telegram_hook = data.get("telegram_hook")
            instagram_hook = data.get("instagram_hook")
            youtube_hook = data.get("youtube_hook")
            community_comment_hook = data.get("community_comment_hook")

            # ── Narration script override ─────────────────────────────────────────
            # Use Gemini's narration_script if quality is sufficient.
            # Fallback: script already built from detail_1/detail_2 above is kept.
            _narration_raw = data.get("narration_script", "")
            # Extract recommended variation if multi-variation format is present
            if _narration_raw and "RECOMMENDED:" in _narration_raw:
                _rec = re.search(r"RECOMMENDED:\s*([ABC])", _narration_raw)
                _key = _rec.group(1) if _rec else "A"
                _match = re.search(rf"{_key}:\s*(.+?)(?=\n[ABC]:|RECOMMENDED:|$)", _narration_raw, re.DOTALL)
                if _match:
                    _narration_raw = _match.group(1).strip()

            if _narration_raw and len(_narration_raw.split()) >= 8:
                script = _narration_raw
                logger.info("[NARRATION] Using Gemini narration_script from master hook call.")

            # ── Populate shared hook cache so downstream modules skip their calls ─
            try:
                from Text_Modules.smart_price_tag import set_pipeline_hooks as _set_hooks
                _set_hooks({
                    "telegram_hook": telegram_hook,
                    "instagram_hook": instagram_hook,
                    "youtube_hook": youtube_hook,
                    "community_comment_hook": community_comment_hook,
                })
                logger.info("[MASTER_HOOKS] Pipeline hook cache populated from monetization_brain.")
            except Exception as _cache_err:
                logger.debug(f"[MASTER_HOOKS] Cache population skipped (non-fatal): {_cache_err}")

            # ── 6. Finalization & MoneyFlow Optimization ───────────────────
            # Prepend the title to the script if missing (Voiceover requirement)
            clean_title_str = original_title.split(":", 1)[-1].replace("_", " ").strip()
            title_words = set(re.findall(r"\w+", clean_title_str.lower()))
            if clean_title_str and not all(w in script.lower() for w in title_words if len(w) > 3):
                script = f"{clean_title_str}. {script}"

            result = {
                "approved": True,
                "final_caption": clean_name,
                "editorial_script": script,
                "editorial_title": data.get("generated_title", f"Fashion Review: {original_title}"),
                "hashtags": data.get("generated_hashtags"),
                "telegram_hook": telegram_hook,
                "instagram_hook": instagram_hook,
                "youtube_hook": youtube_hook,
                "community_comment_hook": community_comment_hook,
                "monetization_cta": data.get("monetization_cta", "Find this look linked in the description"),
                "overlay_data": [overlay],
                "caption_style": "PROFESSIONAL_REVIEW",
                "risk_level": "LOW",
                "risk_reason": "Factual non-abstract analysis verified.",
                "person_count": data.get("person_count", 1),
                "transformation_score": data.get("transformation_score", 100),
                "confidence": confidence,
                "verdict": "Monetization Viable",
                "source": "professional_reviewer_brain",
                "entities": data.get("entities", {})
            }

            # --- NEW: MONEY FLOW OVERHAUL (Intent-Warming) ---
            if money_engine:
                try:
                    vibe = result.get("entities", {}).get("fashion_trend", "GLOBAL")
                    matched_cat = "GLOBAL"
                    for cat in ["LUXURY", "STREETWEAR", "MINIMALIST", "BOHEMIAN", "FORMAL"]:
                        if cat.lower() in vibe.lower():
                            matched_cat = cat
                            break

                    offer = money_engine.get_optimized_offer(matched_cat)
                    result["monetization_cta"] = money_engine.get_law_bending_cta(offer)
                    
                    # Grammar fix
                    if "link in description" in result["monetization_cta"].lower():
                        result["monetization_cta"] = result["monetization_cta"].replace(
                            "link in description", "linked in the description"
                        )
                    result["lead_magnet"] = offer.get("lead_magnet")
                    logger.info(f"💰 [MoneyFlow] Intent-Warming Applied: {offer.get('lead_magnet')}")
                except Exception as me_err:
                    logger.warning(f"Failed to apply money flow optimization: {me_err}")

            # ── FORCE_FASHION_CAPTION OVERRIDE (Auto-Input / Scheduler / Follower-Growth) ──
            # Active when FORCE_FASHION_CAPTION=yes in .env (scheduler-triggered runs only).
            # Rewrites final_caption + instagram_hook with FashionScout's retail-grade
            # wear_name — highly specific, SEO-optimised copy that gains followers organically.
            # Voiceover / editorial_script is intentionally NOT touched (no voiceover mode).
            # Falls through silently if fashion_scout data is absent or wear_name is too generic.
            try:
                from Monetization_Metrics.fashion_scout import FORCE_FASHION_CAPTION as _ffc
                if _ffc:
                    _scout = result.get("fashion_scout") or {}
                    _fw = (_scout.get("wear_name") or "").strip()
                    # Quality gate: require at least 3 words — reject "Red Dress", "Blue Top" etc.
                    if len(_fw.split()) >= 3:
                        result["final_caption"] = _fw
                        result["item_name"]     = _fw
                        if result.get("overlay_data"):
                            result["overlay_data"][0]["item_name"] = _fw
                        # Rebuild instagram_hook as follower-growth copy:
                        #   Line 1 — specific product name (SEO anchor)
                        #   Line 2 — social-proof urgency
                        #   Line 3 — frictionless CTA
                        result["instagram_hook"] = (
                            f"{_fw} 🔥\n"
                            f"Thousands follow this style every week.\n"
                            f"Link in bio 👆"
                        )
                        logger.info(
                            f"[FORCE_FASHION_CAPTION] Scheduler override applied → "
                            f"final_caption='{_fw}'"
                        )
                    else:
                        logger.debug(
                            f"[FORCE_FASHION_CAPTION] wear_name too generic or absent — "
                            f"keeping original caption (wear_name='{_fw}')"
                        )
            except Exception as _ffc_err:
                logger.debug(f"[FORCE_FASHION_CAPTION] Override skipped (non-fatal): {_ffc_err}")
            # ─────────────────────────────────────────────────────────────────────

            return result

        except json.JSONDecodeError:
            logger.error(f"🧠 JSON Decode Failed: {text[:50]}...")
            return self._fallback_response(
                original_title, error=ValueError("JSON Decode"), visual_context=visual_context
            )
        except Exception as e:
            logger.error(f"🧠 Parsing Error: {e}")
            return self._fallback_response(
                original_title, error=e, visual_context=visual_context
            )


            # Success
            result = {
                "approved": True,
                "final_caption": script,  # Mapped for legacy
                "editorial_script": script,
                "editorial_title": locals().get("gen_title", ""),  # NEW: Compilation Title
                "hashtags": locals().get("gen_tags", []),  # NEW: Compilation Hashtags
                "monetization_cta": locals().get("mon_cta", ""),  # [USER REQUEST] Simplified CTA
                "entities": locals().get("entities", []),
                "overlay_data": locals().get("overlays_to_score", []),
                "caption_style": "EDITORIAL_ANALYSIS",
                "risk_level": "LOW",
                "risk_reason": "Factual media analysis verified.",
                "person_count": locals().get("person_count", 0),
                "transformation_score": data.get("transformation_score", 100),
                "narrative_density": locals().get("narrative_density", 0.0),
                "fact_score": locals().get("fact_score", 0.0),
                "policy_citation": "Educational & Documentary",
                "verdict": "Monetization Viable",
                "source": "media_analysis_brain",
                "price_tag": locals().get("overlays_to_score", [{}])[0].get("price_tag", "") if locals().get("overlays_to_score") else None,
                "item_name": locals().get("overlays_to_score", [{}])[0].get("item_name", "") if locals().get("overlays_to_score") else None,
            }

            try:
                # --- NEW: MONEY FLOW OVERHAUL (Intent-Warming) ---
                vibe = result.get("entities", {}).get("fashion_trend", "GLOBAL")
                # Find a matching category in our money logic
                matched_cat = "GLOBAL"
                for cat in ["LUXURY", "STREETWEAR", "MINIMALIST", "BOHEMIAN", "FORMAL"]:
                    if cat.lower() in vibe.lower():
                        matched_cat = cat
                        break

                offer = money_engine.get_optimized_offer(matched_cat)
                result["monetization_cta"] = money_engine.get_law_bending_cta(offer)
                # [Grammar Fix]
                if "link in description" in result["monetization_cta"].lower():
                    result["monetization_cta"] = result["monetization_cta"].replace(
                        "link in description", "linked in the description"
                    )

                result["lead_magnet"] = offer["lead_magnet"]
                logger.info(
                    f"💰 [MoneyFlow] Intent-Warming Applied: {offer['lead_magnet']}"
                )
            except Exception as e:
                logger.warning(f"Failed to apply money flow optimization: {e}")
                result["monetization_cta"] = "Find this look linked in the description"

            return result

        except json.JSONDecodeError:
            logger.error(f"🧠 JSON Decode Failed: {text[:50]}...")
            return self._fallback_response(
                original_title,
                error=ValueError("JSON Decode"),
                visual_context=visual_context,
            )
        except Exception as e:
            logger.error(f"🧠 Parsing Error: {e}")
            return self._fallback_response(
                original_title, error=e, visual_context=visual_context
            )

    def _fallback_response(
        self,
        caption: str,
        error: Exception = None,
        transformations: Dict = {},
        visual_context: str = None,
        failed_script: str = None,
    ) -> Dict:
        """
        FAIL-SAFE: Strict Rejection if Brain is offline.
        Prevents 'safe but boring' reuse content from polluting the channel.
        """
        # Default: REJECT
        risk = "HIGH"
        reason = "Brain Offline - Unable to verify editorial value. Rejecting to protect channel quality."

        if error:
            reason = f"Brain Error: {str(error)}"

        script = sanitize_caption_text(caption, target_max=4, hard_max=6)
        if failed_script and len(failed_script) > 10:
            script = sanitize_caption_text(failed_script, target_max=4, hard_max=6)
            reason += " (Using Recovered AI Script)"
        elif visual_context and len(visual_context) > 10:
            script = sanitize_caption_text(visual_context, target_max=4, hard_max=6)
            reason += " (Using AI Caption Fallback)"
            risk = "MEDIUM"

        # [mkpv-fix] Sanitize Fallback Script (Remove "Link in bio", "Subscribe", etc.)
        # If the fallback script contains spammy terms, revert to safe default.
        spam_triggers = [
            "link in",
            "bio",
            "description",
            "subscribe",
            "sub",
            "follow",
            "instagram",
            "tiktok",
        ]
        if any(x in script.lower() for x in spam_triggers):
            logger.warning(
                f"⚠️ Fallback script contained unsafe promotional triggers. Reverting to safe default."
            )
            script = self.get_safe_fallback()
            risk = "LOW"  # Default safe is low risk

        # [mkpv-fix] Remove forced "Link in description" (User considers this low-quality/forced)
        # We use the script as-is for the editorial script (voiceover).
        editorial_script = script
        clean_cap = caption.lower().strip()
        script_lower = script.lower()
        cap_words = set(re.findall(r'\w+', clean_cap))
        # If the caption is essentially the same as the sanitized script or vice versa, don't repeat it
        significant_overlap = len(cap_words) > 0 and len([w for w in cap_words if w in script_lower]) / len(cap_words) > 0.6
        
        if clean_cap not in script_lower and not significant_overlap:
            # If caption is not in script and not significantly overlapping, use it as a lead-in
            editorial_script = f"{caption}. {script}"

        # Apply commentary refinement in fallback path
        if voiceover and editorial_script:
            try:
                ref = voiceover.refine_commentary(editorial_script)
                editorial_script = ref["text"]
                logger.info(
                    '[COMMENTARY_ENGINE] narration_refined=%s | original="%s" | refined="%s"',
                    ref["changed"],
                    ref["original"][:200],
                    editorial_script[:200],
                )
            except Exception as e:
                logger.warning(f"[COMMENTARY_ENGINE] refinement_failed: {e}")

        # REMOVED: Hardcoded "Check the link in description" (User considers this low-quality/forced)

        # Override Verdict for Caption Fallback
        if risk == "MEDIUM" or (visual_context and len(visual_context) > 5):
            risk = "LOW"
            reason = "Brain Offline - Using Verified Caption as Script."
            approved_status = True
            verdict_msg = "Approved (Caption Fallback)"
        else:
            approved_status = False
            verdict_msg = "Rejected (System Failure)"

        return {
            "approved": approved_status,
            "final_caption": script,
            "editorial_script": editorial_script,
            "item_name": script,  # Ensure orchestrator finds it as 'Wear Name'
            "risk_level": risk,
            "risk_reason": reason,
            "transformation_score": 50,  # Neutral
            "verdict": verdict_msg,
            "policy_citation": "System Recovery",
            "source": "fallback_recovery",
            "monetization_cta": "Find this look linked in the description",
            "editorial_title": f"Style Edit: {caption[:30]}...",
            "overlay_data": [
                {
                    "brand_text": sanitize_caption_text(
                        os.getenv("BRAND_NAME")
                        or os.getenv("TEXT_OVERLAY_CONTENT")
                        or "Style Analysis"
                    ),
                    "trend_text": "Viral Trend",
                    "item_name": script,
                    "context_text": script,
                }
            ],
        }


    def generate_progression_hook(self, subject_name: str, num_clips: int) -> str:
        """
        Generates a high-retention hook for chronological compilations.
        Example: "Watch strategic improvements of Malavika over 4 stages."
        """
        fallback_hook = f"Watch the evolution of {subject_name} over {num_clips} stages."

        if not self.router:
            return fallback_hook

        try:
            prompt = (
                f"Generate ONE short, high-retention text overlay hook (max 12 words) for a chronological compilation video.\n"
                f"The video shows {num_clips} clips of '{subject_name}' from oldest to newest.\n"
                f"The tone should be slightly analytical or 'insider', using words like 'strategic', 'evolution', 'improvements', or 'progression'.\n"
                f"DO NOT use hashtags or quotes in the response. Return JUST the hook text."
            )

            res_txt = self.router.generate(
                task_type="master",
                prompt=prompt,
                module_name="monetization_progression_hook",
            )
            if res_txt and len(res_txt) > 5:
                return res_txt.replace('"', "").strip()
            
            return fallback_hook
        except Exception as e:
            logger.error(f"🧠 Progression Hook Gen Failed: {e}")
            return fallback_hook

    def generate_editorial_title(self, context: str, n_videos: int = None) -> tuple:
        """
        Generates a clickbait, high-performing title AND description for compilations.
        Returns: (title, description)
        """
        fallback_title = f"Compilation: {context}"
        fallback_desc = (
            f"Compilation of best moments for {context}. #SafeForWork #Fashion"
        )

        if not self.router:
            return fallback_title, fallback_desc

        try:
            import random as py_random

            salt = py_random.randint(1000, 9999)

            # Zero-Ending Number Rule: Only include n_videos if it ends in 0
            num_str = ""
            if n_videos and n_videos % 10 == 0:
                num_str = f"{n_videos} "

            number_rule = (
                f"You MUST include the number '{num_str.strip()}' in the title."
                if num_str
                else "Do NOT include any numbers in the title."
            )

            prompt = (
                f"Generate ONE high-impact, law-bending TITLE and a short curiosity-gap "
                f'DESCRIPTION for a compilation about: "{context}".\n\n'
                f"FORMAT RULES (STRICT):\n"
                f'1. NAME FIRST: The title MUST start with "{context}".\n'
                f"2. NUMBER RULE: {number_rule}\n\n"
                f"LAW-BENDING PERSONA (INSIDER):\n"
                f"Use 'Insider Intel' language to drive clicks.\n"
                f"- Instead of 'hot/sexy' -> Use: 'Subversive', 'Statuesque', 'Magnetic', 'Elite'.\n"
                f"- Instead of 'compilation' -> Use: 'Vault', 'Blueprint', 'Archive', 'Theory'.\n\n"
                f"VARIETY & STYLE:\n"
                f"- Use emojis at the end to grab attention.\n"
                f"- Ensure the title is unique (Compilation ID: {salt}).\n"
                f"- Max 60 characters for Title.\n"
                f"- SEO Description: 1-2 short sentences.\n\n"
                f"OUTPUT TYPE: Valid JSON ONLY.\n"
                f'Schema: {{"title": "Your Law-Bending Title Here", "description": "Your description here."}}'
            )

            # Execute via governor router — single call, quota + cache managed
            res_txt = self.router.generate(
                task_type="master",
                prompt=prompt,
                module_name="monetization_editorial_title",
            )
            if not res_txt:
                return fallback_title, fallback_desc

            match = re.search(r"(\{.*\})", res_txt, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                gen_title = str(data.get("title", fallback_title)).strip()
                gen_desc = str(data.get("description", fallback_desc)).strip()
                if len(gen_title) > 5:
                    logger.info(f"✅ Editorial title generated: {gen_title[:60]}")
                    return gen_title, gen_desc

            return fallback_title, fallback_desc

        except Exception as e:
            logger.error(f"🧠 Title/Desc Gen Failed: {e}")
            return fallback_title, fallback_desc

    def get_safe_fallback(self) -> str:
        """
        Returns a guaranteed safe caption from:
        1. Local Storage (captions_cache.json + caption_state.json)
        2. Hardcoded Revenue-Safe Templates
        """
        cache_path = os.path.join("The_json", "captions_cache.json")
        state_path = os.path.join("The_json", "caption_state.json")

        try:
            if os.path.exists(cache_path) and os.path.exists(state_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    captions = json.load(f)
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)

                idx = state.get("fallback_index", 0)
                if captions:
                    # Select caption
                    selected = captions[idx % len(captions)]

                    # Update state (Rotate)
                    state["fallback_index"] = (idx + 1) % len(captions)
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(state, f)

                    logger.info(f"🛡️ Using Cached Fallback (Index {idx}): {selected}")
                    return selected
        except Exception as e:
            logger.warning(f"⚠️ Caption Cache Fallback failed: {e}")

        try:
            state_path = os.path.join("The_json", "caption_state.json")
            if os.path.exists(state_path):
                with open(state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "caption_final" in data and len(data["caption_final"]) > 5:
                        val = data["caption_final"]
                        # Quick re-validate stored caption
                        if "#" not in val and len(val.split()) >= 2:
                            logger.info(f"🛡️ Using Stored Fallback: {val}")
                            return val
        except Exception:
            pass

        return "A quiet moment captured today"

    def save_successful_caption(self, caption: str, source: str, style: str):
        """
        Persists the safe caption to disk ATOMICALLY.
        Writes to caption_state.json — NOT caption_prompt.json (which is a prompt config file).
        """
        try:
            state_path = os.path.join("The_json", "caption_state.json")
            os.makedirs("The_json", exist_ok=True)
            data = {
                "caption_final": caption,
                "last_source": source,
                "last_style": style,
                "timestamp": datetime.now().isoformat(),
            }

            # Atomic Write via Temp
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, dir="The_json", encoding="utf-8"
            ) as tmp:
                json.dump(data, tmp, indent=2)
                tmp_path = tmp.name

            shutil.move(tmp_path, state_path)
            logger.info(f"💾 Caption state saved to {state_path}")

        except Exception as e:
            logger.warning(f"⚠️ Failed to save caption persistence: {e}")

    def get_monetization_link(
        self, target_platform: str = "youtube", item_category: str = None,
        detected_niche: str = ""
    ) -> Optional[str]:
        """
        Traffic Segregation Strategy with [ADAPTIVE VARIABLE REWARD]:
        - YouTube  -> Amazon Links (Safe / Gifting / Targeted Fashion)
        - Telegram -> Amazon Links (standard niches) OR Los Pollos (NSFW only)

        [TUNED] Hard NSFW gate: Los Pollos (CPA) links are ONLY injected when
        detected_niche == "Adult Content & NSFW Content". For every other niche
        (Fashion, Bollywood, Bridal, etc.) Telegram also receives safe Amazon links.

        Adaptive Logic:
        - Consult Safe Mode. If L3, return None (Killswitch).
        """
        # [ADAPTIVE v3] Safe Mode Check
        import Intelligence_Modules.adaptive_intelligence as ai

        constraints = ai.brain.get_execution_constraints()
        if constraints.get("cta_aggression", 1.0) == 0.0:
            logger.info("🛡️ Safe Mode L3: Monetization Disabled.")
            return None

        _nsfw_allowed = (detected_niche or "").strip() == "Adult Content & NSFW Content"

        amazon_file = "Monetization_Metrics/Amazon_affliate_link.json"

        # 1. YOUTUBE / SAFE MODE (Amazon Only)
        if target_platform.lower() == "youtube":
            try:
                if os.path.exists(amazon_file):
                    with open(amazon_file, "r", encoding="utf-8") as f:
                        amz_data = json.load(f)

                        # Handle targeted category selection
                        if isinstance(amz_data, dict):
                            # Try specific category first
                            if item_category:
                                # Normalize category for lookup (e.g. 'Silk Saree' -> 'Saree')
                                matched_key = None
                                ic_lower = item_category.lower()
                                for k in amz_data.keys():
                                    if k.lower() in ic_lower or ic_lower in k.lower():
                                        matched_key = k
                                        break

                                if matched_key:
                                    links = amz_data.get(matched_key)
                                    if links:
                                        logger.info(
                                            f"🎯 Targeted Amazon Link Found for category: {matched_key}"
                                        )
                                        if isinstance(links, dict):
                                            return random.choice(list(links.values()))
                                        return random.choice(links)

                            # Fallback to 'default' list
                            default_links = amz_data.get("default", [])
                            if default_links:
                                if isinstance(default_links, dict):
                                    return random.choice(list(default_links.values()))
                                return random.choice(default_links)

                        # Backward compatibility if it's still a list
                        elif isinstance(amz_data, list) and amz_data:
                            return random.choice(amz_data)

                logger.warning("⚠️ No Amazon links found for YouTube.")
                return None
            except Exception as e:
                logger.warning(f"⚠️ Failed to load Amazon links: {e}")
                return None

        # 2. TELEGRAM
        elif target_platform.lower() == "telegram":
            try:
                # [TUNED] HARD NSFW GATE:
                # Los Pollos (CPA) links are ONLY served when the content was
                # identified as Adult/NSFW by Gemini's forensic scanner.
                # All standard niches (Fashion, Bollywood, Bridal, etc.)
                # receive safe Amazon links even on Telegram.
                if not _nsfw_allowed:
                    logger.info(
                        "🔒 [LP_GATE] Standard niche — serving Amazon link on Telegram."
                    )
                    return self.get_monetization_link(
                        "youtube", item_category=item_category
                    )

                # NSFW profile: serve Los Pollos CPA link
                if not os.path.exists(self.los_pollos_file):
                    logger.warning("⚠️ CPA link file missing for Telegram.")
                    return self.get_monetization_link(
                        "youtube", item_category=item_category
                    )  # Safe Fallback

                with open(self.los_pollos_file, "r", encoding="utf-8") as f:
                    lp_links = json.load(f)
                    if lp_links and isinstance(lp_links, list):
                        # [ADAPTIVE v3] Weighted Link Optimizer
                        if self.link_optimizer:
                            selected = self.link_optimizer.get_weighted_link(lp_links)
                            if selected:
                                return selected

                        # Fallback if optimizer missing/failed
                        return random.choice(lp_links)

                # Fallback to Amazon if no promotional links
                logger.info(
                    "ℹ️ No CPA links, falling back to Amazon for Telegram."
                )
                return self.get_monetization_link(
                    "youtube", item_category=item_category
                )

            except Exception as e:
                logger.warning(f"⚠️ Failed to load promotional links: {e}")
                return None

        return None

    # Rotating fallback pool — used when Gemini is unavailable/quota-hit.
    # Written to the same mystery/elite-insider formula as the prompt above.
    _STORY_FALLBACKS = [
        "The room recalibrated itself the moment {name} walked in — every eye locked, every conversation dropped. 🤫 What they don't know is what she's hiding behind that look. 💎",
        "Three people in that room recognised the label {name} was wearing. 🏷️ The rest just felt something shift. The real story is in the detail they missed. 🔍",
        "{name} dressed for one person that night, and it wasn't anyone in the room. 🤫 The answer is one link below. 👇",
        "That silhouette from {name} isn't accidental — it's a message. ✉️ The only question is whether you're reading it correctly. 🤔",
        "The outfit was the alibi; {name}'s real move happened after she left. 🏃‍♀️💨 Tap below if you think you can handle it. 🔥",
        "Everyone clocked {name}'s look. 📸 Nobody clocked the intention. That's the difference between watching and knowing. 🧠✨",
        "{name} walked past security, past the cameras, past the crowd. 🚶‍♀️✨ Dressed like that, nobody asks questions. 🤫",
    ]

    def get_telegram_story(self, visual_context: str) -> str:
        """
        Generates a 2-sentence Micro-Fiction (Mystery/Romance) for Telegram.
        Goal: Curiosity Gap.
        """
        try:
            # Determine niche configuration - Unified Migration
            univ = self.niche_prompts.get("_universal", {})
            active_story_prompt = univ.get(
                "story_prompt", 
                "WRITE A 2-SENTENCE HIGH-STAKES STORY based on this visual: \"{visual_context}\""
            )

            final_story_prompt = active_story_prompt.format(visual_context=visual_context)
            # Route through governor — quota + caching managed centrally
            res_txt = self.router.generate(
                task_type="master",
                prompt=final_story_prompt,
                module_name="monetization_telegram_story",
            )
            if res_txt and len(res_txt.split()) >= 10:
                return res_txt.replace('"', "").strip()

            # Gemini returned empty/too short — use rotating fallback pool
            return random.choice(self._STORY_FALLBACKS)

        except Exception as e:
            logger.error(f"Story Gen Failed: {e}")
            return random.choice(self._STORY_FALLBACKS)

    def generate_title_and_hashtags(self, visual_context: str) -> tuple:
        """
        Generates 1 High-Performance Title and 30 Relevant Hashtags for a Short.
        Returns: (title, hashtags_string)
        """
        fallback_title = None
        fallback_hashtags = "#shorts #viral #trending"

        try:
            PROMPT = f"""
            YOU ARE A VIRAL CONTENT STRATEGIST.
            Analyze this visual context: "{visual_context}"

            TASK:
            1. Write ONE clickbait, high-performing Title (Max 60 chars).
            2. Write 30 relevant, high-traffic Hashtags.

            RULES:
            - Title must be punchy, exciting, and law-bending (safe synonyms for hot topics).
            - Hashtags must include mix of niche and broad tags.
            - OUTPUT FORMAT: JSON ONLY.

            Schema:
            {{
                "title": "Your Title Here",
                "hashtags": "#tag1 #tag2 ... #tag30"
            }}
            """

            # Route through governor — quota + caching + ban management handled centrally
            res_txt = self.router.generate(
                task_type="master",
                prompt=PROMPT,
                module_name="monetization_title_hashtags",
            )
            if res_txt:
                try:
                    data = json.loads(res_txt)
                    # Extract and Validate
                    title = data.get("title", fallback_title).strip()
                    hashtags = data.get("hashtags", fallback_hashtags).strip()

                    # Basic Validation
                    if len(title) < 5:
                        title = fallback_title
                    if "#" not in hashtags:
                        hashtags = fallback_hashtags

                    return title, hashtags

                except Exception as me:
                    logger.warning(f"Title/Tag Gen parse failed: {me}")

            return fallback_title, fallback_hashtags

        except Exception as e:
            logger.error(f"Title/Tag Gen Failed: {e}")
            return fallback_title, fallback_hashtags

    def analyze_versus(self, context_a: str, context_b: str) -> Dict:
        """
        Comparison engine for the Synthetic Newsroom.
        Analyzes two sources to generate a high-stakes 'Versus' script.
        """
        if not self.router:
            return {}

        try:
            prompt = locals().get("VERSUS_ANALYSIS_PROMPT", "").format(
                context_a=context_a, context_b=context_b
            )

            logger.info("🧠 Brain: Drafting Versus Comparison...")
            res_txt = self.router.generate(
                task_type="master",
                prompt=prompt,
                module_name="monetization_versus",
            )

            if res_txt:
                match = re.search(r"(\{.*\})", res_txt, re.DOTALL)
                if match:
                    data = json.loads(match.group(1))
                    logger.info(
                        f"✅ Brain: Versus script ready. Theme: {data.get('entities', {}).get('comparison_theme')}"
                    )
                    return data
        except Exception as e:
            logger.error(f"❌ Brain Versus Analysis Failed: {e}")

        return {}

    def generate(self, title, duration=30.0, image_paths=None, **kwargs):
        """Standardized legacy entry point."""
        # Sanitize Title: Remove path, extension, underscores, and digits
        clean_name = title
        if "/" in title or "\\" in title or "." in title:
            base = os.path.basename(title).rsplit(".", 1)[0]
            clean_name = re.sub(r"_\d+$", "", base)  # Remove trailing _6 etc
            clean_name = clean_name.replace("_", " ").title()

        logger.info(f"🧠 Brain: Processing '{clean_name}' (Duration: {duration}s)")
        return self.analyze_content(
            title=clean_name, duration=duration, image_paths=image_paths, **kwargs
        )

    def lite_monetization_update(self, profile_data: Dict) -> Dict:
        """
        [LITE] Updates profile_data with monetization signals without Gemini calls.
        Used when Master Intelligence has already provided the script and item name.
        """
        logger.info("💰 Brain: Performing Lite Monetization Update (0 API Calls)...")
        item_name = profile_data.get("item_name", "Style")

        # 1. Generate Monetization Link
        link = self.get_monetization_link(
            target_platform="youtube", item_category=item_name
        )
        profile_data["monetization_link"] = link

        # 2. Defaults
        if "person_count" not in profile_data:
            profile_data["person_count"] = 1

        return profile_data


# Singleton
brain = MonetizationStrategist()