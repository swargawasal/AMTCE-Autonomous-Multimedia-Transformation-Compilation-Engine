import os
import json
import re
import sys
import time
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Anchor all state files to the REPO ROOT (one directory above this file).
# This is critical for GitHub Actions where the CWD is the checkout root
# but os.getcwd() can vary across steps. Using __file__-relative paths
# ensures the same file is always read/written regardless of CWD.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLISH_QUEUE_FILE    = os.path.join(_REPO_ROOT, "publish_queue.json")
_PUBLISHED_REGISTRY   = os.path.join(_REPO_ROOT, "Actress_Modules", "published_registry.json")
PROCESS_LEAD_TIME_MINUTES = int(os.getenv("PROCESS_LEAD_TIME_MINUTES", "6"))


# ── Salesman State (publisher deficit + catch-up tracking) ────────────────
try:
    from Core_Modules.salesman_state import get_publisher_state
    _SALESMAN_AVAILABLE = True
except ImportError:
    _SALESMAN_AVAILABLE = False
    logger.warning("⚠️ [PUBLISHER] salesman_state not found — catch-up tracking disabled")


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


def _get_static_peak_times():
    try:
        from dotenv import load_dotenv
        load_dotenv("Credentials/.env", override=True)
    except ImportError:
        pass
    env_times = os.getenv("ACTRESS_STATIC_PUBLISH_TIMES", "07:30,12:30,19:30")
    return [t.strip() for t in env_times.split(",") if t.strip()]


