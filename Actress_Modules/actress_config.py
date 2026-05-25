"""
actress_config.py — AMTCE Actress Account Router
=================================================
Loads actress_accounts.json (v2 format: key=name, value={id, route})

Public API:
  - get_all_active_accounts()   → list of (instagram_id, route_folder) tuples
  - get_target_folder(ig_id)    → route folder for a given Instagram ID
  - is_account_mode_enabled()   → reads ENV + JSON toggle
  - run_monthly_rescan()        → semantic folder promotion + credential validation
  - start_background_rescan_timer() → launches the 30-day background daemon
"""

import os
import re
import json
import time
import logging
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT  = os.path.dirname(_BASE_DIR)
_CONFIG_PATH   = os.path.join(_BASE_DIR, "actress_accounts.json")
_SOCIAL_DIR    = os.path.join(_PROJECT_ROOT, "Credentials", "social_media")

# ── Rescan interval ───────────────────────────────────────────────────────────
_RESCAN_INTERVAL_DAYS = 30
_last_rescan: float   = 0.0
_rescan_lock          = threading.Lock()

# ── In-memory config cache ────────────────────────────────────────────────────
_config_cache: Optional[Dict] = None


def _load_config(force: bool = False) -> Dict:
    """Load actress_accounts.json. Caches in memory."""
    global _config_cache
    if _config_cache is None or force:
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                _config_cache = json.load(f)
            logger.info("📋 Actress config loaded from %s", _CONFIG_PATH)
        except Exception as exc:
            logger.error("💥 Failed to load actress_accounts.json: %s", exc)
            _config_cache = {"primary": {}, "secondary": {}, "nsfw": {},
                             "account_mode_enabled": False}
    return _config_cache


def _save_config(cfg: Dict) -> None:
    """Persist updated config back to JSON."""
    try:
        tmp = _CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _CONFIG_PATH)
        logger.info("💾 actress_accounts.json updated")
    except Exception as exc:
        logger.error("💥 Failed to save actress_accounts.json: %s", exc)


# ── Semantic matching ─────────────────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """Lowercase, remove punctuation/numbers, split into words."""
    clean = re.sub(r"[^a-z\s]", " ", text.lower())
    return [w for w in clean.split() if len(w) > 2]


def _semantic_score(username: str, folder_name: str) -> float:
    """
    Word-overlap score between Instagram username and folder name.
    Returns 0.0–1.0. A score >= 0.5 is considered a confident match.
    """
    u_tokens = set(_tokenize(username))
    f_tokens = set(_tokenize(folder_name))
    if not f_tokens:
        return 0.0
    overlap = u_tokens & f_tokens
    return len(overlap) / len(f_tokens)


def _get_social_folders() -> List[str]:
    """Return list of valid actress folder names (excludes system folders)."""
    _EXCLUDE = {"face_refinement", "General_Fallback"}
    try:
        return [
            d for d in os.listdir(_SOCIAL_DIR)
            if os.path.isdir(os.path.join(_SOCIAL_DIR, d))
            and d not in _EXCLUDE
        ]
    except Exception as exc:
        logger.error("💥 Cannot read social_media dir: %s", exc)
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def is_account_mode_enabled() -> bool:
    """Returns True if Account Mode (auto-scraping) is enabled."""
    # ENV takes priority, falls back to JSON config
    env_val = os.getenv("ACCOUNT_MODE_ENABLED", "").lower()
    if env_val in ("true", "1", "yes"):
        return True
    if env_val in ("false", "0", "no"):
        return False
    # Fall back to JSON
    cfg = _load_config()
    return bool(cfg.get("account_mode_enabled", False))


def get_target_folder(ig_id: str) -> str:
    """
    Returns the route folder for a given Instagram account ID.
    Reads 'route' from the {id, route} value in actress_accounts.json.
    Falls back to General_Fallback if not found.
    """
    cfg   = _load_config()
    ig_id = ig_id.lstrip("@").lower()

    for tier in ("primary", "secondary", "nsfw"):
        for name, entry in cfg.get(tier, {}).items():
            if name.startswith("_"):
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("id", "").lower() == ig_id:
                return entry.get("route", "General_Fallback")

    logger.warning("⚠️ Instagram ID '%s' not found in config → General_Fallback", ig_id)
    return "General_Fallback"


