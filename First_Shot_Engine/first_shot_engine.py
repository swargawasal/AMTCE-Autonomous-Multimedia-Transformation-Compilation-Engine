"""
First_Shot_Engine/first_shot_engine.py
─────────────────────────────────────────────────────────────────────────────
AI INFLUENCER FIRST SHOT ENGINE
─────────────────────────────────────────────────────────────────────────────
Selects and normalizes a matching AI influencer intro clip from the
First_Shots/ library before the main video pipeline runs.

Workflow:
  1. FirstShotLibrary  → loads library_index.json, resolves clip paths
  2. FirstShotSelector → text-only Gemini call to match title → category
                         keyword fallback if Gemini is unavailable
  3. normalize_intro   → FFmpeg subprocess: 1080×1920 @ 30fps, preserve audio
  4. run_first_shot_engine() → single entry point for orchestrator.py

FAIL-SAFE: Every error path returns None. The main pipeline is NEVER
affected if this engine crashes, times out, or finds no clips.

Integration point in orchestrator.py: Step 0.2 (after job_dir created,
before watermark detection).
─────────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import os
import random
import subprocess
import time
from typing import Optional, Dict, Any

logger = logging.getLogger("first_shot_engine")

# ── Constants ─────────────────────────────────────────────────────────────────
_LIBRARY_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "First_Shots"
)
_INDEX_PATH = os.path.join(_LIBRARY_DIR, "library_index.json")
_FALLBACK_TOPIC = "generic_fashion"
_TARGET_W = 1080
_TARGET_H = 1920
_TARGET_FPS = 30
_MAX_INTRO_DURATION = float(os.getenv("MAX_INTRO_DURATION", "8.0"))   # Configurable via .env, clips longer than this are trimmed
_NORMALIZE_TIMEOUT = 45     # seconds
_GEMINI_TIMEOUT = 8         # seconds — text-only, must be fast


def _send_owner_alert(message: str) -> None:
    """
    Sends a Telegram message to the owner's personal chat to alert about
    missing or new First Shot video assets.
    Uses TELEGRAM_BOT_TOKEN + TELEGRAM_OWNER_CHAT_ID from .env.
    Falls back silently if not configured.
    """
    try:
        import requests as _req
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        owner_chat = os.getenv("TELEGRAM_OWNER_CHAT_ID", "").strip()
        if not bot_token or not owner_chat:
            logger.debug("[FIRST_SHOT] Telegram owner alert skipped (TELEGRAM_OWNER_CHAT_ID not set in .env).")
            return
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": owner_chat,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": False,
        }
        resp = _req.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("[FIRST_SHOT] Owner alert sent via Telegram.")
        else:
            logger.warning(f"[FIRST_SHOT] Telegram alert failed: {resp.status_code} {resp.text[:100]}")
    except Exception as _e:
        logger.debug(f"[FIRST_SHOT] Could not send Telegram owner alert: {_e}")



# ═══════════════════════════════════════════════════════════════════════════════
# PART 1: Library Manager
# ═══════════════════════════════════════════════════════════════════════════════

class FirstShotLibrary:
    """
    Loads First_Shots/library_index.json and resolves absolute clip paths.
    Mirrors the ReactorLibraryManager pattern exactly.
    """

    def __init__(self):
        self._index: Dict[str, Any] = {}
        self._load_index()

    def _load_index(self) -> None:
        try:
            if not os.path.exists(_INDEX_PATH):
                logger.warning(
                    f"[FIRST_SHOT] library_index.json not found at {_INDEX_PATH}. "
                    "All lookups will return None."
                )
                return
            with open(_INDEX_PATH, "r", encoding="utf-8") as f:
                self._index = json.load(f)
            # Remove _meta key — it's not a topic
            self._index.pop("_meta", None)
            logger.info(
                f"[FIRST_SHOT] Library loaded: {list(self._index.keys())}"
            )
        except Exception as e:
            logger.warning(f"[FIRST_SHOT] Failed to load library index (non-fatal): {e}")

    def add_new_placeholder(self, category_key: str, product_name: str) -> None:
        """Dynamically adds a new placeholder to library_index.json and logs a loud warning."""
        if category_key in self._index:
            return

        new_entry = {
            "description": f"Auto-detected new fashion item: {product_name}",
            "keywords": [product_name.lower()],
            "character": "Priyanshi",
            "script": f"When you don't know what to wear, get yourself a {product_name}.",
            "clips": [],
            "is_placeholder": True
        }
        self._index[category_key] = new_entry

        try:
            with open(_INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            data[category_key] = new_entry
            with open(_INDEX_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            
            alert_msg = (
                f"\U0001f6a8 <b>[FIRST SHOT — NEW FASHION]</b>\n\n"
                f"Visual AI detected a <b>new garment</b> not in your library:\n"
                f"\u25b6 <b>{product_name}</b> (key: <code>{category_key}</code>)\n\n"
                f"\u2714 Placeholder added to <code>library_index.json</code>.\n"
                f"\u26a0\ufe0f <b>Action Required:</b> Generate this HeyGen video clip &amp; add its .mp4 path!"
            )
            logger.warning(
                f"\U0001f6a8 [NEW FASHION DETECTED] Added '{product_name}' to library_index.json under '{category_key}'.\n"
                f"   Please generate the AI clip for this new product in HeyGen!"
            )
            _send_owner_alert(alert_msg)
        except Exception as e:
            logger.error(f"[FIRST_SHOT] Failed to save new placeholder: {e}")

    def get_clip(self, topic: str) -> Optional[str]:
        """
        Returns an absolute path to a real (non-placeholder) clip for the topic.
        Falls back to generic_fashion if the primary topic has no clips.
        Returns None if nothing is available.
        """
        candidates = [topic]
        if topic != _FALLBACK_TOPIC:
            candidates.append(_FALLBACK_TOPIC)

        for t in candidates:
            path = self._resolve_clip(t)
            if path:
                if t != topic:
                    logger.info(
                        f"[FIRST_SHOT] Topic '{topic}' has no clips — "
                        f"falling back to '{t}'"
                    )
                return path

        logger.warning(
            f"[FIRST_SHOT] No real clips for '{topic}' or fallback. "
            "First shot will be skipped."
        )
        return None

    def _resolve_clip(self, topic: str) -> Optional[str]:
        entry = self._index.get(topic)
        if not entry:
            return None
        if entry.get("is_placeholder", True):
            return None

        clips = entry.get("clips", [])
        if not clips:
            return None

        random.shuffle(clips)   # Variety — different clip each time

        for clip_name in clips:
            if "/" in clip_name or "\\" in clip_name:
                full_path = os.path.normpath(os.path.join(_LIBRARY_DIR, clip_name))
            else:
                full_path = os.path.normpath(
                    os.path.join(_LIBRARY_DIR, topic, clip_name)
                )
            if os.path.isfile(full_path):
                logger.debug(f"[FIRST_SHOT] Resolved: {full_path}")
                return full_path
            else:
                alert_msg = (
                    f"\U0001f6a8 <b>[FIRST SHOT — MISSING VIDEO FILE]</b>\n\n"
                    f"Category <b>{topic}</b> is mapped in your library, but the .mp4 file is missing:\n"
                    f"\u25b6 <code>{clip_name}</code>\n\n"
                    f"\u26a0\ufe0f <b>Action Required:</b> Generate this HeyGen video &amp; place it in <code>First_Shots/Priyanshi/</code>\n"
                    f"This video will play WITHOUT an AI intro until you fix this."
                )
                logger.warning(
                    f"\U0001f6a8 [MISSING INTRO VIDEO] The library_index.json expects '{clip_name}' "
                    f"for the '{topic}' category, but the .mp4 file is missing from the folder! "
                    f"Skipping intro. Please generate this video in HeyGen!"
                )
                _send_owner_alert(alert_msg)

        return None

    def get_all_topics(self) -> Dict[str, Any]:
        """Returns all topic entries from the index."""
        return self._index

    def get_keywords(self, topic: str) -> list:
        """Returns keyword list for a topic."""
        return self._index.get(topic, {}).get("keywords", [])

    def is_ready(self) -> bool:
        """Returns True if at least generic_fashion has a real clip."""
        return bool(self._resolve_clip(_FALLBACK_TOPIC))

    def get_general_intro(self) -> Optional[str]:
        """
        Scans the First_Shots/general_intro/ folder for any .mp4 files and returns one at random.
        Creates the folder if it doesn't exist. Returns None if no files are found.
        """
        general_dir = os.path.join(_LIBRARY_DIR, "general_intro")
        os.makedirs(general_dir, exist_ok=True)
        try:
            clips = [f for f in os.listdir(general_dir) if f.lower().endswith(".mp4")]
            if not clips:
                return None
            selected = random.choice(clips)
            full_path = os.path.normpath(os.path.join(general_dir, selected))
            logger.debug(f"[FIRST_SHOT] General fallback resolved: {full_path}")
            return full_path
        except Exception as e:
            logger.warning(f"[FIRST_SHOT] Failed to read general_intro folder: {e}")
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2: Topic Selector (Gemini + Keyword Fallback)
# ═══════════════════════════════════════════════════════════════════════════════

class FirstShotSelector:
    """
    Directly extracts the exact intro category (genre) and wear_name from 
    the visual data provided by fashion_scout.py. If that data is missing, 
    falls back to title/description text-guessing logic.
    """

    def __init__(self, library: FirstShotLibrary):
        self._library = library
        self._router = None
        try:
            from Intelligence_Modules.gemini_governor import gemini_router
            self._router = gemini_router
        except Exception:
            logger.warning("[FIRST_SHOT] gemini_router unavailable — keyword matching only.")


    def select_topic(self, profile_data: dict, title: str = "", description: str = "") -> str:
        """
        Returns the best matching topic key directly from fashion_scout.
        Handles 'NEW' genres by adding them to the JSON.
        Falls back to Gemini/Keyword if fashion_scout data is missing.
        """
        mon_data = profile_data.get("monetization_data", {})
        fs_data = mon_data.get("fashion_scout", {})
        first_shot_data = fs_data.get("first_shot", {})

        genre = first_shot_data.get("genre", "").strip()
        wear_name = first_shot_data.get("wear_name", "").strip()

        if genre:
            # Handle dynamic new fashion detection from visual analysis
            if genre.upper().startswith("NEW"):
                if wear_name:
                    # Generate a clean category key
                    cat_key = f"new_wear_{wear_name.replace(' ', '_').replace('-', '_').lower()}"
                    self._library.add_new_placeholder(cat_key, wear_name.lower())
                return "SKIP_NEW"

            if genre in self._library.get_all_topics() and genre != _FALLBACK_TOPIC:
                # Validate that the wear_name actually makes sense for this genre
                if wear_name:
                    keywords = self._library.get_keywords(genre)
                    match_found = any(
                        kw.lower() in wear_name.lower() or wear_name.lower() in kw.lower()
                        for kw in keywords
                    )
                    if not match_found:
                        logger.warning(
                            f"[FIRST_SHOT] Visual genre '{genre}' chosen, but wear_name '{wear_name}' "
                            f"has no keyword overlap. Rejecting hallucinated match and treating as NEW."
                        )
                        cat_key = f"new_wear_{wear_name.replace(' ', '_').replace('-', '_').lower()}"
                        self._library.add_new_placeholder(cat_key, wear_name.lower())
                        return "SKIP_NEW"

                logger.info(f"[FIRST_SHOT] Visual matching selected topic: '{genre}'")
                return genre
            elif genre == _FALLBACK_TOPIC:
                return genre

        logger.info("[FIRST_SHOT] No first_shot_genre provided by fashion_scout. Falling back to title guessing.")
        
        text = f"{title} {description}".strip().lower()
        if not text:
            return _FALLBACK_TOPIC

        # Try Gemini first
        if self._router:
            try:
                topic = self._gemini_match(text)
                if topic == "SKIP_NEW":
                    return "SKIP_NEW"
                if topic and topic in self._library.get_all_topics():
                    if topic != _FALLBACK_TOPIC:
                        keywords = self._library.get_keywords(topic)
                        match_found = any(kw.lower() in text for kw in keywords)
                        if not match_found:
                            logger.warning(
                                f"[FIRST_SHOT] Gemini topic '{topic}' chosen, but no keywords found in text. "
                                f"Rejecting forced match."
                            )
                            # Fall through to keyword match
                            topic = None
                    
                    if topic:
                        logger.info(f"[FIRST_SHOT] Gemini selected topic: '{topic}'")
                        return topic
            except Exception as e:
                logger.warning(f"[FIRST_SHOT] Gemini topic match failed (non-fatal): {e}")

        # Keyword fallback
        return self._keyword_match(text)

    def _gemini_match(self, text: str) -> Optional[str]:
        """Text-only Gemini call — returns a topic key or None."""
        topics = self._library.get_all_topics()
        topic_descriptions = "\n".join(
            f"- {k} (Script: {v.get('script', '')}): {v.get('description', '')}"
            for k, v in topics.items()
        )
        prompt = (
            f"You are a fashion content categorizer.\n\n"
            f"Available categories and their exact products:\n{topic_descriptions}\n\n"
            f"Video title and description: \"{text}\"\n\n"
            f"Task:\n"
            f"1. If the video clearly features one of the exact products in the categories above, return ONLY that category key.\n"
            f"2. If the video features a distinct fashion product NOT covered by the existing products (e.g., a specific type of dress, jacket, pants), return a string in this exact format: NEW|<category_key>|<product_name>\n"
            f"   (For <category_key>, use an existing vibe appended with the product slug, e.g., 'party_evening_velvet_dress', or a completely new one like 'winter_wear_puffer_jacket').\n\n"
            f"Return ONLY the requested string, no explanation, no punctuation."
        )

        start = time.time()
        response = self._router.generate(
            task_type="text_light",
            prompt=prompt,
            module_name="first_shot_selector",
        )
        elapsed = round(time.time() - start, 2)

        if response:
            raw = str(response).strip().replace('"', "").replace("'", "")
            
            # Handle dynamic new fashion detection
            if raw.upper().startswith("NEW|"):
                parts = raw.split("|")
                if len(parts) >= 3:
                    cat_key = parts[1].strip().lower().replace(" ", "_")
                    prod_name = parts[2].strip().lower()
                    self._library.add_new_placeholder(cat_key, prod_name)
                    return "SKIP_NEW"

            # Clean up any extra words Gemini may have returned for existing matches
            for key in self._library.get_all_topics():
                if key in raw.lower():
                    logger.debug(
                        f"[FIRST_SHOT] Gemini match: '{key}' ({elapsed}s)"
                    )
                    return key
        return None

    def _keyword_match(self, text: str) -> str:
        """
        Score each topic's keywords against the video text.
        Returns the highest scoring topic, or generic_fashion.
        """
        best_topic = _FALLBACK_TOPIC
        best_score = 0

        for topic, entry in self._library.get_all_topics().items():
            score = 0
            for kw in entry.get("keywords", []):
                if kw.lower() in text:
                    score += 1
            if score > best_score:
                best_score = score
                best_topic = topic

        logger.info(
            f"[FIRST_SHOT] Keyword match: '{best_topic}' "
            f"(score={best_score})"
        )
        if best_score == 0 and best_topic == _FALLBACK_TOPIC:
            logger.warning(
                f"[FIRST_SHOT] No keyword match found — video does not match any known genre. "
                f"Falling back to generic_fashion. If this is a new garment type, consider adding it to library_index.json."
            )
        return best_topic


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3: FFmpeg Normalizer
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_intro(clip_path: str, output_path: str) -> Optional[str]:
    """
    Normalizes the selected intro clip to 1080×1920 @ 30fps.
    Preserves the original audio track (ElevenLabs voice).
    Trims to _MAX_INTRO_DURATION if clip is too long.
    Returns output_path on success, None on failure.
    """
    try:
        # Probe the clip first
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "json", clip_path,
        ]
        probe_result = subprocess.run(
            probe_cmd, capture_output=True, text=True, timeout=10
        )
        probe_data = json.loads(probe_result.stdout)
        streams = probe_data.get("streams", [{}])
        clip_duration = float(streams[0].get("duration", 5.0)) if streams else 5.0

        # Trim duration if over cap
        trim_filter = (
            f"trim=duration={_MAX_INTRO_DURATION},"
            if clip_duration > _MAX_INTRO_DURATION
            else ""
        )
        atrim_filter = (
            f"atrim=duration={_MAX_INTRO_DURATION},"
            if clip_duration > _MAX_INTRO_DURATION
            else ""
        )

        # Build scale+pad filter to hit exactly 1080×1920 without distortion
        scale_pad = (
            f"scale={_TARGET_W}:{_TARGET_H}:force_original_aspect_ratio=decrease,"
            f"pad={_TARGET_W}:{_TARGET_H}:(ow-iw)/2:(oh-ih)/2:black"
        )

        # Full filter_complex for intro normalization
        vf = f"{trim_filter}{scale_pad},fps={_TARGET_FPS},format=yuv420p"
        af = f"{atrim_filter}aresample=44100"

        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "auto",
            "-i", clip_path,
            "-vf", vf,
            "-af", af,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.info(
            f"[FIRST_SHOT] Normalizing intro: {os.path.basename(clip_path)} → "
            f"{_TARGET_W}×{_TARGET_H} @ {_TARGET_FPS}fps"
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_NORMALIZE_TIMEOUT,
        )

        if result.returncode != 0:
            logger.warning(
                f"[FIRST_SHOT] FFmpeg normalize failed: {result.stderr[-300:]}"
            )
            return None

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            logger.warning("[FIRST_SHOT] Normalized file missing or empty.")
            return None

        logger.info(
            f"✅ [FIRST_SHOT] Intro normalized: {os.path.basename(output_path)} "
            f"({os.path.getsize(output_path) // 1024}KB)"
        )
        return output_path

    except subprocess.TimeoutExpired:
        logger.warning("[FIRST_SHOT] Normalize timed out.")
        return None
    except Exception as e:
        logger.warning(f"[FIRST_SHOT] Normalize error (non-fatal): {e}")
        return None


def get_clip_duration(clip_path: str) -> float:
    """Probe and return the duration of a clip in seconds. Returns 0.0 on error."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=duration",
            "-of", "json", clip_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
        streams = data.get("streams", [{}])
        return float(streams[0].get("duration", 0.0)) if streams else 0.0
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4: Public Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def run_first_shot_engine(
    profile_data: dict,
    job_dir: str,
    title: str = "",
    description: str = "",
) -> Optional[str]:
    """
    Main entry point — called from orchestrator.py Step 3 (after fashion_scout).

    Returns the absolute path to the normalized intro clip on success.
    Returns None on any failure — the main pipeline is NOT affected.

    Side effects on success:
      - profile_data["first_shot_topic"]    → matched topic key
      - profile_data["first_shot_clip"]     → source clip path (pre-normalize)
      - profile_data["first_shot_duration"] → intro duration in seconds
      - profile_data["first_shot_active"]   → True
    """
    # ── Master switch check ────────────────────────────────────────────────────
    if os.getenv("ENABLE_FIRST_SHOT", "no").strip().lower() != "yes":
        return None

    try:
        _t0 = time.time()

        # ── 1. Load library ────────────────────────────────────────────────────
        library = FirstShotLibrary()
        if not library.is_ready():
            logger.warning(
                "[FIRST_SHOT] Library not ready "
                "(generic_fashion has no clips). Skipping."
            )
            return None

        # ── 2. Select topic ────────────────────────────────────────────────────
        selector = FirstShotSelector(library)
        topic = selector.select_topic(profile_data, title, description)
        
        if topic == "SKIP_NEW":
            alert_msg = (
                f"\U0001f6a8 <b>[FIRST SHOT — NEW ITEM SKIPPED]</b>\n\n"
                f"A <b>new fashion item</b> was visually detected that doesn't match any intro in your library.\n"
                f"Video title: <b>{title or 'Unknown'}</b>\n\n"
                f"\u2714 Placeholder added to <code>library_index.json</code>.\n"
                f"\u26a0\ufe0f A <b>general fallback intro</b> will be used if available. Generate the specific HeyGen clip to fix this!"
            )
            _send_owner_alert(alert_msg)
            
            source_clip = library.get_general_intro()
            if not source_clip:
                logger.info(
                    "[FIRST_SHOT] Skipping intro for this video because a NEW fashion item was detected and no general fallback clip was found. "
                    "Check library_index.json to see the new placeholder."
                )
                return None
            
            logger.info("[FIRST_SHOT] NEW fashion item detected. Using general intro as fallback.")
            topic = "general_fallback"
        else:
            logger.info(f"[FIRST_SHOT] Final topic selected: '{topic}'")
    
            # ── 3. Resolve source clip ─────────────────────────────────────────────
            source_clip = library.get_clip(topic)
            if not source_clip:
                logger.warning(
                    f"[FIRST_SHOT] No clip resolved for topic '{topic}'. Attempting general fallback."
                )
                source_clip = library.get_general_intro()
                if not source_clip:
                    logger.warning("[FIRST_SHOT] No general fallback clip available. Skipping.")
                    return None
                logger.info(f"[FIRST_SHOT] Using general intro fallback for missing topic '{topic}'.")
                topic = "general_fallback"

        # ── 4. Normalize intro clip ────────────────────────────────────────────
        normalized_name = f"first_shot_{topic}_{os.path.basename(source_clip)}"
        normalized_path = os.path.join(job_dir, normalized_name)

        # Re-use cached normalized clip within the same job
        if os.path.exists(normalized_path) and os.path.getsize(normalized_path) > 1000:
            logger.info(
                f"[FIRST_SHOT] Using cached normalized intro: {normalized_name}"
            )
            final_path = normalized_path
        else:
            final_path = normalize_intro(source_clip, normalized_path)

        if not final_path:
            return None

        # ── 5. Measure intro duration ──────────────────────────────────────────
        intro_dur = get_clip_duration(final_path)
        if intro_dur <= 0.2:
            logger.warning(
                f"[FIRST_SHOT] Intro duration too short ({intro_dur}s). Skipping."
            )
            return None

        # ── 6. Store metadata in profile_data ─────────────────────────────────
        profile_data["first_shot_topic"] = topic
        # Store the NORMALIZED copy path (in job_dir), never the original source.
        # This ensures no downstream pipeline step can overwrite the First_Shots library file.
        profile_data["first_shot_clip"] = final_path
        profile_data["first_shot_source_clip"] = source_clip  # Original source (read-only ref)
        profile_data["first_shot_duration"] = round(intro_dur, 3)
        profile_data["first_shot_active"] = True

        elapsed = round(time.time() - _t0, 2)
        logger.info(
            f"🎬 [FIRST_SHOT] Engine complete in {elapsed}s | "
            f"topic={topic} | dur={intro_dur:.2f}s | "
            f"clip={os.path.basename(final_path)}"
        )
        return final_path

    except Exception as e:
        logger.warning(f"⚠️ [FIRST_SHOT] Engine error (non-fatal): {e}")
        return None
