# gemini_captions.py - AI-Powered Caption Generator using Gemini Vision API
import os
import time
import random
import logging
import hashlib
import re
import json
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google")
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv("Credentials/.env", override=True)

logger = logging.getLogger("gemini_captions")

from Intelligence_Modules.gemini_governor import gemini_router

HAS_PIL = True
try:
    from PIL import Image
except ImportError:
    HAS_PIL = False

try:
    from assets.system_prompts import GEMINI_SYSTEM_ROLE, STYLE_TEMPLATES
except ImportError:
    # Fallback if file not found locally (dev mode)
    GEMINI_SYSTEM_ROLE = "You are a fashion caption generator. Output short, punchy headline labels only — 2–4 words MAX. Never use full sentences."
    STYLE_TEMPLATES = {"viral": "Bold street energy"}
    logger.warning("⚠️ assets/system_prompts.py not found. Using minimal fallback.")

try:
    from Intelligence_Modules.gemini_status_manager import manager as quota_manager
except ImportError:
    quota_manager = None
    logger.warning("⚠️ Intelligence_Modules/gemini_status_manager.py not found. Quota management disabled.")

try:
    from Intelligence_Modules.caption_memory import memory as caption_memory, stable_hash as memory_hash
except ImportError:
    caption_memory = None
    memory_hash = None

try:
    from Text_Modules.caption_sanitizer import sanitize_caption_text
except ImportError:
    def sanitize_caption_text(text: str, target_max: int = 4, hard_max: int = 6) -> str:
        return text

# --- Helper Functions ---

def stable_hash(text: str) -> int:
    if memory_hash:
        return memory_hash(text)
    return int(hashlib.md5(text.encode()).hexdigest(), 16)


def select_style_from_path(path: str, categories: List[str]) -> str:
    if not categories:
        return "editorial"
    return categories[stable_hash(path) % len(categories)]


def compress_caption_text(caption: str, filler_words: List[str], priority_words: List[str], target_max: int = 4, hard_max: int = 6) -> str:
    words = caption.split()
    # remove fillers
    filtered = [w for w in words if w.lower() not in filler_words]
    if len(filtered) <= target_max:
        return " ".join(filtered[:target_max]).strip()
    # if still long, keep priority words first, then earliest words to preserve rhythm
    ranked = sorted(
        enumerate(filtered),
        key=lambda x: (filtered[x[0]].lower() in priority_words, -x[0]),
        reverse=True,
    )
    keep_indices = sorted([idx for idx, _ in ranked[:hard_max]])
    compressed = [filtered[i] for i in keep_indices][:target_max] if len(keep_indices) > target_max else [filtered[i] for i in keep_indices]
    return " ".join(compressed).strip()


def build_negative_patterns(words: List[str]) -> List[re.Pattern]:
    """Compile case-insensitive word-boundary regex patterns."""
    patterns = []
    for w in words or []:
        escaped = re.escape(w.strip())
        if not escaped:
            continue
        patterns.append(re.compile(rf"\b{escaped}\b", re.IGNORECASE))
    return patterns


def contains_blacklisted(text: str, patterns: List[re.Pattern]) -> bool:
    if not text or not patterns:
        return False
    return any(p.search(text) for p in patterns)


def _similarity_guard(text: str, threshold: float = 0.85) -> bool:
    """Return True if caption is too similar to memory."""
    return caption_memory.is_too_similar(text, threshold=threshold) if caption_memory else False


# --- DYNAMIC FALLBACK SYSTEM ---
# No more hardcoded generic captions.
# The fallback pool is built entirely from real AI-generated captions saved in captions_cache.json.
_CACHE_FALLBACK_PATH = "The_json/captions_cache.json"

