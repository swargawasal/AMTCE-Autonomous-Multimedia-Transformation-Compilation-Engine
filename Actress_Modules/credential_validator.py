"""
credential_validator.py — AMTCE Social Folder Credential Checker
================================================================
Responsibilities:
  1. Scan every actress folder in Credentials/social_media/
  2. Determine if at least ONE real platform credential exists (Telegram OR Meta)
  3. Update the 'route' field in actress_accounts.json automatically:
       - Valid creds found  → route = folder name  (green — upload there)
       - Only placeholders  → route = General_Fallback  (upload there instead)
  4. "Next Session" trigger: when ALL primary folders are credentialed,
     auto-create template credential folders for secondary actresses
     so the user sees them and knows to fill them in.

Placeholder detection rules:
  - BRAND_NAME contains "Your Channel Name Here"  → placeholder
  - TELEGRAM_GROUP_ID contains "Your Telegram"    → placeholder
  - chat_id contains "Your Chat"                  → placeholder
  - meta_config.json missing OR IG_BUSINESS_TOKEN is empty/placeholder → Meta invalid
  At least ONE of (Telegram, Meta) must be real to consider the folder valid.
"""

import os
import re
import json
import logging
import shutil
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BASE_DIR)
_SOCIAL_DIR   = os.path.join(_PROJECT_ROOT, "Credentials", "social_media")
_CONFIG_PATH  = os.path.join(_BASE_DIR, "actress_accounts.json")

# ── Placeholder sentinel strings ───────────────────────────────────────────────
_PLACEHOLDER_PATTERNS = [
    "your channel name here",
    "your telegram",
    "your chat",
    "your group",
    "placeholder",
    "enter your",
    "add your",
]

# ── .env template for new next-session folders ─────────────────────────────────
_ENV_TEMPLATE = """\
# ═══════════════════════════════════════════════
# NICHE-LEVEL CONFIGURATION
# Folder : {actress_name}
# Fill in your real credentials below.
# ═══════════════════════════════════════════════

# Canonical niche identifier (must match folder name exactly)
NICHE_NAME={actress_name}

# Platform toggles
YOUTUBE_ENABLED=no
META_ENABLED=no

# ── BRANDING ──────────────────────────────────
BRAND_NAME="Your Channel Name Here"

# ── TELEGRAM ──────────────────────────────────
TELEGRAM_GROUP_ID="Your Telegram Group ID or Link"
chat_id="Your Chat ID"

# ── HASHTAGS ──────────────────────────────────
DEFAULT_HASHTAGS_SHORTS="#viral #trending #shorts"

# ── ADMIN ─────────────────────────────────────
AUCTION_MANUAL_MODE="false"
ADMIN_IDS=""
TELEGRAM_ADMIN_ID=""
"""


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_placeholder(value: str) -> bool:
    """Returns True if a credential value is empty or a known placeholder."""
    if not value or not value.strip():
        return True
    v_lower = value.strip().lower().strip('"').strip("'")
    return any(p in v_lower for p in _PLACEHOLDER_PATTERNS)


def _parse_env_file(env_path: str) -> Dict[str, str]:
    """Parse a .env file into a dict. Returns empty dict if not found."""
    result = {}
    try:
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    result[key.strip()] = val.strip()
    except Exception:
        pass
    return result


def _parse_meta_config(meta_path: str) -> Dict:
    """Parse meta_config.json. Returns empty dict if not found."""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _check_telegram_valid(env: Dict) -> bool:
    """Returns True if Telegram credentials look real (not placeholder)."""
    group_id = env.get("TELEGRAM_GROUP_ID", "")
    chat_id  = env.get("chat_id", "")
    return not _is_placeholder(group_id) and not _is_placeholder(chat_id)


def _check_meta_valid(meta: Dict) -> bool:
    """Returns True if at least the IG Business Token looks real."""
    token = meta.get("IG_BUSINESS_TOKEN", "")
    return bool(token) and not _is_placeholder(token) and len(token) > 20


def _check_youtube_valid(folder_path: str) -> bool:
    """Returns True if YouTube client_secret.json or token.json exists and isn't empty."""
    client_secret_path = os.path.join(folder_path, "client_secret.json")
    token_path = os.path.join(folder_path, "token.json")
    
    # Check if either file exists and has some size (not an empty placeholder)
    has_secret = os.path.isfile(client_secret_path) and os.path.getsize(client_secret_path) > 10
    has_token = os.path.isfile(token_path) and os.path.getsize(token_path) > 10
    
    return has_secret or has_token


def _load_config() -> Dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_config(cfg: Dict) -> None:
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _CONFIG_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# Core validator
# ─────────────────────────────────────────────────────────────────────────────

