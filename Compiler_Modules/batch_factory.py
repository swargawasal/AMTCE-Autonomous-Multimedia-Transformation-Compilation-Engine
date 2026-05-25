"""
The Great Machine — Batch Factory Engine
==========================================
AMTCE is the feeder. Social platforms are the fed.
This module is the nervous system that connects them.

Architecture (The Loop):
  Input_Queue/ folder → Factory picks up raw videos
    → Full AMTCE pipeline (edit + price_tag + audio sync)
    → Multi-platform distributor (IG + YT Shorts + Snapchat)
    → Analytics scorer (what made money?)
    → Market Memory update (feed winning DNA back into next batch)
    → Repeat. Forever.

The Machine is self-sustaining. Once fed raw footage, it runs
the full supply→demand→profit cycle autonomously.
"""

import os
import json
import time
import logging
import threading
import shutil
from datetime import datetime, timezone
from typing import Optional, Dict, List

logger = logging.getLogger("batch_factory")

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE         = os.path.dirname(os.path.abspath(__file__))
_ROOT         = os.path.dirname(_HERE)
QUEUE_DIR     = os.path.join(_ROOT, "Input_Queue")
DONE_DIR      = os.path.join(_ROOT, "Input_Queue", "processed")
FAILED_DIR    = os.path.join(_ROOT, "Input_Queue", "failed")
MARKET_MEMORY = os.path.join(_ROOT, "Monetization_Metrics", "market_memory.json")
FACTORY_LOG   = os.path.join(_ROOT, "Monetization_Metrics", "factory_log.json")

# ── Factory config ────────────────────────────────────────────────────────────
MAX_WORKERS     = 3       # parallel video pipelines
POLL_INTERVAL_S = 15      # seconds between queue scans
VIDEO_EXTS      = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

# ── Platform targets ──────────────────────────────────────────────────────────
# The Machine feeds every platform. Add/remove as needed.
PLATFORM_TARGETS = {
    "instagram":  True,   # Reels — primary revenue driver
    "youtube":    True,   # Shorts — secondary discovery
    "snapchat":   False,  # Spotlight — enabled when Snap credentials exist
    "telegram":   True,   # Direct affiliate link broadcast
}


def _safe_mkdir(*paths):
    for p in paths:
        os.makedirs(p, exist_ok=True)


