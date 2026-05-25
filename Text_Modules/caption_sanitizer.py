import json
import os
import re
import logging
from typing import List

try:
    from Intelligence_Modules.caption_memory import memory as caption_memory
except Exception:
    caption_memory = None

logger = logging.getLogger("caption_sanitizer")

CONFIG_PATH = "The_json/caption_prompt.json"
CACHE_PATH = "The_json/captions_cache.json"
STATE_PATH = "The_json/caption_state.json"

def get_fallback():
    """
    Selects a fallback caption from captions_cache.json using round-robin.
    Maintains state in caption_state.json.
    """
    selected = "Style Analysis" # Ultimate fallback
    try:
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                captions = json.load(f)
            
            if captions and isinstance(captions, list):
                # Load or init index
                idx = 0
                if os.path.exists(STATE_PATH):
                    try:
                        with open(STATE_PATH, "r", encoding="utf-8") as sf:
                            state = json.load(sf)
                            idx = state.get("fallback_index", 0)
                    except: pass
                
                selected = captions[idx % len(captions)]
                
                # Update index
                new_idx = (idx + 1) % len(captions)
                try:
                    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
                    with open(STATE_PATH, "w", encoding="utf-8") as sf:
                        json.dump({"fallback_index": new_idx}, sf)
                except: pass
                
                logger.info(f"🛡️ [SANITIZER] Used cache fallback (idx {idx}): {selected}")
                return selected
    except Exception as e:
        logger.warning(f"⚠️ [SANITIZER] Fallback system error: {e}")
        
    return selected



def _load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


_config = _load_config()
NEGATIVE_WORDS: List[str] = _config.get("NEGATIVE_WORDS", [])
FILLER_WORDS: List[str] = _config.get("FILLER_WORDS", [])
PRIORITY_WORDS: List[str] = _config.get("PRIORITY_WORDS", [])

_NEG_PATTERNS = [
    re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE) for w in NEGATIVE_WORDS
]


def _contains_blacklisted(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _NEG_PATTERNS)


def _compress(text: str, target_max: int = 4, hard_max: int = 6) -> str:
    if not text:
        return ""
    words = text.split()
    # remove fillers
    filtered = [w for w in words if w.lower() not in [fw.lower() for fw in FILLER_WORDS]]
    if len(filtered) <= hard_max:
        return " ".join(filtered[:target_max or hard_max]).strip()
    # keep priority words first
    ranked = sorted(
        enumerate(filtered),
        key=lambda x: (filtered[x[0]].lower() in [pw.lower() for pw in PRIORITY_WORDS], -x[0]),
        reverse=True,
    )
    keep_indices = sorted([idx for idx, _ in ranked[:hard_max]])
    kept = [filtered[i] for i in keep_indices]
    return " ".join(kept[:target_max]).strip()


def sanitize_caption_text(text: str, target_max: int = 4, hard_max: int = 6) -> str:
    """
    Shared caption sanitizer for all visible text (captions, overlay labels, content director outputs).
    - Drops/rewrites if blacklisted.
    - Compresses to headline length.
    - Similarity guard via caption_memory.
    """
    if not text:
        return get_fallback()

    banned_hit = False
    clean = text.strip().replace("\n", " ")


    # [mkpv-fix] Global Safety Filter: Ensure internal error strings never reach the user
    if "safety_block" in clean.lower() or "error:" in clean.lower():
         logger.warning(f"🛡️ [SANITIZER] internal_error_leakage detected in: '{clean}'. Forcing fallback.")
         clean = get_fallback()
         banned_hit = True

    if _contains_blacklisted(clean):
        # fallback to safe default headline
        clean = get_fallback()
        banned_hit = True

    # compress length
    compressed = _compress(clean, target_max=target_max, hard_max=hard_max)

    # enforce min 2 words when possible
    words = compressed.split()
    if len(words) < 2:
        compressed = get_fallback()

    # similarity guard
    if caption_memory and caption_memory.is_too_similar(compressed):
        logger.warning("[CAPTION_SANITIZER] similarity_reject=True caption=\"%s\"", compressed)
        compressed = get_fallback()

    if banned_hit:
        logger.warning("[CAPTION_SANITIZER] banned_word_detected=True original=\"%s\" -> \"%s\"", text, compressed)

    return compressed
