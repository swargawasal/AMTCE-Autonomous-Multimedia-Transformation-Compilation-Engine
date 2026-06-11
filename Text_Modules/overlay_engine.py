"""
Overlay Text Engine
--------------------
Generates short, punchy on-screen text (max 4 words) with memory-based
similarity guards so overlays stay fresh and independent from captions
or narration outputs.

Also provides select_viral_hook() which intelligently picks a persuasive
Hindi/Hinglish hook from the VIRAL_HOOKS pool based on visual context
(actress name, niche category, content mood).  These hooks are placed as
text overlays in the same position as the fashion-scout caption lane.
"""

import json
import logging
import os
import random
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional

logger = logging.getLogger("overlay_engine")

# ─────────────────────────────────────────────────────────────────────────────
# VIRAL HOOK POOL
# Persuasive Hinglish hooks for engagement-bait overlays.
# Placeholders:
#   {name}  → actress / user title resolved from context (falls back to "Bhai")
# ─────────────────────────────────────────────────────────────────────────────
VIRAL_HOOKS: List[str] = [
    "Bhai tu bas pin comment dekh",
    "Save krle rat ko manna 🥵",
    "Bhai tu bas Pin Comment Dekh 🤪",
    "Kaun kaun aise Ride kiya hai 😜",
    "Aisi maal 910 me hai 🥵",
    "kya Seen Hai yaar..",
    "Agar tumhe ek din ke liye ye mil jaye",
    "To kaun kaun si po$ition try kroge 😁",
    "Aisi Biwi To Tu Bhi Deserve Krta Hai 😍",
    "{name} Expression 😍",
    "Kya Krne Ki Bat Kar Rahe Hai 😁",
    "Ek Din Ke Liye {name} Mil Jaye To Kya Kroge 🥵",
    "Battery charge kar rha hai 🥵",
    "Le Bhai Mood Bana Le 🥵",
    "Uff {name} 🥵",
    "Save kar le Raat ko kam aayega 🥵",
    "Kaun kaun aise Ride kiya hai 😍",
    "Save kr le rat ko marna 😍",
    "Asli maal B!O me hai 🥵",
    "Bhai tu bas Pin Comment Dekh 🥵",
]

# ─────────────────────────────────────────────────────────────────────────────
# HOOK SELECTION RULES
# Maps content signals → preferred hook indices (soft hints, not hard locks)
# ─────────────────────────────────────────────────────────────────────────────
_HOOK_RULES: Dict[str, List[int]] = {
    # When actress/title name is known — prefer hooks with {name} placeholder
    "has_name":      [9, 11, 14],
    # Generic / no name — prefer nameless hooks
    "no_name":       [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 15, 16, 17, 18, 19],
    # Energetic / party vibe
    "energetic":     [3, 6, 7, 11, 12],
    # Romantic / soft vibe
    "romantic":      [8, 9, 10, 14],
    # Curiosity / tease
    "curiosity":     [0, 1, 2, 18, 19],
}

_VIRAL_HOOK_MEMORY_PATH = "The_json/viral_hook_memory.json"
_VIRAL_HOOK_MAX_MEMORY = 50

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




__all__ = ["generate_overlay_text", "select_viral_hook", "VIRAL_HOOKS"]


# ─────────────────────────────────────────────────────────────────────────────
# VIRAL HOOK SELECTOR
# ─────────────────────────────────────────────────────────────────────────────

def _load_viral_hook_memory() -> List[str]:
    """Load recently used viral hook texts to avoid repetition."""
    if not os.path.exists(_VIRAL_HOOK_MEMORY_PATH):
        return []
    try:
        with open(_VIRAL_HOOK_MEMORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data][-_VIRAL_HOOK_MAX_MEMORY:]
    except Exception:
        pass
    return []


