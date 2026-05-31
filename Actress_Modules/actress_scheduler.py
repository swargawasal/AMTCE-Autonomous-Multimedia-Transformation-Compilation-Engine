"""
actress_scheduler.py — AMTCE Daily Auto-Discovery Engine
=========================================================
Runs continuously when ACCOUNT_MODE_ENABLED=true.
Every cycle it:
  1. Reads paparazzi source accounts from Actress_Modules/paparazzi_identities.json
  2. Calls Apify to scrape N reels from those paparazzi pages (one batch call)
  3. For each reel: resolves the featured person's gender + identity
     via channel_router.resolve_channel() → General_Fallback (women) or Paparazzi_Channel
  4. Cross-channel dedup: skips clips already committed to ANY channel
  5. Downloads matched reels via download_video() (enters full pipeline)
  6. Injects target folder into .niche.json so uploader routes correctly
  7. Adds to PublishQueue[channel] for tactically-scheduled publishing
  8. Sleeps until next scheduled slot

Manual override flags (edit in Credentials/.env while running):
  FORCE_HARVEST=yes      → trigger scrape immediately
  FORCE_NEXT_BATCH=yes   → publish next queued clip immediately
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from Actress_Modules.actress_publisher import PublishQueue, start_publish_scheduler

logger = logging.getLogger(__name__)

# ── Salesman State (missed-slot recovery + Apify disk quota) ─────────────────
try:
    from Core_Modules.salesman_state import get_harvest_state, log_full_status as _salesman_log
    _SALESMAN_AVAILABLE = True
except ImportError:
    _SALESMAN_AVAILABLE = False
    logger.warning("⚠️ [HARVEST] salesman_state not found — catch-up tracking disabled")

# ── ENV Config ────────────────────────────────────────────────────────────────
_raw_limit = os.getenv("APIFY_REELS_PER_ACCOUNT", "3").strip().lower()
if _raw_limit == "auto":
    # Read the actual number from APIFY_REELS_AUTO_MAX (set in .env by user).
    # Default 48 is the benchmarked average — change it anytime in .env.
    LIMIT_PER_ACCOUNT = int(os.getenv("APIFY_REELS_AUTO_MAX", "48"))
else:
    LIMIT_PER_ACCOUNT = int(_raw_limit)

# How many reels to actually DOWNLOAD per account per cycle.
# Apify still fetches LIMIT_PER_ACCOUNT as a pool (to survive pinned/restricted
# post filtering), but downloads stop once this cap is reached.
# Set in .env: ACTRESS_DOWNLOADS_PER_ACCOUNT=3
DOWNLOADS_PER_ACCOUNT = int(os.getenv("ACTRESS_DOWNLOADS_PER_ACCOUNT", "3"))

# ACTRESS_SCHEDULE_TIMES — comma-separated HH:MM times (24h) to run the daily cycle.
# Examples:
#   ACTRESS_SCHEDULE_TIMES=03:00          → once a day at 3 AM
#   ACTRESS_SCHEDULE_TIMES=03:00,15:00    → twice a day (3 AM + 3 PM)
#   ACTRESS_SCHEDULE_TIMES=07:38          → useful for quick local tests
# Falls back to ACTRESS_SCHEDULE_HOUR (legacy) if not set.
_raw_times = os.getenv("ACTRESS_SCHEDULE_TIMES", "").strip()
if _raw_times:
    def _parse_time(s: str) -> Tuple[int, int]:
        h, _, m = s.strip().partition(":")
        return int(h), int(m or "0")
    SCHEDULE_SLOTS: List[Tuple[int, int]] = [_parse_time(t) for t in _raw_times.split(",") if t.strip()]
else:
    # Legacy fallback: ACTRESS_SCHEDULE_HOUR (hour only, minute=0)
    _legacy_hour = int(os.getenv("ACTRESS_SCHEDULE_HOUR", "3"))
    SCHEDULE_SLOTS: List[Tuple[int, int]] = [(_legacy_hour, 0)]

# Grace window: if program starts within MISSED_GRACE_MINUTES of a scheduled
# time that already passed, treat it as a missed run and fire immediately.
# Default raised to 60 min (was 0) so a restart within the hour catches the slot.
MISSED_GRACE_MINUTES = int(os.getenv("ACTRESS_MISSED_GRACE_MINUTES", "60"))


def _batch_label(h: int, m: int) -> str:
    """Returns a friendly batch name based on the hour of the slot."""
    if 4 <= h < 12:
        return "Morning Batch"
    elif 12 <= h < 17:
        return "Afternoon Batch"
    elif 17 <= h < 21:
        return "Evening Batch"
    else:
        return "Night Batch"

# ACTRESS_RUN_DAYS — which days the scheduler actually fires.
# Two formats (can mix):
#   Specific dates : ACTRESS_RUN_DAYS=2026-05-18,2026-05-20,2026-05-22
#   Day names      : ACTRESS_RUN_DAYS=mon,wed,fri,sun
#   Both           : ACTRESS_RUN_DAYS=mon,wed,2026-05-30
# Leave blank to run every day (default behaviour).
_raw_run_days = os.getenv("ACTRESS_RUN_DAYS", "").strip()
RUN_DAYS_DATES: List[str] = []   # "YYYY-MM-DD"
RUN_DAYS_WEEKDAYS: List[int] = []  # 0=Mon … 6=Sun
_DAY_MAP = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
if _raw_run_days:
    for _token in _raw_run_days.split(","):
        _token = _token.strip().lower()
        if not _token:
            continue
        if _token in _DAY_MAP:
            RUN_DAYS_WEEKDAYS.append(_DAY_MAP[_token])
        else:
            RUN_DAYS_DATES.append(_token)   # treat as YYYY-MM-DD

def _today_is_scheduled() -> bool:
    """Returns True if today is an allowed run day (or if no restriction is set)."""
    if not RUN_DAYS_DATES and not RUN_DAYS_WEEKDAYS:
        return True  # no restriction — run every day
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    if today_str in RUN_DAYS_DATES:
        return True
    if today.weekday() in RUN_DAYS_WEEKDAYS:
        return True
    return False



# ─────────────────────────────────────────────────────────────────────────────
# Text Filter — Does this reel mention the actress?
# ─────────────────────────────────────────────────────────────────────────────

def _name_in_reel(reel: Dict, actress_folder: str) -> bool:
    """
    Returns True if the actress name (or parts of it) appears in the reel's
    caption, hashtags, or tagged users.
    General_Fallback accounts always pass (no name filter needed).
    """
    if actress_folder == "General_Fallback":
        return True

    # Build search tokens from the actress folder name
    name_tokens = [t.lower() for t in actress_folder.split() if len(t) > 2]

    searchable = " ".join([
        reel.get("caption", ""),
        " ".join(reel.get("hashtags", [])),
        " ".join(
            u.get("username", "") + " " + u.get("full_name", "")
            for u in reel.get("taggedUsers", [])
            if isinstance(u, dict)
        ),
    ]).lower()

    matched = sum(1 for token in name_tokens if token in searchable)
    # Require at least half the name tokens to match
    threshold = max(1, len(name_tokens) // 2)
    return matched >= threshold


# ─────────────────────────────────────────────────────────────────────────────
# Niche Sidecar Injection
# ─────────────────────────────────────────────────────────────────────────────

def _inject_niche(video_path: str, actress_folder: str, actress_title: str = "") -> None:
    """
    Writes a .niche.json sidecar next to the downloaded video.
    Forces the uploader to route to the correct actress social account.
    For NSFW accounts, also writes is_nsfw=True and content_niche=adult_content
    so monetization_brain skips fashion scout + price overlay.
    """
    if not video_path or not os.path.exists(video_path):
        return
    base    = os.path.splitext(video_path)[0]
    sidecar = f"{base}.niche.json"

    # Detect if this actress is in the nsfw tier + read custom niche key
    is_nsfw      = False
    content_niche = actress_folder  # default: route folder name
    try:
        from Actress_Modules.actress_config import _load_config
        cfg = _load_config()
        for _name, _entry in cfg.get("nsfw", {}).items():
            if _name.startswith("_") or not isinstance(_entry, dict):
                continue
            if _name == actress_title or _entry.get("route") == actress_folder:
                is_nsfw      = True
                content_niche = _entry.get("niche", "adult_content")
                break
    except Exception:
        pass

    try:
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({
                "detected_niche": actress_folder,
                "content_niche":  content_niche,
                "is_nsfw":        is_nsfw,
                "timestamp":      time.time(),
                "source":         "actress_scheduler"
            }, f)
        flag = " [NSFW🔞]" if is_nsfw else ""
        logger.info("💾 Niche sidecar injected: %s → %s%s",
                    os.path.basename(sidecar), actress_folder, flag)
    except Exception as exc:
        logger.error("⚠️ Failed to write niche sidecar: %s", exc)

def _safe_title(actress_title: str) -> str:
    """Strip filesystem-unsafe chars from the actress title."""
    return "".join(c for c in actress_title if c.isalnum() or c in " _-").strip()


def _next_batch_folder(actress_title: str, downloads_dir: str) -> str:
    """
    Finds the next available batch subfolder for this actress.
    Scans downloads/ for existing 'Avneet Kaur_001', 'Avneet Kaur_002' …
    and returns the next increment path (does NOT create the folder).

    Example:
      Avneet Kaur_001 exists  →  returns  downloads/Avneet Kaur_002
      None exist              →  returns  downloads/Avneet Kaur_001
    """
    safe = _safe_title(actress_title)
    idx  = 1
    while True:
        candidate = os.path.join(downloads_dir, f"{safe}_{idx:03d}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def _organize_clip(video_path: str, actress_title: str,
                   batch_folder: str, clip_index: int) -> str:
    """
    Moves a downloaded clip into its batch subfolder and renames it.

    Before : downloads/AQPBpntf….mp4
    After  : downloads/Avneet Kaur_001/Avneet Kaur_001_001.mp4

    Returns the new absolute path (or original on failure).
    """
    if not video_path or not os.path.exists(video_path):
        return video_path
    try:
        os.makedirs(batch_folder, exist_ok=True)
        safe        = _safe_title(actress_title)
        ext         = os.path.splitext(video_path)[1]  # .mp4
        new_name    = f"{safe}_{clip_index:02d}{ext}"
        new_path    = os.path.join(batch_folder, new_name)

        # Avoid collision
        counter = clip_index
        while os.path.exists(new_path):
            counter += 1
            new_name = f"{safe}_{counter:02d}{ext}"
            new_path = os.path.join(batch_folder, new_name)

        os.rename(video_path, new_path)
        logger.info("🏷️  Organised: %s → %s/%s",
                    os.path.basename(video_path), os.path.basename(batch_folder), new_name)
        return new_path
    except Exception as exc:
        logger.warning("⚠️ Organise failed, keeping original: %s", exc)
        return video_path


# ─────────────────────────────────────────────────────────────────────────────
# Auto-Publish Pipeline
# Gate: AUTO_PUBLISH_ACTRESS_CLIPS=yes in .env
# ─────────────────────────────────────────────────────────────────────────────

def _auto_publish_clip(video_path: str, actress_title: str, actress_folder: str) -> None:
    """
    After a clip is organised, call Gemini to generate a viral title + hashtags,
    then immediately upload to Instagram (and Facebook if enabled) via the
    existing upload_to_meta pipeline, and to YouTube via uploader.
    """
    if os.getenv("AUTO_PUBLISH_ACTRESS_CLIPS", "no").strip().lower() not in ("yes", "true", "1"):
        return

    if not video_path or not os.path.exists(video_path):
        logger.warning("⚠️ [AUTO_PUBLISH] video_path missing — skipping")
        return

    logger.info("🚀 [AUTO_PUBLISH] Starting auto-publish for: %s", os.path.basename(video_path))

    # ── Content Router: classify clip → pick upload niche ─────────────────────
    # Overrides actress_folder with the correct routing target:
    #   fashion (coverage >= 40%) → General_Fallback / Fashion_Style
    #   nsfw    (coverage <  40%) → NSFW / General_Fallback
    #   general (no human)        → General_Fallback
    try:
        from Uploader_Modules.content_router import classify_content
        route   = classify_content(video_path)
        targets = route["targets"]   # ordered list e.g. ["General_Fallback", "Fashion_Style"]
        content_category = route.get("category", "general")  # "fashion" | "nsfw" | "general"
        logger.info(
            "🗺️  [CONTENT_ROUTER] category=%s coverage=%d%% targets=%s",
            route["category"], route["coverage_pct"], targets,
        )
        # Use the primary target as the upload niche (overrides actress folder for credentials)
        upload_niche = targets[0]
    except Exception as _re:
        logger.warning("⚠️ [CONTENT_ROUTER] Routing failed: %s — falling back to actress_folder", _re)
        upload_niche     = actress_folder
        targets          = [actress_folder]
        content_category = "general"

    # ── Account Limiter: import helpers ───────────────────────────────────────
    try:
        from Uploader_Modules.account_limiter import can_post, record_post, select_posting_style
        _limiter_ok = True
    except Exception as _le:
        logger.warning("⚠️ [LIMITER] Import failed: %s — limits disabled", _le)
        _limiter_ok = False
        def can_post(n, p): return True   # noqa: E731
        def record_post(n, p): pass       # noqa: E731
        def select_posting_style(): return {}  # noqa: E731

    # Pick a random posting style to vary caption structure across accounts
    post_style = select_posting_style()
    logger.info("🎲 [LIMITER] Posting style: %s", post_style)


    # ── Step 1: Extract a thumbnail frame for Gemini vision ──────────────────
    import subprocess, tempfile, asyncio
    frame_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = tmp.name
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01", "-vframes", "1", frame_path],
            check=True, capture_output=True, timeout=30,
        )
        logger.info("🖼️  [AUTO_PUBLISH] Frame extracted → %s", os.path.basename(frame_path))
    except Exception as exc:
        logger.warning("⚠️ [AUTO_PUBLISH] Frame extraction failed: %s", exc)
        frame_path = None

    # ── Step 2: Gemini — generate title + hashtags ────────────────────────────
    title    = None
    hashtags = None
    try:
        from Text_Modules.gemini_captions import GeminiCaptionGenerator
        gen = GeminiCaptionGenerator()

        if frame_path and os.path.exists(frame_path):
            title    = gen.generate_title(frame_path)
            hashtags = gen.generate_hashtags(frame_path, count=15)
        else:
            title = gen.generate_compilation_title(n_videos=1, context=actress_title)

        if not title:
            title = f"{actress_title} | Hot Reel 🔥"

        # Always guarantee actress name tag + viral base tags in hashtags
        _actress_tag = f"#{actress_title.replace(' ', '')}"
        _base_tags   = f"{_actress_tag} #viral #reels #trending #shorts #hot #bollywood"
        if not hashtags:
            hashtags = _base_tags
        elif _actress_tag.lower() not in hashtags.lower():
            # Gemini forgot the actress name — prepend it
            hashtags = f"{_base_tags} {hashtags}"

        logger.info("✨ [AUTO_PUBLISH] Title: %s", title)
        logger.info("✨ [AUTO_PUBLISH] Hashtags: %s", hashtags[:80])

    except Exception as exc:
        logger.warning("⚠️ [AUTO_PUBLISH] Gemini generation failed: %s", exc)
        title    = f"{actress_title} | Viral Reel 🔥"
        hashtags = f"#{actress_title.replace(' ', '')} #viral #reels #trending #shorts #bollywood"

    finally:
        if frame_path and os.path.exists(frame_path):
            try:
                os.remove(frame_path)
            except Exception:
                pass

    # ── Step 3: Build the full caption & description ──────────────────────────
    cta_line    = os.getenv("AUTO_PUBLISH_CTA", "🔗 Link in bio for more!")
    # Build a proper script/description for YouTube
    description = (
        f"Watch {actress_title}'s latest viral reel! "
        f"Subscribe for daily updates on your favourite celebs.\n\n"
        f"{cta_line}"
    )
    caption = f"{title}\n\n{description}\n\n{hashtags}"
    logger.info("📝 [AUTO_PUBLISH] Full caption (%d chars)", len(caption))

    # ── Step 4: Upload to Meta & YouTube ─────────────────────────────────────
    try:
        from Uploader_Modules.meta_uploader import AsyncMetaUploader
        from Uploader_Modules.uploader import upload_to_youtube

        async def _do_upload():
            # Calculate Native YouTube Schedule via Analytics
            yt_publish_at = None
            try:
                from Intelligence_Modules.analytics_optimizer import optimizer
                from datetime import datetime, timedelta, timezone
                opt = optimizer.get_optimal_upload_time()
                if opt and "hour" in opt:
                    h = int(opt["hour"])
                    now = datetime.now()
                    target_dt = now.replace(hour=h, minute=0, second=0, microsecond=0)
                    
                    # If the peak time has already passed today, schedule for tomorrow
                    if target_dt <= now:
                        target_dt += timedelta(days=1)
                        
                    # YouTube requires ISO 8601 string. Convert local to UTC (or aware format).
                    # Since we don't strictly know the system timezone offset via simple stdlib reliably,
                    # astimezone() will format it with the local offset e.g., +05:30
                    yt_publish_at = target_dt.astimezone().isoformat()
                    logger.info("📅 YouTube Native API Schedule set for: %s", yt_publish_at)
            except Exception as e:
                logger.warning("⚠️ Failed to calculate YT schedule time, defaulting to immediate: %s", e)

            # 1. YouTube — guarded by daily limit
            logger.info("📤 Uploading to YouTube...")
            if can_post(upload_niche, "yt"):
                try:
                    yt_link = await upload_to_youtube(
                        file_path=video_path,
                        hashtags=hashtags,
                        title=title,
                        description=description,
                        privacy="public",
                        publish_at=yt_publish_at,
                        niche=upload_niche
                    )
                    if yt_link:
                        logger.info("🎥 [AUTO_PUBLISH] YouTube success: %s", yt_link)
                        record_post(upload_niche, "yt")
                    else:
                        logger.warning("⚠️ [AUTO_PUBLISH] YouTube upload failed.")
                except Exception as e:
                    logger.error("❌ YouTube upload error: %s", e)
            else:
                logger.info("🚫 [LIMITER] YouTube daily limit hit for %s — skipping YT upload", upload_niche)

            # 2. Meta (Instagram + Facebook) — guarded by daily limit
            logger.info("📤 Uploading to Meta (Insta/FB)...")
            if can_post(upload_niche, "ig"):
                meta_result = await AsyncMetaUploader.upload_to_meta(
                    video_path=video_path,
                    caption=caption,
                    upload_type="Reels",
                    skip_facebook=os.getenv("AUTO_PUBLISH_SKIP_FB", "no").lower() in ("yes", "true"),
                    niche=upload_niche,
                )
                ig_ok = meta_result.get("instagram", {}).get("status", "") == "success"
                if ig_ok:
                    record_post(upload_niche, "ig")
                return meta_result
            else:
                logger.info("🚫 [LIMITER] Instagram daily limit hit for %s — skipping IG upload", upload_niche)
                return {"instagram": {"status": "skipped_limit"}, "facebook": {"status": "skipped_limit"}}

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                future = asyncio.run_coroutine_threadsafe(_do_upload(), loop)
                result = future.result(timeout=600)
            else:
                result = loop.run_until_complete(_do_upload())
        except RuntimeError:
            result = asyncio.run(_do_upload())

        ig_status = result.get("instagram", {}).get("status", "unknown")
        fb_status = result.get("facebook",  {}).get("status", "unknown")
        ig_link   = result.get("instagram", {}).get("link", "")
        logger.info("📸 [AUTO_PUBLISH] Instagram: %s%s",
                    ig_status, f" → {ig_link}" if ig_link else "")
        logger.info("📘 [AUTO_PUBLISH] Facebook : %s", fb_status)

        # Telegram integration (Upload actual video with dual CTA buttons)
        try:
            async def _upload_tg():
                import telegram
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                from dotenv import load_dotenv
                load_dotenv("Credentials/.env")

                token = os.getenv("TELEGRAM_BOT_TOKEN")
                if not token:
                    logger.warning("⚠️ [AUTO_PUBLISH] TELEGRAM_BOT_TOKEN missing, skipping TG upload.")
                    return

                # ── Route to the correct niche Telegram group ──────────────────
                from Uploader_Modules.telegram_router import get_telegram_group_id
                chat_id = get_telegram_group_id(content_category)
                if not chat_id:
                    logger.warning(
                        "⚠️ [AUTO_PUBLISH] No Telegram group configured for category=%s — skipping.",
                        content_category
                    )
                    return
                logger.info("📡 [AUTO_PUBLISH] Telegram → %s group: %s", content_category.upper(), chat_id)

                # ── Los Pollos (partner/dating CPA link) ─────────────────────
                from Uploader_Modules.community_promoter import CommunityPromoter as _CP
                _cp_tmp = _CP()
                los_pollos_link = _cp_tmp._get_next_los_pollos_link()

                # ── Group invite link (for the corn / full clips button) ──────
                group_invite_link = os.getenv(
                    "TELEGRAM_GROUP_INVITE_LINK",
                    os.getenv("TELEGRAM_GROUP_ID", "")
                ).strip()
                if group_invite_link and not group_invite_link.startswith("http"):
                    # It's a bare @username or numeric ID — convert to a t.me URL
                    slug = group_invite_link.lstrip("@")
                    group_invite_link = f"https://t.me/{slug}"

                # ── Button labels from .env ──────────────────────────────
                partner_label = os.getenv("TG_BTN_PARTNER_LABEL", "🔥 Find Your Match").strip()
                corn_label    = os.getenv("TG_BTN_CORN_LABEL",   "🎬 Watch Full Clips").strip()

                # ── Build dual-hook caption ─────────────────────────────────
                # Use the title as anchor then inject both hooks below it
                gemini_tg_hook = _cp_tmp._generate_telegram_group_hook(partner_label, corn_label)
                if gemini_tg_hook:
                    dual_hooks = gemini_tg_hook
                    logger.info("✨ Using dynamic Gemini hook for Telegram group post.")
                else:
                    dual_hooks = (
                        f"🔹 The connection you've been looking for is already here.\n"
                        f"➡️ {partner_label}\n"
                        f"\n"
                        f"🔹 Full uncut clips. What the platform won’t show you.\n"
                        f"➡️ {corn_label}"
                    )
                    logger.info("⚠️ Gemini hook failed, using fallback hardcoded hook.")
                
                tg_caption = f"{title}\n\n{dual_hooks}"[:1024]

                # ── Inline keyboard ───────────────────────────────────────
                keyboard_rows = []

                if los_pollos_link:
                    keyboard_rows.append(
                        [InlineKeyboardButton(partner_label, url=los_pollos_link)]
                    )
                    logger.info("[DUAL_BTN] Partner button: %s", los_pollos_link[:60])
                else:
                    logger.info("[DUAL_BTN] No Los Pollos link available — partner button skipped.")

                if group_invite_link:
                    keyboard_rows.append(
                        [InlineKeyboardButton(corn_label, url=group_invite_link)]
                    )
                    logger.info("[DUAL_BTN] Clips button: %s", group_invite_link[:60])

                reply_markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None

                # ── Resolve Telegram chat id ────────────────────────────
                _tg_chat = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
                if isinstance(_tg_chat, str) and not _tg_chat.startswith(("@", "-")):
                    _tg_chat = f"@{_tg_chat}"

                bot = telegram.Bot(token=token)
                if can_post(upload_niche, "telegram"):
                    logger.info("📤 Uploading to Telegram (dual-button) ...")
                    with open(video_path, 'rb') as vf:
                        await bot.send_video(
                            chat_id=_tg_chat,
                            video=vf,
                            caption=tg_caption,
                            reply_markup=reply_markup,
                            read_timeout=600,
                            write_timeout=600,
                            connect_timeout=60
                        )
                    logger.info("✅ [AUTO_PUBLISH] Sent clip to Telegram with dual CTA buttons.")
                    record_post(upload_niche, "telegram")
                else:
                    logger.info("🚫 [LIMITER] Telegram daily limit hit for %s — skipping TG upload", upload_niche)

                # ── Outfit Rating DM → Admin (same as manual pipeline) ───────
                try:
                    admin_id_raw = os.getenv("ADMIN_IDS", "").split(",")[0].strip()
                    if admin_id_raw and admin_id_raw.lstrip("-").isdigit():
                        from Higgsfield_Modules.outfit_swap_engine import register_frame_for_rating
                        from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as _IKM
                        import subprocess as _sp, tempfile as _tf

                        # ── Step 1: Extract a still frame from the video ──────
                        # This is CRITICAL — Higgsfield needs a still IMAGE, not an .mp4
                        _frame_path = None
                        try:
                            with _tf.NamedTemporaryFile(suffix=".jpg", delete=False,
                                                        dir=os.path.dirname(video_path)) as _tmp:
                                _frame_path = _tmp.name
                            _sp.run(
                                ["ffmpeg", "-y", "-ss", "00:00:02", "-i", video_path,
                                 "-vframes", "1", "-q:v", "2", _frame_path],
                                capture_output=True, timeout=15
                            )
                            if not (os.path.exists(_frame_path) and os.path.getsize(_frame_path) > 0):
                                _frame_path = None  # extraction failed, skip registration
                        except Exception as _fe:
                            logger.debug("[OUTFIT_RATING] Frame extraction failed: %s", _fe)
                            _frame_path = None

                        if not _frame_path:
                            logger.warning("[OUTFIT_RATING] Could not extract frame — skipping swap registration.")
                        else:
                            # ── Step 2: Register the still frame (not the video) ──
                            _entry_id = register_frame_for_rating(
                                frame_path         = _frame_path,   # ← JPEG, not .mp4
                                wear_name          = title or actress_title,
                                niche              = actress_folder,
                                video_id           = os.path.splitext(os.path.basename(video_path))[0],
                                outfit_description = "",
                            )

                            is_war_mode = False
                            try:
                                from Uploader_Modules.telegram_auction_engine import AuctionState
                                is_war_mode = AuctionState().state.get("active", False)
                            except Exception:
                                pass

                            if is_war_mode:
                                _rate_buttons = [
                                    _IKB(f"💰 {'🔥' * i}", callback_data=f"outfit_rate:{_entry_id}:{i}")
                                    for i in range(1, 6)
                                ]
                                _rate_kb = _IKM([_rate_buttons[:3], _rate_buttons[3:]])
                                _rate_caption = (
                                    f"⚔️ **WAR MODE: BET ON HER!** 😈\n\n"
                                    f"🎯 Target: `{title or actress_title}`\n\n"
                                    f"Forget the outfit. How much are you dropping on HER tonight? 🔥💦\n"
                                    f"Place your stakes. Only verified whales get the prize.\n"
                                    f"💰🔥🔥🔥🔥🔥 = ALL IN!"
                                )
                            else:
                                _rate_buttons = [
                                    _IKB(f"{'⭐' * i}", callback_data=f"outfit_rate:{_entry_id}:{i}")
                                    for i in range(1, 6)
                                ]
                                _rate_kb = _IKM([_rate_buttons[:3], _rate_buttons[3:]])
                                _rate_caption = (
                                    f"👗 **Outfit Rating Request**\n\n"
                                    f"📦 Product: `{title or actress_title}`\n\n"
                                    f"Rate the outfit quality for AI swap generation.\n"
                                    f"⭐⭐⭐⭐⭐ = Generate AI influencer outfit swap."
                                )

                            # ── Step 3: DM the frame to admin with rating buttons ──
                            _admin_int = int(admin_id_raw)
                            with open(_frame_path, "rb") as _img:
                                await bot.send_photo(
                                    chat_id=_admin_int,
                                    photo=_img,
                                    caption=_rate_caption,
                                    reply_markup=_rate_kb,
                                    parse_mode="Markdown",
                                )
                            logger.info("[OUTFIT_RATING] Rating DM sent to admin %s for: %s", admin_id_raw, title)
                            
                            # Clean up the temporary frame
                            try:
                                if os.path.exists(_frame_path):
                                    os.remove(_frame_path)
                            except Exception:
                                pass
                except Exception as _re:
                    logger.debug("[OUTFIT_RATING] Rating DM skipped (non-fatal): %s", _re)

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    future = asyncio.run_coroutine_threadsafe(_upload_tg(), loop)
                    future.result(timeout=600)
                else:
                    loop.run_until_complete(_upload_tg())
            except RuntimeError:
                asyncio.run(_upload_tg())
            
            # ── ULTIMATE SAFETY: IMMEDIATE HARD DELETE AFTER PUBLISH ──
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
                    logger.info(f"🗑️ [SAFETY] Hard-deleted published clip: {os.path.basename(video_path)}")
                
                base_path = os.path.splitext(video_path)[0]
                for ext in [".niche.json", ".jpg", ".txt", ".route.json"]:
                    sidecar = base_path + ext
                    if os.path.exists(sidecar):
                        os.remove(sidecar)
                        logger.info(f"🗑️ [SAFETY] Hard-deleted sidecar: {os.path.basename(sidecar)}")
            except Exception as del_err:
                logger.error(f"⚠️ [SAFETY] Failed to delete clip after publish: {del_err}")

        except Exception as e:
            logger.warning(f"⚠️ [AUTO_PUBLISH] Telegram upload failed: {e}")

    except Exception as exc:
        logger.error("❌ [AUTO_PUBLISH] Upload failed (non-fatal): %s", exc)


def _process_account_batch(username: str, actress_folder: str, actress_title: str) -> int:
    """
    Scrapes N reels from one account, filters, downloads, organises into subfolder.
    actress_title : human-readable name from actress_accounts.json (e.g. 'Avneet Kaur')
    Returns the count of videos successfully downloaded.
    """
    from Download_Modules.apify_downloader import apify_scrape_actress_accounts
    from Download_Modules.downloader       import download_video
    from Actress_Modules.actress_ledger    import get_ledger, extract_shortcode

    ledger = get_ledger()   # process-level singleton — zero reload cost

    reels = apify_scrape_actress_accounts(
        actress_name    = actress_folder,
        source_accounts = [username],
        limit_per_account = LIMIT_PER_ACCOUNT,
    )

    if not reels:
        logger.info("📭 No reels returned for @%s", username)
        return 0

    # Determine the batch subfolder ONCE for this entire account run.
    # All clips from this run go into the same folder: downloads/Avneet Kaur_001/
    downloads_dir = os.getenv("DOWNLOADS_DIR", "downloads")
    batch_folder  = _next_batch_folder(actress_title, downloads_dir)
    logger.info("📂 Batch folder: %s", os.path.basename(batch_folder))

    downloaded = 0
    for reel in reels:
        # Stop once we've downloaded enough for this cycle
        if downloaded >= DOWNLOADS_PER_ACCOUNT:
            logger.info(
                "🛑 Download cap reached (%d/%d) for @%s — stopping",
                downloaded, DOWNLOADS_PER_ACCOUNT, username
            )
            break

        video_url = reel.get("videoUrl", "")
        if not video_url:
            logger.warning("⚠️ Reel has no videoUrl — skipping")
            continue

        # Step 1: Text filter (cheap — no API cost)
        if not _name_in_reel(reel, actress_folder):
            logger.info(
                "🔍 Caption/tag filter: actress '%s' not mentioned → skipping @%s reel",
                actress_folder, username
            )
            continue

        logger.info(
            "✅ Filter passed — downloading reel %d/%d from @%s for '%s'",
            downloaded + 1, DOWNLOADS_PER_ACCOUNT, username, actress_folder
        )

        # ── LAYER 1: Shortcode lock (pre-download, zero cost) ─────────────
        shortcode = reel.get("shortcode")
        if not shortcode:
            post_url   = reel.get("url") or reel.get("postUrl") or video_url
            shortcode  = extract_shortcode(post_url)

        if shortcode and ledger.shortcode_seen(shortcode):
            logger.info(
                "🔒 [LEDGER-L1] Already downloaded (shortcode %s) — skipping",
                shortcode
            )
            continue

        # ── Step: Download (enters full AMTCE pipeline) ───────────────────
        video_path, is_cached = download_video(video_url)

        if not video_path:
            logger.warning("❌ Download failed for reel: %s", video_url[:80])
            continue

        # ── LAYER 2: Content hash lock (post-download) ────────────────────
        if ledger.hash_seen(video_path):
            try:
                # Commit the shortcode now so we don't re-download it in the future!
                if shortcode:
                    ledger.commit(shortcode, video_path)
                os.remove(video_path)
                logger.info("🗑️ [LEDGER-L2] Hash duplicate deleted: %s",
                            os.path.basename(video_path))
            except Exception:
                pass
            continue

        # ── All clear — organise + commit ─────────────────────────────────
        video_path = _organize_clip(video_path, actress_title, batch_folder, downloaded + 1)
        _inject_niche(video_path, actress_folder, actress_title)
        ledger.commit(shortcode, video_path)   # persist to disk
        downloaded += 1

        # Add to Publish Queue instead of publishing immediately
        PublishQueue.add(video_path, actress_title, actress_folder)

    return downloaded


# ─────────────────────────────────────────────────────────────────────────────
# Daily Run
# ─────────────────────────────────────────────────────────────────────────────

def run_daily_cycle() -> None:
    """
    Dispatcher for harvest cycles.
    PAPARAZZI_MODE=yes in Credentials/.env -> run_paparazzi_cycle() (gender routing).
    Otherwise -> classic actress-account mode (backward compatible).
    Also runs posting_time_analyzer to dynamically optimize schedule slots.
    """
    import re as _re, os as _os
    _env_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "Credentials", ".env")
    
    # ── Auto-Optimize Harvest & Publish Timings ──
    try:
        from Actress_Modules.posting_time_analyzer import get_recommendations, patch_env
        logger.info("🧠 [ANALYZER] Dynamically optimizing harvest & publish schedules...")
        recs = get_recommendations()
        patch_env(recs, env_path=_env_path)
    except Exception as _ae:
        logger.warning("⚠️ [ANALYZER] Schedule optimization failed (non-fatal): %s", _ae)

    _paparazzi_mode = False
    try:
        with open(_env_path, "r", encoding="utf-8", errors="ignore") as _f:
            _txt = _f.read()
        _m = _re.search(r"(?m)^PAPARAZZI_MODE=(.+)$", _txt)
        if _m and _m.group(1).strip().lower() in ("yes", "true", "1"):
            _paparazzi_mode = True
    except Exception:
        pass

    if _paparazzi_mode:
        run_paparazzi_cycle()
    else:
        _run_classic_cycle()


def _run_classic_cycle() -> None:
    """Classic actress-account mode (legacy). Reads from actress_accounts.json."""
    from Actress_Modules.actress_config import (
        get_all_active_accounts,
        run_monthly_rescan,
        is_account_mode_enabled,
    )

    if not is_account_mode_enabled():
        logger.info("Account Mode OFF - skipping classic cycle")
        return

    if not _today_is_scheduled():
        logger.info("Today not in ACTRESS_RUN_DAYS - skipping")
        return

    logger.info("=" * 60)
    logger.info("AMTCE CLASSIC SCHEDULER - Daily Cycle Starting")
    logger.info("Time: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    run_monthly_rescan()

    from Download_Modules.apify_downloader import apify_scrape_actress_accounts
    from Download_Modules.downloader       import download_video
    from Actress_Modules.actress_ledger    import get_ledger, extract_shortcode

    accounts = get_all_active_accounts()
    ledger   = get_ledger()

    all_usernames = [username for username, _, _ in accounts]
    logger.info("Fetching ALL %d accounts in ONE Apify call ...", len(all_usernames))

    all_reels = apify_scrape_actress_accounts(
        actress_name      = "batch",
        source_accounts   = all_usernames,
        limit_per_account = LIMIT_PER_ACCOUNT,
    )

    if not all_reels:
        logger.warning("Apify returned no reels.")
        return

    account_map = {
        username.lstrip("@"): (folder, title)
        for username, folder, title in accounts
    }

    from collections import defaultdict
    reels_by_account: dict = defaultdict(list)
    for reel in all_reels:
        owner = reel.get("ownerUsername", "").lstrip("@").lower()
        reels_by_account[owner].append(reel)

    total_dl      = 0
    total_skipped = 0
    downloads_dir = os.getenv("DOWNLOADS_DIR", "downloads")

    for username_raw, folder_title in account_map.items():
        username       = username_raw.lower()
        actress_folder, actress_title = folder_title
        reels          = reels_by_account.get(username, [])

        if not reels:
            continue

        batch_folder = _next_batch_folder(actress_title, downloads_dir)
        downloaded = 0
        for reel in reels:
            if downloaded >= DOWNLOADS_PER_ACCOUNT:
                break

            video_url = reel.get("videoUrl", "")
            if not video_url:
                continue

            if not _name_in_reel(reel, actress_folder):
                continue

            shortcode = reel.get("shortcode")
            if not shortcode:
                post_url  = reel.get("url") or reel.get("postUrl") or video_url
                shortcode = extract_shortcode(post_url)

            if shortcode and ledger.shortcode_seen(shortcode):
                continue

            os.environ["SKIP_AUDIO_EXTRACT"] = "true"
            video_path, _ = download_video(video_url)
            os.environ.pop("SKIP_AUDIO_EXTRACT", None)

            if not video_path:
                continue

            if ledger.hash_seen(video_path):
                if shortcode:
                    ledger.commit(shortcode, video_path)
                try:
                    os.remove(video_path)
                except Exception:
                    pass
                continue

            video_path = _organize_clip(video_path, actress_title, batch_folder, downloaded + 1)
            _inject_niche(video_path, actress_folder, actress_title)
            ledger.commit(shortcode, video_path)
            downloaded += 1
            total_dl   += 1
            PublishQueue.add(video_path, actress_title, actress_folder)

        total_skipped += (LIMIT_PER_ACCOUNT - downloaded)

    logger.info("Classic cycle complete: %d downloaded, %d skipped", total_dl, total_skipped)


def run_paparazzi_cycle() -> None:
    """
    NEW Paparazzi Channel Harvest Cycle (PAPARAZZI_MODE=yes in .env).
    Scrapes paparazzi source accounts from actress_accounts.json.
    Resolves gender/identity via channel_router:
      - Women identified  -> General_Fallback
      - Men / Unknown     -> Paparazzi_Channel
    Cross-channel dedup: same clip NEVER appears on 2 channels.
    """
    from Actress_Modules.actress_config import is_account_mode_enabled
    from Actress_Modules.channel_router import (
        get_source_accounts,
        resolve_channel,
        CHANNEL_WOMEN,
        CHANNEL_ALL,
    )

    if not is_account_mode_enabled():
        logger.info("AAS OFF - skipping paparazzi cycle")
        return

    if not _today_is_scheduled():
        logger.info("Today not in ACTRESS_RUN_DAYS - skipping")
        return

    logger.info("=" * 60)
    logger.info("AMTCE PAPARAZZI SCHEDULER - Harvest Cycle Starting")
    logger.info("%s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    from Download_Modules.apify_downloader import apify_scrape_actress_accounts
    from Download_Modules.downloader       import download_video
    from Actress_Modules.actress_ledger    import get_ledger, extract_shortcode

    source_accounts = get_source_accounts()
    if not source_accounts:
        logger.error("No source accounts in actress_accounts.json. Add IG IDs and restart.")
        return

    ledger = get_ledger()
    
    # ── Apify Rotating Batch Logic ─────────────────────────────────────────────
    cursor_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apify_cursor.json")
    batch_size = int(os.getenv("APIFY_ACCOUNTS_BATCH_SIZE", "3"))
    
    cursor = {"pending": source_accounts.copy(), "done_this_round": []}
    if os.path.exists(cursor_path):
        try:
            with open(cursor_path, "r", encoding="utf-8") as f:
                saved_cursor = json.load(f)
            # Ensure pending elements are actually in source_accounts (in case list changed)
            valid_pending = [a for a in saved_cursor.get("pending", []) if a in source_accounts]
            if valid_pending:
                cursor["pending"] = valid_pending
                cursor["done_this_round"] = saved_cursor.get("done_this_round", [])
        except Exception as e:
            logger.warning("Failed to load apify_cursor.json, starting fresh: %s", e)

    # If pending is empty, reset the cycle
    if not cursor["pending"]:
        logger.info("🔄 Full round complete — resetting Apify cursor")
        cursor["pending"] = source_accounts.copy()
        cursor["done_this_round"] = []
        
    current_batch = cursor["pending"][:batch_size]
    cursor["pending"] = cursor["pending"][batch_size:]
    cursor["done_this_round"].extend(current_batch)
    
    try:
        with open(cursor_path, "w", encoding="utf-8") as f:
            json.dump(cursor, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save apify_cursor.json: %s", e)

    logger.info("Fetching %d paparazzi source accounts via Apify (Batch out of %d total)...", len(current_batch), len(source_accounts))

    all_reels = apify_scrape_actress_accounts(
        actress_name      = "paparazzi",
        source_accounts   = current_batch,
        limit_per_account = LIMIT_PER_ACCOUNT,
    )

    if not all_reels:
        logger.warning("Apify returned no reels for this harvest cycle.")
        return

    logger.info("Total reels from Apify (post-filter): %d", len(all_reels))

    downloads_dir = os.getenv("DOWNLOADS_DIR", "downloads")
    total_dl      = 0
    total_skipped = 0
    channel_counts: dict = {}

    for reel in all_reels:
        video_url = reel.get("videoUrl", "")
        if not video_url:
            continue

        ig_id = reel.get("ownerUsername", "").lower()
        actress_folder, actress_title, is_nsfw = resolve_channel(ig_id, reel)

        ch_count = channel_counts.get(actress_folder, 0)
        if ch_count >= DOWNLOADS_PER_ACCOUNT:
            total_skipped += 1
            continue

        shortcode = reel.get("shortcode")
        if not shortcode:
            post_url  = reel.get("url") or reel.get("postUrl") or video_url
            shortcode = extract_shortcode(post_url)

        if shortcode and ledger.shortcode_seen(shortcode):
            logger.info("[L1] Shortcode %s already downloaded - skipping", shortcode)
            total_skipped += 1
            continue

        if shortcode:
            existing_ch = ledger.channel_for_shortcode(shortcode)
            if existing_ch and existing_ch != actress_folder:
                logger.info("[CROSS-DEDUP] %s used by %s - skip for %s",
                            shortcode, existing_ch, actress_folder)
                total_skipped += 1
                continue

        logger.info("Downloading: @%s -> %s (%s)%s",
                    ig_id, actress_folder, actress_title, " [NSFW]" if is_nsfw else "")

        os.environ["SKIP_AUDIO_EXTRACT"] = "true"
        video_path, _ = download_video(video_url)
        os.environ.pop("SKIP_AUDIO_EXTRACT", None)

        if not video_path:
            logger.warning("Download failed: %s", video_url[:80])
            continue

        if ledger.hash_seen(video_path):
            if shortcode:
                ledger.commit_with_channel(shortcode, video_path, actress_folder,
                                           post_timestamp=reel.get("timestamp"))
            try:
                os.remove(video_path)
            except Exception:
                pass
            continue

        safe_ch   = actress_folder.replace(" ", "_")
        safe_name = _safe_title(actress_title)
        batch_folder = _next_batch_folder("%s_%s" % (safe_ch, safe_name), downloads_dir)
        clip_idx  = ch_count + 1

        video_path = _organize_clip(
            video_path,
            "%s - %s" % (actress_folder, actress_title),
            batch_folder,
            clip_idx,
        )
        _inject_niche(video_path, actress_folder, actress_title)
        ledger.commit_with_channel(shortcode, video_path, actress_folder,
                                   post_timestamp=reel.get("timestamp"))

        channel_counts[actress_folder] = ch_count + 1
        total_dl += 1
        PublishQueue.add(video_path, actress_title, actress_folder)

    logger.info("=" * 60)
    logger.info("Paparazzi cycle complete: %d downloaded | %d skipped", total_dl, total_skipped)
    for ch, cnt in channel_counts.items():
        logger.info("  %s: %d clip(s)", ch, cnt)
    logger.info("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler Loop (runs in background thread)
# ─────────────────────────────────────────────────────────────────────────────

def _next_slot_datetime() -> Tuple[datetime, bool]:
    """
    Returns (next_run_datetime, is_missed).
    is_missed=True means a slot passed within MISSED_GRACE_MINUTES → run now.
    """
    now = datetime.now()
    candidates = []
    for h, m in SCHEDULE_SLOTS:
        # Today's slot
        slot_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff_seconds = (now - slot_today).total_seconds()

        if diff_seconds > 0:  # slot already passed today
            if diff_seconds <= MISSED_GRACE_MINUTES * 60:
                # Within grace window — treat as missed, fire immediately
                return now, True
            # Fully missed → schedule for tomorrow
            candidates.append(slot_today + timedelta(days=1))
        else:
            # Still in the future today
            candidates.append(slot_today)

    next_dt = min(candidates)
    return next_dt, False


def _seconds_until_next_run() -> Tuple[float, str]:
    """Returns (seconds_to_wait, label) for the next scheduled slot."""
    next_dt, is_missed = _next_slot_datetime()
    if is_missed:
        return 0.0, "MISSED — running now"
    wait = (next_dt - datetime.now()).total_seconds()
    label = next_dt.strftime("%H:%M")
    return wait, label


def _next_future_slot() -> "datetime":
    """
    Returns the next scheduled slot that is STRICTLY in the future (>= 1 min).
    Used after a run completes to prevent the grace window from re-firing the
    same slot immediately.
    """
    now = datetime.now()
    candidates = []
    for h, m in SCHEDULE_SLOTS:
        slot_today     = now.replace(hour=h, minute=m, second=0, microsecond=0)
        slot_tomorrow  = slot_today + timedelta(days=1)
        # Only accept slots that are at least 60 s away
        if (slot_today - now).total_seconds() > 60:
            candidates.append(slot_today)
        candidates.append(slot_tomorrow)
    return min(candidates)


def start_scheduler() -> None:
    """
    Launches the actress scheduler as a background daemon thread.
    Safe to call at bot startup. Does nothing if ACCOUNT_MODE_ENABLED=false.
    Features:
      - Multiple schedule slots (ACTRESS_SCHEDULE_TIMES=03:00,15:00)
      - Missed-run recovery within ACTRESS_MISSED_GRACE_MINUTES (default 60)
      - Salesman State: persists slot history across restarts for proper catch-up
      - Works locally and on cloud — just set the right times in .env
    """
    from Actress_Modules.actress_config import is_account_mode_enabled
    if not is_account_mode_enabled():
        logger.info("Account Mode OFF — Actress Scheduler not started")
        return

    # ── Startup: Salesman catch-up check ──────────────────────────────────────
    # On every (re)start we check if harvest slots were missed since last run.
    # If yes, we fire immediately BEFORE entering the normal wait loop.
    _configured_slot_strs = [f"{h:02d}:{m:02d}" for h, m in SCHEDULE_SLOTS]
    _startup_catchup_needed = False
    if _SALESMAN_AVAILABLE:
        _harvest_state = get_harvest_state()
        _salesman_log()
        if _harvest_state.should_catchup(_configured_slot_strs):
            logger.warning(
                "🔄 [HARVEST SALESMAN] Missed harvest detected on startup — "
                "firing catch-up cycle NOW before entering normal schedule loop"
            )
            _startup_catchup_needed = True

    # Compute countdown for startup log
    _init_wait, _init_label = _seconds_until_next_run()
    if _init_wait > 0 and not _startup_catchup_needed:
        _init_mins = int(_init_wait // 60)
        _next_h, _next_m = min(SCHEDULE_SLOTS, key=lambda s: (datetime.now().replace(hour=s[0], minute=s[1], second=0, microsecond=0) - datetime.now()).total_seconds() % 86400)
        _batch = _batch_label(_next_h, _next_m)
        logger.info(
            "🚜 HARVESTER ready — %s starts in %d min | Grace: %d min | ACCOUNT_MODE=true",
            _batch, _init_mins, MISSED_GRACE_MINUTES
        )
    else:
        logger.info("🚜 HARVESTER ready — running missed batch now | ACCOUNT_MODE=true")
    
    # Start the peak-time publisher in parallel
    start_publish_scheduler()

    def _loop():
        nonlocal _startup_catchup_needed
        _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Credentials", ".env")

        def _read_force_flag(flag_name: str) -> bool:
            """Read a flag directly from the .env file (picks up live edits)."""
            try:
                if os.path.exists(_env_path):
                    import re as _re
                    with open(_env_path, "r", encoding="utf-8") as _f:
                        _txt = _f.read()
                    _m = _re.search(rf"(?m)^{flag_name}=(.+)$", _txt)
                    if _m:
                        return _m.group(1).strip().lower() in ("yes", "1", "true")
            except Exception:
                pass
            return False

        def _reset_force_flag(flag_name: str):
            """Write flag=no back into .env file."""
            try:
                import re as _re
                with open(_env_path, "r", encoding="utf-8") as _f:
                    _txt = _f.read()
                _txt = _re.sub(rf"(?m)^{flag_name}=.*$", f"{flag_name}=no", _txt)
                with open(_env_path, "w", encoding="utf-8") as _f:
                    _f.write(_txt)
            except Exception as _e:
                logger.warning("⚠️ Could not reset %s in .env: %s", flag_name, _e)

        while True:
            # ── 0. Salesman startup catch-up (fires once per session if missed) ──────
            if _startup_catchup_needed:
                _startup_catchup_needed = False
                logger.info("🔄 [HARVEST SALESMAN] Running catch-up harvest cycle now...")
                try:
                    run_daily_cycle()
                    if _SALESMAN_AVAILABLE:
                        _hs = get_harvest_state()
                        _hs.mark_catchup_fired()
                        # Mark the most recently passed slot as completed by catch-up
                        _now = datetime.now()
                        for _slot_str in _configured_slot_strs:
                            _hh, _mm = int(_slot_str[:2]), int(_slot_str[3:])
                            _slot_dt = _now.replace(hour=_hh, minute=_mm, second=0, microsecond=0)
                            if _slot_dt < _now:
                                _hs.mark_slot_complete(f"{_slot_str} [catchup]")
                        logger.info("📊 [HARVEST SALESMAN] Catch-up complete: %s", _hs.get_summary())
                except Exception as exc:
                    logger.error("Catch-up harvest error: %s", exc)

            # ── FORCE_HARVEST override: skip wait and run immediately ──────────
            elif _read_force_flag("FORCE_HARVEST"):
                logger.info("🔥 [FORCE_HARVEST] Manual trigger — running harvest now!")
                _reset_force_flag("FORCE_HARVEST")
                logger.info("✅ [FORCE_HARVEST] Reset to 'no' in .env.")
            else:
                wait_secs, label = _seconds_until_next_run()
                if wait_secs > 0:
                    wait_mins = int(wait_secs // 60)
                    _next_h, _next_m = [s for s in SCHEDULE_SLOTS if f"{s[0]:02d}:{s[1]:02d}" == label][0] if label in [f"{s[0]:02d}:{s[1]:02d}" for s in SCHEDULE_SLOTS] else SCHEDULE_SLOTS[0]
                    _batch = _batch_label(_next_h, _next_m)
                    logger.info(
                        "🚜 HARVESTER — %s in %d min",
                        _batch, wait_mins
                    )
                    # Sleep in 30s intervals so FORCE_HARVEST is picked up quickly
                    _slept = 0
                    while _slept < wait_secs:
                        time.sleep(30)
                        _slept += 30
                        if _read_force_flag("FORCE_HARVEST"):
                            logger.info("🔥 [FORCE_HARVEST] Manual trigger detected mid-sleep — breaking out!")
                            _reset_force_flag("FORCE_HARVEST")
                            logger.info("✅ [FORCE_HARVEST] Reset to 'no' in .env.")
                            break
                else:
                    logger.info("🚜 HARVESTER — Running missed batch NOW")

            # ── Run the daily harvest + mark slot complete in salesman state ──────
            if not _startup_catchup_needed:   # already ran above if catch-up
                _ran_slot = False
                try:
                    run_daily_cycle()
                    _ran_slot = True
                except Exception as exc:
                    logger.error("Scheduler loop error: %s", exc)

                if _ran_slot and _SALESMAN_AVAILABLE:
                    # Record which scheduled slot just completed
                    _now = datetime.now()
                    for _slot_str in _configured_slot_strs:
                        _hh, _mm = int(_slot_str[:2]), int(_slot_str[3:])
                        _slot_dt = _now.replace(hour=_hh, minute=_mm, second=0, microsecond=0)
                        diff = abs((_now - _slot_dt).total_seconds())
                        if diff < 1800:   # within 30 min of the slot = it's this slot
                            get_harvest_state().mark_slot_complete(_slot_str)
                            break

            # ── After any run (missed or on-time), sleep until the NEXT future
            # slot. This prevents the missed-grace window from re-triggering the
            # same run every 60 s in a loop.
            _next_future = _next_future_slot()
            _sleep_secs  = max(60.0, (_next_future - datetime.now()).total_seconds())
            _sleep_mins  = int(_sleep_secs // 60)
            _fb = _batch_label(_next_future.hour, _next_future.minute)
            _current_batch = _batch if '_batch' in locals() else _batch_label(datetime.now().hour, datetime.now().minute)
            logger.info(
                "🚜 HARVESTER — %s done. Next batch (%s at %02d:%02d) in %d min.",
                _current_batch, _fb, _next_future.hour, _next_future.minute, _sleep_mins
            )
            # Sleep in 30s chunks so FORCE_HARVEST can break out even post-run
            _slept = 0
            while _slept < _sleep_secs:
                time.sleep(30)
                _slept += 30
                if _read_force_flag("FORCE_HARVEST"):
                    logger.info("🔥 [FORCE_HARVEST] Trigger detected — running next harvest early!")
                    _reset_force_flag("FORCE_HARVEST")
                    break

    t = threading.Thread(target=_loop, daemon=True, name="ActressScheduler")
    t.start()
