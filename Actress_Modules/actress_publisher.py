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


def _sync_to_gh_pages(video_to_add: str = None, video_to_delete: str = None, queue_to_write: list = None) -> bool:
    import subprocess
    import tempfile
    import shutil
    
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    
    if not token or not repo:
        logger.info("ℹ️ [STUDIO] GITHUB_TOKEN or GITHUB_REPOSITORY not set. Skipping remote gh-pages sync (running locally).")
        return False
        
    remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            logger.info("🎬 [STUDIO] Cloning gh-pages branch...")
            subprocess.run(
                ["git", "clone", "-b", "gh-pages", "--single-branch", remote_url, "repo_clone"],
                check=True, cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            clone_path = os.path.join(tmpdir, "repo_clone")
            
            subprocess.run(["git", "config", "user.name", "AMTCE Bot"], check=True, cwd=clone_path)
            subprocess.run(["git", "config", "user.email", "amtce-bot@users.noreply.github.com"], check=True, cwd=clone_path)
            
            changed = False
            
            if queue_to_write is not None:
                # Prune completed reviews to keep file size small (keep all PENDING, and last 10 completed)
                pending = [item for item in queue_to_write if item.get("status") == "PENDING_REVIEW"]
                completed = [item for item in queue_to_write if item.get("status") != "PENDING_REVIEW"]
                completed.sort(key=lambda x: x.get("queued_at", 0), reverse=True)
                pruned_queue = pending + completed[:10]
                
                queue_file_path = os.path.join(clone_path, "review_queue.json")
                with open(queue_file_path, "w", encoding="utf-8") as wf:
                    json.dump(pruned_queue, wf, indent=2)
                changed = True
                
            if video_to_add and os.path.exists(video_to_add):
                previews_dir = os.path.join(clone_path, "previews")
                os.makedirs(previews_dir, exist_ok=True)
                dest_video = os.path.join(previews_dir, os.path.basename(video_to_add))
                shutil.copy2(video_to_add, dest_video)
                changed = True
                
            if video_to_delete:
                previews_dir = os.path.join(clone_path, "previews")
                target_video = os.path.join(previews_dir, os.path.basename(video_to_delete))
                if os.path.exists(target_video):
                    os.remove(target_video)
                    changed = True
                    
            if changed:
                subprocess.run(["git", "add", "-A"], check=True, cwd=clone_path)
                diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=clone_path)
                if diff_check.returncode != 0:
                    subprocess.run(["git", "commit", "-m", "ci: update review queue and previews [skip ci]"], check=True, cwd=clone_path)
                    logger.info("🎬 [STUDIO] Pushing updates to gh-pages...")
                    subprocess.run(["git", "push", "origin", "gh-pages"], check=True, cwd=clone_path)
                    logger.info("🎬 [STUDIO] Successfully updated gh-pages.")
                    return True
                else:
                    logger.info("🎬 [STUDIO] No changes to push to gh-pages.")
            
        except Exception as e:
            logger.warning("⚠️ [STUDIO] Failed to sync to gh-pages: %s", e)
            
    return False