def _load_cache_fallbacks() -> list:
    """Load previously AI-generated captions from disk as the fallback pool."""
    try:
        if os.path.exists(_CACHE_FALLBACK_PATH):
            with open(_CACHE_FALLBACK_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return [str(c).strip() for c in data if c and len(str(c).strip()) > 3]
    except Exception:
        pass
    return []

def _save_to_cache_fallback(caption: str):
    """Append a newly generated caption to captions_cache.json for future fallback use."""
    try:
        existing = _load_cache_fallbacks()
        if caption not in existing:
            existing.append(caption)
            # Keep cache to max 200 entries (rotate out oldest)
            if len(existing) > 200:
                existing = existing[-200:]
            os.makedirs(os.path.dirname(_CACHE_FALLBACK_PATH), exist_ok=True)
            with open(_CACHE_FALLBACK_PATH, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"⚠️ Could not save caption to cache: {e}")

# Load once at startup — will be empty on first run, grows with each processed video
FALLBACK_CAPTIONS = _load_cache_fallbacks()

class GeminiCaptionGenerator:
    """
    AI-powered caption generator using Google Gemini Vision API.
    Analyzes video frames and generates engaging, context-aware captions.
    """
    
    def __init__(self):
        # Use centralized router
        self.router = gemini_router
        
        # Initialize Caption Cache
        self.cache_file = "The_json/captions_cache.json"
        self.caption_cache = self._load_cache()

        # Initialize persistent state
        self.state_file = "The_json/caption_state.json"
        _state = self._load_state()
        self.fallback_index = _state.get("fallback_index", 0)
        self.style_index = _state.get("style_index", 0)

        # Load Diversity Config
        # Check root first (our master config), then The_json/ as fallback
        self.prompt_config_path = (
            "caption_prompt.json"
            if os.path.exists("caption_prompt.json")
            else "The_json/caption_prompt.json"
        )
        self.diversity_config = self._load_diversity_config()
        self._negative_patterns = build_negative_patterns(
            self.diversity_config.get("NEGATIVE_WORDS", [])
        )
        
        # Load usage for rotation
        self.usage_file = "The_json/captions_usage.json"
        self.usage_data = self._load_usage_data()

    def _load_usage_data(self) -> dict:
        try:
            if os.path.exists(self.usage_file):
                with open(self.usage_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except: pass
        return {}

    def _save_usage_data(self):
        try:
            with open(self.usage_file, 'w', encoding='utf-8') as f:
                json.dump(self.usage_data, f, indent=2)
        except: pass

    def _load_cache(self):
        try:
            if os.path.exists(self.cache_file):
                import json
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    filtered = [
                        c for c in data
                        if not contains_blacklisted(str(c), self._negative_patterns)
                    ]
                    if len(filtered) != len(data):
                        logger.info(f"♻️ Purged {len(data)-len(filtered)} blacklisted cached captions.")
                    logger.info(f"💾 Loaded {len(filtered)} captions from cache.")
                    return filtered
        except Exception:
            pass
        return []

    def _save_cache(self):
        try:
            import json
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.caption_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_state(self) -> dict:
        """Loads persistent indices for fallback and style rotation."""
        try:
            if os.path.exists(self.state_file):
                import json
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
                    if isinstance(data, int):
                        return {"fallback_index": data, "style_index": 0}
        except Exception:
            pass
        return {"fallback_index": 0, "style_index": 0}

    def _save_state(self):
        """Saves persistent indices for fallback and style rotation."""
        try:
            import json
            with open(self.state_file, 'w') as f:
                json.dump(
                    {
                        "fallback_index": self.fallback_index,
                        "style_index": getattr(self, "style_index", 0),
                    },
                    f,
                )
        except Exception:
            pass

    def _load_diversity_config(self):
        try:
            if os.path.exists(self.prompt_config_path):
                import json
                with open(self.prompt_config_path, 'r', encoding='utf-8') as f:
                    content = json.load(f)
                    return content
        except Exception as e:
            logger.error(f"❌ Failed to load diversity config: {e}")
        return {}

    def _compress_caption(self, caption: str) -> str:
        """
        Priority-aware compression using config.
        """
        filler = self.diversity_config.get("FILLER_WORDS", [])
        priority = self.diversity_config.get("PRIORITY_WORDS", [])
        return compress_caption_text(caption, filler, priority, target_max=4, hard_max=6)

    def _get_style_prompt(self, style: str, category: str = "editorial") -> str:
        """
        Visual garment identification prompt.
        Gemini looks at the frame and describes what it SEES: color + garment type + texture.
        NO garment family pre-seeding — any category hint biases Gemini to hallucinate
        that garment type even when a completely different garment is visible in the frame.
        """
        full_role = self.diversity_config.get("content", GEMINI_SYSTEM_ROLE)
        neg_words = self.diversity_config.get("NEGATIVE_WORDS", [])
        banned_brands = self.diversity_config.get("BANNED_BRANDS", [])
        color_words = self.diversity_config.get("COLOR_WORDS", [])
        texture_words = self.diversity_config.get("TEXTURE_WORDS", [])

        prompt = (
            f"{full_role}\n\n"
            f"# YOUR TASK\n"
            f"Look at the person in this frame. Identify the garment they are wearing.\n"
            f"Describe ONLY what you actually see in the image — do NOT guess.\n\n"
            f"# OUTPUT FORMAT (MANDATORY)\n"
            f"Return EXACTLY 2-3 words: [COLOR] [GARMENT TYPE] or [COLOR] [GARMENT TYPE] [TEXTURE]\n"
            f"Color must be the actual dominant color of the garment (not skin, not background).\n"
            f"Use one of these colors if visible: {', '.join(color_words[:15])}, etc.\n"
            f"Use one of these textures if visible: {', '.join(texture_words[:10])}, etc.\n\n"
            f"# ABSOLUTE PROHIBITIONS\n"
            f"NEVER use any brand or designer name. Banned: "
            f"{', '.join(banned_brands[:10])}, and all others.\n"
            f"NEVER use these adjectives: {', '.join(neg_words[:12])}, etc.\n"
            f"NEVER write more than 3 words.\n"
            f"NEVER describe foreground objects, accessories, or background flowers/decor.\n"
            f"ONLY describe the GARMENT on the primary person in focus.\n"
        )
        return prompt
    
    def _next_style_category(self) -> str:
        categories = self.diversity_config.get("STYLE_CATEGORIES") or [
            "attitude",
            "mood",
            "movement",
            "texture",
            "statement",
            "minimal",
        ]
        if not categories:
            return "editorial"
        category = categories[self.style_index % len(categories)]
        self.style_index = (self.style_index + 1) % len(categories)
        self._save_state()
        return category
    
    def get_diversified_fallback(self) -> str:
        """
        Pulls a fallback caption from real previously-generated captions in captions_cache.json.
        sorted by least-recently-used to ensure rotation/diversity.
        """
        # Reload cache from disk to get latest
        fresh_pool = _load_cache_fallbacks()
        
        if fresh_pool:
            # Sort by least used for diversity
            candidates = sorted(fresh_pool, key=lambda x: self.usage_data.get(x, 0))
            fallback = candidates[0]
            self.usage_data[fallback] = self.usage_data.get(fallback, 0) + 1
            self._save_usage_data()
            logger.info(f"[CAPTION_FALLBACK] Using cached caption: '{fallback}'")
            return fallback

        # Absolute last resort — cache is empty (first ever run)
        logger.warning("[CAPTION_FALLBACK] Cache is empty — this is the first run. Returning safe default.")
        return "Fashion Item"
    
    def _validate_caption_raw(self, text: str) -> bool:
        """
        Validation gate: blacklist + brand ban + meta phrases.
        Uses BANNED_BRANDS from caption_prompt.json dynamically so any
        new brand added to the JSON is auto-blocked without code changes.
        """
        if not text:
            return False
        text_lower = text.lower()
        # length guard (2-3 words)
        word_count = len(text_lower.split())
        if word_count < 2:
            return False

        if contains_blacklisted(text_lower, self._negative_patterns):
            logger.warning(f"⚠️ Banned word detected in: '{text}'")
            return False

        # ── Dynamic brand ban from config ──────────────────────────────────
        # BANNED_BRANDS is loaded from caption_prompt.json — add a new brand
        # there and it's automatically blocked here. No code changes needed.
        banned_brands = self.diversity_config.get("BANNED_BRANDS", [])
        for brand in banned_brands:
            if brand.lower() in text_lower:
                logger.warning(f"⚠️ [BRAND_BAN] Blocked brand name in caption: '{brand}' in '{text}'")
                return False

        hard_banned = [
            "caption:", "here is", "this is a video", "output:",
            "analyze:", "assessment:", "image shows",
            "focusing on", "this video", "in this video", "the model",
            "the outfit", "she is wearing", "she wears", "we see",
            "featuring a", "this shows", "the look", "this is a",
        ]
        if any(b in text_lower for b in hard_banned):
            return False

        return True

    @staticmethod
    def _enforce_caption_rules(text: str, max_words: int = 3) -> str:
        """
        Enforce headline rules: EXACTLY 2-3 words, no trailing period.
        """
        if not text:
            return ""
        cleaned = text.replace('"', '').replace("'", "").replace('\n', ' ').strip()
        words = cleaned.split()
        trimmed = words[:max_words]
        if len(trimmed) < 2:
            return " ".join((trimmed + ["energy"])[:max_words]).strip()
        caption = " ".join(trimmed).strip()
        if caption:
            caption = caption[0].upper() + caption[1:]
        return caption

    def generate_caption(self, image_path: str, style: str = "viral") -> str:
        """
        Generate AI caption from video frame (DIRECT MODE ONLY).
        """
        try:
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image not found: {image_path}")
                
            # Rotate style categories instead of hashing for freshness
            category = self._next_style_category()
            
            logger.info(f"🤖 Generating caption (Direct Mode) [Category: {category}]...")
            
            import time
            import random
            
            # 1. BATCH GENERATION: Request 3 candidates in one call
            prompt = (
                f"{self._get_style_prompt(style, category=category)}\n\n"
                f"RULES:\n"
                f"1. Generate exactly 3 unique candidates.\n"
                f"2. Return ONLY a JSON list of strings: [\"candidate 1\", \"candidate 2\", \"candidate 3\"]"
            )

            candidates = []
            source = "fallback"

            # 1 attempt only — if Gemini is rate-limited, retrying wastes 90+s and double-burns budget.
            # The cached fallback pool is used immediately on failure.
            max_retries = 1
            for attempt in range(max_retries):
                try:
                    res_txt = self.router.generate(
                        task_type="caption",
                        prompt=[prompt, Image.open(image_path)] if HAS_PIL else prompt,
                        module_name="gemini_captions",
                        gen_config={"response_mime_type": "application/json"}
                    )
                    
                    if not res_txt: continue

                    # Parse JSON list
                    try:
                        raw_list = json.loads(res_txt)
                        if isinstance(raw_list, list):
                            candidates = [str(c) for c in raw_list[:3]]
                            break
                    except: continue
                                
                except Exception as e:
                    logger.warning(f"⚠️ Caption batch attempt {attempt+1} failed: {e}")

            if not candidates:
                logger.error("❌ Caption Batch Generation Failed. Using Diversified Fallback.")
                generated_text = self.get_diversified_fallback()
                candidates = [generated_text]
            else:
                # Select best locally using existing validation logic
                valid_candidates = []
                for c in candidates:
                    cleaned = c.replace('"', '').replace("'", "").replace('\n', ' ').strip()
                    if self._validate_caption_raw(cleaned) and not _similarity_guard(cleaned):
                        valid_candidates.append(self._compress_caption(cleaned))
                
                if valid_candidates:
                    generated_text = random.choice(valid_candidates)
                else:
                    generated_text = candidates[0] # Fallback to first if all fail validation

            # Logging
            print(f"[CAPTION] source={source}")
            print(f"[CAPTION] candidates={len(candidates)} selected=1")
            
            # --- HARD FALLBACK WITH PERSISTENT ROTATION & PRUNING ---
            if not generated_text:
                logger.error("❌ Caption Generation Failed. Using cached fallback.")
                generated_text = self.get_diversified_fallback()
                    
            if generated_text and generated_text not in self.caption_cache:
                self.caption_cache.append(generated_text)
                self._save_cache()
                # Also persist to global fallback pool
                _save_to_cache_fallback(generated_text)

            final_caption = self._enforce_caption_rules(generated_text, max_words=3)
            final_caption = sanitize_caption_text(final_caption, target_max=3, hard_max=3)
            logger.info(f"[CAPTION_ENGINE] caption=\"{final_caption}\"")
            return final_caption

        except Exception as e:
            logger.error(f"❌ Critical Caption Error: {e}")
            fallback = self.get_diversified_fallback()
            final_caption = self._enforce_caption_rules(fallback)
            final_caption = sanitize_caption_text(final_caption, target_max=3, hard_max=3)
            logger.info(f"[CAPTION_ENGINE] caption=\"{final_caption}\"")
            return final_caption

    def generate_hashtags(self, image_path: str, count: int = 5) -> str:
        prompt = (
            f"Analyze this image and generate {count} relevant, popular hashtags "
            f"that would work well on YouTube Shorts or Instagram Reels. "
            f"Return ONLY the hashtags separated by spaces, starting with #. "
            f"Focus on trending, viral topics."
        )
        try:
            with Image.open(image_path) as img:
                hashtags = self.router.generate(
                    task_type="caption",
                    prompt=[prompt, img],
                    module_name="gemini_hashtags"
                )
                if not hashtags: return "#viral #trending #shorts"
                hashtags = ' '.join([tag for tag in hashtags.split() if tag.startswith('#')])
                logger.info(f"✨ Generated hashtags: {hashtags}")
                return hashtags
        except Exception as e:
            logger.error(f"❌ Hashtag generation failed: {e}")
            return "#viral #trending #shorts"
    
    def generate_title(self, image_path: str) -> str:
        prompt = (
            "Generate a CATCHY and LAW-BENDING YouTube title (max 60 characters) for this video. "
            "Target high search intent by using professional synonyms for suggestive terms. "
            "Make it clickable, engaging, and optimized for YouTube algorithm. "
            "Use capitalization strategically. Be creative!"
        )
        try:
            with Image.open(image_path) as img:
                title = self.router.generate(
                    task_type="caption",
                    prompt=[prompt, img],
                    module_name="gemini_titles"
                )
                if not title: return "Amazing Video You Need To See!"
                title = title.replace('"', '').replace("'", '')
                if len(title) > 60:
                    title = title[:60].rsplit(' ', 1)[0]
                logger.info(f"✨ Generated title: '{title}'")
                return title
        except Exception as e:
            logger.error(f"❌ Title generation failed: {e}")
            return "Amazing Video You Need To See!"

    def generate_compilation_title(self, n_videos: int, style: str = "compilation_intro", context: str = None) -> str:
        num_str = ""
        if n_videos and n_videos % 10 == 0:
            num_str = f"{n_videos} "
        context_str = f" about: \"{context}\"" if context else ""
        name_rule = f"1. NAME FIRST: The title MUST start with '{context}'.\n" if context else ""
        prompt = (
            f"Generate a HIGHLY CLICKABLE, VIRAL, and LAW-BENDING title for a video compilation containing {n_videos} clips{context_str}. "
            "\nFORMAT RULES (STRICT):\n"
            f"{name_rule}"
            f"2. NUMBER RULE: {'You MUST include the number ' + num_str.strip() + ' in the title.' if num_str else 'Do NOT include any numbers like 11 or 12 in the title.'}\n"
            "Max 60 characters. RETURN ONLY THE TITLE TEXT."
        )
        try:
            import time
            import random
            title = self.router.generate(
                task_type="caption",
                prompt=prompt,
                module_name="gemini_compilation_titles"
            )
            if title:
                title = title.replace('"', '').replace("'", "").replace("\n", " ")
                if len(title) > 60:
                    title = title[:60].rsplit(' ', 1)[0]
                if context and context.lower() not in title.lower()[:len(context)+5]:
                    title = f"{context}: {title}"
                return title
        except Exception:
            return None

def generate_caption_from_video(video_path: str, style: str = "viral", timestamp: str = "00:00:01") -> Optional[str]:
    import subprocess
    import tempfile
    import os
    frame_path = None
    try:
        try:
            from Intelligence_Modules.smart_reuse_engine import get_frame_for_gemini
            cached_path = get_frame_for_gemini(video_path, timestamp)
            if cached_path:
                frame_path = cached_path
        except Exception: pass
        if not frame_path:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                frame_path = tmp.name
            cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", timestamp, "-vframes", "1", frame_path]
            subprocess.run(cmd, check=True, capture_output=True)
        generator = GeminiCaptionGenerator()
        return generator.generate_caption(frame_path, style)
    except Exception:
        return None
    finally:
        if frame_path and os.path.exists(frame_path) and not frame_path.endswith('.cache'):
             try: os.remove(frame_path)
             except: pass

def generate_hashtags_from_video(video_path: str, count: int = 5) -> Optional[str]:
    import subprocess
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            frame_path = tmp.name
        cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01", "-vframes", "1", frame_path]
        subprocess.run(cmd, check=True, capture_output=True)
        generator = GeminiCaptionGenerator()
        return generator.generate_hashtags(frame_path, count)
    except Exception:
        return None
    finally:
        if 'frame_path' in locals() and os.path.exists(frame_path):
             try: os.remove(frame_path)
             except: pass

def generate_caption_direct(video_path: str) -> Optional[str]:
    return generate_caption_from_video(video_path, style="viral")


def generate_caption_from_context(visual_context: dict, video_path: str = "") -> str:
    """
    Text-only caption generation using Gemini system role + diversity config.
    Uses rotating STYLE_CATEGORIES to keep headlines fresh, then applies
    validation, compression, blacklist, similarity, and sanitization.
    """
    fallback_value = visual_context.get("item_name") or visual_context.get("outfit_type") or (_load_cache_fallbacks() or ["Fashion Item"])[0]
    try:
        generator = GeminiCaptionGenerator()
    except Exception as e:
        logger.error(f"❌ Caption engine init failed: {e}")
        return sanitize_caption_text(fallback_value, target_max=3, hard_max=3)

    category = generator._next_style_category()

    # --- ALWAYS GENERATE FRESH: never trust pre-computed caption_candidates ---
    # Pre-generated candidates come from pipeline brain data that was computed with OLD prompts.
    # They consistently produce 'Luxury Couture Ensemble' style garbage that gets blacklisted.
    # Force a live Gemini call with the STRICT system prompt so the ban list is rarely needed.

    # Use the strict system prompt from caption_prompt.json (the 'content' key)
    strict_system_prompt = generator.diversity_config.get("content", "")
    if not strict_system_prompt:
        strict_system_prompt = (
            "You are a literal garment identifier. Output EXACTLY 2-3 words describing "
            "the physical visible clothing. Example: 'Brown Leopard Dress', 'Red Silk Saree'. "
            "No brand names, no editorial words like Couture, Bespoke, Luxury, Heritage."
        )

    prompt = (
        f"{strict_system_prompt}\n\n"
        f"Style Category: {category.upper()}\n"
        f"Generate exactly 3 unique 2-3 word garment descriptions based on this context: "
        f"{json.dumps({k: v for k, v in visual_context.items() if k not in ('caption_candidates',)}, ensure_ascii=False)[:800]}\n"
        f"Return ONLY a JSON list of 3 strings. Each string must be exactly 2-3 words describing a visible garment."
    )

    generated_list = []
    try:
        res = generator.router.generate(
            task_type="caption",
            prompt=prompt,
            module_name="gemini_captions_context",
            gen_config={"response_mime_type": "application/json"},
            metadata={"bypass_cache": random.random()}
        )
        if res:
            generated_list = json.loads(res)
            if isinstance(generated_list, list):
                generated_list = [str(c).strip() for c in generated_list if c]
    except Exception as e:
        logger.warning(f"⚠️ Caption generation call failed: {e}")

    if not generated_list:
        selected = generator.get_diversified_fallback()
        print(f"[CAPTION] source=emergency_fallback")
        return sanitize_caption_text(selected, target_max=3, hard_max=3)

    # Validate generated candidates
    valid = [c for c in generated_list if generator._validate_caption_raw(c)]
    if valid:
        selected = random.choice(valid)
        # [CACHE POISONING PREVENTED] Do NOT save to cache to prevent cross-video contamination.
        # _save_to_cache_fallback(selected)
        print(f"[CAPTION] source=primary")
    else:
        # All generated still failed validation — use cache fallback
        logger.warning(f"⚠️ All generated candidates failed validation: {generated_list}")
        selected = generator.get_diversified_fallback()
        print(f"[CAPTION] source=validation_fallback")

    final_caption = sanitize_caption_text(selected, target_max=3, hard_max=3)
    print(f"[CAPTION] candidates={len(generated_list)} selected=1")
    return final_caption

    if not generated:
        logger.warning("⚠️ Context caption failed. Using Diversified Fallback.")
        generated = generator.get_diversified_fallback()

    final_caption = sanitize_caption_text(generated, target_max=4, hard_max=6)
    logger.info(f"[CAPTION_ENGINE] caption_generated=\"{final_caption}\"")
    logger.info(f"[CAPTION_FINAL] caption=\"{final_caption}\"")
    return final_caption


def generate_caption(profile_data: dict = None, video_path: str = "") -> str:
    """
    Public helper for upload caption generation.
    Accepts optional profile_data (expects content_director-style context) and
    relies on rotating STYLE_CATEGORIES plus similarity + blacklist guards.
    """
    visual_context = {}
    if isinstance(profile_data, dict):
        visual_context = (
            profile_data.get("content_director")
            or profile_data.get("fashion_data")
            or {}
        )
    return generate_caption_from_context(visual_context, video_path=video_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        generator = GeminiCaptionGenerator()
        print("✅ Gemini initialized successfully!")
    except Exception as e:
        print(f"❌ Initialization failed: {e}")

def generate_compilation_title(n_videos: int, context: str = None) -> str:
    try:
        generator = GeminiCaptionGenerator()
        return generator.generate_compilation_title(n_videos, context=context)
    except Exception:
        return f"{context + ': ' if context else ''}Best {n_videos} Viral Moments Compilation"