def _load_market_memory() -> Dict:
    """Load the winning creative DNA from previous batch performance."""
    try:
        if os.path.exists(MARKET_MEMORY):
            with open(MARKET_MEMORY, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[FACTORY] Could not load market memory: {e}")
    return {}


def _save_factory_log(entry: Dict):
    """Append one job result to the factory log."""
    log = []
    try:
        if os.path.exists(FACTORY_LOG):
            with open(FACTORY_LOG, "r", encoding="utf-8") as f:
                log = json.load(f)
    except Exception:
        pass
    log.append(entry)
    # Keep last 500 entries
    log = log[-500:]
    try:
        os.makedirs(os.path.dirname(FACTORY_LOG), exist_ok=True)
        with open(FACTORY_LOG, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        logger.warning(f"[FACTORY] Could not save factory log: {e}")


def _inject_market_dna(profile_data: Dict, memory: Dict) -> Dict:
    """
    Inject the winning creative DNA from market memory into this job's profile.
    If the last 7 viral videos used 'hype' vibe + 'groove' music — this job
    starts with those as defaults, not random guesses.
    """
    if not memory:
        return profile_data

    winning_vibe  = memory.get("top_vibe")
    winning_genre = memory.get("top_music_genre")
    winning_pace  = memory.get("top_pacing_style")

    if winning_vibe and not profile_data.get("bgm_vibe"):
        profile_data["bgm_vibe"] = winning_vibe
        logger.info(f"[FACTORY] Market DNA: injected winning vibe='{winning_vibe}'")

    if winning_genre and not profile_data.get("bgm_genre"):
        profile_data["bgm_genre"] = winning_genre
        logger.info(f"[FACTORY] Market DNA: injected winning genre='{winning_genre}'")

    if winning_pace:
        if not profile_data.get("creative_intent"):
            profile_data["creative_intent"] = {}
        if not profile_data["creative_intent"].get("pacing_style"):
            profile_data["creative_intent"]["pacing_style"] = winning_pace
            logger.info(f"[FACTORY] Market DNA: injected winning pacing='{winning_pace}'")

    return profile_data


def _distribute_to_platforms(output_video: str, metadata: Dict) -> Dict:
    """
    Push the finished video to every enabled platform.
    Returns a dict of platform → result.
    """
    results = {}

    if PLATFORM_TARGETS.get("instagram"):
        try:
            from Uploader_Modules.meta_uploader import MetaUploader
            uploader = MetaUploader()
            caption  = metadata.get("caption", "")
            hashtags = metadata.get("hashtags", "")
            ig_result = uploader.upload_reel(
                video_path=output_video,
                caption=f"{caption}\n\n{hashtags}",
            )
            results["instagram"] = ig_result
            logger.info(f"[FACTORY] IG upload: {ig_result}")
        except Exception as e:
            results["instagram"] = {"error": str(e)}
            logger.warning(f"[FACTORY] IG upload failed: {e}")

    if PLATFORM_TARGETS.get("youtube"):
        try:
            from Uploader_Modules.uploader import YouTubeUploader
            yt = YouTubeUploader()
            yt_result = yt.upload_short(
                video_path=output_video,
                title=metadata.get("title", ""),
                description=metadata.get("caption", ""),
                tags=metadata.get("tags", []),
            )
            results["youtube"] = yt_result
            logger.info(f"[FACTORY] YT Shorts upload: {yt_result}")
        except Exception as e:
            results["youtube"] = {"error": str(e)}
            logger.warning(f"[FACTORY] YT upload failed: {e}")

    if PLATFORM_TARGETS.get("telegram"):
        try:
            from Uploader_Modules.meta_uploader import MetaUploader
            uploader = MetaUploader()
            tg_result = uploader.send_telegram(
                video_path=output_video,
                caption=metadata.get("telegram_hook", metadata.get("caption", "")),
            )
            results["telegram"] = tg_result
            logger.info(f"[FACTORY] Telegram broadcast: {tg_result}")
        except Exception as e:
            results["telegram"] = {"error": str(e)}
            logger.warning(f"[FACTORY] Telegram failed: {e}")

    return results


def _process_one_video(video_path: str, memory: Dict) -> Dict:
    """
    Run the full AMTCE pipeline on one video file.
    Returns a result dict with output path, platform results, and metadata.
    """
    fname   = os.path.basename(video_path)
    job_id  = f"factory_{int(time.time())}_{fname}"
    started = datetime.now(timezone.utc).isoformat()
    logger.info(f"[FACTORY] ▶ Starting job: {fname}")

    try:
        # Import the orchestrator — it runs the entire pipeline
        from Compiler_Modules.orchestrator import run_pipeline

        # Base profile: will be enriched by orchestrator internally
        base_profile = {
            "source_video": video_path,
            "job_id":       job_id,
            "mode":         "factory_auto",
        }

        # Inject market-winning creative DNA
        base_profile = _inject_market_dna(base_profile, memory)

        # Run the full pipeline
        result = run_pipeline(video_path, profile_data=base_profile)

        if not result or not result.get("output_video"):
            raise ValueError("Pipeline returned no output video.")

        output_video = result["output_video"]
        metadata     = result.get("metadata", {})

        # Distribute to all platforms
        platform_results = _distribute_to_platforms(output_video, metadata)

        # Move source to done
        shutil.move(video_path, os.path.join(DONE_DIR, fname))

        entry = {
            "job_id":            job_id,
            "source":            fname,
            "output":            output_video,
            "started":           started,
            "finished":          datetime.now(timezone.utc).isoformat(),
            "status":            "success",
            "platform_results":  platform_results,
            "vibe":              base_profile.get("bgm_vibe", "unknown"),
            "pacing":            base_profile.get("creative_intent", {}).get("pacing_style", "unknown"),
        }
        _save_factory_log(entry)
        logger.info(f"[FACTORY] ✅ Completed: {fname} → {output_video}")
        return entry

    except Exception as e:
        logger.error(f"[FACTORY] ❌ Failed: {fname} — {e}")
        try:
            shutil.move(video_path, os.path.join(FAILED_DIR, fname))
        except Exception:
            pass
        entry = {
            "job_id":  job_id,
            "source":  fname,
            "started": started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "status":  "failed",
            "error":   str(e),
        }
        _save_factory_log(entry)
        return entry


class BatchFactory:
    """
    The Great Machine's production floor.

    Watches Input_Queue/ for new video files.
    Processes up to MAX_WORKERS videos in parallel.
    Distributes finished videos to every enabled platform.
    Runs forever until stopped.
    """

    def __init__(self, max_workers: int = MAX_WORKERS):
        self.max_workers = max_workers
        self._running    = False
        self._semaphore  = threading.Semaphore(max_workers)
        self._threads: List[threading.Thread] = []
        _safe_mkdir(QUEUE_DIR, DONE_DIR, FAILED_DIR,
                    os.path.dirname(MARKET_MEMORY))

    def _worker(self, video_path: str, memory: Dict):
        with self._semaphore:
            _process_one_video(video_path, memory)

    def _scan_queue(self) -> List[str]:
        """Return list of video files in Input_Queue/ (not in sub-folders)."""
        found = []
        try:
            for f in os.listdir(QUEUE_DIR):
                fp = os.path.join(QUEUE_DIR, f)
                if os.path.isfile(fp):
                    ext = os.path.splitext(f)[1].lower()
                    if ext in VIDEO_EXTS:
                        found.append(fp)
        except Exception as e:
            logger.warning(f"[FACTORY] Queue scan error: {e}")
        return sorted(found)

    def start(self):
        """Start the factory loop. Blocks until stop() is called."""
        self._running = True
        logger.info(
            f"[FACTORY] 🏭 The Great Machine is running. "
            f"Watching: {QUEUE_DIR} | Workers: {self.max_workers}"
        )
        print(f"\n🏭 BATCH FACTORY ACTIVE — Drop videos into: {QUEUE_DIR}\n")

        while self._running:
            memory = _load_market_memory()
            queue  = self._scan_queue()

            for video_path in queue:
                if not self._running:
                    break
                # Launch worker thread (semaphore limits concurrency)
                t = threading.Thread(
                    target=self._worker,
                    args=(video_path, memory),
                    daemon=True,
                )
                t.start()
                self._threads.append(t)
                time.sleep(0.5)  # brief stagger to avoid I/O collision

            # Clean up finished threads
            self._threads = [t for t in self._threads if t.is_alive()]

            if not queue:
                logger.debug(f"[FACTORY] Queue empty. Sleeping {POLL_INTERVAL_S}s...")

            time.sleep(POLL_INTERVAL_S)

    def stop(self):
        """Gracefully stop the factory after current jobs finish."""
        self._running = False
        logger.info("[FACTORY] 🛑 Stop signal sent. Waiting for active jobs...")
        for t in self._threads:
            t.join(timeout=300)
        logger.info("[FACTORY] ✅ All jobs complete. Factory stopped.")

    def status(self) -> Dict:
        """Return current factory state."""
        queue = self._scan_queue()
        active = sum(1 for t in self._threads if t.is_alive())
        return {
            "running":       self._running,
            "queued":        len(queue),
            "active_jobs":   active,
            "max_workers":   self.max_workers,
            "queue_dir":     QUEUE_DIR,
            "market_memory": _load_market_memory(),
        }


# ── Module-level singleton ────────────────────────────────────────────────────
factory = BatchFactory()


if __name__ == "__main__":
    # Run standalone: python -m Compiler_Modules.batch_factory
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")
    print("🏭 Starting AMTCE Batch Factory — The Great Machine")
    factory.start()