def _get_remote_queue() -> list:
    import subprocess
    
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    
    if not token or not repo:
        _REVIEW_QUEUE_FILE = os.path.join(_REPO_ROOT, "review_queue.json")
        if os.path.exists(_REVIEW_QUEUE_FILE):
            try:
                with open(_REVIEW_QUEUE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []
        
    try:
        subprocess.run(
            ["git", "fetch", "origin", "gh-pages"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        res = subprocess.run(
            ["git", "show", "origin/gh-pages:review_queue.json"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
        )
        return json.loads(res.stdout)
    except Exception as e:
        logger.warning("⚠️ [STUDIO] Failed to fetch remote queue via Git: %s. Trying fallback raw HTTP...", e)
        try:
            import urllib.request
            raw_url = f"https://raw.githubusercontent.com/{repo}/gh-pages/review_queue.json?t={int(time.time())}"
            req = urllib.request.Request(raw_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as he:
            logger.warning("⚠️ [STUDIO] HTTP queue fetch fallback failed: %s", he)
            
    return []


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
    env_times = os.getenv("ACTRESS_STATIC_PUBLISH_TIMES", "").strip()
    if not env_times:
        env_times = os.getenv("ACTRESS_SCHEDULE_TIMES", "07:30,12:30,19:30")
    return [t.strip() for t in env_times.split(",") if t.strip()]


class PublishQueue:
    _lock = threading.Lock()

    @classmethod
    def load(cls):
        if not os.path.exists(PUBLISH_QUEUE_FILE):
            return []
        try:
            with open(PUBLISH_QUEUE_FILE, "r", encoding="utf-8") as f:
                queue = json.load(f)
                # Auto-migrate any absolute or platform-dependent paths to relative paths with forward slashes
                for q in queue:
                    vp = q["video_path"]
                    if os.path.isabs(vp) or "/" in vp or "\\" in vp:
                        q["video_path"] = os.path.relpath(vp, _REPO_ROOT).replace("\\", "/")
                return queue
        except Exception:
            return []

    @classmethod
    def save(cls, queue):
        with open(PUBLISH_QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2)

    @classmethod
    def add(cls, video_path: str, actress_title: str, actress_folder: str, shortcode: str = None):
        with cls._lock:
            queue = cls.load()
            
            # Normalize target path to relative path with forward slashes
            rel_video_path = os.path.relpath(video_path, _REPO_ROOT).replace("\\", "/")
            
            # ── Avoid List Check (ledger) ───────────────────────────────────────
            try:
                from Actress_Modules.actress_ledger import get_ledger
                _ledger = get_ledger()
                if _ledger.is_in_avoid_list(shortcode, video_path):
                    logger.warning(f"🚫 [QUEUE-DEDUP] Avoid list match for {os.path.basename(video_path)} (sc={shortcode}) — skipping queue add.")
                    abs_path = os.path.join(_REPO_ROOT, rel_video_path)
                    if os.path.exists(abs_path):
                        try:
                            os.remove(abs_path)
                            logger.info(f"🗑️ [QUEUE-DEDUP] Deleted duplicate video: {os.path.basename(abs_path)}")
                        except Exception as _de:
                            logger.warning(f"⚠️ [QUEUE-DEDUP] Could not delete duplicate video file: {_de}")
                    return
            except Exception as _le:
                logger.warning(f"⚠️ [QUEUE-DEDUP] Avoid list check failed: {_le}")

            # Check published registry to prevent re-queuing
            published = set()
            if os.path.exists(_PUBLISHED_REGISTRY):
                try:
                    with open(_PUBLISHED_REGISTRY, "r", encoding="utf-8") as f:
                        for p in json.load(f):
                            # Normalize path formatting to relative with forward slashes
                            if os.path.isabs(p) or "/" in p or "\\" in p:
                                rel_p = os.path.relpath(p, _REPO_ROOT).replace("\\", "/")
                            else:
                                rel_p = p
                            published.add(rel_p)
                except Exception:
                    pass
            
            if rel_video_path in published:
                abs_path = os.path.join(_REPO_ROOT, rel_video_path)
                if os.path.exists(abs_path):
                    # File is already in published_registry but still exists on disk — it's a
                    # leftover from a previous run where cleanup failed.  DO NOT remove it from
                    # the registry (that was the root cause of the 4x duplicate upload bug).
                    # Instead, delete the stale file and skip re-queuing.
                    try:
                        os.remove(abs_path)
                        logger.info(f"🗑️ [DEDUP] Deleted leftover published clip: {os.path.basename(abs_path)}")
                    except Exception as _de:
                        logger.warning(f"⚠️ [DEDUP] Could not delete leftover file {abs_path}: {_de}")
                logger.info(f"⏭️ [DEDUP] Skipping already-published clip: {os.path.basename(rel_video_path)}")
                return
                
            if not any(os.path.relpath(q["video_path"], _REPO_ROOT).replace("\\", "/") == rel_video_path for q in queue):
                queue.append({
                    "video_path": rel_video_path,
                    "actress_title": actress_title,
                    "actress_folder": actress_folder,
                    "added_at": time.time(),
                    "shortcode": shortcode
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
            # Resolve to absolute path so downstream tasks receive a valid, local absolute path
            item["video_path"] = os.path.abspath(os.path.join(_REPO_ROOT, item["video_path"]))
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
    existing = {os.path.relpath(q["video_path"], base).replace("\\", "/") for q in PublishQueue.load()}

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
            rel_path = os.path.relpath(full_path, base).replace("\\", "/")
            if rel_path not in existing:
                # Deduplication check: compute file hash and check avoid list
                try:
                    from Actress_Modules.actress_ledger import get_ledger
                    _ledger = get_ledger()
                    if _ledger.is_in_avoid_list(None, full_path):
                        logger.warning(f"🚫 [AUTO_FILL-DEDUP] Avoid list match for {mp4} — deleting instantly.")
                        try:
                            os.remove(full_path)
                        except Exception:
                            pass
                        continue
                except Exception as _le:
                    logger.warning(f"⚠️ [AUTO_FILL-DEDUP] Avoid list check failed: {_le}")

                PublishQueue.add(full_path, actress_title, actress_folder)
                existing.add(rel_path)
                added += 1

    if added:
        logger.info(f"📥 [AUTO_FILL] Added {added} clip(s) from downloads/ to queue.")
    else:
        logger.debug("[AUTO_FILL] No new clips found in downloads/ to queue.")
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

    # ── EDITORIAL REVIEW GATE ─────────────────────────────────────────────────
    # If EDITORIAL_REVIEW_MODE=on, write processed clip to review_queue.json
    # and WAIT for a human to Approve or Reject via the Studio Panel UI.
    # The clip will NOT publish until approved.
    _editorial_mode = os.getenv("EDITORIAL_REVIEW_MODE", "off").strip().lower() in ("on", "yes", "true", "1")
    if _editorial_mode:
        import uuid as _uuid_mod
        import time as _time_mod
        import json as _json_mod

        _REVIEW_QUEUE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "review_queue.json")

        # Build review queue entry
        _review_id = str(_uuid_mod.uuid4())
        _review_entry = {
            "id":            _review_id,
            "video_path":    final_video_path,
            "actress_title": actress_title,
            "actress_folder": actress_folder,
            "title":         f"{actress_title} | Viral Reel 🔥",
            "caption":       f"Watch {actress_title}'s latest reel! Subscribe for more.",
            "hashtags":      "#viral #reels #trending #shorts #bollywood",
            "status":        "PENDING_REVIEW",
            "platforms":     {"youtube": True, "instagram": True, "tiktok": False},
            "queued_at":     int(_time_mod.time()),
        }

        # Attempt to prefill with Gemini-generated title/hashtags from the log state
        # (if actress_scheduler already generated them, we use those)
        try:
            _review_q_existing = []
            if os.path.exists(_REVIEW_QUEUE_FILE):
                with open(_REVIEW_QUEUE_FILE, "r", encoding="utf-8") as _rf:
                    _review_q_existing = _json_mod.load(_rf)
            _review_q_existing.append(_review_entry)
            with open(_REVIEW_QUEUE_FILE, "w", encoding="utf-8") as _rf:
                _json_mod.dump(_review_q_existing, _rf, indent=2)
            logger.info(
                "🎬 [STUDIO] Clip queued for review: %s (ID=%s)",
                os.path.basename(final_video_path), _review_id,
            )
            # Sync to remote gh-pages branch (if in CI/Actions)
            _sync_to_gh_pages(video_to_add=final_video_path, queue_to_write=_review_q_existing)
            logger.info("   → Open https://swargawasal.github.io/AMTCE-Autonomous-Multimedia-Transformation-Compilation-Engine/studio.html to review and approve.")
        except Exception as _we:
            logger.warning("⚠️ [STUDIO] Could not write to review_queue.json: %s — skipping review gate", _we)
            _editorial_mode = False   # Fall through to immediate publish

        if _editorial_mode:
            # Poll remote/local review queue until status changes from PENDING_REVIEW
            _poll_interval = 10
            _max_wait_s    = int(os.getenv("STUDIO_REVIEW_TIMEOUT_MINUTES", "10")) * 60
            _elapsed       = 0
            _final_status  = "PENDING_REVIEW"
            _approved_entry = None

            logger.info("⏳ [STUDIO] Waiting for editorial decision (max %d min)…", _max_wait_s // 60)

            while _elapsed < _max_wait_s:
                _time_mod.sleep(_poll_interval)
                _elapsed += _poll_interval
                try:
                    # Get remote queue (or local if offline)
                    _rq = _get_remote_queue()
                    for _ritem in _rq:
                        if _ritem["id"] == _review_id:
                            _final_status = _ritem.get("status", "PENDING_REVIEW")
                            if _final_status != "PENDING_REVIEW":
                                _approved_entry = _ritem
                                break
                    if _final_status != "PENDING_REVIEW":
                        break
                except Exception:
                    pass

            if _final_status == "APPROVED" and _approved_entry:
                logger.info("✅ [STUDIO] Clip APPROVED — publishing with edits.")
                # Override publish params with user edits from Studio Panel
                _custom_title    = _approved_entry.get("title", actress_title)
                _custom_hashtags = _approved_entry.get("hashtags", "")
                _custom_platforms = _approved_entry.get("platforms", {})
                # Pass overrides through env so _auto_publish_clip picks them up
                os.environ["_STUDIO_TITLE"]    = _custom_title
                os.environ["_STUDIO_HASHTAGS"] = _custom_hashtags
                os.environ["_STUDIO_SKIP_YT"]  = "0" if _custom_platforms.get("youtube", True)   else "1"
                os.environ["_STUDIO_SKIP_IG"]  = "0" if _custom_platforms.get("instagram", True) else "1"
                os.environ["_STUDIO_SKIP_TT"]  = "0" if _custom_platforms.get("tiktok", False)   else "1"
                
                # Remote clean up (delete temporary video file from gh-pages)
                try:
                    _clean_q = _get_remote_queue()
                    for _ritem in _clean_q:
                        if _ritem["id"] == _review_id:
                            _ritem["status"] = "APPROVED"
                    _sync_to_gh_pages(video_to_delete=final_video_path, queue_to_write=_clean_q)
                except Exception as _ce:
                    logger.warning("⚠️ [STUDIO] Failed to clean up remote previews: %s", _ce)
                
                _auto_publish_clip(final_video_path, actress_title, actress_folder)
            elif _final_status == "REJECTED":
                logger.info("🗑️ [STUDIO] Clip REJECTED — skipping publish.")
                
                # Remote clean up (delete temporary video file from gh-pages and update status)
                try:
                    _clean_q = _get_remote_queue()
                    for _ritem in _clean_q:
                        if _ritem["id"] == _review_id:
                            _ritem["status"] = "REJECTED"
                    _sync_to_gh_pages(video_to_delete=final_video_path, queue_to_write=_clean_q)
                except Exception as _ce:
                    logger.warning("⚠️ [STUDIO] Failed to clean up remote previews: %s", _ce)
                
                return
            else:
                logger.warning(
                    "⏰ [STUDIO] Review timeout after %d min — auto-publishing without edits.",
                    _max_wait_s // 60,
                )
                
                # Remote clean up on timeout
                try:
                    _clean_q = _get_remote_queue()
                    for _ritem in _clean_q:
                        if _ritem["id"] == _review_id:
                            _ritem["status"] = "TIMED_OUT"
                    _sync_to_gh_pages(video_to_delete=final_video_path, queue_to_write=_clean_q)
                except Exception as _ce:
                    logger.warning("⚠️ [STUDIO] Failed to clean up remote previews: %s", _ce)
                
                _auto_publish_clip(final_video_path, actress_title, actress_folder)
            return
    # ── /EDITORIAL REVIEW GATE ────────────────────────────────────────────────

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

            # Auto-fill queue from downloads if SCHEDULE_MANUAL_INPUTS is enabled
            if os.getenv("SCHEDULE_MANUAL_INPUTS", "no").strip().lower() in ("yes", "1", "true"):
                _auto_fill_queue_from_downloads()

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
