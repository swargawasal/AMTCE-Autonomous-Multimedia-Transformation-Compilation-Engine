"""
Overlay Text Engine
--------------------
Generates short, punchy on-screen text (max 4 words) with memory-based
similarity guards so overlays stay fresh and independent from captions
or narration outputs.
"""

import json
import logging
import os
import random
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional

logger = logging.getLogger("overlay_engine")

MEMORY_PATH = "The_json/overlay_memory.json"
MAX_MEMORY = 100
SIMILARITY_THRESHOLD = 0.8

NEGATIVE_WORDS_FALLBACK = [
    "effortless",
    "stunning",
    "beautiful",
    "chic",
    "elegant",
    "sexy",
    "hot",
    "camera",
    "video",
    "clip",
    "shows",
]

OVERLAY_POOLS: Dict[str, List[str]] = {
    "attitude": [
        "Own the moment",
        "Main character stance",
        "Command the room",
        "Lead with presence",
        "Move like you mean it",
    ],
    "luxury": [
        "Quiet power",
        "Calm authority",
        "Understated shine",
        "Soft luxe",
        "Rare air energy",
    ],
    "minimal": [
        "Less noise",
        "Clean lines only",
        "Signal over noise",
        "Sharp and simple",
        "More presence",
    ],
    "statement": [
        "Icon in motion",
        "Own your frame",
        "Nothing accidental",
        "Signature move",
        "Attention follows",
    ],
}


def _load_negative_words() -> List[str]:
    cfg_path = "The_json/caption_prompt.json"
    try:
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("NEGATIVE_WORDS", []) or NEGATIVE_WORDS_FALLBACK
    except Exception:
        pass
    return NEGATIVE_WORDS_FALLBACK


NEGATIVE_PATTERNS = [re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE) for w in _load_negative_words()]


def _load_memory() -> List[str]:
    if not os.path.exists(MEMORY_PATH):
        return []
    try:
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data][-MAX_MEMORY:]
    except Exception as e:
        logger.warning(f"[OVERLAY_ENGINE] memory_load_failed: {e}")
    return []


def _save_memory(memory: List[str]) -> None:
    try:
        os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(memory[-MAX_MEMORY:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[OVERLAY_ENGINE] memory_save_failed: {e}")


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _too_similar(text: str, memory: List[str]) -> bool:
    return any(_similarity(text, prev) > SIMILARITY_THRESHOLD for prev in memory)


def _contains_negative(text: str) -> bool:
    lowered = text.lower()
    return any(p.search(lowered) for p in NEGATIVE_PATTERNS)


def _pick_theme(context: Optional[Dict]) -> str:
    ctx = context or {}
    for key in ("style_category", "persona", "vibe", "tone"):
        val = ctx.get(key)
        if isinstance(val, str) and val:
            return val.lower()
    return "attitude"


def _get_pool(theme: str) -> List[str]:
    if theme in OVERLAY_POOLS:
        return OVERLAY_POOLS[theme][:]
    # Map fuzzy themes to known buckets
    if "lux" in theme:
        return OVERLAY_POOLS["luxury"][:]
    if "minimal" in theme or "clean" in theme:
        return OVERLAY_POOLS["minimal"][:]
    if "statement" in theme:
        return OVERLAY_POOLS["statement"][:]
    return OVERLAY_POOLS["attitude"][:]


def _trim_to_four_words(text: str) -> str:
    words = text.split()
    return " ".join(words[:4]).strip()


def generate_overlay_text(context: Optional[Dict] = None) -> str:
    """
    Generate short overlay text independent of captions/narration.
    - Max 4 words
    - Avoid NEGATIVE_WORDS
    - Reject similarity > 0.8 against last 100 overlays
    """
    memory = _load_memory()
    theme = _pick_theme(context)
    pool = _get_pool(theme)
    
    candidate = None

    # 1. Use Stored Overlay Pools (No API Call)

    # 2. Fallback to Stored Pools
    if not candidate:
        logger.info("⚠️ Falling back to stored overlay pool.")
        random.shuffle(pool)
        for phrase in pool + OVERLAY_POOLS.get("attitude", []):
            text = _trim_to_four_words(phrase)
            if not text or _contains_negative(text) or _too_similar(text, memory):
                continue
            candidate = text
            break

    # 3. Last Resort
    if not candidate:
        candidate = "Own the moment"

    memory.append(candidate)
    memory = memory[-MAX_MEMORY:]
    _save_memory(memory)
    logger.info(f"[OVERLAY_ENGINE] overlay_generated=\"{candidate}\"")
    return candidate


__all__ = ["generate_overlay_text"]
