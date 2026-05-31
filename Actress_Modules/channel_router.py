"""
channel_router.py — AMTCE Paparazzi Channel Router
====================================================
Determines which destination channel a scraped reel should go to,
based on identity lookup in paparazzi_identities.json + gender heuristics.

Public API:
  resolve_channel(ig_id, reel)  → (folder: str, title: str, is_nsfw: bool)
  get_source_accounts()         → list of source Instagram account IDs to scrape
  detect_gender_from_name(name) → "female" | "male" | "unknown"

Channel Map:
  General_Fallback   → Women-identified content + NSFW
  Paparazzi_Channel  → All other content (men + unknown)
  Fashion_Style      → Manual input only (never auto-routed here)

Cross-channel deduplication is enforced at the ledger level.
"""

import os
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
_IDENTITIES_PATH = os.path.join(_BASE_DIR, "actress_accounts.json")

# ── Channel constants ─────────────────────────────────────────────────────────
CHANNEL_WOMEN     = "General_Fallback"   # Women content → General_Fallback (mixed channel)
CHANNEL_ALL       = "General_Fallback"   # Men/unknown → also General_Fallback (mixed paparazzi style)
CHANNEL_PAPARAZZI = "Paparazzi"         # Future: separate Paparazzi account (activates when META_CONFIG_JSON_PAPARAZZI secret is added)
CHANNEL_FASHION   = "Fashion_Style"     # Pure women/fashion — manual input or Fashion_XX accounts only
CHANNEL_MANUAL    = "Fashion_Style"     # Manual input only — never auto-routed

# ── Runtime: detect if a Paparazzi credential folder exists ──────────────────
def _paparazzi_creds_exist() -> bool:
    """Returns True if Credentials/social_media/Paparazzi/ folder has files.
    When True, men/unknown content routes to Paparazzi instead of General_Fallback."""
    import os
    base = os.path.join("Credentials", "social_media", "Paparazzi")
    if not os.path.isdir(base):
        return False
    return any(
        os.path.isfile(os.path.join(base, f))
        for f in os.listdir(base)
    )

# ── In-memory cache ───────────────────────────────────────────────────────────
_identities_cache: Optional[Dict] = None


def _load_identities(force: bool = False) -> Dict:
    """Load actress_accounts.json. Caches in memory."""
    global _identities_cache
    if _identities_cache is None or force:
        try:
            with open(_IDENTITIES_PATH, "r", encoding="utf-8") as f:
                raw_cfg = json.load(f)
            
            paparazzi_block = raw_cfg.get("_paparazzi", {})
            
            # Dynamically build women_map by scanning primary, secondary, nsfw
            women_map = {}
            for tier in ["primary", "secondary", "nsfw"]:
                if tier in raw_cfg:
                    for name, entry in raw_cfg[tier].items():
                        if not name.startswith("_") and isinstance(entry, dict):
                            ig_id = entry.get("id", "").lower()
                            if ig_id:
                                women_map[name] = ig_id

            # We don't really have a 'men' block in actress_accounts yet
            men_map = {}

            _identities_cache = {
                "source_accounts": paparazzi_block.get("source_accounts", []),
                "nsfw_accounts": paparazzi_block.get("nsfw_accounts", []),
                "female_name_tokens": paparazzi_block.get("female_name_tokens", []),
                "male_name_tokens": paparazzi_block.get("male_name_tokens", []),
                "women": women_map,
                "men": men_map
            }
            logger.info(
                "📋 Paparazzi identities loaded — %d women, %d men, %d source accounts",
                len(women_map), len(men_map), len(_identities_cache["source_accounts"])
            )
        except Exception as exc:
            logger.error("💥 Failed to load actress_accounts.json: %s", exc)
            _identities_cache = {
                "source_accounts": [],
                "women": {},
                "men": {},
                "nsfw_accounts": [],
                "female_name_tokens": [],
                "male_name_tokens": [],
            }
    return _identities_cache


# ── Public API ─────────────────────────────────────────────────────────────────