def validate_folder(folder_name: str) -> Tuple[bool, str]:
    """
    Check if a social_media subfolder has at least one real credential.
    Returns (is_valid: bool, reason: str)
    """
    folder_path = os.path.join(_SOCIAL_DIR, folder_name)

    if not os.path.isdir(folder_path):
        return False, "folder does not exist"

    env_path  = os.path.join(folder_path, ".env")
    meta_path = os.path.join(folder_path, "meta_config.json")

    env  = _parse_env_file(env_path)
    meta = _parse_meta_config(meta_path)

    telegram_ok = _check_telegram_valid(env)
    meta_ok     = _check_meta_valid(meta)
    youtube_ok  = _check_youtube_valid(folder_path)

    valid_platforms = []
    if telegram_ok: valid_platforms.append("Telegram")
    if meta_ok:     valid_platforms.append("Meta")
    if youtube_ok:  valid_platforms.append("YouTube")

    if valid_platforms:
        return True, " ✅ ".join(valid_platforms) + " ✅"
    else:
        return False, "Telegram ❌  Meta ❌  YouTube ❌ (all placeholders)"


# ─────────────────────────────────────────────────────────────────────────────
# Main scan — updates actress_accounts.json routes
# ─────────────────────────────────────────────────────────────────────────────

def run_credential_scan(report_only: bool = False) -> Dict:
    """
    Scans all actress folders, validates credentials, and updates routes
    in actress_accounts.json. It auto-detects new folders and dynamically 
    moves them between 'primary' (valid) and 'secondary' (invalid).
    
    Args:
        report_only: If True, only logs — does NOT write changes to JSON.

    Returns:
        dict with keys: valid_folders, invalid_folders, promoted, demoted
    """
    logger.info("🔐 [CREDENTIAL SCAN] Starting credential validation…")

    try:
        cfg = _load_config()
    except Exception as exc:
        logger.error("💥 Cannot load actress_accounts.json: %s", exc)
        return {}

    valid_folders   = []
    invalid_folders = []
    promoted        = []
    demoted         = []

    # 1. Gather all known and new folder names
    all_names = set()
    if os.path.exists(_SOCIAL_DIR):
        all_names.update([f for f in os.listdir(_SOCIAL_DIR) if os.path.isdir(os.path.join(_SOCIAL_DIR, f))])
    
    all_names.update(cfg.get("primary", {}).keys())
    all_names.update(cfg.get("secondary", {}).keys())
    
    IGNORE_FOLDERS = {"General_Fallback", "NSFW", "face_refinement"}
    all_names = {n for n in all_names if not n.startswith("_") and n not in IGNORE_FOLDERS}

    
    new_primary = {"_comment": cfg.get("primary", {}).get("_comment", "Actress folders exist in social_media/. route auto-set by credential validator.")}
    new_secondary = {"_comment": cfg.get("secondary", {}).get("_comment", "No actress folder yet. route=General_Fallback until folder+creds are created.")}
    
    # 2. Evaluate Primary and Secondary (and new ones)
    for actress_name in all_names:
        # Avoid processing NSFW here
        if actress_name in cfg.get("nsfw", {}):
            continue

        entry = cfg.get("primary", {}).get(actress_name) or cfg.get("secondary", {}).get(actress_name)
        if not isinstance(entry, dict):
            entry = {"id": actress_name.lower().replace(" ", ""), "route": "General_Fallback"}

        current_route = entry.get("route", "General_Fallback")
        was_primary = actress_name in cfg.get("primary", {})
        
        is_valid, reason = validate_folder(actress_name)

        if is_valid:
            valid_folders.append(actress_name)
            if not report_only:
                entry["route"] = actress_name
                new_primary[actress_name] = entry
            
            if not was_primary or current_route == "General_Fallback":
                logger.info("🟢 PROMOTED: '%s' — credentials valid (%s) → moving to PRIMARY", actress_name, reason)
                promoted.append(actress_name)
            else:
                logger.info("✅ '%s' — credentials valid (%s)", actress_name, reason)
        else:
            invalid_folders.append(actress_name)
            if not report_only:
                entry["route"] = "General_Fallback"
                new_secondary[actress_name] = entry
                
            if was_primary or current_route != "General_Fallback":
                logger.warning("🔴 DEMOTED: '%s' — credentials invalid (%s) → moving to SECONDARY", actress_name, reason)
                demoted.append(actress_name)
            else:
                logger.warning("⚠️  '%s' — no valid credentials (%s) → staying in SECONDARY", actress_name, reason)

    # 3. Evaluate NSFW (stay in NSFW category, just update route)
    for actress_name, entry in cfg.get("nsfw", {}).items():
        if actress_name.startswith("_") or not isinstance(entry, dict):
            continue
        
        current_route = entry.get("route", "General_Fallback")
        is_valid, reason = validate_folder(actress_name)
        
        if is_valid:
            valid_folders.append(actress_name)
            if not report_only:
                entry["route"] = actress_name
            if current_route == "General_Fallback":
                logger.info("🟢 PROMOTED: '%s' (NSFW) — credentials valid (%s) → route set to folder", actress_name, reason)
                promoted.append(actress_name)
            else:
                logger.info("✅ '%s' (NSFW) — credentials valid (%s)", actress_name, reason)
        else:
            invalid_folders.append(actress_name)
            if not report_only:
                entry["route"] = "General_Fallback"
            if current_route != "General_Fallback":
                logger.warning("🔴 DEMOTED: '%s' (NSFW) — credentials invalid (%s) → route → General_Fallback", actress_name, reason)
                demoted.append(actress_name)
            else:
                logger.warning("⚠️  '%s' (NSFW) — no valid credentials (%s) → staying at General_Fallback", actress_name, reason)

    if not report_only:
        cfg["primary"] = new_primary
        cfg["secondary"] = new_secondary
        
        if promoted or demoted or (set(cfg["primary"].keys()) != set(new_primary.keys())):
            _save_config(cfg)
            logger.info("💾 actress_accounts.json updated — %d promoted, %d demoted", len(promoted), len(demoted))

    logger.info("🔐 Credential scan complete — ✅ %d valid  ❌ %d invalid", len(valid_folders), len(invalid_folders))


    # ── Check if Next Session should be triggered ──────────────────────────
    _check_next_session(cfg, valid_folders, report_only)

    return {
        "valid_folders":   valid_folders,
        "invalid_folders": invalid_folders,
        "promoted":        promoted,
        "demoted":         demoted,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Next Session trigger
# ─────────────────────────────────────────────────────────────────────────────

def _check_next_session(cfg: Dict, valid_folders: List[str], report_only: bool) -> None:
    """
    If ALL primary entries have valid credentials (route ≠ General_Fallback),
    create template credential folders for all secondary actresses and
    update '_pending_next_session' in the config.
    """
    primary = {k: v for k, v in cfg.get("primary", {}).items() if not k.startswith("_")}

    all_primary_valid = all(
        isinstance(v, dict) and v.get("route", "General_Fallback") != "General_Fallback"
        for v in primary.values()
    )

    if not all_primary_valid:
        logger.info(
            "⏳ Next Session: %d primary folder(s) still need credentials. Holding.",
            sum(
                1 for v in primary.values()
                if isinstance(v, dict) and v.get("route") == "General_Fallback"
            )
        )
        return

    logger.info(
        "🎉 ALL primary folders credentialed! Triggering NEXT SESSION folder creation…"
    )

    secondary = {k: v for k, v in cfg.get("secondary", {}).items() if not k.startswith("_")}
    created   = []

    for actress_name in secondary:
        folder_path = os.path.join(_SOCIAL_DIR, actress_name)
        if os.path.isdir(folder_path):
            continue  # Already exists

        if not report_only:
            os.makedirs(folder_path, exist_ok=True)
            env_content = _ENV_TEMPLATE.format(actress_name=actress_name)
            with open(os.path.join(folder_path, ".env"), "w", encoding="utf-8") as f:
                f.write(env_content)
            logger.info("📁 Created next-session folder: Credentials/social_media/%s/", actress_name)
        created.append(actress_name)

    if created:
        # Update _pending_next_session in config
        cfg.setdefault("_pending_next_session", {})["folders_to_create"] = created
        if not report_only:
            _save_config(cfg)
        logger.info(
            "📋 Next session: %d folders created → %s",
            len(created), ", ".join(created)
        )
    else:
        logger.info("📋 Next session: all secondary folders already exist.")


# ─────────────────────────────────────────────────────────────────────────────
# Quick status report (no writes)
# ─────────────────────────────────────────────────────────────────────────────

def print_status_report() -> None:
    """Prints a readable credential status table. No writes."""
    result = run_credential_scan(report_only=True)
    print("\n" + "=" * 55)
    print("  AMTCE CREDENTIAL STATUS REPORT")
    print("=" * 55)
    print(f"  ✅ Valid   : {len(result.get('valid_folders', []))}")
    print(f"  ❌ Invalid : {len(result.get('invalid_folders', []))}")
    if result.get("invalid_folders"):
        print("\n  Folders needing credentials:")
        for f in result["invalid_folders"]:
            print(f"    → {f}")
    print("=" * 55 + "\n")