def _save_viral_hook_memory(memory: List[str]) -> None:
    try:
        os.makedirs(os.path.dirname(_VIRAL_HOOK_MEMORY_PATH), exist_ok=True)
        with open(_VIRAL_HOOK_MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(memory[-_VIRAL_HOOK_MAX_MEMORY:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[VIRAL_HOOK] memory_save_failed: {e}")


def select_viral_hook(context: Optional[Dict] = None) -> str:
    """
    Intelligently select a viral Hinglish hook based on visual context.

    Context keys used:
        title          (str) : Video title — used to extract actress/subject name
        actress_name   (str) : Detected actress name (highest priority for name slot)
        niche_category (str) : Content niche, e.g. "fashion", "entertainment", "adult"
        energy_score   (float): Visual energy 0.0–1.0 from editing plan
        mood           (str) : "romantic" | "energetic" | "funny" | "curiosity"

    Returns:
        The selected hook string with {name} placeholder resolved.
    """
    ctx = context or {}
    memory = _load_viral_hook_memory()

    # ── 1. Resolve subject name ────────────────────────────────────────────
    name = (
        ctx.get("actress_name")
        or ctx.get("user_title")
        or ctx.get("title", "")
    )
    # Strip system prefixes and take first meaningful word group (≤3 words)
    if name:
        name = re.sub(
            r"(?i)^(?:viral|fashion|entertainment|nsfw|adult|paparazzi|general|process|cli)[\s:]+",
            "", name
        ).strip()
        # Remove file-name style underscores/dashes
        name = re.sub(r"[_\-]+", " ", name).strip()
        # Keep max 3 words
        words = name.split()
        name = " ".join(words[:3]).strip(" '\",.")

    has_name = bool(name and len(name) > 2)

    # ── 2. Resolve mood / vibe ─────────────────────────────────────────────
    mood = ctx.get("mood", "")
    energy_raw = ctx.get("energy_score", 0.5)
    try:
        energy = float(energy_raw) if energy_raw is not None else 0.5
    except (TypeError, ValueError):
        energy = 0.5
    niche = str(ctx.get("niche_category", "")).lower()

    if not mood:
        if energy >= 0.70 or "party" in niche or "dance" in niche:
            mood = "energetic"
        elif "romantic" in niche or energy < 0.35:
            mood = "romantic"
        else:
            mood = "curiosity"

    # ── 3. Build candidate pool using rules ───────────────────────────────
    candidate_indices: List[int] = []

    if has_name:
        candidate_indices.extend(_HOOK_RULES["has_name"])
    else:
        candidate_indices.extend(_HOOK_RULES["no_name"])

    mood_rule = _HOOK_RULES.get(mood, [])
    # Intersect mood preferences with name-availability pool (soft preference)
    mood_candidates = [i for i in mood_rule if i in candidate_indices]
    if mood_candidates:
        candidate_indices = mood_candidates + candidate_indices  # Prefer mood matches

    # Deduplicate while preserving order
    seen: set = set()
    ordered: List[int] = []
    for idx in candidate_indices:
        if idx not in seen:
            seen.add(idx)
            ordered.append(idx)

    # ── 4. Pick first hook not in recent memory ───────────────────────────
    selected_raw: str = ""
    for idx in ordered:
        hook = VIRAL_HOOKS[idx]
        resolved = hook.replace("{name}", name) if has_name else hook.replace("{name}", "Bhai")
        if not any(_similarity(resolved, prev) > 0.75 for prev in memory):
            selected_raw = resolved
            break

    # Last resort: any random hook
    if not selected_raw:
        hook = random.choice(VIRAL_HOOKS)
        selected_raw = hook.replace("{name}", name) if has_name else hook.replace("{name}", "Bhai")

    # ── 5. Save to memory ─────────────────────────────────────────────────
    memory.append(selected_raw)
    _save_viral_hook_memory(memory)
    logger.info(f"[VIRAL_HOOK] selected=\"{selected_raw}\" mood={mood} has_name={has_name}")
    return selected_raw


def _similarity(a: str, b: str) -> float:
    """Compute fuzzy string similarity (0.0 – 1.0)."""
    from difflib import SequenceMatcher as _SM
    return _SM(None, a.lower(), b.lower()).ratio()