def get_all_active_accounts() -> List[Tuple[str, str, str]]:
    """
    Returns list of (instagram_id, route_folder, actress_title) for ALL accounts.
    actress_title is the human-readable JSON key (e.g. 'Avneet Kaur').
    route_folder is what the credential validator set — could be General_Fallback.
    """
    cfg    = _load_config()
    result = []
    for tier in ("primary", "secondary", "nsfw"):
        for name, entry in cfg.get(tier, {}).items():
            if name.startswith("_"):
                continue
            if not isinstance(entry, dict):
                continue
            ig_id = entry.get("id", "")
            route = entry.get("route", "General_Fallback")
            if ig_id:
                result.append((ig_id, route, name))
    return result


def get_primary_accounts() -> Dict[str, str]:
    """Returns {instagram_id: route} for primary accounts only."""
    cfg = _load_config()
    return {
        v["id"]: v["route"]
        for k, v in cfg.get("primary", {}).items()
        if not k.startswith("_") and isinstance(v, dict)
    }


def get_secondary_accounts() -> Dict[str, str]:
    """Returns {instagram_id: route} for secondary accounts."""
    cfg = _load_config()
    return {
        v["id"]: v["route"]
        for k, v in cfg.get("secondary", {}).items()
        if not k.startswith("_") and isinstance(v, dict)
    }


# ── Monthly rescan logic ──────────────────────────────────────────────────────

def run_monthly_rescan(force: bool = False) -> int:
    """
    Checks social_media/ folders and promotes any secondary IDs that now
    semantically match a new/renamed actress folder.

    Returns the number of IDs promoted this run.
    Called automatically by the scheduler. Can be forced manually.
    """
    global _last_rescan

    with _rescan_lock:
        now = time.time()
        elapsed_days = (now - _last_rescan) / 86400

        if not force and elapsed_days < _RESCAN_INTERVAL_DAYS:
            logger.debug(
                "⏳ Monthly rescan skipped — %.1f days since last scan (interval: %d days)",
                elapsed_days, _RESCAN_INTERVAL_DAYS
            )
            return 0

        logger.info("🔍 [MONTHLY RESCAN] Checking social_media/ for new actress folders…")
        _last_rescan = now

    cfg           = _load_config(force=True)
    folders       = _get_social_folders()
    primary       = cfg.get("primary", {})
    secondary     = cfg.get("secondary", {})
    nsfw          = cfg.get("nsfw", {})
    promoted_count = 0

    # ── Step 1: Semantic folder promotion (secondary + nsfw → primary) ────────
    for tier_name, tier_dict in [("secondary", secondary), ("nsfw", nsfw)]:
        for actress_name in list(tier_dict.keys()):
            if actress_name.startswith("_"):
                continue
            entry = tier_dict[actress_name]
            if not isinstance(entry, dict):
                continue

            best_folder = None
            best_score  = 0.0

            # Match actress NAME (key) against folder names
            for folder in folders:
                score = _semantic_score(actress_name, folder)
                if score > best_score:
                    best_score  = score
                    best_folder = folder

            if best_score >= 0.5 and best_folder:
                logger.info(
                    "🎯 PROMOTED: '%s' → '%s' (score: %.2f) [%s → primary]",
                    actress_name, best_folder, best_score, tier_name
                )
                entry["route"] = best_folder
                primary[actress_name] = entry
                del tier_dict[actress_name]
                promoted_count += 1

    if promoted_count:
        cfg["primary"]   = primary
        cfg["secondary"] = secondary
        cfg["nsfw"]      = nsfw
        _save_config(cfg)
        global _config_cache
        _config_cache = None
        logger.info("🎯 Semantic promotion: %d account(s) moved to primary", promoted_count)
    else:
        logger.info("🔍 Semantic promotion: no new folder matches found")

    # ── Step 2: Credential validation (updates routes in all tiers) ───────────
    try:
        from Actress_Modules.credential_validator import run_credential_scan
        run_credential_scan()
        _config_cache = None  # Invalidate after validator writes
    except Exception as exc:
        logger.error("💥 Credential scan failed during monthly rescan: %s", exc)

    logger.info("✅ Monthly rescan complete")
    return promoted_count


def start_background_rescan_timer() -> None:
    """
    Starts a background daemon thread that calls run_monthly_rescan()
    every 30 days while the bot process is alive.
    Safe to call at bot startup.
    """
    def _loop():
        # Wait 24 hours before first check so startup is not delayed
        time.sleep(86400)
        while True:
            try:
                run_monthly_rescan()
            except Exception as exc:
                logger.error("💥 Background rescan error: %s", exc)
            time.sleep(86400)  # Check daily, rescan only if 30 days elapsed

    t = threading.Thread(target=_loop, daemon=True, name="ActressMonthlyRescan")
    t.start()
    logger.info("🕐 Background actress folder rescan timer started (interval: %d days)", _RESCAN_INTERVAL_DAYS)