def get_source_accounts() -> List[str]:
    """
    Returns the list of paparazzi Instagram account IDs to scrape from.
    Reads from paparazzi_identities.json['source_accounts'].
    Also reads PAPARAZZI_SOURCE_ACCOUNTS env var (comma-separated) as override.
    """
    env_override = os.getenv("PAPARAZZI_SOURCE_ACCOUNTS", "").strip()
    if env_override:
        accounts = [a.strip().lstrip("@") for a in env_override.split(",") if a.strip()]
        logger.info("📡 Source accounts from ENV: %s", accounts)
        return accounts

    cfg = _load_identities()
    accounts = cfg.get("source_accounts", [])
    # Filter out placeholder values
    accounts = [
        a for a in accounts
        if a and not a.startswith("REPLACE_WITH")
    ]
    if not accounts:
        logger.warning(
            "⚠️ No paparazzi source accounts configured! "
            "Edit Actress_Modules/actress_accounts.json['_paparazzi']['source_accounts'] "
            "or set PAPARAZZI_SOURCE_ACCOUNTS in .env"
        )
    return accounts


def detect_gender_from_name(name: str) -> str:
    """
    Detects gender from a person's name string.
    Uses curated token lists from paparazzi_identities.json.

    Returns: 'female' | 'male' | 'unknown'
    """
    if not name:
        return "unknown"

    cfg = _load_identities()
    name_lower = name.lower()

    # Tokenize: split on spaces, underscores, dots
    tokens = re.split(r"[\s_.\-]+", name_lower)

    female_tokens = set(cfg.get("female_name_tokens", []))
    male_tokens   = set(cfg.get("male_name_tokens", []))

    female_score = sum(1 for t in tokens if t in female_tokens)
    male_score   = sum(1 for t in tokens if t in male_tokens)

    if female_score > male_score:
        logger.debug("👩 Gender detected FEMALE from name '%s' (score F:%d M:%d)", name, female_score, male_score)
        return "female"
    elif male_score > female_score:
        logger.debug("👨 Gender detected MALE from name '%s' (score F:%d M:%d)", name, male_score, male_score)
        return "male"
    else:
        logger.debug("❓ Gender UNKNOWN from name '%s' (score F:%d M:%d)", name, female_score, male_score)
        return "unknown"


def _extract_person_name(reel: Dict) -> str:
    """
    Tries to extract the featured person's name from reel metadata.
    Checks: taggedUsers fullName, caption name patterns, ownerUsername display name.
    Returns best candidate name string.
    """
    # Priority 1: Tagged users (most reliable — the person is actually tagged)
    tagged = reel.get("taggedUsers", [])
    if tagged and isinstance(tagged, list):
        for user in tagged:
            if isinstance(user, dict):
                full_name = user.get("full_name") or user.get("fullName", "")
                if full_name and len(full_name) > 2:
                    return full_name

    # Priority 2: ownerUsername display name (Apify returns this under different keys)
    owner = reel.get("ownerUsername", "")
    owner_display = (
        reel.get("ownerFullName")
        or reel.get("ownerName")
        or reel.get("fullName")   # Some Apify responses use 'fullName' at top level
        or ""
    )
    if owner_display and len(owner_display) > 2:
        return owner_display

    # Priority 3: Caption — look for a name-like pattern (2+ capitalized words)
    caption = reel.get("caption", "") or ""
    # Find sequences of 2-3 capitalized words (Name patterns)
    name_match = re.findall(r"([A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)", caption)
    if name_match:
        return name_match[0]

    return owner  # Last resort: just the username