class PublishQueue:
    _lock = threading.Lock()

    @classmethod
    def load(cls):
        if not os.path.exists(PUBLISH_QUEUE_FILE):
            return []
        try:
            with open(PUBLISH_QUEUE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    @classmethod
    def save(cls, queue):
        with open(PUBLISH_QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2)

    @classmethod
    def add(cls, video_path: str, actress_title: str, actress_folder: str):
        with cls._lock:
            queue = cls.load()
            
            # Check published registry to prevent re-queuing
            published = set()
            if os.path.exists(_PUBLISHED_REGISTRY):
                try:
                    with open(_PUBLISHED_REGISTRY, "r", encoding="utf-8") as f:
                        published = set(json.load(f))
                except Exception:
                    pass
            
            if video_path in published:
                if os.path.exists(video_path):
                    # Physical file exists, meaning it was never actually published (or needs republishing)
                    logger.info(f"🔄 Clip physically exists on disk but is marked as published. Bypassing skip and cleaning registry: {os.path.basename(video_path)}")
                    try:
                        published.remove(video_path)
                        with open(_PUBLISHED_REGISTRY, "w", encoding="utf-8") as f:
                            json.dump(sorted(published), f, indent=2)
                    except Exception as e:
                        logger.warning(f"⚠️ Could not clean published registry for {video_path}: {e}")
                else:
                    logger.info(f"⏭️ Skipping already published clip: {os.path.basename(video_path)}")
                    return
                
            if not any(q["video_path"] == video_path for q in queue):
                queue.append({
                    "video_path": video_path,
                    "actress_title": actress_title,
                    "actress_folder": actress_folder,
                    "added_at": time.time()
                })
                cls.save(queue)
                logger.info(f"📥 Added to Publish Queue: {os.path.basename(video_path)} (Total: {len(queue)})")

    @classmethod
    def pop_one(cls, last_folder=None, last_gender=None):
        with cls._lock:
            queue = cls.load()
            if not queue:
                return None

            def get_gender(folder):
                f = folder.lower()
                if f.startswith("paparazzi"): return "men"
                if f.startswith("fashion"): return "women_fashion"
                return "women_general"

            # Try to find a clip that differs in both account and gender
            best_idx = 0
            for i, item in enumerate(queue):
                f = item["actress_folder"]
                g = get_gender(f)
                if last_folder and f == last_folder:
                    continue
                if last_gender and g == last_gender:
                    # Still consider it if we only have same gender but different account
                    best_idx = i
                    continue
                
                # Different account AND different gender (or no previous) -> optimal choice
                best_idx = i
                break

            item = queue.pop(best_idx)
            cls.save(queue)
            return item


def _auto_fill_queue_from_downloads():
    """
    Scans the downloads/ folder for actress subfolders containing .mp4 files
    and adds any un-queued clips to the PublishQueue.
    Called automatically when FORCE_NEXT_BATCH=yes and queue is empty.
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    downloads_dir = os.path.join(base, "downloads")
    if not os.path.isdir(downloads_dir):
        logger.warning("[AUTO_FILL] downloads/ folder not found.")
        return 0

    added = 0
    existing = {q["video_path"] for q in PublishQueue.load()}

    for entry in sorted(os.scandir(downloads_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        # Folder name format: "Actress Name_001"
        folder_name = entry.name
        # Derive actress title: strip trailing _NNN batch suffix
        import re as _re
        actress_title = _re.sub(r"_\d+$", "", folder_name).strip()
        # Clean prefix: "General_Fallback_bollywooddazzle" -> "bollywooddazzle"
        if actress_title.startswith("General_Fallback_"):
            actress_title = actress_title.replace("General_Fallback_", "", 1).strip()
        actress_folder = actress_title  # matches the Social_Media folder convention

        for mp4 in sorted(entry.path and [f for f in os.listdir(entry.path) if f.lower().endswith(".mp4")]):
            full_path = os.path.join(entry.path, mp4)
            if full_path not in existing:
                PublishQueue.add(full_path, actress_title, actress_folder)
                existing.add(full_path)
                added += 1

    if added:
        logger.info(f"📥 [AUTO_FILL] Added {added} clip(s) from downloads/ to queue.")
    else:
        logger.info("[AUTO_FILL] No new clips found in downloads/ to queue.")
    return added


def _get_active_publish_slots():
    now = datetime.now()
    slots = []
    for t_str in _get_static_peak_times():
        try:
            h, m = map(int, t_str.split(":"))
            # Calculate yesterday's, today's, and tomorrow's slots for seamless rollover
            slots.append((now - timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0))
            slots.append(now.replace(hour=h, minute=m, second=0, microsecond=0))
            slots.append((now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0))
        except Exception as e:
            logger.warning(f"Failed to parse static time {t_str}: {e}")
    return sorted(list(set(slots)))


def _process_queue_item():
    """Pops one item from the queue and runs it through the full AMTCE pipeline."""
    item = PublishQueue.pop_one()
    if not item:
        logger.info("📭 Publish queue is empty. Nothing to process.")
        return

    video_path     = item['video_path']
    actress_title  = item['actress_title']
    actress_folder = item['actress_folder']

    logger.info(f"🎬 Popped video for processing: {os.path.basename(video_path)}")
    logger.info(f"⚙️ Running full AMTCE pipeline via CLI for: {actress_title}")

    final_video_path = video_path
    try:
        from main import process_clip
        result_path = process_clip(video_path, actress_title)
        if result_path:
            final_video_path = result_path
            logger.info(f"✅ AMTCE PROCESS SUCCESS: output → {final_video_path}")
        else:
            logger.error(f"⚠️ AMTCE PROCESS Failed to process clip")
    except Exception as e:
        logger.error(f"⚠️ Failed to run AMTCE PROCESS: {e}")

    # Rename: Lisa_001_001.mp4 → Lisa_01.mp4
    if os.path.exists(final_video_path) and final_video_path != video_path:
        base_dir   = os.path.dirname(final_video_path)
        raw_stem   = os.path.splitext(os.path.basename(video_path))[0]
        ext        = os.path.splitext(video_path)[1]
        nums       = re.findall(r"\d+", raw_stem)
        idx        = int(nums[0]) if nums else 1
        safe_title = actress_title.replace("/", "-").replace("\\", "-")
        clean_name = f"{safe_title}_{idx:02d}{ext}"
        titled_path = os.path.join(base_dir, clean_name)
        try:
            os.replace(final_video_path, titled_path)
            final_video_path = titled_path
            logger.info(f"✅ Final video renamed: {clean_name}")
        except Exception as e:
            logger.warning(f"⚠️ Could not rename output: {e}")
    else:
        logger.warning("⚠️ Processed video not found. Using raw video as fallback.")
        final_video_path = video_path

    from Actress_Modules.actress_scheduler import _auto_publish_clip
    _auto_publish_clip(final_video_path, actress_title, actress_folder)

    # Belt-and-suspenders: ensure both paths are gone even if _auto_publish_clip's
    # finally block was skipped (e.g. Telegram not configured, function returned early).
    for _cleanup_path in {final_video_path, video_path}:
        try:
            if _cleanup_path and os.path.exists(_cleanup_path):
                os.remove(_cleanup_path)
                logger.info(f"🗑️ [PUBLISHER] Cleanup: deleted input after publish: {os.path.basename(_cleanup_path)}")
        except Exception as _ce:
            logger.warning(f"⚠️ [PUBLISHER] Could not delete {_cleanup_path}: {_ce}")


def _publish_loop():
    static_times = _get_static_peak_times()
    slots = _get_active_publish_slots()
    queue_size = len(PublishQueue.load())

    # ── Startup: Salesman missed-slot detection & catch-up planning ──────────
    _catchup_slots: list = []
    if _SALESMAN_AVAILABLE:
        _pub_state = get_publisher_state()
        _active_start = os.getenv("ACTRESS_ACTIVE_HOURS_START", "07:00")
        _active_end   = os.getenv("ACTRESS_ACTIVE_HOURS_END",   "23:00")

        # Detect missed slots since last run
        _missed = _pub_state.get_missed_slots(static_times)
        for _ms in _missed:
            _pub_state.mark_slot_missed(_ms)

        if _missed:
            logger.warning(
                "🚨 [PUBLISHER SALESMAN] %d missed publish slot(s) detected: %s",
                len(_missed), _missed
            )
            # Plan catch-up: spread across remaining active hours today
            _catchup_slots = _pub_state.plan_catchup_slots(
                static_times, _active_start, _active_end
            )
            if _catchup_slots:
                logger.info(
                    "📅 [PUBLISHER SALESMAN] Catch-up publish slots planned: %s",
                    _catchup_slots
                )
        else:
            logger.info("✅ [PUBLISHER SALESMAN] No missed publish slots. All good.")

    if slots:
        _future = [s for s in slots if s > datetime.now() - timedelta(minutes=PROCESS_LEAD_TIME_MINUTES)]
        next_slot = min(_future) if _future else slots[0]
        mins_left = max(0, int((next_slot - timedelta(minutes=PROCESS_LEAD_TIME_MINUTES) - datetime.now()).total_seconds() // 60))
        label = _batch_label(next_slot.hour, next_slot.minute)
        logger.info(f"📤 PUBLISHER ready — {label} in {mins_left} min | Queue: {queue_size} clip(s) | Lead: {PROCESS_LEAD_TIME_MINUTES} min")
    else:
        logger.info(f"📤 PUBLISHER ready — Smart Auto-Schedule ON | Queue: {queue_size} clip(s)")

    last_processed_slot = None

    while True:
        try:
            now        = datetime.now()
            slots      = _get_active_publish_slots()
            queue_size = len(PublishQueue.load())
            _auto_interval = int(os.getenv("ACTRESS_AUTO_PROCESS_INTERVAL_MINUTES", "90"))

            # Refresh catch-up slots from salesman state each tick
            if _SALESMAN_AVAILABLE:
                _pub_state = get_publisher_state()
                _catchup_slots = _pub_state.plan_catchup_slots(
                    static_times,
                    os.getenv("ACTRESS_ACTIVE_HOURS_START", "07:00"),
                    os.getenv("ACTRESS_ACTIVE_HOURS_END", "23:00"),
                )

            # ── 0. FORCE_NEXT_BATCH override (for testing) ────────────────────
            # Set FORCE_NEXT_BATCH=yes in .env to trigger the next item immediately.
            # Reads the FILE directly every tick so live edits are picked up instantly
            # without restarting main.py. Auto-resets to 'no' after firing.
            _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Credentials", ".env")
            _force = "no"
            try:
                if os.path.exists(_env_path):
                    import re as _re
                    with open(_env_path, "r", encoding="utf-8") as _ef:
                        _env_text = _ef.read()
                    _m = _re.search(r"(?m)^FORCE_NEXT_BATCH=(.+)$", _env_text)
                    if _m:
                        _force = _m.group(1).strip().lower()
            except Exception:
                pass

            if _force in ("yes", "1", "true"):
                logger.info("🔥 [FORCE_NEXT_BATCH] Manual trigger detected — firing immediately!")
                # Reset to 'no' in the file so it doesn't loop on next tick
                try:
                    _env_text = _re.sub(r"(?m)^FORCE_NEXT_BATCH=.*$", "FORCE_NEXT_BATCH=no", _env_text)
                    with open(_env_path, "w", encoding="utf-8") as _ef:
                        _ef.write(_env_text)
                    logger.info("✅ [FORCE_NEXT_BATCH] Reset to 'no' in .env — will not loop.")
                except Exception as _fe:
                    logger.warning(f"⚠️ [FORCE_NEXT_BATCH] Could not reset .env: {_fe}")
                # Auto-fill queue from downloads/ if empty
                if not PublishQueue.load():
                    logger.info("📭 Queue empty — auto-scanning downloads/ to fill it...")
                    _auto_fill_queue_from_downloads()
                _process_queue_item()

            # ── 1. Fire at static slots (6 min early) ────────────────────────
            for slot in slots:
                target_time = slot - timedelta(minutes=PROCESS_LEAD_TIME_MINUTES)
                diff_sec    = (now - target_time).total_seconds()
                if 0 <= diff_sec < 60:
                    slot_id = slot.strftime("%Y-%m-%d %H:%M")
                    if last_processed_slot != slot_id:
                        last_processed_slot = slot_id
                        slot_hhmm = slot.strftime("%H:%M")
                        batch = _batch_label(slot.hour, slot.minute)
                        logger.info(f"⏰ {batch} — Processing {PROCESS_LEAD_TIME_MINUTES} min before slot {slot.strftime('%H:%M')} | Queue: {queue_size}")
                        _process_queue_item()
                        # Mark this slot as published in salesman state
                        if _SALESMAN_AVAILABLE:
                            get_publisher_state().mark_slot_published(slot_hhmm)

            # ── 2. Fire catch-up slots (salesman planned times) ──────────────
            for cu_slot_str in _catchup_slots:
                try:
                    cu_h, cu_m = map(int, cu_slot_str.split(":"))
                except Exception:
                    continue
                cu_slot_dt  = now.replace(hour=cu_h, minute=cu_m, second=0, microsecond=0)
                cu_fire_dt  = cu_slot_dt - timedelta(minutes=PROCESS_LEAD_TIME_MINUTES)
                cu_diff_sec = (now - cu_fire_dt).total_seconds()
                cu_slot_id  = f"CATCHUP_{cu_slot_dt.strftime('%Y-%m-%d_%H:%M')}"

                if 0 <= cu_diff_sec < 60 and last_processed_slot != cu_slot_id:
                    last_processed_slot = cu_slot_id
                    logger.info(
                        "🔄 [PUBLISHER SALESMAN] Catch-up slot firing at %s | Queue: %d",
                        cu_slot_str, queue_size
                    )
                    _process_queue_item()
                    if _SALESMAN_AVAILABLE:
                        get_publisher_state().mark_slot_published(f"{cu_slot_str}[catchup]")

            # ── 3. Smart Auto-Schedule ────────────────────────────────────────
            # If queue has items and no static slot is firing within auto_interval,
            # trigger now automatically — no manual .env editing needed.
            # Restricted to human active hours only to prevent overnight off-hour spamming.
            if queue_size > 0:
                upcoming_secs = [
                    (s - timedelta(minutes=PROCESS_LEAD_TIME_MINUTES) - now).total_seconds()
                    for s in slots
                    if (s - timedelta(minutes=PROCESS_LEAD_TIME_MINUTES) - now).total_seconds() > 60
                ]
                nearest_mins = min(upcoming_secs) / 60 if upcoming_secs else 9999

                if nearest_mins > _auto_interval:
                    _active_start_str = os.getenv("ACTRESS_ACTIVE_HOURS_START", "07:00")
                    _active_end_str   = os.getenv("ACTRESS_ACTIVE_HOURS_END", "23:00")
                    try:
                        sh, sm = map(int, _active_start_str.split(":"))
                        eh, em = map(int, _active_end_str.split(":"))
                    except Exception:
                        sh, sm = 7, 0
                        eh, em = 23, 0

                    now_mins   = now.hour * 60 + now.minute
                    start_mins = sh * 60 + sm
                    end_mins   = eh * 60 + em

                    if start_mins <= end_mins:
                        is_active_hour = (start_mins <= now_mins < end_mins)
                    else:
                        is_active_hour = (now_mins >= start_mins or now_mins < end_mins)

                    if not is_active_hour:
                        pass
                    else:
                        # Key by hour so it only fires once per hour maximum
                        auto_id = f"AUTO_{now.strftime('%Y-%m-%d_%H')}"
                        if last_processed_slot != auto_id:
                            last_processed_slot = auto_id
                            batch = _batch_label(now.hour, now.minute)
                            logger.info(
                                f"🤖 PUBLISHER Auto-Schedule — {batch} | {queue_size} clip(s) waiting | "
                                f"Next static slot in {int(nearest_mins)} min — processing now!"
                            )
                            _process_queue_item()

            time.sleep(30)

        except Exception as e:
            logger.error(f"Publisher loop error: {e}")
            time.sleep(60)


def start_publish_scheduler():
    t = threading.Thread(target=_publish_loop, daemon=True, name="PublishScheduler")
    t.start()