def resolve_channel(ig_id: str, reel: Dict) -> Tuple[str, str, bool]:
    """
    Determines the destination channel for a scraped reel.

    Args:
        ig_id : Instagram username of the account that POSTED the reel
                (i.e., the paparazzi page's username, NOT the featured person)
        reel  : Full reel metadata dict from Apify

    Returns:
        (channel_folder, person_title, is_nsfw)
        - channel_folder : "General_Fallback" | "Paparazzi_Channel"
        - person_title   : Human-readable name of the featured person (for captions)
        - is_nsfw        : True if the featured person is in the nsfw_accounts list

    Routing priority:
        1. Check taggedUsers / caption against women identity dict
           → found in women → General_Fallback
        2. Check against men identity dict
           → found in men → Paparazzi_Channel
        3. Not found in any dict → detect gender from name tokens
           → female → General_Fallback
           → male / unknown → Paparazzi_Channel (safe default)
        4. NSFW overlay: if person is in nsfw_accounts → is_nsfw=True
           (still routes to General_Fallback — the women social)
    """
    cfg         = _load_identities()
    women_map   = cfg.get("women", {})   # {"Name": "ig_id"}
    men_map     = cfg.get("men", {})
    nsfw_ids    = set(cfg.get("nsfw_accounts", []))

    # ── Build reverse lookup: ig_id → name ────────────────────────────────────
    women_by_id = {v.lower(): k for k, v in women_map.items()}
    men_by_id   = {v.lower(): k for k, v in men_map.items()}

    ig_id_clean = ig_id.lower().lstrip("@")

    # ── Runtime channel for men/unknown (Paparazzi if creds exist, else General_Fallback) ──
    _men_channel = CHANNEL_PAPARAZZI if _paparazzi_creds_exist() else CHANNEL_ALL

    # ── Step 0: Direct ig_id match (fastest — no reel metadata needed) ────────
    # Covers: direct artist pages, or paparazzi pages that ARE known accounts
    if ig_id_clean in women_by_id:
        name    = women_by_id[ig_id_clean]
        is_nsfw = ig_id_clean in nsfw_ids
        logger.info(
            "👩 [ROUTER] Direct ID match (women): @%s → %s → %s%s",
            ig_id_clean, name, CHANNEL_WOMEN, " [NSFW]" if is_nsfw else ""
        )
        return CHANNEL_WOMEN, name, is_nsfw

    if ig_id_clean in men_by_id:
        name = men_by_id[ig_id_clean]
        logger.info("👨 [ROUTER] Direct ID match (men): @%s → %s → %s", ig_id_clean, name, _men_channel)
        return _men_channel, name, False

    # ── Step 1: Check taggedUsers against identity dicts ──────────────────────
    tagged = reel.get("taggedUsers", [])
    if isinstance(tagged, list):
        for user in tagged:
            if not isinstance(user, dict):
                continue
            uid = (user.get("username") or user.get("id", "")).lower()
            if uid and uid in women_by_id:
                name    = women_by_id[uid]
                is_nsfw = uid in nsfw_ids
                logger.info(
                    "👩 [ROUTER] Tagged match (women): @%s → %s → %s%s",
                    uid, name, CHANNEL_WOMEN, " [NSFW]" if is_nsfw else ""
                )
                return CHANNEL_WOMEN, name, is_nsfw
            if uid and uid in men_by_id:
                name = men_by_id[uid]
                logger.info("👨 [ROUTER] Tagged match (men): @%s → %s → %s", uid, name, _men_channel)
                return _men_channel, name, False


    # ── Step 2: Extract featured person name and look up ──────────────────────
    person_name = _extract_person_name(reel)
    person_lower = person_name.lower()

    # Check women by name (key lookup)
    for name, wid in women_map.items():
        name_tokens = [t.lower() for t in name.split() if len(t) > 2]
        if any(t in person_lower for t in name_tokens):
            is_nsfw = wid.lower() in nsfw_ids
            logger.info(
                "👩 [ROUTER] Name match (women): '%s' → %s → %s%s",
                person_name, name, CHANNEL_WOMEN, " [NSFW]" if is_nsfw else ""
            )
            return CHANNEL_WOMEN, name, is_nsfw

    # Check men by name
    for name, mid in men_map.items():
        name_tokens = [t.lower() for t in name.split() if len(t) > 2]
        if any(t in person_lower for t in name_tokens):
            logger.info("👨 [ROUTER] Name match (men): '%s' → %s → %s", person_name, name, _men_channel)
            return _men_channel, name, False

    # ── Step 3: Gender heuristic from name tokens ──────────────────────────────
    gender = detect_gender_from_name(person_name)

    if gender == "female":
        display = person_name or "Unknown Female"
        logger.info(
            "👩 [ROUTER] Heuristic FEMALE: '%s' → %s", display, CHANNEL_WOMEN
        )
        return CHANNEL_WOMEN, display, False

    # male or unknown → General_Fallback (or Paparazzi if that account is set up)
    display = person_name or "Unknown"
    logger.info(
        "👤 [ROUTER] Heuristic %s: '%s' → %s", gender.upper(), display, _men_channel
    )
    return _men_channel, display, False
