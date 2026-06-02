import os
import sys
import uuid
import warnings
from typing import List
from dotenv import load_dotenv

load_dotenv()
load_dotenv("Credentials/.env")

# Suppress Python 3.10 EOL warning from Google SDKs
warnings.filterwarnings("ignore", category=FutureWarning, module="google")

def lazy_load_genai_trace():
    import google.generativeai as genai
    from Diagnostics_Modules.gemini_trace import GeminiTrace
    
    _original_generate = genai.GenerativeModel.generate_content
    
    def patched_generate(self, *args, **kwargs):
        model_name = getattr(self, "model_name", "unknown")
        start = GeminiTrace.log_start(model_name, args, kwargs)
        result = _original_generate(self, *args, **kwargs)
        GeminiTrace.log_end(start)
        return result
        
    genai.GenerativeModel.generate_content = patched_generate

import asyncio
import csv
import gc
import glob
import json
import logging
import random
import re
import shutil
import string
import subprocess
import signal
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# External Libs (Safe Imports)
try:
    from dotenv import load_dotenv
    load_dotenv("Credentials/.env")
except ImportError:
    load_dotenv = lambda **kwargs: None  # Dummy fallback
    logging.warning(
        "⚠️ 'python-dotenv' not found. Environment variables must be set manually."
    )

try:
    import httpx
    from telegram import (
        CallbackQuery,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        InputMediaVideo,
        Update,
    )
    from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        TypeHandler,
        filters,
    )
    from telegram.request import HTTPXRequest
except ImportError as e:
    # Critical dependency check
    logging.critical(f"❌ Telegram Bot API not installed: {e}")
    sys.exit(1)

# 1. Immediate Logging Setup (captured before heavy modules)
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# 1. PORTAL GATEKEEPER (Cached)
_CACHED_PORTAL = None
def get_portal_cached():
    global _CACHED_PORTAL
    if _CACHED_PORTAL is None:
        from Health_handlers import get_portal
        _CACHED_PORTAL = get_portal()
    return _CACHED_PORTAL

portal = get_portal_cached()
globals().update(portal.__dict__)

from Compiler_Modules import compiler
from Audio_Modules import audio_deduplicator
from Intelligence_Modules.deduplication import DedupEngine

# Configurable Constants with Safe Defaults
CLEANUP_POLICY = os.getenv(
    "CLEANUP_POLICY", "delayed"
)  # immediate, on_success, delayed
DEBUG_JSON = int(os.getenv("DEBUG_JSON", "0"))
NET_RETRY_COUNT = int(os.getenv("NET_RETRY_COUNT", "3"))
NET_BACKOFF_BASE = float(os.getenv("NET_BACKOFF_BASE", "2.0"))
LOCK_WAIT_SECS = int(os.getenv("LOCK_WAIT_SECS", "5"))
TELEGRAM_MAX_UPLOAD_MB = int(os.getenv("TELEGRAM_MAX_UPLOAD_MB", "50"))
SESSION_TTL_SECS = int(os.getenv("SESSION_TTL_SECS", "86400"))
# --- REAL-TIME CASH-MAXIMIZER OVERRIDE ---
# [TUNED] CASH_MAX_MODE is permanently ON. Sequential processing prevents RAM
# crashes, ensuring maximum render throughput and zero failed uploads.
CASH_MAX_MODE = True
logger.info(
    "💰 [MONEY PRINTER ACTIVE] CASH_MAX_MODE permanently ON. Sequential rendering locked."
)
THREAD_POOL_SIZE = 1
COMPILATION_BATCH_SIZE = int(os.getenv("COMPILATION_BATCH_SIZE", "5"))
# Minimum clips needed to actually build a compilation (decoupled from trigger count)
COMPILATION_MIN_CLIPS = int(os.getenv("COMPILATION_MIN_CLIPS", "3"))
COMPILATION_INTRO_FALLBACK_SECS = float(os.getenv("COMPILATION_INTRO_FALLBACK_SECS", "8.0"))
DOWNLOADS_RETENTION_DAYS       = int(os.getenv("DOWNLOADS_RETENTION_DAYS", "30"))
PROCESSED_SHORTS_RETENTION_DAYS = int(os.getenv("PROCESSED_SHORTS_RETENTION_DAYS", "30"))
FINAL_COMPILATIONS_RETENTION_DAYS = int(os.getenv("FINAL_COMPILATIONS_RETENTION_DAYS", "30"))
# New purge policies (days) — override via .env
TEMP_RETENTION_HOURS           = int(os.getenv("TEMP_RETENTION_HOURS", "24"))   # temp/ — 1 day
DEBUG_FRAMES_RETENTION_HOURS   = int(os.getenv("DEBUG_FRAMES_RETENTION_HOURS", "24"))  # debug_frames/ — 1 day
KARAOKE_TMP_RETENTION_HOURS    = int(os.getenv("KARAOKE_TMP_RETENTION_HOURS", "24"))  # _karaoke_tmp/ — 1 day
INTELLIGENCE_CACHE_MAX_MB      = int(os.getenv("INTELLIGENCE_CACHE_MAX_MB", "50"))   # intelligence_cache/ — size cap

# --- NSFW PROFILE GATE ---
# Los Pollos (CPA) links are ONLY allowed when the video is classified as
# Adult/NSFW content by the Gemini forensic scanner. For all other niches,
# the engine uses safe Amazon/affiliate links only.
_NSFW_NICHE_LABEL = "Adult Content & NSFW Content"
def _is_nsfw_profile(detected_niche: str = "") -> bool:
    """Returns True only if the active video niche is Adult/NSFW."""
    return (detected_niche or "").strip() == _NSFW_NICHE_LABEL

ALLOWED_DOMAINS = ["instagram.com", "youtube.com", "youtu.be"]

# Directory Setup
JOB_DIR = "jobs"
COMPILATIONS_DIR = "final_compilations"

os.makedirs(JOB_DIR, exist_ok=True)
os.makedirs(COMPILATIONS_DIR, exist_ok=True)
os.makedirs("downloads", exist_ok=True)
os.makedirs("music", exist_ok=True)
os.makedirs("Original_audio", exist_ok=True)
os.makedirs("remarks", exist_ok=True)
os.makedirs("logo", exist_ok=True)
os.makedirs("models", exist_ok=True)

def init_audio_dirs():
    """Initializes the active and cooldown pools for the audio management system."""
    base_dir = "Original_audio"
    os.makedirs(os.path.join(base_dir, "active"), exist_ok=True)
    os.makedirs(os.path.join(base_dir, "cooldown"), exist_ok=True)

init_audio_dirs()

# --- SETUP VERIFICATION (Runs once on first boot) ---
# Checks DNN model, feature tests, and pipeline health.
# Skips automatically after first passing run (.setup_ok sentinel).
try:
    from setup_modules.setup_runner import run_setup
    from Audio_Modules.audio_pool_manager import pool_manager

    run_setup(quick=True)  # quick=True skips full render test at startup
except Exception as _setup_err:
    logger.warning(f"⚠️ Setup verification skipped: {_setup_err}")


# Thread Pool for Heavy Tasks
executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)

# --- ADMIN CONFIGURATION ---
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
if not ADMIN_IDS:
    # Auto-fallback: check if numeric owner ID or chat_id is available
    _owner_id = os.getenv("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if _owner_id.isdigit():
        ADMIN_IDS = [int(_owner_id)]
    else:
        _cid = os.getenv("chat_id", "").strip()
        if _cid.isdigit():
            ADMIN_IDS = [int(_cid)]


# --- SMART LOGGING FILTER ---
class PollingFilter(logging.Filter):
    def filter(self, record):
        # Filter out "getUpdates" spam but allow other API calls
        return "getUpdates" not in record.getMessage()


# Apply filter to noisy libraries
# We allow INFO level but filter out the polling spam
for lib in ["httpx", "telegram", "apscheduler"]:
    l = logging.getLogger(lib)
    l.setLevel(logging.INFO)
    l.addFilter(PollingFilter())

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN not found in .env! Exiting.")
    sys.exit(1)

# Global Activity State (Smart Idle Tracking)
PROCESSING_LOCK = asyncio.Lock()
QUEUE_SIZE = 0
QS_LOCK = threading.Lock()
background_tasks = set()
UPLOAD_SEMAPHORE = asyncio.Semaphore(1)

# TELEGRAM HOOK POOL (For High-Impact Variety & Trust)
HIGH_VOLTAGE_CTA_HOOKS = [
    "WARNING: {name}'s look is fatal. Handle with care. 🧨 / Sambhal ke, yeh look tabahi hai. / सावधान: यह लुक कहर है।",
    "{name} didn't just walk, she arrived. Main Character Energy. 👑 / Bas entry li aur sab khatam. / उसने बस प्रवेश किया और सब जीत लिया।",
    "Absolutely unlawful levels of perfection from {name}. ⚖️✨ / Yeh look kanuni taur par illegal hona chahiye. / यह स्तर अवैध होना चाहिए।",
    "Obsessed with {name} is an understatement. This is art. 🎨💎 / Sirf pasand nahi, nasha hai yeh. / यह सिर्फ पसंद नहीं, नशा है।",
    "The definition of 'Iconic' just got updated by {name}. 📈🌟 / Isey kehte hain asli Icon. / इसे कहते हैं असली आइकन।",
    "Zero competition. {name} owns the lane. 🏁🔥 / Koi muqabla hi nahi. / कोई मुकाबला नहीं।",
    "Stop scrolling. Witness {name}'s greatness. 🛑🤩 / Ruk jao. Isey dekho. / रुकें। इसे देखें।",
    "{name}'s fit is playing mind games. Unreal. 🤯🎭 / Dimag kharab karne wala look. / दिमाग खराब करने वाला लुक।",
    "Level 1000 Boss Vibes from {name}. Respect the drip. 💼🔥 / Boss level swag. / बॉस लेवल स्वैग。",
    "Shop {name}'s look to master the trend! 🛍️✨ / Is look ko apnao aur trend set karo! / इस लुक को apnao और ट्रेंड सेट करें!",
    "Shop for {name}'s outfit and own the spotlight! 💡👗 / Is outfit ko kharidein aur chha jayein! / इस ऑउटफिट को खरीदें और छा जाएं!",
]


class GlobalState:
    is_busy = False
    last_activity = time.time()
    _lock = threading.Lock()

    @classmethod
    def set_busy(cls, busy: bool):
        with cls._lock:
            cls.is_busy = busy
            cls.last_activity = time.time()

    @classmethod
    def get_idleness(cls):
        with cls._lock:
            if cls.is_busy:
                return 0
            return time.time() - cls.last_activity


# Locking Mechanisms
file_locks = {}
fl_lock = threading.Lock()


@contextmanager
def file_lock(path_str):
    """
    Simple in-process file/path locking.
    """
    path_str = str(path_str)
    with fl_lock:
        if path_str not in file_locks:
            file_locks[path_str] = threading.Lock()
        lock = file_locks[path_str]

    acquired = lock.acquire(timeout=LOCK_WAIT_SECS)
    try:
        if not acquired:
            logger.warning(
                f"🔒 Could not acquire lock for {path_str} in {LOCK_WAIT_SECS}s. Proceeding anyway (Split Brain Risk)."
            )
        yield acquired
    finally:
        if acquired:
            lock.release()


def atomic_write(target_path, content, mode="w", encoding="utf-8"):
    """
    Atomic write using tempfile and os.replace.
    Includes robustness for Windows file locking (WinError 5/32).
    """
    target_path = Path(target_path)
    # Write to a temp file in the same directory (to ensure same filesystem for atomic rename)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(dir=target_path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, mode, encoding=encoding) as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass  # Some systems/pipes don't support fsync

        # Atomic Rename with Retry
        max_retries = 3
        last_error = None

        for i in range(max_retries):
            try:
                os.replace(temp_path, target_path)
                return  # Success
            except OSError as e:
                last_error = e
                # WinError 5: Access denied, WinError 32: Used by process
                # If these occur, we wait and try again or use fallback
                if getattr(e, "winerror", 0) in [5, 32]:
                    time.sleep(0.5)
                    # Force delete strategy for Windows if standard replace fails
                    try:
                        if os.path.exists(target_path):
                            os.remove(target_path)
                        os.rename(temp_path, target_path)
                        return
                    except Exception:
                        pass  # Retry standard loop
                elif i == max_retries - 1:
                    raise e

        # If loop finishes without success
        if last_error:
            raise last_error

    except Exception as e:
        logger.error(f"❌ Atomic write failed: {e}")
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except:
            pass


# Presets Cache
CACHED_PRESETS = None
PRESETS_LOCK = threading.Lock()


def get_presets():
    global CACHED_PRESETS
    with PRESETS_LOCK:
        if CACHED_PRESETS is not None:
            return CACHED_PRESETS

        try:
            if os.path.exists("The_json/title_expansion_presets.json"):
                with open(
                    "The_json/title_expansion_presets.json", "r", encoding="utf-8"
                ) as f:
                    CACHED_PRESETS = json.load(f)
                logger.debug(
                    f"✅ Loaded {len(CACHED_PRESETS)} title expansion presets."
                )
            else:
                CACHED_PRESETS = {}
        except Exception as e:
            logger.error(f"❌ Failed to load presets: {e}")
            return {}

        return CACHED_PRESETS


def sanitize_logs(text):
    """Redact sensitive keys from logs/debug artifacts."""
    if not isinstance(text, str):
        return text
    pattern = (
        r'(?i)(token|key|secret|password|cookie|auth)\s*[:=]\s*["\']?([^"\',\s]+)["\']?'
    )
    return re.sub(pattern, r"\1=***REDACTED***", text)


def sanitise_ig_caption(text: str) -> str:
    """
    Strips internal tracking tags (#vid_XXXXXX) from captions before
    they are stored or published to Instagram.
    These tags are only used internally by the comment-bot UID lookup;
    they must never appear in the live published caption.
    """
    if not isinstance(text, str):
        return text
    return re.sub(r'\s*#vid_[a-f0-9]{6}', '', text, flags=re.IGNORECASE).strip()


# Global State
user_sessions = {}
user_result_locks = {}
g_session_lock = threading.Lock()

# Per-user pending job queue: {user_id: deque([(url, title), ...])}
# When a job is running and user sends another URL, it goes here.
import collections as _collections
user_pending_jobs: dict = _collections.defaultdict(_collections.deque)

WATCHDOG_TIMEOUT = int(os.getenv("JOB_WATCHDOG_SECS", "1200"))  # 20 min stuck-job kill
_job_start_time: float = 0.0  # epoch timestamp when current job started


def get_session_lock(user_id):
    with g_session_lock:
        if user_id not in user_result_locks:
            user_result_locks[user_id] = threading.RLock()
        return user_result_locks[user_id]

@contextmanager
def acquire_session_lock(user_id, timeout=10):
    lock = get_session_lock(user_id)
    acquired = lock.acquire(timeout=timeout)
    if not acquired:
        logger.error(f"?? [DEADLOCK_PREVENT] Could not acquire lock for user {user_id} within {timeout}s")
        raise TimeoutError(f"Session busy. Please try again in a moment.")
    try:
        yield lock
    finally:
        lock.release()


def save_session(user_id):
    """Persist individual session to disk."""
    if user_id in user_sessions:
        try:
            data = json.dumps(user_sessions[user_id], default=str)
            atomic_write(os.path.join(JOB_DIR, f"session_{user_id}.json"), data)
        except Exception as e:
            logger.error(f"Failed to persist session {user_id}: {e}")


def load_sessions():
    """Recover sessions from disk on startup."""
    try:
        now = time.time()
        count = 0
        for f in glob.glob(os.path.join(JOB_DIR, "session_*.json")):
            try:
                # Check age
                mtime = os.path.getmtime(f)
                if now - mtime > SESSION_TTL_SECS:
                    os.remove(f)  # Expired
                    continue

                with open(f, "r") as fp:
                    data = json.load(fp)
                    # Extract user_id from filename
                    fname = os.path.basename(f)
                    uid = int(fname.replace("session_", "").replace(".json", ""))
                    user_sessions[uid] = data
                    count += 1
            except Exception:
                pass
        logger.info(f"🔄 Restored {count} active sessions from disk.")
    except Exception as e:
        logger.warning(f"Session recovery failed: {e}")


def _get_session_niche(video_path: str) -> str:
    """
    Reads the niche sidecar file or infers niche from metadata.
    Returns detected niche or 'General_Fallback'.

    Strategy (in priority order):
      1. Exact sidecar next to the video file
      2. Glob scan in downloads/ and Processed Shorts/ using increasingly
         stripped base names (handles _short, _01, _processed, timestamps, etc.)
      3. Keyword inference from companion .json metadata  ← LAST RESORT ONLY
    """
    import re as _re

    def _read_sidecar(path: Path) -> str | None:
        """Return niche string from a sidecar file, or None on any error."""
        try:
            with open(path, "r", encoding="utf-8") as _f:
                _d = json.load(_f)
            _n = _d.get("detected_niche", "")
            if _n:
                # Sanitize the raw niche name so it matches the GitHub workflow credentials folders
                if "fashion" in _n.lower() or "style" in _n.lower():
                    _n = "Fashion"
                elif "nsfw" in _n.lower() or "adult" in _n.lower():
                    _n = "NSFW"
                elif "paparazzi" in _n.lower():
                    _n = "Paparazzi"
                
                logger.info(f"📂 [NICHE ROUTER] Sidecar niche read: '{_n}' ← {path.name}")
                return _n
        except Exception:
            pass
        return None

    try:
        if not video_path:
            return "General_Fallback"

        video_path_obj = Path(video_path)
        base_name = video_path_obj.stem  # e.g. "Akanksha_puri_short_01"

        # ── 1. Exact sidecar next to the video ─────────────────────────────
        _exact = video_path_obj.with_suffix(".niche.json")
        if _exact.exists():
            _r = _read_sidecar(_exact)
            if _r:
                return _r

        # ── 2. Build a ranked list of candidate base-name prefixes ─────────
        # Strip common machine-added suffixes so we can match the original
        # "Akanksha_puri.niche.json" no matter how the compiled filename grew.
        _strip_patterns = [
            r"_processed$",
            r"_short$",
            r"_compiled$",
            r"_reencoded$",
            r"_final$",
            r"_\d+$",           # trailing _01, _02, _123 …
            r"_\d{8,}$",        # trailing unix timestamps
        ]
        _prefixes_seen: list[str] = [base_name]
        _current = base_name
        for _pat in _strip_patterns:
            _stripped = _re.sub(_pat, "", _current)
            if _stripped and _stripped != _current and _stripped not in _prefixes_seen:
                _prefixes_seen.append(_stripped)
                _current = _stripped   # chain-strip (e.g. _short_01 → _short → base)

        _search_dirs = [
            Path("downloads"),
            Path("Processed Shorts"),
            video_path_obj.parent,
        ]

        for _prefix in _prefixes_seen:
            for _dir in _search_dirs:
                _candidate = _dir / f"{_prefix}.niche.json"
                if _candidate.exists():
                    _r = _read_sidecar(_candidate)
                    if _r:
                        return _r

        # ── 2b. Glob fallback: scan downloads/ for any .niche.json whose
        #        stem is a prefix of our base_name (catches actress name + extras)
        for _sidecar_file in sorted(Path("downloads").glob("*.niche.json")):
            _sc_stem = _sidecar_file.stem.replace(".niche", "")
            # Accept if our base_name starts with the sidecar's stem
            if base_name.startswith(_sc_stem) or any(p.startswith(_sc_stem) for p in _prefixes_seen):
                _r = _read_sidecar(_sidecar_file)
                if _r:
                    logger.info(f"🔍 [NICHE ROUTER] Glob-matched sidecar: {_sidecar_file.name}")
                    return _r

        # ── 3. LAST RESORT: keyword inference from companion .json ──────────
        # NOTE: This should almost never fire now that glob covers most cases.
        main_json = video_path_obj.with_suffix(".json")
        if not main_json.exists():
            main_json = Path("Processed Shorts") / f"{base_name}.json"

        if main_json.exists():
            try:
                with open(main_json, "r", encoding="utf-8") as f:
                    meta = json.load(f)

                item_name = str(meta.get("pipeline_metrics", {}).get("monetization", {}).get("item_name", "")).lower()
                caption = str(meta.get("caption_data", {}).get("caption", "")).lower()

                mappings = {
                    "Fashion & Style": ["bra", "outfit", "dress", "style", "wear", "clothing", "fashion", "look"],
                    "AI Tech & Futuristic Content": ["ai", "tech", "robot", "future", "gadget"],
                    "Comedy & Relatable Meme": ["joke", "funny", "meme", "comedy", "laugh"],
                    "Food & Cooking": ["food", "cooking", "recipe", "chef", "eat"],
                    "Fitness & Body Transformation": ["fitness", "gym", "workout", "body", "muscle"],
                }

                for niche_name, keywords in mappings.items():
                    if any(kw in item_name or kw in caption for kw in keywords):
                        logger.info(
                            f"🧠 [NICHE ROUTER] Keyword-inferred niche: '{niche_name}' "
                            f"(item_name='{item_name[:60]}')"
                        )
                        return niche_name
            except Exception as _inf_e:
                logger.debug(f"Niche inference failed: {_inf_e}")

    except Exception as e:
        logger.warning(f"⚠️ [NICHE ROUTER] Error: {e}")

    return "General_Fallback"



# Global State
COMPILATION_BATCH_SIZE = int(os.getenv("COMPILATION_BATCH_SIZE", "5"))
# Minimum clips required for a compilation (independently controlled from trigger count)
COMPILATION_MIN_CLIPS = int(os.getenv("COMPILATION_MIN_CLIPS", "3"))

# ==================== AUTO-INSTALL & SETUP ====================

# ==================== AUTO-INSTALL & SETUP ====================

# Cached Hardware Capabilites
_hardware_cache = None


def detect_hardware_capabilities():
    """
    Detect hardware capabilities (Cached) via ComputeCaps.
    """
    global _hardware_cache
    if _hardware_cache:
        return _hardware_cache

    from Upscale_Modules.compute_caps import ComputeCaps

    caps = ComputeCaps.get()

    hardware_info = {
        "has_gpu": caps["has_cuda"] or caps["gpu_fast"],  # Logical GPU presence
        "gpu_name": "NVIDIA GPU" if caps["has_cuda"] else "CPU",
        "vram_gb": caps["vram_gb"],
        "cuda_available": caps["has_cuda"],
    }

    if hardware_info["has_gpu"]:
        logger.info(
            f"🎮 GPU Detected via ComputeCaps: {hardware_info['gpu_name']} ({hardware_info['vram_gb']:.1f} GB VRAM)"
        )
    else:
        logger.info("ℹ️ No GPU detected (ComputeCaps).")

    _hardware_cache = hardware_info
    return hardware_info


def resolve_compute_mode():
    """
    Resolve the final compute mode.
    Downgrades to CPU if VRAM is too low (< 6GB).
    """
    cpu_mode = os.getenv("CPU_MODE", "auto").lower()
    gpu_mode = os.getenv("GPU_MODE", "auto").lower()
    min_vram = int(os.getenv("MIN_VRAM_GB", "6"))

    # 1. Forced Modes
    if cpu_mode == "on":
        return "cpu"

    # 2. Hardware Capability Check
    hardware = detect_hardware_capabilities()

    # 3. GPU Logic
    if gpu_mode in ["on", "auto"]:
        if hardware["cuda_available"]:
            # Check VRAM - Safe threshold for Heavy AI is 6GB
            if hardware["vram_gb"] < min_vram:
                if gpu_mode == "on":
                    logger.warning(
                        f"⚠️ GPU_MODE=ON requested, but VRAM ({hardware['vram_gb']:.1f}GB) is below stable limit ({min_vram}GB)."
                    )
                    logger.info(
                        "⚡ Downgrading to CPU mode for stability (Heavy modules will be disabled)."
                    )
                else:
                    logger.info(
                        f"⚙️ VRAM ({hardware['vram_gb']:.1f}GB) < {min_vram}GB. Selecting CPU mode."
                    )
                return "cpu"

            logger.info(
                f"🤖 {'GPU_MODE=ON' if gpu_mode == 'on' else 'GPU_MODE=auto'}: Sufficient VRAM ({hardware['vram_gb']:.1f}GB). Selecting GPU."
            )
            return "gpu"

    # Default fallback
    return "cpu"


def check_and_update_env():
    """
    Auto-updates .env file with missing keys and smart defaults.
    """
    env_path = "Credentials/.env"
    if not os.path.exists(env_path):
        logger.warning("⚠️ .env file not found. Creating template...")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("""# ==================== CORE SETTINGS ====================
# REQUIRED: Get your bot token from @BotFather on Telegram
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE

# REQUIRED: Get your API key from https://aistudio.google.com/app/apikey
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE

# ==================== PERFORMANCE ====================
# Modes: auto, on, off
CPU_MODE=auto
GPU_MODE=auto
REENCODE_PRESET=fast
REENCODE_CRF=25

# ==================== ENHANCEMENT ====================
ENHANCEMENT_LEVEL=medium
TARGET_RESOLUTION=1080:1920

# ==================== TRANSFORMATIVE FEATURES ====================
ADD_TEXT_OVERLAY=yes
TEXT_OVERLAY_TEXT=YOUR_BRAND_HANDLE
TEXT_OVERLAY_POSITION=bottom
TEXT_OVERLAY_STYLE=modern

ADD_COLOR_GRADING=yes
COLOR_FILTER=cinematic
COLOR_INTENSITY=0.5

ADD_SPEED_RAMPING=yes
SPEED_VARIATION=0.15

FORCE_AUDIO_REMIX=yes

# ==================== COMPILATION ====================
COMPILATION_BATCH_SIZE=6
SEND_TO_YOUTUBE=off
DEFAULT_HASHTAGS_SHORTS=#shorts #viral #trending
DEFAULT_HASHTAGS_COMPILATION=#compilation #funny #viral

# ==================== MONETIZATION ====================
LOS_POLLOS_YOUTUBE=no
LOS_POLLOS_TELEGRAM=yes

# ==================== TRANSITIONS ====================
TRANSITION_DURATION=0.5
TRANSITION_INTERVAL=5
GEMINI_TITLE_COMPLICATION=on
""")
        logger.info(
            "✅ Created .env template. Please update TELEGRAM_BOT_TOKEN and GEMINI_API_KEY!"
        )

    # Load current env content
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    updates = []

    # Define required keys and defaults (HARDENED)
    required_keys = {
        "CPU_MODE": "auto",
        "GPU_MODE": "auto",
        "ENHANCEMENT_LEVEL": "medium",
        "TRANSITION_INTERVAL": "5",
        "TRANSITION_DURATION": "0.5",
        "FORCE_AUDIO_REMIX": "yes",
        "ADD_TEXT_OVERLAY": "yes",
        "ADD_SPEED_RAMPING": "yes",
        "NET_RETRY_COUNT": "3",
        "NET_BACKOFF_BASE": "2.0",
        "LOCK_WAIT_SECS": "5",
        "TELEGRAM_MAX_UPLOAD_MB": "50",
        "SESSION_TTL_SECS": "86400",
        "TELEGRAM_MAX_UPLOAD_MB": "50",
        "GEMINI_TITLE_COMPLICATION": "on",
        "ENABLE_COMMUNITY_POST_COMPILATION": "yes",
        "ENABLE_COMMUNITY_POST_SHORTS": "no",
        "META_COMPILE_UPLOAD": "no",
        "LOS_POLLOS_YOUTUBE": "no",
        "LOS_POLLOS_TELEGRAM": "yes",
        "ENABLE_FASHION_SCOUT": "yes",
        "ENABLE_REACTION_ENGINE": "no",        # Reaction Engine — opt-in only
        "ENABLE_INFLUENCER_REFINEMENT": "no",  # AI Influencer Face Swap — flip to yes on GPU
    }

    for key, default in required_keys.items():
        if key not in os.environ and f"{key}=" not in content:
            logger.info(f"➕ Auto-adding missing key: {key}={default}")
            updates.append(f"\n# Auto-added by Smart Installer\n{key}={default}")
            os.environ[key] = default

    if updates:
        with open(env_path, "a", encoding="utf-8") as f:
            f.writelines(updates)
        logger.info(f"✅ Auto-added {len(updates)} missing keys to .env")

    # Expose resolved compute mode
    cm = resolve_compute_mode()
    os.environ["COMPUTE_MODE"] = cm
    logger.info(f"🚀 FINAL COMPUTE MODE: {cm.upper()}")

    # 3. Heal JSON State Files
    check_and_heal_json_files()


def check_and_heal_json_files():
    """
    Auto-Heals missing JSON state/config files with intelligent defaults.
    Analyzes user behavior patterns to populate initial data where applicable.
    """

    # 1. cleanup_state.json
    # Tracks last cleanup time. Default: Never run checking.
    p_cleanup = "The_json/cleanup_state.json"
    if not os.path.exists(p_cleanup):
        try:
            with open(p_cleanup, "w") as f:
                json.dump({"last_run": 0}, f)
            logger.info(f"🩹 Auto-Healed: {p_cleanup}")
        except:
            pass

    # 2. community_promo_state.json
    # Tracks community post rate limits and hashes.
    p_promo = "The_json/community_promo_state.json"
    if not os.path.exists(p_promo):
        try:
            with open(p_promo, "w") as f:
                json.dump({"last_run": 0, "posted_hashes": []}, f)
            logger.info(f"🩹 Auto-Healed: {p_promo}")
        except:
            pass

    # 3. policy_memory.json
    # Tracks strategy success rates. Default: Empty memory.
    p_policy = "The_json/policy_memory.json"
    if not os.path.exists(p_policy):
        try:
            with open(p_policy, "w") as f:
                json.dump({}, f)
            logger.info(f"🩹 Auto-Healed: {p_policy}")
        except:
            pass

    # 4. caption_prompt.json
    # Stores the "Safe Fallback" caption.
    # We populate this with a high-quality "Transformative" example.
    p_caption = "The_json/caption_prompt.json"
    if not os.path.exists(p_caption):
        try:
            default_data = {
                "caption_final": "Mixing vintage denim with modern confidence for a timeless look",
                "last_source": "auto_healer",
                "timestamp": datetime.now().isoformat(),
            }
            with open(p_caption, "w") as f:
                json.dump(default_data, f, indent=2)
            logger.info(f"🩹 Auto-Healed: {p_caption}")
        except:
            pass

    # 5. title_expansion_presets.json
    # Presets for interactive title composition.
    # We populate this with "Viral/Clickbait" patterns tailored for Shorts.
    p_titles = "The_json/title_expansion_presets.json"
    if not os.path.exists(p_titles):
        try:
            presets = {
                "1": {"label": "Wait for it... 😱", "suffix": " #waitforit"},
                "2": {"label": "You won't believe this!", "suffix": " #shocking"},
                "3": {"label": "Satisfying 😌", "suffix": " #satisfying"},
                "4": {"label": "Viral Moment", "suffix": " #viral"},
                "5": {"label": "Must Watch", "suffix": " #mustwatch"},
                "6": {"label": "Relatable 😂", "suffix": " #relatable"},
            }
            with open(p_titles, "w", encoding="utf-8") as f:
                json.dump(presets, f, indent=2, ensure_ascii=False)
            logger.info(f"🩹 Auto-Healed: {p_titles}")
        except:
            pass


# Conditional imports removed for lazy loading
# compute_mode = os.environ.get("COMPUTE_MODE", "cpu") - moved to resolve_compute_mode if needed

# ==================== UTILS ====================

UPLOAD_LOG = "Datasets_and_text_files/upload_log.csv"


def _ensure_log_header():
    if not os.path.exists(UPLOAD_LOG):
        with open(UPLOAD_LOG, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "video_id",
                    "caption_style",
                    "ypp_risk",
                    "approved",
                    "user_decision",
                    "channel_name",
                    "affiliate_link",
                ]
            )


def log_video(
    file_path: str,
    yt_link: str,
    title: str,
    style: str = "unknown",
    ypp_risk: str = "unknown",
    action: str = "approved",
    channel_name: str = "default_channel",
    affiliate_link: str = "none",
    video_uid: str = "none",
):
    _ensure_log_header()
    # Atomic Append
    video_id = yt_link.split("/")[-1] if yt_link else "upload_failed"
    approved_bool = "true" if action == "approved" else "false"

    # Schema: timestamp, video_id, caption_style, ypp_risk, approved, user_decision, channel_name
    row = [
        datetime.utcnow().isoformat(),
        video_id,
        style,
        ypp_risk,
        approved_bool,
        action,
        channel_name,
        affiliate_link,
    ]

    with file_lock(UPLOAD_LOG):
        with open(UPLOAD_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)

    # Metadata JSON Sidecar
    try:
        final_meta = {
            "unique_id": video_id,
            "video_uid": video_uid,
            "source_path": file_path,
            "youtube_link": yt_link,
            "title": title,
            "caption_style": style,
            "ypp_risk": ypp_risk,
            "user_decision": action,
            "channel_name": channel_name,
            "affiliate_link": affiliate_link,
            "created_at": datetime.utcnow().isoformat(),
            "pipeline_version": "4.0-final-lock",
        }
        meta_path = str(file_path) + ".final.json"
        atomic_write(meta_path, json.dumps(final_meta))
    except Exception:
        pass


def total_uploads() -> int:
    if not os.path.exists(UPLOAD_LOG):
        return 0
    with open(UPLOAD_LOG, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
        return max(0, len(rows) - 1)


def last_n_filepaths(n: int) -> list:
    """Get the last N video file paths from the upload log, filtered by recency."""
    if not os.path.exists(UPLOAD_LOG):
        return []

    with open(UPLOAD_LOG, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Filter by timestamp - only videos from last 24 hours
    from datetime import datetime, timedelta

    cutoff_time = datetime.utcnow() - timedelta(hours=24)

    recent_rows = []
    for r in rows:
        try:
            timestamp_str = r.get("timestamp", "")
            if timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if timestamp > cutoff_time:
                    recent_rows.append(r)
        except:
            # If timestamp parsing fails, skip this row
            continue

    # Get last N from recent rows
    subset = recent_rows[-n:]
    paths = [r.get("file_path") for r in subset if r.get("file_path")]

    # Return only paths that exist
    valid_paths = [p for p in paths if p and os.path.exists(p)]

    logger.info(f"📊 Found {len(valid_paths)} recent videos for compilation (last 24h)")
    return valid_paths


# Rate Limiting
class RateLimiter:
    def __init__(self, limit=10, period=60):
        self.limit = limit
        self.period = period
        self.users = {}
        self.lock = threading.Lock()

    def check(self, user_id):
        with self.lock:
            now = time.time()
            if user_id not in self.users:
                self.users[user_id] = []

            # Filter timestamps
            self.users[user_id] = [
                ts for ts in self.users[user_id] if now - ts < self.period
            ]

            if len(self.users[user_id]) >= self.limit:
                return False

            self.users[user_id].append(now)
            return True


# Initialize Rate Limiter
user_limiter = RateLimiter(
    limit=9999,  # REMOVED: int(os.getenv("USER_RATE_LIMIT_PER_MIN", "10")),
    period=60,
)


async def with_retry(func, *args, **kwargs):
    """
    Robust Retry Wrapper for Network Calls.
    """
    last_exception = None
    for attempt in range(NET_RETRY_COUNT):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            # Fail fast on 4xx (Client Error)
            msg = str(e)
            if "40" in msg or "400" in msg or "404" in msg or "403" in msg:
                # Very rough heuristic, standard http libs usually provide status codes
                logger.error(f"❌ Non-Retriable Error: {e}")
                raise e

            wait = NET_BACKOFF_BASE**attempt
            logger.warning(
                f"⚠️ Network Op Failed ({attempt + 1}/{NET_RETRY_COUNT}): {e}. Retrying in {wait}s..."
            )
            await asyncio.sleep(wait)

    logger.error(f"❌ Network Op Failed after {NET_RETRY_COUNT} attempts.")
    raise last_exception


async def safe_reply(update: Update, text: str, force: bool = False, **kwargs):
    """
    Robust message sender with improved error handling and force-bypass for rate limits.
    Handles CallbackQuery updates gracefully.
    """
    try:
        if not update.effective_user:
            logger.warning("⚠️ safe_reply: No effective_user (channel post?). Skipping.")
            return
        user_id = update.effective_user.id

        # Rate Limit Check (Unless Forced)
        if not force and not user_limiter.check(user_id):
            logger.warning(f"🛑 Rate limit hit for user {user_id}")
            return

        if text and len(text) > 4096:
            logger.warning(
                f"✂️ Message too long ({len(text)} chars). Truncating to 4096 for user {user_id}"
            )
            text = text[:4093] + "..."

        for attempt in range(1, 4):
            try:
                # Handle CallbackQuery Logic (Where update.message might be None)
                target_msg = update.effective_message
                if not target_msg:
                    # Fallback for weird updates
                    if update.callback_query:
                        target_msg = update.callback_query.message

                if target_msg:
                    await target_msg.reply_text(
                        text,
                        read_timeout=30,
                        write_timeout=30,
                        connect_timeout=30,
                        pool_timeout=30,
                        **kwargs
                    )
                else:
                    logger.warning("⚠️ safe_reply: No target message found to reply to.")

                return
            except (NetworkError, TimedOut, httpx.HTTPError) as e:
                logger.warning(
                    f"🛑 Reply failed (Attempt {attempt}/3): {e}. Retrying in 5s..."
                )
                await asyncio.sleep(5)
            except Exception as e:
                # Catch BadRequest: "Message is not modified" or "Chat not found"
                # Do NOT retry fatal errors
                logger.warning(f"⚠️ safe_reply fatal error (No Retry): {e}")
                return

        logger.error("❌ Failed to send message after retries.")

    except Exception as e:
        logger.error(f"❌ safe_reply Crashed: {e}", exc_info=True)


class ProgressFile:
    def __init__(self, filename, logger_func):
        self._f = open(filename, "rb")
        self._size = os.path.getsize(filename)
        self._seen = 0
        self._last_log = -10
        self._logger = logger_func
        self._path = filename

    def read(self, size=-1):
        chunk = self._f.read(size)
        if chunk:
            self._seen += len(chunk)
            if self._size > 0:
                pct = int((self._seen / self._size) * 100)
                if pct >= self._last_log + 10:
                    if pct < 100:
                        self._logger(
                            f"📤 Uploading: {pct}% ({os.path.basename(self._path)})"
                        )
                        self._last_log = pct
        return chunk

    def seek(self, offset, whence=0):
        return self._f.seek(offset, whence)

    def tell(self):
        return self._f.tell()

    def close(self):
        return self._f.close()

    def fileno(self):
        return self._f.fileno()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


async def safe_video_reply(
    update: Update, video_path: str, caption: str = None, reply_markup=None
):
    """
    Robust video sender with a 3-Tier Multi-Upload Strategy:
    Tier 1: Local API File:// URI (0s Delay)
    Tier 2: Compressed FFmpeg Proxy (~10s Delay)
    Tier 3: Full 46MB Upload (~5m Delay)
    """
    user_id = update.effective_user.id
    if not user_limiter.check(user_id):
        return

    try:
        f_size = os.path.getsize(video_path)
        if f_size == 0:
            logger.error(
                f"❌ Critical: Video file is 0 bytes! Cannot send. ({video_path})"
            )
            await safe_reply(
                update,
                "❌ Processing Error: Resulting video is empty (0 bytes). Check logs.",
            )
            return

        size_mb = f_size / (1024 * 1024)
        if size_mb > TELEGRAM_MAX_UPLOAD_MB:
            await safe_reply(
                update,
                f"⚠️ Video is {size_mb:.1f}MB (Max {TELEGRAM_MAX_UPLOAD_MB}MB). Link/File saved locally.",
            )
            return

        # Character Limit Truncation (Telegram Limit: 1024 for captions)
        if caption and len(caption) > 1024:
            logger.warning(
                f"✂️ Caption too long ({len(caption)} chars). Truncating to 1024."
            )
            caption = caption[:1021] + "..."

    except Exception as e:
        logger.error(f"Failed size check: {e}")
        pass

    # --- TIER 1: LOCAL API SERVER (FILE:// URI) ---
    local_api = os.getenv("LOCAL_BOT_API_URL")
    if local_api:
        abs_path = f"file://{os.path.abspath(video_path)}"
        logger.info(f"⚡ Tier 1: Attempting Local API Upload (0s Delay) -> {abs_path}")
        try:
            if update.message:
                async with UPLOAD_SEMAPHORE:
                    await update.message.reply_video(
                        video=abs_path,
                        caption=caption,
                        read_timeout=120,
                        write_timeout=120,
                        reply_markup=reply_markup,
                    )
            logger.info("✅ Tier 1 (Local API) Success!")
            return
        except Exception as e:
            logger.warning(f"⚠️ Tier 1 Local API Failed (falling back to Tier 2): {e}")

    # --- TIER 2: COMPRESSED PROXY GENERATION ---
    logger.info("⏱️ Tier 2: Generating Compressed Telegram Preview (~10s)...")
    proxy_name = f"temp_{''.join(random.choices(string.ascii_letters + string.digits, k=6))}_proxy.mp4"
    proxy_path = os.path.join(os.path.dirname(video_path) or ".", proxy_name)

    tier2_success = False
    try:
        # Compress video for Telegram review (720p, veryfast preset, good CRF)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-vf",
            "scale=-2:720",  # Scale down to 720p height for legible text
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            proxy_path,
        ]
        # Run sync or async
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )

        if os.path.exists(proxy_path) and os.path.getsize(proxy_path) > 0:
            logger.info(
                f"📤 Tier 2 Proxy Ready ({os.path.getsize(proxy_path) / 1024 / 1024:.2f}MB). Uploading..."
            )
            for attempt in range(1, 4):
                try:
                    if update.message:
                        async with UPLOAD_SEMAPHORE:
                            with open(proxy_path, "rb") as f:
                                await update.message.reply_video(
                                    video=f,
                                    caption=caption,
                                    reply_markup=reply_markup,
                                    read_timeout=300,
                                    write_timeout=300,
                                )
                    tier2_success = True
                    logger.info("✅ Tier 2 Compressed Preview Uploaded!")
                    break
                except Exception as e:
                    logger.warning(
                        f"🛑 Tier 2 Proxy send failed (Attempt {attempt}/3): {e}"
                    )
                    await asyncio.sleep(5)
    except Exception as e:
        logger.warning(f"⚠️ Tier 2 Proxy Generation Failed: {e}")
    finally:
        # Cleanup proxy file
        if os.path.exists(proxy_path):
            try:
                os.remove(proxy_path)
            except:
                pass

    if tier2_success:
        return

    # --- TIER 3: FULL FALLBACK UPLOAD ---
    logger.info(
        f"🐢 Tier 3: Falling back to FULL standard upload (This may take 5+ mins for {size_mb:.1f}MB)..."
    )
    for attempt in range(1, 6):
        try:
            if update.message:
                async with UPLOAD_SEMAPHORE:
                    with open(video_path, "rb") as f:
                        await update.message.reply_video(
                            video=f,
                            caption=caption,
                            read_timeout=None,
                            write_timeout=None,
                            connect_timeout=600,
                            pool_timeout=600,
                            reply_markup=reply_markup,
                        )
                logger.info(
                    f"✅ Tier 3 Full Upload Success -> {os.path.basename(video_path)}"
                )
            return
        except (NetworkError, TimedOut, httpx.HTTPError) as e:
            logger.warning(
                f"🛑 Video reply failed (Attempt {attempt}/5): {e}. Retrying in 10s..."
            )
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"❌ Video reply error: {e}")
            break

    logger.error("❌ Failed to send video after retries.")
    await safe_reply(update, "❌ Failed to send video due to network timeout.")


def _validate_url(url: str) -> bool:
    """Detects if a string contains an authorized URL anywhere."""
    if not url:
        return False
    # Robust URL detection regex
    url_pattern = r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
    matches = re.findall(url_pattern, url)
    if not matches:
        return False

    # Check if any matched URL is from allowed domains
    for m in matches:
        parsed = urlparse(m)
        domain = parsed.netloc.lower()
        if any(allowed in domain for allowed in ALLOWED_DOMAINS):
            logger.info(f"✅ [URL_OK] Detected allowed domain: {domain}")
            return True
    logger.warning(f"⚠️ [URL_REJECT] No allowed domains found in: {url}")
    return False


def _sanitize_title(title: str) -> str:
    # Allow spaces but remove other special characters
    clean = re.sub(r"[^\w\s-]", "", title)
    # clean = clean.replace(' ', '_')  <-- REMOVED: Keep spaces for YouTube title
    return clean[:100]  # Increased limit slightly for better titles


def _get_hashtags(text: str) -> str:
    link_count = len(re.findall(r"https?://", text))
    if link_count > 1:
        return os.getenv("DEFAULT_HASHTAGS_COMPILATION", "").strip()
    return os.getenv("DEFAULT_HASHTAGS_SHORTS", "").strip()

    return os.getenv("DEFAULT_HASHTAGS_SHORTS", "").strip()


def parse_user_input(text: str):
    """
    Parses the inline Telegram reply used at the title-selection step.

    Accepted formats
    ----------------
    "2"                               → title_index=2,  affiliate_link=None,  link_type=None, real_mrp=None
    "2 https://amzn.to/xyz"           → title_index=2,  affiliate_link=URL,   link_type='exact', real_mrp=None
    "2  https://amzn.to/xyz"          → title_index=2,  affiliate_link=URL,   link_type='alternative', real_mrp=None
    "2 https://amzn.to/xyz 25300"     → title_index=2,  affiliate_link=URL,   link_type='exact', real_mrp=25300
    "2  https://amzn.to/xyz 25300"    → title_index=2,  affiliate_link=URL,   link_type='alternative', real_mrp=25300
    "/skip"                           → title_index=None, affiliate_link=None, link_type=None, real_mrp=None
    "/skip https://amzn.to/x 25300"  → title_index=None, affiliate_link=URL, link_type='exact', real_mrp=25300

    Link type detection (raw string, before split):
      - SINGLE space between number and URL  → link_type='exact'        (user found exact product)
      - DOUBLE space between number and URL  → link_type='alternative'  (user found similar product)

    Real MRP (optional):
      - Last token after the URL, if it is a plain integer (no ₹ symbol needed)
      - Example: "2 https://amzn.to/xyz 25300"  → real_mrp=25300

    Returns
    -------
    (title_index: int|None, affiliate_link: str|None, link_type: str|None, real_mrp: int|None)
    """
    raw = text  # keep raw for space-count detection
    parts = raw.split()
    title_index = None
    affiliate_link = None
    link_type = None
    real_mrp = None

    if parts:
        # First token: number or /skip
        try:
            title_index = int(parts[0])
        except ValueError:
            pass  # /skip or garbage — leave title_index None

        # Second token (optional): affiliate URL
        if len(parts) >= 2 and re.match(r'https?://', parts[1]):
            affiliate_link = parts[1]

            # ── Space-count detection ───────────────────────────────────────
            # Find where the URL starts in the raw string
            _url_pos = raw.find(parts[1])
            _prefix  = raw[:_url_pos] if _url_pos != -1 else ""
            # Count trailing spaces after the first token
            _spaces_after_token = len(_prefix) - len(_prefix.rstrip(" "))
            if _spaces_after_token >= 2:
                link_type = "alternative"   # double-space = user found a similar/alt product
            else:
                link_type = "exact"         # single-space = user found exact celebrity product

            logger.info(
                f"[AFFILIATE_PARSE] link_type='{link_type}' spaces={_spaces_after_token} "
                f"link={affiliate_link}"
            )

            # ── Optional real MRP (third token, last) ─────────────────────
            # Strip ₹/Rs./INR prefix if user typed it, then parse as int
            if len(parts) >= 3:
                _remaining_text = " ".join(parts[2:])
                _price_nums = re.findall(r'\d+', _remaining_text.replace(",", ""))
                if _price_nums:
                    try:
                        real_mrp = int(_price_nums[0])
                        logger.info(f"[AFFILIATE_PARSE] real_mrp=₹{real_mrp:,} parsed from input")
                    except ValueError:
                        pass  # Not a price — ignore

    return title_index, affiliate_link, link_type, real_mrp



# Helper for Incremental Filenaming
def _get_video_duration(video_path: str) -> float:
    """Uses ffprobe to get video duration in seconds."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to get duration for {video_path}: {e}")
        return 999.0  # Default bypass


async def pre_process_watermark(video_path: str) -> str:
    """
    Phase 5: Smart Compilation Queue
    Strips watermarks from short clips BEFORE queuing to prevent watermark AI tracking failures
    when multiple clips are joined. Saves the cleaned clip using visually-lossless CRF 14 encoding.
    """
    from Visual_Refinement_Modules import watermark_auto

    buffer_dir = os.path.join("downloads", "Watermark_Buffer")
    os.makedirs(buffer_dir, exist_ok=True)
    
    filename = os.path.basename(video_path)
    safe_name, ext = os.path.splitext(filename)
    if not ext: ext = ".mp4"
    
    cv2_temp_path = os.path.join(buffer_dir, f"temp_{safe_name}{ext}")
    final_buffer_path = os.path.join(buffer_dir, f"clean_{safe_name}{ext}")
    
    # If already cached, return
    if os.path.exists(final_buffer_path):
        return final_buffer_path

    logger.info(f"🚿 [BUFFER PRE-PROCESS] Removing watermark from short clip: {filename}")
    
    try:
        # 1. Strip Watermark (Produces large cv2 mp4v output)
        res = await asyncio.to_thread(
            watermark_auto.process_video_with_watermark,
            str(video_path),
            cv2_temp_path,
            retry_mode=False
        )
        
        if res.get("success"):
            # 2. Compress to Visually Lossless CRF 14 to avoid Generation Loss.
            # OpenCV mp4v output is VIDEO-ONLY — audio is stripped during inpainting.
            # We mux audio back from the original source so downstream modules
            # (beat detection, rhythm builder) receive a complete A/V file.
            logger.info(f"🗜️ [BUFFER PRE-PROCESS] Compressing to Visually Lossless (CRF 14) + restoring audio from source...")
            cmd = [
                "ffmpeg", "-y",
                "-i", cv2_temp_path,   # video-only inpainted file (stream 0)
                "-i", str(video_path), # original file for audio (stream 1)
                "-map", "0:v:0",       # take video from inpainted output
                "-map", "1:a?",        # take audio from original (optional — safe if source also has no audio)
                "-c:v", "libx264", "-crf", "14", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",           # trim to shortest stream (video) to avoid tail silence
                final_buffer_path
            ]
            await asyncio.to_thread(
                subprocess.run, cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            if os.path.exists(cv2_temp_path):
                os.remove(cv2_temp_path)
            
            logger.info(f"✅ [BUFFER PRE-PROCESS] Success! Stored in {final_buffer_path}")
            return final_buffer_path
        else:
            logger.info(f"✨ [BUFFER PRE-PROCESS] No watermark detected on {filename}. Copying to cache.")
            import shutil
            await asyncio.to_thread(shutil.copy2, str(video_path), final_buffer_path)
            return final_buffer_path
            
    except Exception as e:
        logger.error(f"❌ [BUFFER PRE-PROCESS] Fallback triggered due to error: {e}")
        return str(video_path)


def _handle_short_clip_queue(title: str, video_path: str, max_clips: int = 3) -> tuple:
    """
    Adds a short clip to the queue based on title.
    Returns (True, list_of_paths) if batch is full, else (False, current_queue).

    Note: If the user sends the same file twice (same path via smart-reuse),
    we copy it to a unique slot so the counter always increments correctly.
    """
    queue_file = "The_json/pending_batch_state.json"
    os.makedirs("The_json", exist_ok=True)
    os.makedirs("temp/clip_queue_slots", exist_ok=True)  # unique slot copies

    try:
        if os.path.exists(queue_file):
            with open(queue_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        else:
            state = {}

        safe_key = _sanitize_title(title).strip().lower()
        if not safe_key:
            safe_key = "untitled"

        if safe_key not in state:
            state[safe_key] = {"files": [], "updated_at": time.time()}

        # --- Stale path cleanup ------------------------------------------------
        # If a user manually deletes files from disk, old queue entries can point
        # to non-existent paths (e.g. 'downloads\\..._5.mp4'), which would cause
        # the compiler/orchestrator to fail later. We prune those here so that
        # every queued path is guaranteed to exist before compilation.
        existing_files = []
        for p in state[safe_key].get("files", []):
            if os.path.exists(p):
                existing_files.append(p)
            else:
                logger.warning(f"🧹 Removing stale queued path for '{safe_key}': {p}")
        state[safe_key]["files"] = existing_files

        # ── Duplicate-path guard ─────────────────────────────────────────────
        # When smart-reuse returns the same file path for different sends, the
        # path-equality check would silently skip the second clip. Instead we
        # copy it to a uniquely-named slot so every send counts.
        dest_path = str(video_path)
        if dest_path in state[safe_key]["files"]:
            import shutil as _shutil

            slot_idx = len(state[safe_key]["files"])
            ext = os.path.splitext(video_path)[1] or ".mp4"
            slot = os.path.join(
                "temp",
                "clip_queue_slots",
                f"{safe_key}_slot{slot_idx}_{int(time.time())}{ext}",
            )
            try:
                _shutil.copy2(video_path, slot)
                dest_path = slot
                logger.info(
                    f"📋 Queue slot copy: {os.path.basename(video_path)} → {os.path.basename(slot)}"
                )
            except Exception as _ce:
                # If copy fails, still add with a suffix marker so it's unique
                dest_path = f"{video_path}?slot={slot_idx}"
                logger.warning(f"⚠️ Queue slot copy failed: {_ce} — using marker path")

        state[safe_key]["files"].append(dest_path)
        state[safe_key]["updated_at"] = time.time()

        current_queue = list(state[safe_key]["files"])
        batch_ready = len(current_queue) >= max_clips

        # TIMEOUT FALLBACK: If queue has been sitting > 10 minutes, process as-is
        QUEUE_TIMEOUT_SECONDS = 600
        queue_age = time.time() - state[safe_key].get("updated_at", time.time())
        if (
            not batch_ready
            and len(current_queue) > 0
            and queue_age > QUEUE_TIMEOUT_SECONDS
        ):
            logger.warning(
                f"⏰ Queue timeout for '{safe_key}' ({len(current_queue)} clips, "
                f"{queue_age:.0f}s old). Processing as-is."
            )
            batch_ready = True

        if batch_ready:
            del state[safe_key]

        with open(queue_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        return batch_ready, current_queue

    except Exception as e:
        logger.error(f"Error handling short clip queue: {e}")
        return False, [video_path]  # Fallback to single file on error


def _register_output_and_check_batch(
    title: str,
    output_path: str,
    max_clips: int = 3,
) -> tuple:
    """
    Registers a successfully processed output under its title key.
    Returns (True, [path1, path2, path3]) when 3 outputs are ready for compilation.
    """
    store_file = "The_json/output_batch_state.json"
    os.makedirs("The_json", exist_ok=True)

    try:
        state = {}
        if os.path.exists(store_file):
            with open(store_file, "r", encoding="utf-8") as f:
                state = json.load(f)

        safe_key = _sanitize_title(title).strip().lower() or "untitled"

        if safe_key not in state:
            state[safe_key] = {"files": [], "updated_at": time.time()}

        # Prune stale paths
        state[safe_key]["files"] = [
            p for p in state[safe_key]["files"] if os.path.exists(p)
        ]

        if output_path not in state[safe_key]["files"]:
            state[safe_key]["files"].append(output_path)
            state[safe_key]["updated_at"] = time.time()

        current = list(state[safe_key]["files"])
        batch_ready = len(current) >= max_clips

        if batch_ready:
            del state[safe_key]

        with open(store_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        return batch_ready, current[:max_clips]

    except Exception as e:
        logger.error(f"_register_output_and_check_batch error: {e}")
        return False, [output_path]


async def _compile_rhythm_sync_batch(update, title: str, clip_paths: list) -> None:
    """Compiles 3 processed outputs into a single compilation."""
    import asyncio, os, re
    await safe_reply(
        update,
        f"🎬 **3 clips ready for '{title}'!**\n\nStarting rhythm-sync compilation...",
    )

    try:
        from Compiler_Modules.rhythm_timeline_builder import RhythmTimelineBuilder
        from Compiler_Modules.compiler import NarratedCompilationBuilder

        builder = RhythmTimelineBuilder()
        beat_maps = []
        for cp in clip_paths:
            try:
                bm = await asyncio.to_thread(builder.analyze_beats, cp)
                beat_maps.append(bm)
            except Exception as e:
                logger.warning(f"Beat analysis failed for {cp}: {e}")
                beat_maps.append([])

        timeline = await asyncio.to_thread(
            builder.build_timeline, clips=clip_paths, beat_maps=beat_maps
        )

        out_dir = "Compilations"
        os.makedirs(out_dir, exist_ok=True)
        safe_t = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")
        merged_path = _generate_next_filename(out_dir, safe_t, ".mp4")

        ok = await asyncio.to_thread(
            NarratedCompilationBuilder.compile_with_timeline,
            clip_paths=clip_paths,
            timeline=timeline,
            output_path=merged_path,
            audio_mix=True,
            add_transitions=True,
        )

        if not ok or not os.path.exists(merged_path):
            await safe_reply(update, f"❌ Compilation failed for '{title}'.")
            return

        from Health_handlers import get_portal
        hashtags = os.getenv("DEFAULT_HASHTAGS_COMPILATION", "#compilation #viral #shorts")
        
        await initiate_compilation_title_flow(
            update=update,
            merged_path=merged_path,
            n_videos=len(clip_paths),
            hashtags=hashtags,
            base_title=title,
        )

    except Exception as e:
        logger.error(f"_compile_rhythm_sync_batch error: {e}")
        await safe_reply(update, f"❌ Rhythm compilation error: {e}")


def _generate_next_filename(
    directory: str, prefix: str, extension: str = ".mp4"
) -> str:
    """
    Scans directory for files matching prefix_XX.mp4 and returns the next incremental filename.
    Format: prefix_01.mp4, prefix_02.mp4, etc.
    """
    try:
        if not os.path.exists(directory):
            return os.path.join(directory, f"{prefix}_01{extension}")

        # List all possible matches
        # We look for files starting with prefix
        candidates = glob.glob(os.path.join(directory, f"{prefix}_*{extension}"))

        max_idx = 0

        # Regex to extract the number at the end
        # We expect: prefix_(\d+).mp4
        # We must be careful not to match prefix_2025... as a huge number if the prefix matches partially.
        # So we ensure the prefix is followed by an UNDERSCORE and then DIGITS only.
        # But wait, our prefix might result in "compile_last_2" and we want "compile_last_2_01".
        # So pattern is: prefix + "_" + digits + extension

        pattern = re.compile(rf"^{re.escape(prefix)}_(\d+){re.escape(extension)}$")

        for f in candidates:
            fname = os.path.basename(f)
            match = pattern.match(fname)
            if match:
                try:
                    idx = int(match.group(1))
                    if idx > max_idx:
                        max_idx = idx
                except:
                    pass

        # If no strict match found (e.g. only timestamped files exist), we start at 01.
        # Timestamped files (prefix_2025...) won't match the regex `_(\d+).mp4` easily
        # unless user named it `compile_last_2_20251228`.
        # But timestamp usually has time too: `20251228_123456`. That contains `_`, so `\d+` won't match it fully if strict anchor.

        return os.path.join(directory, f"{prefix}_{max_idx + 1:02d}{extension}")

    except Exception as e:
        logger.error(f"Filename generation error: {e}")
        # Fallback to timestamp if logic fails
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return os.path.join(directory, f"{prefix}_{stamp}{extension}")


async def initiate_compilation_title_flow(
    update: Update,
    merged_path: str,
    n_videos: int,
    hashtags: str,
    base_title: str = None,
):
    """
    New Flow:
    1. Check GEMINI_TITLE_COMPLICATION
    2. ON -> Try Gemini -> Finish
    3. FAIL/OFF -> Ask User (Mandatory) -> Wait
    """
    user_id = update.effective_user.id
    gemini_mode = os.getenv("GEMINI_TITLE_COMPLICATION", "on").lower()

    generated_title = None
    generated_desc = None

    # Defaults
    if not base_title:
        base_title = f"Compilation {n_videos} Videos"

    # 0. Check Sidecar (Primary: One-Request Strategy)
    json_path = os.path.splitext(merged_path)[0] + ".json"
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                sidecar_data = json.load(f)
                # Check for either editorial_title or brain_analysis[editorial_title]
                title = sidecar_data.get("editorial_title") or sidecar_data.get(
                    "brain_analysis", {}
                ).get("editorial_title")
                desc = sidecar_data.get("brain_analysis", {}).get("final_caption")
                if title and len(title) > 5:
                    generated_title = title
                    generated_desc = desc
                    logger.info(
                        f"✨ Using Brain-Generated Title from Sidecar: {generated_title}"
                    )
        except:
            pass

    if not generated_title and gemini_mode == "on":
        try:
            # Try smart generation via Brain if valid base context
            from Intelligence_Modules.monetization_brain import brain

            # Construct context for brain from base_title if it looks like a query
            context = (
                base_title.replace("Compilation", "").replace("Videos", "").strip()
            )
            if not context:
                context = "Influencer Fashion"

            logger.info(
                f"🧠 Generating Compilation Title via Brain: {context} (Clips: {n_videos})"
            )
            smart = brain.generate_editorial_title(context, n_videos=n_videos)

            # Smart is now likely a tuple (title, desc) if updated
            if isinstance(smart, tuple):
                title_cand, desc_cand = smart
            else:
                title_cand, desc_cand = smart, None

            if title_cand and title_cand != f"Compilation: {context}":
                generated_title = title_cand
                generated_desc = desc_cand

        except Exception as e:
            logger.warning(f"Gemini Title Gen Failed: {e}")

    if generated_title:
        await safe_reply(update, f"✨ AI Generated Title: {generated_title}")
        await finish_compilation_upload(
            update,
            merged_path,
            generated_title,
            hashtags,
            n_videos=n_videos,
            description=generated_desc,
            clips=user_sessions.get(user_id, {}).get("compilation_clips")
        )
        return

    # --- FALLBACK: ASK USER (MANDATORY) ---
    presets_msg = ""
    try:
        presets = get_presets()

        if presets:
            msg_lines = [f"📌 Select title expansion for: '{base_title}' (optional):"]
            # Ensure sorted keys
            for k in sorted(
                presets.keys(), key=lambda x: int(x) if x.isdigit() else 99
            ):
                v = presets[k]
                msg_lines.append(f"{k}️⃣ {v['label']}")
            msg_lines.append(
                    "\nReply format:\n"
                    "  <number>              → e.g.  2\n"
                    "  <number> <link>       → e.g.  2 https://amzn.to/xyz\n"
                    "  (affiliate link is optional)\n"
                    "/skip  (or /skip https://amzn.to/xyz)"
                )
            presets_msg = "\n".join(msg_lines)
    except Exception as e:
        logger.error(f"Failed to load presets: {e}")

    if presets_msg:
        # Save State
        with acquire_session_lock(user_id):
            user_sessions[user_id] = {
                "state": "WAITING_FOR_COMPILATION_TITLE",
                "pending_compilation_path": merged_path,
                "pending_n_videos": n_videos,
                "pending_hashtags": hashtags,
                "pending_base_title": base_title,
            }
            save_session(user_id)

        await safe_reply(update, presets_msg)
    else:
        # No presets found? Fallback to generic
        await finish_compilation_upload(
            update, 
            merged_path, 
            base_title, 
            hashtags, 
            n_videos=n_videos,
            clips=user_sessions.get(user_id, {}).get("compilation_clips")
        )


async def finish_compilation_upload(
    update: Update,
    merged_path: str,
    title: str,
    hashtags: str,
    n_videos: int = 10,
    description: str = None,
    affiliate_link: str = None,        # ← user-supplied inline affiliate link
    affiliate_link_type: str = None,   # ← 'exact' or 'alternative'
    real_mrp: int = None,              # ← real Amazon MRP (optional, user-supplied)
    clips: List[str] = None,
):
    """
    Final step: Upload, Log, Reply.
    affiliate_link — if provided by the user at title-selection time, it overrides
    the auto-detected Amazon link and is injected into YouTube description,
    Instagram caption, and Telegram post.
    """
    # Explicitly log the final location for user clarity
    logger.info(f"💾 Compilation Saved Confirmation: {merged_path}")
    if affiliate_link:
        logger.info(f"[MONETIZE] User affiliate link will be injected: {affiliate_link}")

    # Imports provided via Health_handlers portal
    from Health_handlers import get_portal
    portal = get_portal_cached()
    uploader = getattr(portal, 'uploader', None)
    meta_uploader = getattr(portal, 'meta_uploader', None)
    community_promoter = getattr(portal, 'community_promoter', None)

    # Check if we should send to YouTube or Telegram
    try:
        send_to_youtube = os.getenv("SEND_TO_YOUTUBE", "off").lower() in [
            "on",
            "yes",
            "true",
        ]

        link = None
        yt_status_msg = "🚫 YouTube: Skipped"

        # ── Resolve Affiliate Link for Socials (YouTube & Meta) ──
        social_affiliate_link = None
        social_cta_var = ""
        if affiliate_link:
            social_affiliate_link = affiliate_link
            # ── Space-aware branding for compilations ──
            if affiliate_link_type == "exact":
                _badge = "🎯 Exact Wear on Amazon"
            elif affiliate_link_type == "alternative":
                _badge = "🔁 Alternative on Amazon"
            else:
                _badge = "🛍️ Shop the Look"

            # ── Price Hook: real MRP if user provided it ──────────────────────
            _price_hook = ""
            if real_mrp and real_mrp > 0:
                # No brain overlay in compilation scope — just anchor MRP + tease deal
                _price_hook = f"MRP: ₹{real_mrp:,}\n🔥 Big discount on Amazon 👇"
                logger.info(f"[PRICE_HOOK] compilation real_mrp=₹{real_mrp:,}")

            social_cta_var = f"{_badge}\n🔗 Buy here"
            if _price_hook:
                social_cta_var = f"{_price_hook}\n{_badge}\n🔗 Buy here"
        else:
            try:
                from Health_handlers import get_portal
                portal = get_portal_cached()
                _mb2 = getattr(portal, "monetization_brain", None)
                _mb2_brain = getattr(_mb2, "brain", None) if _mb2 else None
                yt_safe_link = _mb2_brain.get_monetization_link(target_platform="youtube") if _mb2_brain else None
                if yt_safe_link:
                    social_affiliate_link = yt_safe_link
                    import random
                    partner_hooks = [
                        "Shop the trending styles here",
                        "Get these compilation outfits",
                        "Treat someone to these looks"
                    ]
                    social_cta_var = f"🛍️ {random.choice(partner_hooks)}"
            except Exception as e:
                logger.warning(f"Failed to resolve affiliate link: {e}")

        # ----- ADDITIONAL HASHTAGS: User Title, Channel Name -----
        import re
        _h_list = set(hashtags.split()) if hashtags else set()
        
        # Add Default Compilation Hashtags
        default_comp = os.getenv("DEFAULT_HASHTAGS_COMPILATION", "#compilation #funny #viral")
        for tag in default_comp.split():
            _h_list.add(tag)

        if title:
            _title_clean = re.sub(r'[^a-zA-Z0-9]', '', title)
            if _title_clean: _h_list.add(f"#{_title_clean}")
        _channel_name = os.getenv("CHANNEL_NAME", os.getenv("BRAND_NAME", "ViralShorts"))
        _channel_clean = re.sub(r'[^a-zA-Z0-9]', '', _channel_name)
        if _channel_clean: _h_list.add(f"#{_channel_clean}")
        hashtags = " ".join(_h_list)
        # ---------------------------------------------------------

        if send_to_youtube:
            await safe_reply(update, f"📤 Uploading compilation: '{title}'...")

            # --- Inject Affiliate Link for Compilations ---
            if social_affiliate_link:
                if not description: description = ""
                description += f"\n\n{social_cta_var}: {social_affiliate_link}\n\n"
                logger.info(f"[MONETIZE] Affiliate link injected into YouTube description")
            
            # --- INJECT ONLY RELEVANT WEAR NAMES AND AFFILIATE LINKS ---
            try:
                amzn_json_path = "Monetization_Metrics/Amazon_affliate_link.json"
                if os.path.exists(amzn_json_path) and clips:
                    with open(amzn_json_path, "r", encoding="utf-8") as _f:
                        amzn_data = json.load(_f)
                    
                    by_uid = amzn_data.get("by_uid", {})
                    if by_uid:
                        # Collect relevant UIDs by matching links from clip sidecars
                        relevant_uids = set()
                        for clip_path in clips:
                            sidecar_p = clip_path + ".final.json"
                            if os.path.exists(sidecar_p):
                                try:
                                    with open(sidecar_p, "r") as sf:
                                        s_data = json.load(sf)
                                        c_link = s_data.get("affiliate_link")
                                        c_uid = s_data.get("video_uid")
                                        
                                        # Match by UID if available
                                        if c_uid and c_uid in by_uid:
                                            relevant_uids.add(c_uid)
                                        # Fallback: match by Link
                                        elif c_link and c_link != "none":
                                            for uid, val in by_uid.items():
                                                if val.get("link") == c_link:
                                                    relevant_uids.add(uid)
                                                    break
                                except: pass

                        if relevant_uids:
                            if not description: description = ""
                            description += "\n🛍️ Shop Compilation Looks:\n"
                            unique_looks = {}
                            for uid in relevant_uids:
                                item_data = by_uid[uid]
                                item_name = item_data.get("item_name")
                                link = item_data.get("link")
                                if item_name and link:
                                    display_name = item_name.title()
                                    if display_name not in unique_looks:
                                        unique_looks[display_name] = link
                            
                            for name, link in unique_looks.items():
                                description += f"• {name}: {link}\n"
                            description += "\n"
                            logger.info(f"[MONETIZE] Injected {len(relevant_uids)} relevant links into compilation description.")
            except Exception as e:
                logger.warning(f"Failed to inject wear names from Amazon_affliate_link.json: {e}")
            # -----------------------------------------------------------

            try:
                # 1. YouTube Upload
                link = await with_retry(
                    uploader.upload_to_youtube,
                    merged_path,
                    hashtags=hashtags,
                    title=title,
                    description=description,
                )

                if link:
                    log_video(merged_path, link, title, affiliate_link=affiliate_link or "none")
                    yt_status_msg = f"✅ YouTube: Uploaded! ({link})"

                    # Reset/Clear user session if strictly compilation (optional, but good hygiene)
                    user_id = update.effective_user.id
                    with acquire_session_lock(user_id):
                        # Only clear if we were in the waiting state
                        if (
                            user_sessions.get(user_id, {}).get("state")
                            == "WAITING_FOR_COMPILATION_TITLE"
                        ):
                            user_sessions.pop(user_id, None)
                            save_session(user_id)
                else:
                    yt_status_msg = "❌ YouTube: Failed."
            except Exception as e:
                logger.error(f"YouTube Upload Failed: {e}")
                yt_status_msg = f"❌ YouTube Error: {e}"
        else:
            await safe_reply(
                update,
                f"✅ Compilation saved locally (YouTube Skipped):\n`{merged_path}`",
            )

        # 2. Meta Upload (Instagram + Facebook)
        # Independent of YouTube failure (as per requirement)
        # Imports provided via Health_handlers portal
        meta_results = {}
        if os.getenv("ENABLE_META_UPLOAD", "no").lower() in [
            "yes",
            "true",
            "on",
        ] and os.getenv("META_COMPILE_UPLOAD", "no").lower() in ["yes", "true", "on"]:
            await safe_reply(
                update, "📤 Attempting Meta (Instagram/Facebook) Uploads..."
            )
            # Use generated description or title for caption
            # For compilations, maybe use title + hashtags
            meta_caption = f"{title}\n\n{hashtags}"
            if description:
                meta_caption = f"{title}\n\n{description}\n\n{hashtags}"
            # ── Inject affiliate link into Instagram caption ──
            if social_affiliate_link:
                if "#" in meta_caption:
                    _first_hash = meta_caption.find("\n#")
                    if _first_hash != -1:
                        meta_caption = meta_caption[:_first_hash] + f"\n\n{social_cta_var}: {social_affiliate_link}" + meta_caption[_first_hash:]
                    else:
                        meta_caption += f"\n\n{social_cta_var}: {social_affiliate_link}"
                else:
                    meta_caption += f"\n\n{social_cta_var}: {social_affiliate_link}"

            # --- FACEBOOK TITLE TRANSFORMATION ---
            fb_caption = meta_caption  # Default fallback
            try:
                # Load Mappings
                fb_map_file = "The_json/title_expansion_fb.json"
                presets_file = "The_json/title_expansion_presets.json"

                if os.path.exists(fb_map_file) and os.path.exists(presets_file):
                    with open(fb_map_file, "r", encoding="utf-8") as f:
                        fb_presets = json.load(f)
                    with open(presets_file, "r", encoding="utf-8") as f:
                        main_presets = json.load(f)

                    # Find which preset was used in the title
                    found_key = None
                    for k, v in main_presets.items():
                        # Check if the Main Preset's Label is in the current title
                        # e.g. Title: "Disha Patani: Red Carpet Event" -> Label: "Red Carpet Event"
                        if v["label"] in title:
                            found_key = k
                            break

                    if found_key and found_key in fb_presets:
                        # Map to FB Title
                        clean_fb_title = fb_presets[found_key]["label"]
                        # Re-construct caption for FB: Clean Title + Hashtags (No verbose description)
                        fb_caption = f"{clean_fb_title}\n\n{hashtags}"
                        logger.info(
                            f"📘 Facebook Title Swapped: '{title}' -> '{clean_fb_title}'"
                        )
            except Exception as e:
                logger.warning(f"FB Title Mapping Failed: {e}")

            # ── [DYNAMIC FACEBOOK GATE] ──────────────────────────────────────────────
            # Facebook Reels SUPPRESSES reused content without heavy transformation
            # or narration. We check the transformation_score from the pipeline.
            # If the video is raw/minimally-transformed reused content, we skip FB
            # automatically to protect the FB page's reach score.
            # Threshold is configurable via FB_MIN_TRANSFORMATION_SCORE (default: 60).
            _fb_transform_threshold = int(os.getenv("FB_MIN_TRANSFORMATION_SCORE", "60"))
            _fb_transformation_score = 0
            try:
                # Try wm_context from local scope (set earlier in compilation flow)
                _fb_wm_ctx = locals().get("wm_context") or {}
                _fb_transformation_score = int(
                    _fb_wm_ctx.get("transformation_score", 0)
                    or _fb_wm_ctx.get("pipeline_metrics", {}).get("transformation_score", 0)
                    or 0
                )
                # Try companion sidecar JSON if wm_context was empty
                if not _fb_transformation_score:
                    _fb_sidecar = os.path.splitext(str(merged_path))[0] + ".json"
                    if os.path.exists(_fb_sidecar):
                        with open(_fb_sidecar, "r", encoding="utf-8") as _fb_sf:
                            _fb_sc = json.load(_fb_sf)
                        _fb_transformation_score = int(
                            _fb_sc.get("pipeline_metrics", {}).get("transformation_score", 0)
                            or _fb_sc.get("transformation_score", 0)
                            or 0
                        )
            except Exception as _fb_ts_e:
                logger.debug("[FB_GATE] transformation_score read failed (using 0): %s", _fb_ts_e)

            _skip_facebook_dynamic = _fb_transformation_score < _fb_transform_threshold
            if _skip_facebook_dynamic:
                logger.info(
                    "🚫 [FB_GATE] Skipping Facebook — transformation_score=%d < threshold=%d "
                    "(reused content without sufficient transformation would be suppressed by FB algo).",
                    _fb_transformation_score, _fb_transform_threshold
                )
            else:
                logger.info(
                    "✅ [FB_GATE] Facebook enabled — transformation_score=%d >= threshold=%d.",
                    _fb_transformation_score, _fb_transform_threshold
                )

            meta_results = await meta_uploader.AsyncMetaUploader.upload_to_meta(
                merged_path,
                meta_caption,
                upload_type=os.getenv("META_UPLOAD_TYPE", "Reels"),
                facebook_caption=fb_caption,
                skip_facebook=_skip_facebook_dynamic,
            )

        else:
            if os.getenv("ENABLE_META_UPLOAD", "no").lower() in ["yes", "true", "on"]:
                logger.info(
                    "⏩ Meta Compilation Upload skipped (META_COMPILE_UPLOAD is OFF)"
                )
            else:
                logger.info(
                    "🚫 Meta Upload Disabled globally (ENABLE_META_UPLOAD is OFF)"
                )

        # 3. Final Report
        report_lines = [f"🎉 Compilation Processing Complete!", ""]
        report_lines.append(yt_status_msg)

        if meta_results:
            # Instagram
            ig_res = meta_results.get("instagram", {"status": "skipped"})
            if isinstance(ig_res, str):
                ig_res = {"status": ig_res}
            ig_status = ig_res.get("status", "skipped")
            ig_link = ig_res.get("link", "")
            icon_ig = (
                "✅"
                if ig_status == "success"
                else "❌"
                if "failed" in ig_status
                else "⏩"
            )
            line_ig = f"{icon_ig} Instagram: {ig_status}"
            if ig_link:
                line_ig += f" ({ig_link})"
            report_lines.append(line_ig)

            # Facebook
            fb_res = meta_results.get("facebook", {"status": "skipped"})
            if isinstance(fb_res, str):
                fb_res = {"status": fb_res}
            fb_status = fb_res.get("status", "skipped")
            fb_link = fb_res.get("link", "")
            icon_fb = (
                "✅"
                if fb_status == "success"
                else "❌"
                if "failed" in fb_status
                else "⏩"
            )
            line_fb = f"{icon_fb} Facebook: {fb_status}"
            if fb_link:
                line_fb += f" ({fb_link})"
            report_lines.append(line_fb)

        await safe_reply(update, "\n".join(report_lines))

        # --- COMMUNITY PROMOTION ADD-ON ---
        if (
            link
            and os.getenv("ENABLE_COMMUNITY_POST_COMPILATION", "yes").lower() == "yes"
        ):
            # Just REGISTER the link for future shorts. Do NOT post comment on the compilation itself.
            logger.info("💾 Registering Compilation Link for future cross-promotion...")
            community_promoter.promoter.register_compilation_url(link)

            # ── Inject user affiliate link into Telegram group post ────────
            # The Telegram group broadcast uses the session's public_caption.
            # For compilations, we build it here directly from the YouTube link
            # since there is no single-video session flowing through _perform_upload.
            tg_group_id = os.getenv("TELEGRAM_GROUP_ID", "").strip()
            if tg_group_id and affiliate_link:
                try:
                    tg_comp_caption = (
                        f"🎬 **{title}**\n\n"
                        f"🛍 Shop the Look: \n🇮🇳 India → {affiliate_link}\n\n"
                        f"📺 Watch: {link}"
                    )
                    _tg_chat = (
                        int(tg_group_id)
                        if tg_group_id.lstrip("-").isdigit()
                        else tg_group_id
                    )
                    async with UPLOAD_SEMAPHORE:
                        with open(merged_path, "rb") as _vf:
                            await locals().get("context").bot.send_video(
                                chat_id=_tg_chat,
                                video=_vf,
                                caption=tg_comp_caption[:1024],
                                parse_mode="Markdown",
                                read_timeout=600,
                                write_timeout=600,
                                connect_timeout=60,
                            )
                    logger.info("[MONETIZE] Affiliate link injected into Telegram group compilation post.")
                except Exception as _tge:
                    logger.warning(f"⚠️ Telegram affiliate post failed (non-fatal): {_tge}")

    except Exception as e:
        logger.exception("Upload failed: %s", e)
        await safe_reply(update, f"❌ Pipeline failed: {e}")


# ==================== COMPILATION LOGIC ====================


def get_unique_processed_shorts(n=5):
    """
    Robustly find the last N processed videos for compilation.
    Checks 'Processed Shorts' directory.
    Renamed from 'last_n_filepaths' to avoid shadowing the CSV-based helper.
    """
    source_dir = "Processed Shorts"
    if not os.path.exists(source_dir):
        logger.warning(f"get_unique_processed_shorts: {source_dir} does not exist.")
        return []

    all_files = glob.glob(os.path.join(source_dir, "*.mp4"))
    # Filter out compilations AND invalid 0-byte files
    valid_files = [
        f
        for f in all_files
        if "compile" not in os.path.basename(f)
        and "compilation" not in os.path.basename(f)
        and os.path.getsize(f) > 1024
    ]

    # Sort by modification time (Newest -> Oldest)
    valid_files.sort(key=os.path.getmtime, reverse=True)

    # De-duplicate based on filename stem (e.g. 'Sakshi_malik_01' vs 'Sakshi_malik_01_reaction')
    seen_stems = set()
    unique_list = []
    for f in valid_files:
        # Extract stem: 'Sakshi_malik_01_reaction' -> 'Sakshi_malik_01'
        base = os.path.basename(f)
        stem = base.split('.')[0]
        # Remove common variant suffixes
        clean_stem = re.sub(r'_(reaction|textreaction|hook|clip)$', '', stem, flags=re.IGNORECASE)
        
        if clean_stem not in seen_stems:
            seen_stems.add(clean_stem)
            unique_list.append(f)
            if len(unique_list) >= n:
                break

    logger.info(f"📊 Found {len(unique_list)} unique base videos for compilation (deduplicated from {len(valid_files)})")
    return unique_list


def _strip_intro_from_clip(src_path: str, tmp_dir: str) -> str:
    """
    If the clip has a .json sidecar with first_shot_duration, trim that many
    seconds from the front. Returns path to trimmed temp file, or original 
    path if no trimming needed / sidecar missing.
    """
    sidecar = os.path.splitext(src_path)[0] + ".json"
    intro_secs = COMPILATION_INTRO_FALLBACK_SECS  # default 8.0
    if os.path.exists(sidecar):
        try:
            with open(sidecar) as f:
                meta = json.load(f)
            dur = float(meta.get("first_shot_duration", 0) or 0)
            if dur > 0.5:
                intro_secs = dur
            else:
                return src_path  # No intro baked in, skip
        except Exception:
            pass  # sidecar corrupt → use fallback
    
    # FFmpeg trim: skip first intro_secs
    # [AUDIO_BLEED_FIX] Do NOT use "-ss" before "-i" with "-c copy".
    # Pre-input seek + stream-copy snaps video to nearest keyframe but
    # copies audio from t=0, causing intro audio to bleed into the output.
    # Fix: use post-input "-ss" (accurate seek) + re-encode both streams
    # so audio and video are cut at exactly the same timestamp.
    out_name = f"stripped_{os.path.basename(src_path)}"
    out_path = os.path.join(tmp_dir, out_name)
    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-ss", str(intro_secs),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        out_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and os.path.exists(out_path):
            logger.info(f"[COMPILE_STRIP] Stripped {intro_secs}s intro from {os.path.basename(src_path)}")
            return out_path
    except Exception as e:
        logger.warning(f"Failed to strip intro from {src_path}: {e}")
    return src_path  # Fallback: use original if strip fails


async def maybe_compile_and_upload(update: Update):
    from Compiler_Modules.compiler import compile_batch_with_transitions

    # Explicitly get portal modules to ensure visibility in async scope
    from Health_handlers import get_portal

    portal = get_portal_cached()
    community_promoter = getattr(portal, "community_promoter", None)
    meta_uploader = getattr(portal, "meta_uploader", None)
    # [ADAPTIVE v3] Allow disabling auto-compilation via environment variable
    if os.getenv("ENABLE_AUTO_COMPILATION", "yes").lower() != "yes":
        return

    count = total_uploads()
    n = COMPILATION_BATCH_SIZE
    min_clips = COMPILATION_MIN_CLIPS  # Minimum clips to allow compilation (controlled via .env)
    if n <= 0 or count == 0 or count % n != 0:
        return

    # Collect up to n most-recent processed shorts
    files = get_unique_processed_shorts(n)

    # Guard: need at least COMPILATION_MIN_CLIPS files to make a meaningful compilation
    if len(files) < min_clips:
        logger.info(
            f"[COMPILE_GUARD] Only {len(files)} file(s) available "
            f"(need at least {min_clips}). Skipping compilation."
        )
        return

    # Use however many clips are available (up to n)
    actual_count = len(files)
    await safe_reply(update, f"⏳ Creating compilation of {actual_count} shorts...📦")

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tmp_strip_dir = os.path.join(COMPILATIONS_DIR, f"_strip_{stamp}")
    os.makedirs(tmp_strip_dir, exist_ok=True)
    
    # --- INTRO STRIP ---
    files = [_strip_intro_from_clip(f, tmp_strip_dir) for f in files]
    
    output_name = os.path.join(COMPILATIONS_DIR, f"compilation_{n}_{stamp}.mp4")
    await safe_reply(update, f"🔨 Merging {actual_count} videos now...🛸")

    try:
        await safe_reply(update, "✨ Running full AI pipeline for batch compilation…")

        # --- Single Stage: Batch Compile with Transitions ---
        # This replaces the old 2-stage process (raw merge -> enhance)
        # Now we normalize -> transition -> merge -> remix -> assemble in one go

        # Use Output Name directly (contains Path)
        from claw_vanguard.vanguard_director import VanguardDirector
        director = VanguardDirector()
        result = await asyncio.to_thread(
            director.execute_mission, 
            niche=os.getenv("DEFAULT_NICHE", "entertainment"),
            video_request=f"Combine {len(files)} high-retention shorts into a seamless cinematic compilation.",
            input_paths=files
        )
        merged = result.output if result.success else None

        if not merged or not os.path.exists(merged):
            await safe_reply(update, "❌ Failed to create compilation.")
            return

        # Prepare Metadata
        count = total_uploads()
        # Default Title (will be overridden by logic likely, but passed as backup or logic param)
        # Actually logic generates title. We just need hashtags.

        comp_hashtags = (
            os.getenv("DEFAULT_HASHTAGS_COMPILATION", "")
            .replace("#Shorts", "")
            .replace("#shorts", "")
            .strip()
        )

        # Store clips in session for finish_compilation_upload access
        with acquire_session_lock(update.effective_user.id):
            user_sessions[update.effective_user.id]["compilation_clips"] = files
            save_session(update.effective_user.id)

        # Initiate New Flow
        await initiate_compilation_title_flow(update, merged, n, comp_hashtags)

    except Exception as e:
        logger.exception("Compilation/upload failed: %s", e)
        await safe_reply(update, f"❌ Compilation failed: {e}")
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_strip_dir, ignore_errors=True)
        except Exception:
            pass


async def compile_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global QUEUE_SIZE
    user_id = update.effective_user.id

    # --- QUEUE HANDLING ---
    is_queued = False
    with QS_LOCK:
        if PROCESSING_LOCK.locked():
            QUEUE_SIZE += 1
            is_queued = True
            pos = QUEUE_SIZE

    if is_queued:
        await safe_reply(
            update,
            f"⏳ System Busy. Your compilation request is at position #{pos} in the queue...",
        )

    async with PROCESSING_LOCK:
        if is_queued:
            with QS_LOCK:
                QUEUE_SIZE = max(0, QUEUE_SIZE - 1)

        # Original Logic...
    """
    Compiles the last N downloaded videos from the downloads/ folder.
    Usage:
      /compile_last <number> (default 6)
      /compile_last <number> <name_prefix> (e.g. /compile_last 6 reem hot)
    """
    try:
        from Compiler_Modules.compiler import compile_batch_with_transitions

        # Imports provided via Health_handlers portal
        # 1. Parse arguments
        n = 6
        name_query = None

        if context.args:
            try:
                n = int(context.args[0])
            except ValueError:
                await safe_reply(update, "⚠️ Invalid number. Using default: 6")

            if len(context.args) > 1:
                name_query = " ".join(context.args[1:])

        if n <= 1:
            await safe_reply(update, "⚠️ Please specify at least 2 videos.")
            return

        # Source from Processed Shorts
        source_dir = "Processed Shorts"
        if not os.path.exists(source_dir):
            await safe_reply(update, f"❌ Directory '{source_dir}' not found.")
            return

        selected_files = []

        if name_query:
            # --- NAMED SORT COMPILATION (Subfolders) ---
            clean_query = _sanitize_title(name_query).replace(" ", "_")

            logger.info(f"🔍 Searching for subdirectories matching: {clean_query}")
            await safe_reply(update, f"🔍 Searching for {n} subfolders matching '{clean_query}'...")

            all_dirs = [os.path.join(source_dir, d) for d in os.listdir(source_dir) if os.path.isdir(os.path.join(source_dir, d))]
            matching_dirs = [d for d in all_dirs if os.path.basename(d).lower().startswith(clean_query.lower())]

            def natural_keys(text):
                return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", text)]

            matching_dirs.sort(key=lambda d: natural_keys(os.path.basename(d)))

            if len(matching_dirs) < n:
                await safe_reply(update, f"⚠️ Not enough subfolders found matching '{clean_query}'. Found {len(matching_dirs)}, need {n}.")
                return

            # Take the LAST N (Newest folders) for selection, but then sort Oldest to Newest to show progression
            selected_dirs = matching_dirs[-n:]
            selected_dirs.sort(key=lambda d: natural_keys(os.path.basename(d)))

            selected_files = []
            for d in selected_dirs:
                mp4s = glob.glob(os.path.join(d, "*.mp4"))
                main_mp4s = [f for f in mp4s if not f.endswith("_reaction.mp4") and not f.endswith("_textreaction.mp4") and not os.path.basename(f).startswith("compile_")]
                if main_mp4s:
                    selected_files.append(main_mp4s[0])
                elif mp4s:
                    selected_files.append(mp4s[0])

        else:
            # --- DEFAULT: TIME BASED ---
            all_files = glob.glob(os.path.join(source_dir, "*.mp4"))
            files = [
                f for f in all_files if not os.path.basename(f).startswith("compile_")
            ]

            if not files:
                await safe_reply(
                    update, f"❌ No processed videos found in '{source_dir}' folder."
                )
                return

            # Sort by modification time (newest first)
            files.sort(key=os.path.getmtime, reverse=True)

            # Take top N
            selected_files = files[:n]

        if len(selected_files) < 2:
            await safe_reply(
                update,
                f"⚠️ Found {len(selected_files)} videos, but need at least 2 to compile.",
            )
            return

        # Log selected files for user confirmation
        msg = f"✅ Found {len(selected_files)} videos:\n"
        for f in selected_files:
            msg += f"- {os.path.basename(f)}\n"
        await safe_reply(update, msg)

        # 4. Compile
        if name_query:
            prefix = f"compile_last_{n}_{clean_query}"
        else:
            prefix = f"compile_last_{n}"

        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        tmp_strip_dir = os.path.join(COMPILATIONS_DIR, f"_strip_{stamp}")
        os.makedirs(tmp_strip_dir, exist_ok=True)
        
        selected_files = [_strip_intro_from_clip(f, tmp_strip_dir) for f in selected_files]

        output_filename = _generate_next_filename(COMPILATIONS_DIR, prefix, ".mp4")

        if name_query:
            from Intelligence_Modules.monetization_brain import brain
            from Compiler_Modules.compiler import compile_monetizable_longform
            
            progression_hook = brain.generate_progression_hook(name_query, len(selected_files))
            await safe_reply(update, f"🚀 Starting Monetizable long-form compilation with hook:\n'{progression_hook}'")
            
            GlobalState.set_busy(True)
            merged = await asyncio.to_thread(
                compile_monetizable_longform, selected_files, output_filename, progression_hook
            )
            GlobalState.set_busy(False)
        else:
            await safe_reply(update, "🚀 Starting batch compilation with transitions...")
            GlobalState.set_busy(True)
            merged = await asyncio.to_thread(
                compile_batch_with_transitions, selected_files, output_filename
            )
            GlobalState.set_busy(False)

        if not merged or not os.path.exists(merged):
            await safe_reply(update, "❌ Compilation failed (check logs).")
            return

        # Prepare Hashtags
        comp_hashtags = (
            os.getenv("DEFAULT_HASHTAGS_COMPILATION", "#compilation #viral")
            .replace("#Shorts", "")
            .strip()
        )

        # If user provided a name query, use smart logic
        if name_query:
            # Smart Title Generation via Brain
            # Logic: Try Brain -> If Fail -> Initiate Title Flow (Fallback)

            try:
                from Intelligence_Modules.monetization_brain import brain

                logger.info(
                    f"🧠 Generating Smart Title for: {name_query} (Clips: {len(selected_files)})"
                )
                smart_res = brain.generate_editorial_title(
                    name_query, n_videos=len(selected_files)
                )

                # Unpack tuple
                if isinstance(smart_res, tuple):
                    smart_title, smart_desc = smart_res
                else:
                    smart_title, smart_desc = smart_res, None

                # Check for Failure
                is_fallback = smart_title == f"Compilation: {name_query}"

                if smart_title and not is_fallback and len(smart_title) > 5:
                    final_title = smart_title
                    await finish_compilation_upload(
                        update,
                        merged,
                        final_title,
                        comp_hashtags,
                        description=smart_desc,
                        clips=user_sessions.get(user_id, {}).get("compilation_clips")
                    )
                else:
                    # Smart Gen Failed -> Ask User
                    await initiate_compilation_title_flow(
                        update,
                        merged,
                        len(selected_files),
                        comp_hashtags,
                        base_title=name_query,
                    )

            except Exception as e:
                logger.warning(f"⚠️ Smart Title Generation Failed: {e}")
                await initiate_compilation_title_flow(
                    update,
                    merged,
                    len(selected_files),
                    comp_hashtags,
                    base_title=name_query,
                )

        else:
            # New Flow (No base name provided)
            await initiate_compilation_title_flow(
                update, merged, len(selected_files), comp_hashtags
            )

    except Exception as e:
        logger.exception(f"/compile_last failed: {e}")
        await safe_reply(update, f"❌ Error: {e}")
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_strip_dir, ignore_errors=True)
        except Exception:
            pass


async def register_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manually register a compilation URL for cross-promotion.
    Usage: /register_promo <url>
    """
    try:
        if not context.args:
            await safe_reply(update, "⚠️ Usage: /register_promo <youtube_url>")
            return

        url = context.args[0]
        # Explicitly get portal modules
        from Health_handlers import get_portal

        portal = get_portal_cached()
        community_promoter = getattr(portal, "community_promoter", None)

        if community_promoter:
            community_promoter.promoter.register_compilation_url(url)
        await safe_reply(
            update,
            f"✅ Promotion Link Registered!\nTarget: {url}\nFuture Shorts will link to this.",
        )

    except Exception as e:
        logger.error(f"Register Promo Failed: {e}")
        await safe_reply(update, f"❌ Error: {e}")


async def compile_first(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global QUEUE_SIZE
    user_id = update.effective_user.id

    # --- QUEUE HANDLING ---
    is_queued = False
    with QS_LOCK:
        if PROCESSING_LOCK.locked():
            QUEUE_SIZE += 1
            is_queued = True
            pos = QUEUE_SIZE

    if is_queued:
        await safe_reply(
            update,
            f"⏳ System Busy. Your compilation request is at position #{pos} in the queue...",
        )

    async with PROCESSING_LOCK:
        if is_queued:
            with QS_LOCK:
                QUEUE_SIZE = max(0, QUEUE_SIZE - 1)

        # Original Logic...
    """
    Compiles the FIRST N downloaded videos from the downloads/ folder.
    Usage:
      /compile_first <number> (default 6)
      /compile_first <number> <name_prefix> (e.g. /compile_first 6 reem hot)
    """
    try:
        from Compiler_Modules.compiler import compile_batch_with_transitions

        # Imports provided via Health_handlers portal
        # 1. Parse arguments
        n = 6
        name_query = None

        if context.args:
            try:
                n = int(context.args[0])
            except ValueError:
                await safe_reply(update, "⚠️ Invalid number. Using default: 6")

            if len(context.args) > 1:
                name_query = " ".join(context.args[1:])

        if n <= 1:
            await safe_reply(update, "⚠️ Please specify at least 2 videos.")
            return

        # Source from Processed Shorts
        source_dir = "Processed Shorts"
        if not os.path.exists(source_dir):
            await safe_reply(update, f"❌ Directory '{source_dir}' not found.")
            return

        selected_files = []

        if name_query:
            # --- NAMED SORT COMPILATION (Subfolders) ---
            clean_query = _sanitize_title(name_query).replace(" ", "_")

            logger.info(f"🔍 Searching for subdirectories matching: {clean_query}")
            await safe_reply(update, f"🔍 Searching for {n} subfolders matching '{clean_query}'...")

            all_dirs = [os.path.join(source_dir, d) for d in os.listdir(source_dir) if os.path.isdir(os.path.join(source_dir, d))]
            matching_dirs = [d for d in all_dirs if os.path.basename(d).lower().startswith(clean_query.lower())]

            def natural_keys(text):
                return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", text)]

            matching_dirs.sort(key=lambda d: natural_keys(os.path.basename(d)))

            if len(matching_dirs) < n:
                await safe_reply(update, f"⚠️ Not enough subfolders found matching '{clean_query}'. Found {len(matching_dirs)}, need {n}.")
                return

            # Take the FIRST N
            selected_dirs = matching_dirs[:n]
            selected_dirs.sort(key=lambda d: natural_keys(os.path.basename(d)))

            selected_files = []
            for d in selected_dirs:
                mp4s = glob.glob(os.path.join(d, "*.mp4"))
                main_mp4s = [f for f in mp4s if not f.endswith("_reaction.mp4") and not f.endswith("_textreaction.mp4") and not os.path.basename(f).startswith("compile_")]
                if main_mp4s:
                    selected_files.append(main_mp4s[0])
                elif mp4s:
                    selected_files.append(mp4s[0])

        else:
            # --- DEFAULT: TIME BASED ---
            all_files = glob.glob(os.path.join(source_dir, "*.mp4"))
            files = [
                f for f in all_files if not os.path.basename(f).startswith("compile_")
            ]

            if not files:
                await safe_reply(
                    update, f"❌ No processed videos found in '{source_dir}' folder."
                )
                return

            # Sort by modification time (OLDEST first)
            files.sort(key=os.path.getmtime, reverse=False)

            # Take top N (which are now the oldest)
            selected_files = files[:n]

        if len(selected_files) < 2:
            await safe_reply(
                update,
                f"⚠️ Found {len(selected_files)} videos, but need at least 2 to compile.",
            )
            return

        # Log selected files for user confirmation
        msg = f"✅ Found {len(selected_files)} videos:\n"
        for f in selected_files:
            msg += f"- {os.path.basename(f)}\n"
        await safe_reply(update, msg)

        # 4. Compile
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        
        tmp_strip_dir = os.path.join(COMPILATIONS_DIR, f"_strip_{stamp}")
        os.makedirs(tmp_strip_dir, exist_ok=True)
        selected_files = [_strip_intro_from_clip(f, tmp_strip_dir) for f in selected_files]
        
        output_filename = os.path.join(
            COMPILATIONS_DIR, f"compile_first_{n}_{stamp}.mp4"
        )
        if name_query:
            output_filename = os.path.join(
                COMPILATIONS_DIR, f"compile_{clean_query}_first_{n}_{stamp}.mp4"
            )

        if name_query:
            from Intelligence_Modules.monetization_brain import brain
            from Compiler_Modules.compiler import compile_monetizable_longform
            
            progression_hook = brain.generate_progression_hook(name_query, len(selected_files))
            await safe_reply(update, f"🚀 Starting Monetizable long-form compilation with hook:\n'{progression_hook}'")
            
            GlobalState.set_busy(True)
            merged = await asyncio.to_thread(
                compile_monetizable_longform, selected_files, output_filename, progression_hook
            )
            GlobalState.set_busy(False)
        else:
            await safe_reply(update, "🚀 Starting batch compilation with transitions...")
            GlobalState.set_busy(True)
            merged = await asyncio.to_thread(
                compile_batch_with_transitions, selected_files, output_filename
            )
            GlobalState.set_busy(False)

        if not merged or not os.path.exists(merged):
            await safe_reply(update, "❌ Compilation failed (check logs).")
            return

        # Prepare Hashtags
        comp_hashtags = (
            os.getenv("DEFAULT_HASHTAGS_COMPILATION", "#compilation #viral")
            .replace("#Shorts", "")
            .strip()
        )

        if name_query:
            # Smart Title Generation via Brain
            # Logic: Try Brain -> If Fail -> Initiate Title Flow (Fallback)

            try:
                from Intelligence_Modules.monetization_brain import brain

                logger.info(
                    f"🧠 Generating Smart Title for: {name_query} (Clips: {len(selected_files)})"
                )
                smart_res = brain.generate_editorial_title(
                    name_query, n_videos=len(selected_files)
                )

                # Unpack tuple
                if isinstance(smart_res, tuple):
                    smart_title, smart_desc = smart_res
                else:
                    smart_title, smart_desc = smart_res, None

                # Check for Failure
                is_fallback = smart_title == f"Compilation: {name_query}"

                if smart_title and not is_fallback and len(smart_title) > 5:
                    final_title = smart_title
                    await finish_compilation_upload(
                        update,
                        merged,
                        final_title,
                        comp_hashtags,
                        description=smart_desc,
                        clips=user_sessions.get(user_id, {}).get("compilation_clips")
                    )
                else:
                    # Fail -> Fallback to User
                    await initiate_compilation_title_flow(
                        update,
                        merged,
                        len(selected_files),
                        comp_hashtags,
                        base_title=name_query,
                    )

            except Exception as e:
                logger.warning(f"⚠️ Smart Title Generation Failed: {e}")
                await initiate_compilation_title_flow(
                    update,
                    merged,
                    len(selected_files),
                    comp_hashtags,
                    base_title=name_query,
                )

        else:
            # New Flow
            await initiate_compilation_title_flow(
                update, merged, len(selected_files), comp_hashtags
            )

    except Exception as e:
        logger.exception(f"/compile_first failed: {e}")
        await safe_reply(update, f"❌ Error: {e}")
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_strip_dir, ignore_errors=True)
        except Exception:
            pass


# ==================== HANDLERS ====================


async def cmd_compile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /compile <EntityName> <Count>
    Generates a Narrative Compilation for the given entity.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    args = context.args
    if len(args) < 2:
        await safe_reply(
            update, "Usage: /compile <Name> <Count>\nExample: /compile Avneet 5"
        )
        return

    entity_name = args[0]
    try:
        count = int(args[1])
    except ValueError:
        await safe_reply(update, "❌ Count must be a number.")
        return

    # Check Portal Access
    if not hasattr(portal, "narrative_brain"):
        await safe_reply(
            update, "❌ Narrative Brain is NOT enabled in Health_handlers."
        )
        return

    await safe_reply(
        update,
        f"🎬 **Director Mode Active**\n\n🔎 Scouting assets for '{entity_name}' (Limit: {count})...",
    )
    # 1. Asset Discovery
    try:
        _nd_director = getattr(portal.narrative_brain, "director", None)
        if not _nd_director:
            await safe_reply(
                update, "❌ Narrative Director failed to initialize. Check logs."
            )
            return

        assets = await asyncio.to_thread(
            _nd_director.find_associated_assets,
            entity_name,
            limit=count,
        )
    except Exception as e:
        await safe_reply(update, f"❌ Discovery Failed: {e}")
        return

    if not assets:
        await safe_reply(
            update,
            f"⚠️ No assets found for '{entity_name}'.\nEnsure you have 'Processed Shorts/{entity_name}*.json' and matching thumbnails.",
        )
        return

    if len(assets) < 2:
        await safe_reply(
            update,
            f"⚠️ Not enough clips found ({len(assets)}). Need at least 2 for a compilation.",
        )
        return

    await safe_reply(
        update, f"✅ Found {len(assets)} clips. Generating Script & Voiceover... 🎙️"
    )

    # 2. Script & Voiceover
    try:
        # Generate Script (Narrative Brain)
        _script_result = await asyncio.to_thread(
            _nd_director.generate_compilation_script, assets
        )
        # generate_compilation_script returns either a dict or a string
        if isinstance(_script_result, dict):
            script = _script_result.get("script", "")
            _gen_title = _script_result.get("title_suggestion", "")
            if _gen_title and len(_gen_title) > 5:
                entity_name = _gen_title  # Use AI-generated title if available
        else:
            script = str(_script_result) if _script_result else ""

        if not script or len(script) < 50:
            await safe_reply(update, "❌ Script generation failed or too short.")
            return

        # Generate Voiceover
        job_id = int(time.time())
        vo_path = os.path.join("temp", f"narration_{job_id}.mp3")

        vo_success = await asyncio.to_thread(
            portal.voiceover.generate_long_form_narration, script, vo_path
        )

        if not vo_success:
            await safe_reply(update, "❌ Voiceover generation failed.")
            return

    except Exception as e:
        await safe_reply(update, f"❌ Narrative/VO Failed: {e}")
        return

    # 3. Assembly
    await safe_reply(
        update,
        f"🎞️ Assembling Video (This may take a minute commit to visual/audio sync)...",
    )

    # Pick BGM
    bgm_files = glob.glob("music/*.mp3")
    bgm_path = random.choice(bgm_files) if bgm_files else None

    if not bgm_path:
        await safe_reply(
            update, "⚠️ No BGM found in music/ folder. Video will be silent music."
        )
        # We can implement a silence fallback or just fail.
        # assemble_narrated_compilation requires BGM.
        # Let's create a silent mp3? No, fail is better.
        await safe_reply(update, "❌ BGM Missing. Please add mp3s to music/ folder.")
        return

    output_filename = f"Compilation_{entity_name}_{job_id}.mp4"
    output_path = os.path.join(COMPILATIONS_DIR, output_filename)

    video_paths = [
        a["video_path"] for a in assets if os.path.exists(a.get("video_path", ""))
    ]

    if len(video_paths) < 2:
        await safe_reply(update, "❌ Video paths missing from metadata assets.")
        return

    from Compiler_Modules.compiler import assemble_narrated_compilation as _anc
    success = await asyncio.to_thread(
        _anc,
        video_paths,
        vo_path,
        bgm_path,
        output_path,
    )

    if success:
        await safe_reply(
            update,
            f"✅ **Compilation Ready!**\n\n📂 {output_filename}\n📝 Script Length: {len(script)} chars",
        )

        # Send File
        with open(output_path, "rb") as f:
            await locals().get("context").bot.send_video(
                chat_id=update.effective_chat.id,
                video=f,
                caption=f"🎬 **{entity_name} Compilation**\n\n✨ {len(assets)} Clips\n🎙️ AI Narration",
                read_timeout=600,
                write_timeout=600,
                pool_timeout=600,
            )
    else:
        await safe_reply(update, "❌ Compilation Assembly Failed.")


async def cmd_versus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /versus <EntityA> <EntityB>
    Generates a high-stakes juxtaposition video between two entities.
    """
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return

    args = context.args
    if len(args) < 2:
        await safe_reply(
            update, "Usage: /versus <NameA> <NameB>\nExample: /versus Avneet Disha"
        )
        return

    name_a = args[0]
    name_b = args[1]

    # Check Portal Access for Narrative Brain (Search)
    if not hasattr(portal, "narrative_brain"):
        await safe_reply(update, "❌ Narrative Brain is NOT enabled.")
        return

    await safe_reply(
        update,
        f"⚔️ **Versus Mode Active**\n\n🔎 Scouting assets for '{name_a}' vs '{name_b}'...",
    )

    try:
        # 1. Scouting Clips
        from Intelligence_Modules import narrative_brain as _nb_module

        _vs_director = getattr(_nb_module, "director", None)
        if not _vs_director:
            await safe_reply(update, "❌ Narrative Director unavailable. Check logs.")
            return

        assets_a = await asyncio.to_thread(
            _vs_director.find_associated_assets, name_a, limit=5
        )
        assets_b = await asyncio.to_thread(
            _vs_director.find_associated_assets, name_b, limit=5
        )

        if not assets_a or not assets_b:
            await safe_reply(
                update,
                f"❌ Could not find enough assets for one or both entities.\nFound {len(assets_a)} for {name_a}, {len(assets_b)} for {name_b}.",
            )
            return

        # Pick representative clips
        clip_a = assets_a[0]["video_path"]
        clip_b = assets_b[0]["video_path"]

        if not os.path.exists(clip_a) or not os.path.exists(clip_b):
            await safe_reply(
                update, "❌ One of the source videos is missing from disk."
            )
            return

        # 2. Orchestrate Compilation
        from Compiler_Modules import orchestrator

        job_id = f"vs_{uuid.uuid4().hex[:6]}"
        output_path = os.path.join(
            COMPILATIONS_DIR, f"versus_{name_a}_{name_b}_{job_id}.mp4"
        )

        await safe_reply(
            update,
            "🧠 [Synthetic Newsroom] Comparing styles and rendering juxtaposition...",
        )

        success, report = await asyncio.to_thread(
            orchestrator.compile_juxtaposition,
            job_id,
            clip_a,
            clip_b,
            output_path,
            title=f"{name_a} vs {name_b}",
        )

        if success:
            script = report.get("script", "")
            cta = report.get("cta", "")
            caption = f"🏆 {name_a} vs {name_b}\n\n🎙️ {script}\n\n🔗 {cta}\n\n#versus #fashion #amtce"

            await safe_video_reply(update, output_path, caption=caption)
        else:
            await safe_reply(
                update,
                f"❌ Versus Render Failed: {report.get('error', 'Unknown Error')}",
            )

    except Exception as e:
        logger.error(f"Versus Operation Failed: {e}", exc_info=True)
        await safe_reply(update, f"❌ Error: {e}")




async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — Show current queue and job state."""
    is_busy   = PROCESSING_LOCK.locked()
    q_size    = QUEUE_SIZE
    now       = time.time()
    job_secs  = int(now - _job_start_time) if (is_busy and _job_start_time) else 0

    if is_busy:
        m, s   = divmod(job_secs, 60)
        stuck  = " ⚠️ POSSIBLY STUCK" if job_secs > WATCHDOG_TIMEOUT * 0.8 else ""
        status = (
            f"⚙️ <b>Bot Status</b>\n\n"
            f"🔄 Job running: {m}m {s}s{stuck}\n"
            f"📋 Queued behind it: {q_size} job(s)\n\n"
            f"⏱️ Watchdog limit: {WATCHDOG_TIMEOUT//60} min"
        )
    else:
        status = "✅ <b>Bot Status</b>\n\n💤 Idle — no job running.\n📋 Queue: empty"

    await update.message.reply_text(status, parse_mode="HTML")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(
        update, "❓ Please send an Instagram reel or YouTube link to begin."
    )


async def cmd_ytcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /ytcode          -> Triggers YouTube auth refresh (sends Google sign-in link to this chat)
    /ytcode <code>   -> Submits the auth code/URL back to complete the flow
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return

    if not context.args:
        # ── TRIGGER: run auth script in background, it sends the link via Telegram ──
        await update.message.reply_text(
            "🔄 <b>Triggering YouTube auth refresh...</b>\n\n"
            "The Google sign-in link will appear here in a moment.\n"
            "Tap it → sign in → copy the code shown → send:\n"
            "<code>/ytcode YOUR_CODE</code>",
            parse_mode="HTML"
        )
        import threading
        def _run_auth():
            try:
                subprocess.run(
                    [sys.executable, "scripts/auth_youtube.py"],
                    cwd=os.path.abspath("."),
                    timeout=660
                )
            except Exception as e:
                logger.error(f"auth_youtube background run failed: {e}")
        threading.Thread(target=_run_auth, daemon=True).start()
        return

    # ── SUBMIT: paste the code or full URL back ───────────────────────────────
    raw = " ".join(context.args).strip()

    if raw.startswith("http"):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
        extracted = qs.get("code", [None])[0]
        if extracted:
            raw = extracted
            await update.message.reply_text("✅ Extracted code from URL. Exchanging for token...")
        else:
            await update.message.reply_text("❌ No 'code=' found in that URL. Paste just the code.")
            return
    else:
        await update.message.reply_text("✅ Code received. Exchanging for token...")

    try:
        os.makedirs("Credentials", exist_ok=True)
        with open("Credentials/yt_auth_code.txt", "w", encoding="utf-8") as f:
            f.write(raw)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to save code: {e}")

async def cmd_getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /getid — Returns the chat_id of the current chat.
    Send this command in your Telegram GROUP to get the negative group ID,
    then add it to Credentials/.env as: TELEGRAM_GROUP_ID=-100xxxxxxxxxx
    """
    chat = update.effective_chat
    user = update.effective_user
    msg = (
        f"📋 **Chat Info**\n\n"
        f"🆔 **Chat ID:** `{chat.id}`\n"
        f"📛 **Chat Title:** {getattr(chat, 'title', 'N/A')}\n"
        f"🏷️ **Chat Type:** {chat.type}\n\n"
        f"👤 **Your User ID:** `{user.id}`\n\n"
        f"💡 Add to `.env`:\n`TELEGRAM_GROUP_ID={chat.id}`"
    )
    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def getbatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(
        update,
        f"📦 Compilation Settings:\n"
        f"  Trigger every: {COMPILATION_BATCH_SIZE} uploads (COMPILATION_BATCH_SIZE)\n"
        f"  Min clips needed: {COMPILATION_MIN_CLIPS} (COMPILATION_MIN_CLIPS)"
    )


async def setbatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global COMPILATION_BATCH_SIZE, COMPILATION_MIN_CLIPS
    try:
        if not context.args:
            await safe_reply(
                update,
                "Usage: /setbatch <trigger_count> [min_clips]\n"
                "  trigger_count: compile every N uploads (COMPILATION_BATCH_SIZE)\n"
                "  min_clips: minimum clips required to compile (COMPILATION_MIN_CLIPS, default 3)"
            )
            return
        n = int(context.args[0])
        if n <= 0:
            await safe_reply(update, "Please provide a positive integer.")
            return
        COMPILATION_BATCH_SIZE = n
        msg = f"✅ COMPILATION_BATCH_SIZE set to {n}."
        # Optional: second arg to set min clips
        if len(context.args) >= 2:
            m = int(context.args[1])
            if m < 1:
                await safe_reply(update, "⚠️ min_clips must be at least 1.")
                return
            COMPILATION_MIN_CLIPS = m
            msg += f" COMPILATION_MIN_CLIPS set to {m}."
        await safe_reply(update, msg)
    except Exception:
        await safe_reply(update, "Usage: /setbatch <trigger_count> [min_clips]")



async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles direct video file uploads (Video or Document).
    """
    logger.info(
        f"📨 Handle attachment triggered! Message ID: {update.message.message_id}"
    )

    # Re-verify critical imports from portal just in case
    from Health_handlers import get_portal

    portal = get_portal_cached()
    if not portal:
        logger.error("❌ Critical: Portal failed to load in handle_attachment")
        await safe_reply(update, "❌ System Error: Module Portal Failed.")
        return

    load_dotenv(override=True)

    user_id = update.effective_user.id
    message = update.message

    # Identify attachment
    attachment = message.video or message.document
    if not attachment:
        return  # Should be filtered out by handlers but safe check

    # Filter non-video documents if needed
    if message.document:
        mime = getattr(attachment, "mime_type", "")
        if not mime or not mime.startswith("video/"):
            await safe_reply(update, "⚠️ Document is not a recognized video format.")
            return

    file_name = (
        getattr(attachment, "file_name", None) or f"upload_{int(time.time())}.mp4"
    )

    # Check size (Telegram Bot API limit is 20MB for download, Local API is unlimited, MTProto is 2GB)
    file_size = getattr(attachment, "file_size", None) or 0
    limit_mb = int(os.getenv("TELEGRAM_MAX_UPLOAD_MB", "50"))
    if file_size > limit_mb * 1024 * 1024:
        await safe_reply(
            update,
            f"⚠️ File is too large ({file_size / 1024 / 1024:.1f}MB). Max: {limit_mb}MB.",
        )
        return

    await safe_reply(update, "📥 Receiving video file...")

    try:
        new_file = await attachment.get_file()
        logger.debug(f"[Step 1] File object retrieved: {new_file.file_id}")

        # Sanitize filename
        clean_name = _sanitize_title(file_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join("downloads", f"{clean_name}_{timestamp}.mp4")
        logger.debug(f"[Step 2] Save path generated: {save_path}")

        # [ADAPTIVE] Momentum Throttling Check
        # [ADAPTIVE v3] Safe Mode Constraints & Throttling
        _ai = getattr(portal, "adaptive_intelligence", None)
        _ai_brain = getattr(_ai, "brain", None) if _ai else None
        if _ai_brain:
            constraints = _ai_brain.get_execution_constraints()
            # 1. Check if allowed at all
            if constraints.get("upload_delay", 0) > 3000:  # Level 3 (3600s)
                # Soft Reject in Survival Mode
                await safe_reply(
                    update,
                    "🛡️ **Bio-Defense Active**\nSystem is in Deep Healing Cycle. Uploads paused for channel protection.\nTry again in ~1 hour.",
                )
                return

            # 2. Check Momentum (using v3 constraint logic)
            # We can still use the helper, but let's respect the dynamic delay
            allowed, wait_time = _ai_brain.check_momentum(user_id)
            if not allowed:
                # Override wait_time if Safe Level demands higher
                required_delay = constraints.get("upload_delay", 0)
                final_wait = max(wait_time, required_delay)
                await safe_reply(
                    update,
                    f"⏳ **Adaptive Pacing**\nPlease wait {final_wait}s to match current safe levels.",
                )
                return

        # Download
        logger.debug("[Step 3] Starting download...")
        await new_file.download_to_drive(save_path)
        logger.info("[Step 4] Download completed!")

        # [ADAPTIVE] Risk Scoring Check
        if hasattr(portal, "risk_engine"):
            risk_score, details = portal.risk_engine.RiskEngine.calculate_weighted_risk(
                save_path
            )
            if risk_score > 80:  # Critical Risk Threshold
                os.remove(save_path)
                await safe_reply(
                    update,
                    f"🚫 **File Rejected (Risk Score: {risk_score})**\nReason: High Entropy/Low Quality.\nDetails: {details}",
                )
                return
            logger.info(f"🛡️ Adaptive Risk Score: {risk_score} (Details: {details})")

        # Setup Session for Title Input (Unified Flow)
        logger.info(f"💾 Setting up session for User {user_id} -> WAITING_FOR_TITLE")
        with acquire_session_lock(user_id):
            user_sessions[user_id] = {
                "state": "WAITING_FOR_TITLE",
                "pending_local_path": str(save_path),
                "pending_url": None,  # Explicitly clear URL
            }
            save_session(user_id)

        # Ask for Title
        default_hashtags = os.getenv("DEFAULT_HASHTAGS_SHORTS", "#shorts")
        logger.info(f"📤 Sending Title Prompt to User {user_id}")
        await safe_reply(
            update,
            f"✅ File Received!\n\n📌 Hashtags:\n{default_hashtags}\n\n✏️ Now send the title to start processing.",
            force=True,
        )

    except Exception as e:
        logger.error(f"Attachment handler failed: {e}", exc_info=True)

        # Smart Error Handling for Large Files
        if "File is too big" in str(e):
            await safe_reply(
                update,
                "⚠️ **Telegram API Limit Reached (20MB)**\n"
                "Since I am running locally alongside your files, simply **Reply with the File Path** instead!\n\n"
                "Example:\n"
                "`D:\\Videos\\my_clip.mp4`",
            )
        else:
            await safe_reply(update, f"❌ Error handling file: {e}")

        GlobalState.set_busy(False)


async def global_debug_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deep diagnostic: logs every single update before it hits any filters."""
    try:
        u_id = update.update_id
        user = update.effective_user.id if update.effective_user else "None"
        msg_type = "TEXT" if update.message and update.message.text else "OTHER"
        content = update.message.text[:30] if update.message and update.message.text else "N/A"
        logger.info(f"🔍 [WATCHER] ID:{u_id} | User:{user} | Type:{msg_type} | Content:'{content}...'")
    except Exception as e:
        logger.error(f"❌ Watcher failed: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    portal = get_portal_cached()
    downloader = getattr(portal, "downloader", None)
    quality_orchestrator = getattr(portal, "quality_orchestrator", None)
    monetization_brain = getattr(portal, "monetization_brain", None)
    narrative_brain = getattr(portal, "narrative_brain", None)

    load_dotenv(override=True)
    send_to_youtube = os.getenv("SEND_TO_YOUTUBE", "off").lower() in [
        "on",
        "yes",
        "true",
    ]

    logger.info(f"📥 [GLOBAL_RECV] Update {update.update_id} | User: {update.effective_user.id if update.effective_user else 'None'} | Chat: {update.effective_chat.id if update.effective_chat else 'None'}")
    
    text = update.message.text.strip() if update.message and update.message.text else ""
    user_id = update.effective_user.id
    
    # Non-blocking session check
    try:
        with acquire_session_lock(user_id, timeout=10):
            session = user_sessions.get(user_id, {})
            state = session.get("state")
    except TimeoutError:
        await safe_reply(update, "⚠️ Your previous request is still being processed. Please wait a moment.")
        return
    except Exception as e:
        logger.error(f"❌ Session lock failed for {user_id}: {e}")
        return

    logger.info(f"📩 [DEBUG_MSG] Received text from {user_id}: '{text[:50]}...' | State: {state}")

    # Case 1: New URL — ALWAYS accepted, even while processing.
    # PROCESSING_LOCK handles serialization. Session is overwritten so
    # the new URL+title gets queued behind the running job automatically.
    if _validate_url(text):
        with acquire_session_lock(user_id):
            user_sessions[user_id] = {"state": "WAITING_FOR_TITLE", "pending_url": text}
            save_session(user_id)
        default_hashtags = os.getenv("DEFAULT_HASHTAGS_SHORTS", "#shorts")
        await safe_reply(
            update,
            f"✅ Got the link!\n\n📌 Hashtags:\n{default_hashtags}\n\n✏️ Now send the title.",
        )
        return

    # Case 2: Waiting for Title (Prioritize over local file check)
    if state == "WAITING_FOR_TITLE":
        pending_url = session.get("pending_url")
        pending_local_path = session.get("pending_local_path")

        if not pending_url and not pending_local_path:
            await safe_reply(
                update, "❌ Error: No pending upload found. Please start over."
            )
            return

        # HARDENING: Reject if text is a URL (Reset state for new URL)
        if _validate_url(text) or text.lower().startswith("http"):
            with acquire_session_lock(user_id):
                user_sessions[user_id] = {
                    "state": "WAITING_FOR_TITLE",
                    "pending_url": text,
                    "pending_local_path": None,
                }
                save_session(user_id)
            await safe_reply(
                update, "🔄 New link received. Please send the title for THIS one."
            )
            return

        # Case 1.5: Local File Path (Large File Bypass - Only if NOT already waiting for title)
        # We handle this as a 'Reset' if it looks like a real path, but prioritize title.
        # To avoid title collision (like user sending "my_video" which exists),
        # we check if it has a video extension.
        possible_path = text.strip('"').strip("'")  # Remove quotes if user added them
        looks_like_path = (
            os.path.exists(possible_path)
            and os.path.isfile(possible_path)
            and any(
                possible_path.lower().endswith(ext)
                for ext in [".mp4", ".mkv", ".mov", ".avi"]
            )
        )

        if looks_like_path:
            # User sent a file path instead of a title - Reset!
            file_name = os.path.basename(possible_path)
            file_size = os.path.getsize(possible_path)
            await safe_reply(
                update,
                f"📂 New Local File detected: `{file_name}`. Switching source...",
            )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            clean_name = _sanitize_title(file_name)
            save_path = os.path.join("downloads", f"local_{clean_name}_{timestamp}.mp4")
            try:
                shutil.copy2(possible_path, save_path)
            except Exception as e:
                await safe_reply(update, f"❌ Failed to copy local file: {e}")
                return

            with acquire_session_lock(user_id):
                user_sessions[user_id] = {
                    "state": "WAITING_FOR_TITLE",
                    "pending_local_path": str(save_path),
                    "pending_url": None,
                }
                save_session(user_id)
            await safe_reply(
                update,
                f"✅ New File Staged!\n\n✏️ Now send the title to start processing.",
            )
            return

        custom_title = text

        # --- SYSTEM HEALTH GUARD ---
        h_verdict = {"status": "ok", "safe": True}
        if not h_verdict.get("safe", True):
            await safe_reply(
                update,
                f"⚠️ SYSTEM PROTECTION ACTIVE:\n{h_verdict['summary']}\n\nProcessing paused for safety.",
            )
            return

        # CRITICAL: Lock session to PROCESSING immediately so any subsequent
        # messages (from concurrent_updates=True) don't also become titles.
        with acquire_session_lock(user_id):
            if user_id in user_sessions:
                user_sessions[user_id]["state"] = "PROCESSING"
                user_sessions[user_id]["title"] = custom_title
                save_session(user_id)

        await safe_reply(update, f"✅ Title set: '{custom_title}'")

        # --- QUEUE HANDLING ---
        global QUEUE_SIZE
        is_queued = False
        with QS_LOCK:
            if PROCESSING_LOCK.locked():
                QUEUE_SIZE += 1
                is_queued = True
                pos = QUEUE_SIZE

        if is_queued:
            await safe_reply(
                update,
                f'⏳ System Busy. Your video ("{custom_title}") is at position #{pos} in the queue...',
            )

        async with PROCESSING_LOCK:
            global _job_start_time
            _job_start_time = time.time()

            if is_queued:
                with QS_LOCK:
                    QUEUE_SIZE = max(0, QUEUE_SIZE - 1)

            await safe_reply(update, "✨ Starting process...")

            video_path = None
            unique_filename = None
            url_hash = "local_upload"

            import hashlib

            # --- PATH A: PRE-DOWNLOADED FILE (Direct Upload) ---
            source_type = "raw_upload"
            if pending_local_path:
                if os.path.exists(pending_local_path):
                    video_path = pending_local_path
                    url_hash = hashlib.md5(
                        f"{pending_local_path}_{time.time()}".encode()
                    ).hexdigest()[:8]
                else:
                    await safe_reply(
                        update,
                        "❌ Error: Uploaded file verification failed. Please try again.",
                    )
                    _job_start_time = 0.0
                    return

            # --- PATH B: URL DOWNLOAD ---
            elif pending_url:
                source_type = "link"
                await safe_reply(update, "📥 Downloading content...")

                url_hash = hashlib.md5(pending_url.encode()).hexdigest()[:8]

                clean_title = "".join(
                    [c for c in custom_title if c.isalnum() or c in (" ", "-", "_")]
                ).strip()[:30]

                GlobalState.set_busy(True)

                download_result = await asyncio.to_thread(
                    downloader.download_video, pending_url, custom_title=custom_title
                )

                if isinstance(download_result, tuple):
                    video_path, was_skipped = download_result
                else:
                    video_path, was_skipped = download_result, False

                if was_skipped:
                    logger.info("⏳ Processing continues, but Raw Telegram Upload will be skipped.")
                    await safe_reply(
                        update,
                        f"♻️ **Smart Reuse**\nFile exists. Skipping download & group upload, but continuing AI processing...",
                        force=True,
                    )

                with acquire_session_lock(user_id):
                    if user_id not in user_sessions:
                        user_sessions[user_id] = {}
                    user_sessions[user_id]["is_reused"] = was_skipped

            if not video_path:
                GlobalState.set_busy(False)
                _job_start_time = 0.0
                await safe_reply(update, "❌ Download failed (Strict Abort).")
                with acquire_session_lock(user_id):
                    user_sessions.pop(user_id, None)
                    try:
                        os.remove(os.path.join(JOB_DIR, f"session_{user_id}.json"))
                    except:
                        pass
                return


        # --- COMMON PROCESSING ---
        if not video_path:
            await safe_reply(update, "❌ Critical Error: Video path missing.")
            return

        # DEDUPLICATION CHECK (STEP 2)
        # Check for collision
        col_type, col_msg = DedupEngine.check_collision(url_hash, video_path)

        if col_type != "NONE":
            logger.warning(col_msg)
            logger.warning(
                "⚠️ Content Collision Detected: Forcing FRESH processing pipeline."
            )
            meta_path = str(video_path) + ".json"
            if os.path.exists(meta_path):
                try:
                    os.remove(meta_path)
                except:
                    pass

        DedupEngine.register_content(url_hash, video_path, source="user_submission")

        # Load metadata (for hashtags etc)
        metadata = {}
        try:
            meta_path = os.path.splitext(video_path)[0] + ".json"
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load metadata: {e}")

        # Use user title, but sanitize it for display/files
        title = custom_title

        # Combine Metadata Tags + Default Hashtags
        meta_tags = metadata.get("tags", [])
        default_hashtags = os.getenv(
            "DEFAULT_HASHTAGS_SHORTS", "#shorts #viral #trending"
        )

        if meta_tags:
            # Take top 5 meta tags
            meta_tag_str = " ".join([f"#{t}" for t in meta_tags[:5]])
            hashtags = f"{default_hashtags} {meta_tag_str}"
        else:
            hashtags = default_hashtags

        # Store Downloaded Path for Retries (CRITICAL FOR NUCLEAR RETRY)
        with acquire_session_lock(user_id):
            if user_id not in user_sessions:
                user_sessions[user_id] = {}
            user_sessions[user_id]["source_path"] = str(video_path)
            # Bug fix: Ensure retry_count is initialized
            user_sessions[user_id]["retry_count"] = 0
            # Explicitly save title here too just in case
            user_sessions[user_id]["title"] = custom_title
            save_session(user_id)

        # Removed redundant "Downloaded" message here as we sent custom ones above

        # --- DURATION CHECK & QUEUE LOGIC ---
        vid_duration = _get_video_duration(video_path)
        logger.info(f"⏱️ Video Duration: {vid_duration}s")
        is_short_clip = vid_duration <= 21.0
        
        target_paths = [video_path]  # All clips process immediately now
        
        if is_short_clip:
            logger.info(f"⚡ Short clip detected ({vid_duration:.1f}s) — will batch post-processing.")
            await safe_reply(update, f"⚡ Short clip detected! Processing immediately, then queuing for compilation...")


        # Compile/Process
        # Ensure we set busy if it wasn't set (Local Path case)
        GlobalState.set_busy(True)
        await safe_reply(
            update,
            "🚀 **Fast-Track Processing Initiated!**\n\n- Mirroring/Cleaning 🔄\n- AI Captioning 🤖\n- Human Safety Check 🛡️\n- Color Grading 🎨\n\n*Hang tight, final polish in progress...*",
        )

        # [ADAPTIVE] Start Timer
        process_start = time.time()

        # Determine if fresh processing is required (due to collision)
        should_force = locals().get("col_type") != "NONE"

        # Generate a proper output path with extension
        final_dir = "Processed Shorts"
        os.makedirs(final_dir, exist_ok=True)
        # Sanitize title for filename
        safe_title = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")
        final_target = _generate_next_filename(final_dir, safe_title, ".mp4")

        # ── [REAL_MRP SEED] ─────────────────────────────────────────────────────
        # Seed user-supplied real MRP into PIPELINE_REAL_MRP env var so the
        # orchestrator price estimator can use the actual Amazon price instead of
        # a random estimate. Cleared immediately after the pipeline returns.
        _session_real_mrp = user_sessions.get(user_id, {}).get("real_mrp")
        if _session_real_mrp and int(_session_real_mrp) > 0:
            os.environ["PIPELINE_REAL_MRP"] = str(int(_session_real_mrp))
            logger.info(f"💰 [REAL_MRP_SEED] Seeded real_mrp=₹{int(_session_real_mrp):,} into pipeline env.")
        else:
            os.environ.pop("PIPELINE_REAL_MRP", None)
        # ────────────────────────────────────────────────────────────────────────

        # [CLAW] Universal Vanguard: every short goes through 4-turn Plan→Execute→Verify→Repair
        from claw_vanguard.vanguard_director import VanguardDirector as _VD
        _director = _VD()
        # Acquire lock again to strictly serialize the heavy AI rendering (preventing parallel collapse)
        async with PROCESSING_LOCK:
            _claw_result = await asyncio.to_thread(
                _director.execute_mission,
                niche=os.getenv("DEFAULT_NICHE", "viral"),
                title=title,
                video_request=(
                    f"Niche: {os.getenv('DEFAULT_NICHE', 'viral').upper()} | "
                    f"Title: {title} | "
                    f"Mode: {'Batch' if len(target_paths) > 1 else 'Single'} | "
                    f"Strategy: Viral hooks & cinematic transitions | "
                    f"Source: {source_type}"
                ),
                input_paths=[str(p) for p in target_paths],
                output_path=str(final_target),
            )
        final_path = _claw_result.output if _claw_result.success else None
        # Clear real_mrp env var — must not bleed into the next job
        os.environ.pop("PIPELINE_REAL_MRP", None)
        
        # [BEAST CONTROL] Safely handle tuple returns from Vanguard/Pipeline outputs
        if isinstance(final_path, tuple):
            final_path = final_path[0]
            
        if final_path is not None:
            final_path = str(final_path)

        # Reconstruct wm_context from ToolResult so downstream code is unchanged
        wm_context = {}
        _sc = {}
        if _claw_result.success and _claw_result.output:
            _sidecar = os.path.splitext(str(_claw_result.output))[0] + ".json"
            if os.path.exists(_sidecar):
                try:
                    with open(_sidecar, "r") as _sf:
                        _sc = json.load(_sf)
                        wm_context = _sc.get("pipeline_metrics", _sc)
                except Exception:
                    pass
        if not wm_context:
            wm_context = {}

        # ── V2: PERSIST NICHE SIDECAR FOR FINAL OUTPUT ──
        try:
            # Ensure final_str is available (as string)
            _final_str = str(final_path)
            
            # Extract niche from all possible metadata paths (Forensic vs Narrative Brain)
            detected_niche = (
                wm_context.get("detected_niche") or 
                wm_context.get("monetization", {}).get("niche") or
                wm_context.get("classification") or 
                wm_context.get("content_category") or
                _sc.get("context", {}).get("detected_niche") or 
                _sc.get("detected_niche")
            )
            
            # If still missing, try manual inference from item_name before saving
            if not detected_niche:
                item_name = str(wm_context.get("monetization", {}).get("item_name", "")).lower()
                if any(kw in item_name for kw in ["bra", "outfit", "dress", "style", "wear", "clothing", "fashion", "look"]):
                    detected_niche = "Fashion & Style"
            
            if detected_niche:
                from Visual_Refinement_Modules.hybrid_watermark import save_detected_niche
                save_detected_niche(_final_str, detected_niche)
                logger.info(f"🎯 [NICHE ROUTER] Synchronized niche sidecar: {detected_niche} → {os.path.basename(_final_str)}.niche.json")
                
                # [NEW] Load the niche-specific .env variables so BRANDING_NAME is applied correctly downstream
                try:
                    _niche_env = os.path.join("Credentials", "social_media", detected_niche, ".env")
                    if os.path.exists(_niche_env):
                        load_dotenv(_niche_env, override=True)
                        logger.info(f"🗂️ [NICHE ROUTER] Loaded branding config for '{detected_niche}'")
                except Exception as _env_e:
                    logger.warning(f"⚠️ [NICHE ROUTER] Failed to load niche env: {_env_e}")
        except Exception as _sn_e:
            logger.warning(f"⚠️ [NICHE ROUTER] Failed to synchronize niche sidecar: {_sn_e}")


        # [PHASE 5] Auto-Cleanup for Watermark Buffers generated for short clips
        for tp in target_paths:
            if "Watermark_Buffer" in str(tp) and os.path.exists(tp):
                try:
                    os.remove(tp)
                    logger.info(f"🧹 Cleaned up buffer file: {tp}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to clean buffer file {tp}: {e}")

        # [ADAPTIVE] Stop Timer & Log Efficiency
        process_duration = time.time() - process_start
        _ai3 = getattr(portal, "adaptive_intelligence", None)
        _ai3_brain = getattr(_ai3, "brain", None) if _ai3 else None
        if _ai3_brain:
            # Estimate video duration (fallback 15s if unknown)
            vid_dur = wm_context.get("duration", 15.0)
            # Get quality score (transformation score, default 50)
            q_score = wm_context.get("transformation_score", 50)

            eff_score = _ai3_brain.compute_efficiency_score(
                duration=vid_dur, time_taken=process_duration, quality_score=q_score
            )
            logger.info(
                f"⚡ Compute Efficiency: {eff_score} (Time: {process_duration:.2f}s, Q: {q_score})"
            )
        # --- OUTPUT STATE RESOLVER ---
        if not final_path or not os.path.exists(final_path):
            await safe_reply(
                update,
                "❌ Processing failed (Critical Error).",
            )
            # Clean session
            with acquire_session_lock(user_id):
                user_sessions.pop(user_id, None)
            return

        final_str = str(final_path)

        if locals().get("is_short_clip"):
            batch_done, batch_paths = _register_output_and_check_batch(title, final_str)
            if batch_done:
                logger.info(f"🎬 3 short-clip outputs ready for '{title}' — triggering rhythm-sync compilation")
                asyncio.create_task(_compile_rhythm_sync_batch(update, title, batch_paths))
            else:
                logger.info(f"📦 Output #{len(batch_paths)} registered for '{title}'")
                await safe_reply(update, f"✅ Processed & stored short clip **{len(batch_paths)}/3** for '{title}'.\nSend more clips with this title to trigger auto-compilation.")
            
            # The individual short clip continues through the remaining flow for the user to upload if desired.


        # Enforce defaults if variables are somehow empty/None
        if not locals().get("wm_context"):
            wm_context = {}

        # Retrieve Sidecar Metadata (Ferrari Audit Fix)
        mon_meta = {}
        pipeline_metrics = {}
        opt_caption = None
        try:
            sidecar_path = os.path.splitext(final_str)[0] + ".json"
            if os.path.exists(sidecar_path):
                with open(sidecar_path, "r") as f:
                    sc_data = json.load(f)
                    pipeline_metrics = sc_data.get("pipeline_metrics", {})
                    # [FIX] Try pipeline_metrics.monetization first, fall back to top-level monetization
                    mon_meta = pipeline_metrics.get("monetization", {})
                    if not mon_meta:
                        mon_meta = sc_data.get("monetization", {})
                    if "caption_data" in sc_data:
                        opt_caption = sc_data["caption_data"].get("caption")

                    # [FIX] Also try pulling caption from monetization data if caption_data was empty
                    if not opt_caption:
                        opt_caption = mon_meta.get("final_caption") or mon_meta.get(
                            "editorial_script"
                        )

                    # [FIX] Use Gemini Generated Title if available
                    editorial_title = sc_data.get("editorial_title")
                    if (
                        editorial_title
                        and len(editorial_title) > 5
                        and editorial_title != "None"
                    ):
                        title = editorial_title
                        logger.info(
                            f"🧠 [MAIN] Upgrading Title to Gemini Editorial: '{title}'"
                        )
        except:
            pass

        # Default Safety Values
        ypp_risk = mon_meta.get("risk_level", "UNKNOWN")
        is_approved = ypp_risk in ["LOW", "MEDIUM"]
        style = "Transformative"  # Default
        action = "APPROVE" if is_approved else "REVIEW"
        # Reason Safety (Check both Brain 'risk_reason' and Compiler 'reason')
        reason = mon_meta.get("risk_reason") or mon_meta.get(
            "reason", "Analysis pending or not performed."
        )

        # Watermark Status Derivation
        wm_status = wm_context.get("watermark_status", "CLEAN")
        if wm_status == "NOT_DETECTED":
            wm_status = "CLEAN"

        # Monetization Status Derivation
        monetization_status = "PASSED" if is_approved else "REVIEW"
        if ypp_risk == "HIGH":
            monetization_status = "BLOCKED"

        # Reason Safety (Fallback)
        if not reason:
            reason = "Transformative edit approved."

        # Dynamic Refinement Message (FINAL UPDATE)
        # Ensure message matches reality of wm_context even if exceptions occurred above
        wm_msg = "(No refinement needed - reply 'no' if missed)"
        final_status = wm_context.get("watermark_status")

        if final_status == "DETECTED_AND_REMOVED":
            wm_msg = "(Visual refinement applied - verify result)"
        elif final_status == "DETECTED_BUT_SKIPPED":
            wm_msg = "(Refinement detected but skipped for safety)"
        elif final_status == "DETECTED_BUT_FAILED":
            wm_msg = "(Visual refinement FAILED - verify result)"
        elif final_status == "UNVERIFIED_QUOTA_LIMIT":
            wm_msg = "⚠️ (Refinement Check SKIPPED - Quota Exceeded)"

        # Caption Genuineness (Ensuring we don't just show the title)
        display_caption = opt_caption
        if not display_caption or len(display_caption.split()) < 3:
            # Try pulling from brain reasoning if available (Standardized to 'final_caption')
            brain_cap = mon_meta.get("final_caption") or mon_meta.get("caption")
            display_caption = (
                brain_cap if brain_cap and len(brain_cap.split()) > 3 else title
            )

        # If still just the title, add a viral psychological hook (gender-neutral)
        if display_caption == title:
            fallback_hooks = [
                f"✨ Stay stunning like {title}, this outfit is so beautiful it gives you a totally unique look ✨\n\nLink in bio / Telegram to grab it 🛍️",
                f"🤫 Look flawless like {title}! This dress is an absolute head-turner and gives you that perfect unique vibe.\n\nDirect affiliate link in our Telegram ✨",
                f"🎀 You'll look absolutely stunning in this, just like {title}! The fit is so unique and gorgeous.\n\nGrab it through the link in bio/Telegram! 🛒💨",
                f"💖 Get that unique, beautiful glow just like {title} ✨\n\nClick the link in bio/Telegram to shop this exact look and treat yourself! 🛍️💖",
                f"🔍✨ A dress so beautiful it instantly upgrades your vibe, just like {title}!\n\nLink in our Telegram to shop directly. Don't miss out! 🏃💨"
            ]
            display_caption = random.choice(fallback_hooks)
        # [FIX] Prefer detected/config brand over 'swargawasal'
        overlay_text = os.getenv("BRANDING_NAME") or os.getenv("BRAND_NAME") or os.getenv(
            "TEXT_OVERLAY_CONTENT", "Style Analysis"
        )

        # [USER REQUEST] AUTO-THUMBNAIL GENERATION
        thumb_path = None
        try:
            from Thumb_Modules.generator import generate_thumbnail

            logger.info("🎨 Generating Auto-Thumbnail...")

            # Use sample_thumbs directory to avoid clutter
            sample_dir = "sample_thumbs"
            os.makedirs(sample_dir, exist_ok=True)

            # Construct a clean name: basename_thumb.jpg
            base_name = os.path.basename(final_str)
            base_name_no_ext = os.path.splitext(base_name)[0]
            thumb_target = os.path.join(sample_dir, f"{base_name_no_ext}_thumb.jpg")

            # [AI_TITLE] Extract the Gemini-detected product/wear name from the sidecar.
            # This is what Gemini identified in the overlay — e.g. "Red Silk Lehenga Choli".
            # It gives Imagen a precise, rich prompt instead of the raw video filename.
            _ai_title = None
            try:
                _ov_data = mon_meta.get("overlay_data", [])
                if isinstance(_ov_data, dict):
                    _ov_data = [_ov_data]
                if _ov_data and isinstance(_ov_data[0], dict):
                    _ai_title = _ov_data[0].get("item_name", "")
                if not _ai_title:
                    _ai_title = mon_meta.get("item_name") or mon_meta.get("final_caption", "")
                # Strip short/generic values — must be at least 4 chars of real product text
                if _ai_title and len(_ai_title.strip()) < 4:
                    _ai_title = None
                if _ai_title:
                    logger.info(f"👗 [THUMB] Gemini-detected wear for AI prompt: '{_ai_title}'")
            except Exception:
                _ai_title = None

            _intro_dur = 0.0
            if "wm_context" in locals() and isinstance(wm_context, dict):
                _intro_dur = float(wm_context.get("first_shot_duration", 0.0) or 0.0)

            thumb_path = generate_thumbnail(
                final_str, title, accent_color="yellow", output_path=thumb_target,
                ai_title=_ai_title, intro_offset=_intro_dur
            )

            if thumb_path:
                logger.info(f"✅ Thumbnail Ready: {thumb_path}")
                # [FIX] Save thumb_path to session so _perform_upload can access it later
                with acquire_session_lock(user_id):
                    if user_id not in user_sessions:
                        user_sessions[user_id] = {}
                    user_sessions[user_id]["thumb_path"] = thumb_path
                    save_session(user_id)
            else:
                logger.warning("⚠️ Thumbnail generation returned None.")
        except Exception as e:
            logger.error(f"❌ Thumbnail Generation Failed: {e}")

        # --- Resolve profile_data from available pipeline outputs ---
        # profile_data is sourced from wm_context (reconstructed from sidecar JSON)
        # or the sidecar sc_data loaded above. Build a unified dict so downstream
        # caption/price logic has a single source of truth.
        profile_data = {}
        try:
            if isinstance(wm_context, dict):
                profile_data = wm_context.copy()
            # Merge sidecar data on top (richer, more complete)
            if "sc_data" in dir() or "sc_data" in locals():
                profile_data.update({
                    k: v for k, v in sc_data.items()
                    if k not in profile_data or not profile_data[k]
                })
            # Pull nested monetization fields up to top level for easy access
            _pm = profile_data.get("pipeline_metrics", {})
            _mn = _pm.get("monetization", {}) or profile_data.get("monetization", {})
            for _k in ("item_category", "item_name", "price_tag", "overlay_data"):
                if not profile_data.get(_k):
                    profile_data[_k] = (
                        _mn.get(_k)
                        or _mn.get("overlay_data", {}).get(_k)
                        or profile_data.get(_k)
                    )
        except Exception as _pd_e:
            logger.warning(f"[MAIN] profile_data resolution failed (non-fatal): {_pd_e}")
            profile_data = {}

        # --- PREPARE DATA FOR SESSION & REPORT ---
        # [LP_GATE v2] Los Pollos activates for ALL actress/fashion niches (not just NSFW).
        # Strategy: fashion channel audience = voyeurs who want actress content.
        # We monetize them via Los Pollos CPA links inside the Telegram group.
        # YouTube comment only drives to TG group — CPA link lives inside the group post.
        _active_niche = (
            profile_data.get("detected_niche")
            or wm_context.get("detected_niche")
            or ""
        )
        _lp_env_enabled = os.getenv("LOS_POLLOS_TELEGRAM", "yes").lower() in ["yes", "true", "on"]
        # REMOVED NSFW gate — actress/fashion content also gets Los Pollos monetization
        enable_lp_tele = _lp_env_enabled
        if enable_lp_tele:
            logger.info(
                f"✅ [LP_GATE] Niche='{_active_niche}' — Los Pollos ENABLED (all actress niches)."
            )
        _mon_brain_2 = getattr(
            getattr(portal, "monetization_brain", None), "brain", None
        )
        mon_link = (
            _mon_brain_2.get_monetization_link(
                target_platform="telegram",
                detected_niche=_active_niche,
            )
            if enable_lp_tele and _mon_brain_2
            else None
        )
        cta_text = mon_meta.get("monetization_cta", "Shop for the outfit")

        enable_fashion = os.getenv("ENABLE_FASHION_SCOUT", "yes").lower() in [
            "yes",
            "true",
            "on",
        ]
        fashion = mon_meta.get("fashion_scout") if enable_fashion else None

        # --- CASH_MAX_MODE: Session Pre-Save ---
        # [IMPORTANT] CASH_MAX_MODE saves session data early (fixes final_path=None bug)
        # but does NOT auto-upload. Video is always sent to Telegram for YOUR approval first.
        # Nothing goes to YouTube or Instagram without your explicit /approve command.
        if CASH_MAX_MODE:
            logger.info(
                "💰 [CASH_MAX_MODE] Pre-saving session data. Awaiting your Telegram approval."
            )
            with acquire_session_lock(user_id):
                if user_id not in user_sessions:
                    user_sessions[user_id] = {}
                user_sessions[user_id]["monetization_report"] = {
                    "risk": ypp_risk,
                    "style": style,
                    "approved": is_approved,
                    "action": action,
                    "caption": display_caption,
                    "fashion_scout": fashion,
                    "item_name": mon_meta.get("item_name"),
                    "monetization_cta": mon_link if mon_link else cta_text,
                    "cta_text": cta_text,
                }
                # Critical: store the rendered video path so _perform_upload can find it
                user_sessions[user_id]["final_path"] = final_str
                user_sessions[user_id]["title"] = title

                # [AUDIO_CLEANUP] Read audio paths from sidecar so reject can clean the pool
                try:
                    _sc_path_cm = os.path.splitext(final_str)[0] + ".json"
                    if os.path.exists(_sc_path_cm):
                        with open(_sc_path_cm, "r") as _scf:
                            _sc_data_cm = json.load(_scf)
                        _bgm = _sc_data_cm.get("bgm_audio_path", "")
                        _ext = _sc_data_cm.get("extracted_audio_path", "")
                        _all = _sc_data_cm.get("all_extracted_audio", [])
                        if _bgm:
                            user_sessions[user_id]["bgm_audio_path"] = _bgm
                        if _ext:
                            user_sessions[user_id]["extracted_audio_path"] = _ext
                        if _all:
                            user_sessions[user_id]["all_extracted_audio"] = _all
                except Exception as _sc_ae:
                    logger.debug(f"[AUDIO_CLEANUP] Could not read audio paths from sidecar: {_sc_ae}")

                # Store clean path for Instagram DM mode (SEND_TO_YOUTUBE=off)
                _clean_path = (
                    locals().get("pipeline_extras", {}).get("clean_source_path")
                    if isinstance(locals().get("pipeline_extras"), dict)
                    else None
                )
                if _clean_path and os.path.exists(_clean_path):
                    user_sessions[user_id]["clean_source_path"] = _clean_path

                user_sessions[user_id]["state"] = "WAITING_FOR_APPROVAL"
                save_session(user_id)
            logger.info(f"💾 [CASH_MAX_MODE] Session saved. final_path={final_str}")
            # Fall through to normal preview + approval flow below ↓

        # Update Session with Brain Data
        with acquire_session_lock(user_id):
            if user_id not in user_sessions:
                user_sessions[user_id] = {}
            user_sessions[user_id]["monetization_report"] = {
                "risk": ypp_risk,
                "style": style,
                "approved": is_approved,
                "action": action,
                "caption": display_caption,
                "fashion_scout": fashion,
                "item_name": mon_meta.get(
                    "item_name"
                ),  # Store for targeted monetization links
                "monetization_cta": mon_link
                if mon_link
                else cta_text,  # Store actual link if possible
            }
            # Add secondary field for explicit CTA text
            user_sessions[user_id]["monetization_report"]["cta_text"] = cta_text

            # BUG FIX: Save the Final Video Path to session so /approve can find it
            user_sessions[user_id]["final_path"] = final_str
            user_sessions[user_id]["title"] = title  # Update title too if needed

            # [AUDIO_CLEANUP] Read audio paths from sidecar so reject can clean the pool
            try:
                _sc_path_main = os.path.splitext(final_str)[0] + ".json"
                if os.path.exists(_sc_path_main):
                    with open(_sc_path_main, "r") as _scf2:
                        _sc_data_main = json.load(_scf2)
                    _bgm2 = _sc_data_main.get("bgm_audio_path", "")
                    _ext2 = _sc_data_main.get("extracted_audio_path", "")
                    _all2 = _sc_data_main.get("all_extracted_audio", [])
                    if _bgm2:
                        user_sessions[user_id]["bgm_audio_path"] = _bgm2
                    if _ext2:
                        user_sessions[user_id]["extracted_audio_path"] = _ext2
                    if _all2:
                        user_sessions[user_id]["all_extracted_audio"] = _all2
            except Exception as _sc_ae2:
                logger.debug(f"[AUDIO_CLEANUP] Could not read audio paths from sidecar (main): {_sc_ae2}")

            # Save clean pre-overlay path for Instagram DM mode (when SEND_TO_YOUTUBE=off)
            _clean_path = (
                locals().get("pipeline_extras", {}).get("clean_source_path")
                if isinstance(locals().get("pipeline_extras"), dict)
                else None
            )
            if _clean_path and os.path.exists(_clean_path):
                user_sessions[user_id]["clean_source_path"] = _clean_path
                logger.info(
                    f"💾 Clean (No-Overlay) Path Stored for Instagram DM Mode: {os.path.basename(_clean_path)}"
                )

            # BUG FIX: Explicitly set state to WAITING_FOR_APPROVAL so commands work
            user_sessions[user_id]["state"] = "WAITING_FOR_APPROVAL"
            save_session(user_id)

        await safe_reply(update, "✅ Video processed! Sending preview...")

        # --- REPORT ASSEMBLY (MULTI-PART TO PREVENT TRUNCATION) ---
        # --- REPORT ASSEMBLY (STRICT SPLIT) ---

        # 1. Construct Telegram Public Caption (Attraction/Motivation ONLY)
        # User Request: "telegram = attraction message or motivation to find partner message and sponsorship link"

        # --- Extract person name from title for localization ---
        _name_raw = title.replace("_", " ").strip()
        _name_clean = re.sub(r'\s+\d+$', '', _name_raw).strip()
        _display_name = _name_clean.title() if _name_clean else title

        # Consistent 18+ Hook (Using mon_meta text if brain ran, else randomized from pool)
        # Safety net: Only use pool if brain returns empty or very short "filler" (less than 10 words).
        # We want to keep Gemini's new "Detailed Creative" (e.g. Cheetah print teasing).
        raw_cta = mon_meta.get("monetization_cta")
        is_filler = (
            not raw_cta
            or len(raw_cta.split()) < 10
            or any(
                x in raw_cta.lower()
                for x in ["check this out", "click here", "more details"]
            )
        )

        # Override filler check if it looks like a genuine descriptive teaser (Trust but verify)
        if raw_cta and len(raw_cta.split()) >= 10:
            is_filler = False

        if is_filler:
            try:
                # [Personalization Fix] Format randomized hook with display name
                raw_cta = random.choice(HIGH_VOLTAGE_CTA_HOOKS).format(name=_display_name)
            except NameError:
                # Fallback if variable rename missed
                raw_cta = "Warning: This look is fatal. Handle with care."

        # --- DUAL FUNNEL LOGIC MOVED FOR SEQUENCING ---
        # User Request: "Verify user upload before channel sync"
        # We prepare the variables here but dispatch later.

        # Extract the actual descriptive text from the brain's output (not the non-existent visual_description key)
        # We use editorial_script as it contains the richest visual context generated by the first brain pass.
        vis_desc = mon_meta.get("editorial_script", "A stunning outfit.")
        _mb = getattr(portal, "monetization_brain", None)
        _mb_brain = getattr(_mb, "brain", None) if _mb else None

        # --- Mystery story — 4-tier fallback hierarchy ---
        # Tier 0: Gemini hook from price estimator (zero extra API call — piggybacked)
        # Tier 1: Gemini-generated via monetization_brain.get_telegram_story
        # Tier 2: _STORY_FALLBACKS from monetization_brain
        # Tier 3: HIGH_VOLTAGE_CTA_HOOKS (last resort static)
        mystery_story = None

        # Tier 0 — Dynamic Business Hook
        # (Disabled by user request: Saves 1 Gemini API call and avoids "shitty" generated titles)
        mystery_story = None

        # Tier 1 — Reuse already-generated Gemini telegram_hook (ZERO extra API calls)
        # The hook was generated during analyze_content — no new quota consumed here.
        if not mystery_story and mon_meta.get("telegram_hook"):
            mystery_story = mon_meta.get("telegram_hook")

        # Tier 2 — _STORY_FALLBACKS (cold, elite, mystery tone — [PERSONALIZED])
        if not mystery_story or len(mystery_story.split()) < 6:
            try:
                mystery_story = random.choice(_mb_brain._STORY_FALLBACKS).format(name=_display_name) if _mb_brain else None
            except Exception:
                mystery_story = None

        # Tier 3 — HIGH_VOLTAGE last resort only ([PERSONALIZED])
        if not mystery_story or len(mystery_story.split()) < 6:
            mystery_story = random.choice(HIGH_VOLTAGE_CTA_HOOKS).format(name=_display_name)

        # --- Detect item category for targeted links + price psychology ---
        _item_category = (
            mon_meta.get("item_category")
            or (mon_meta.get("fashion_scout") or {}).get("item_category")
            or profile_data.get("item_category")
            or "default"
        )

        # --- Links: US (amazon.com) + India (amazon.in) + CPA ---
        # ── PRIORITY GATE ─────────────────────────────────────────────────────────
        # Rule: user-supplied affiliate link is the SINGLE SOURCE OF TRUTH.
        #
        #  user_affiliate_link present  →  use it, save it keyed by category+item_name
        #  user_affiliate_link absent   →  fuzzy-match stored JSON → brain → None
        # ──────────────────────────────────────────────────────────────────────────
        _user_aff = session.get("user_affiliate_link")

        # Extract the Gemini-generated item_name (caption overlay) for the product key
        # e.g. "Brown Sleeveless Dress" — the actual marketable product description
        _ov_list_aff = mon_meta.get("overlay_data", [])
        if isinstance(_ov_list_aff, dict):
            _ov_list_aff = [_ov_list_aff]
        _item_name_aff = ""
        if _ov_list_aff and isinstance(_ov_list_aff[0], dict):
            _item_name_aff = _ov_list_aff[0].get("item_name", "")
        if not _item_name_aff:
            _item_name_aff = mon_meta.get("final_caption", "")
        _item_name_key = _item_name_aff.lower().strip()   # normalized storage key

        amazon_in_link = None
        amazon_us_link = None

        if _user_aff:
            # ── Tier 0: user-provided link wins ──────────────────────────────────
            amazon_in_link = _user_aff
            amazon_us_link = _user_aff
            logger.info(f"[AFFILIATE] User link (priority): {_user_aff}")

            # ── Auto-save: nested hybrid format {category: {item_name: link}} ────
            # No "_default" key. Every entry is product-specific.
            try:
                _amz_file = "Monetization_Metrics/Amazon_affliate_link.json"
                _amz_data = {}
                if os.path.exists(_amz_file):
                    with open(_amz_file, "r", encoding="utf-8") as _f:
                        _amz_data = json.load(_f)

                _save_cat = _item_category if _item_category else "unknown"
                _save_name = _item_name_key if _item_name_key else "general"

                # Migrate old flat {category: [links]} → nested if needed
                existing_cat = _amz_data.get(_save_cat, {})
                if isinstance(existing_cat, list):
                    # Migrate: convert list to dict keyed by "general"
                    existing_cat = {"general": existing_cat[0]} if existing_cat else {}

                existing_cat[_save_name] = _user_aff        # overwrite with newest link
                _amz_data[_save_cat] = existing_cat

                with open(_amz_file, "w", encoding="utf-8") as _f:
                    json.dump(_amz_data, _f, indent=4, ensure_ascii=False)
                logger.info(
                    f"[AFFILIATE] Saved → {_save_cat} / '{_save_name}' = {_user_aff}"
                )
                await safe_reply(
                    update,
                    f"✅ Affiliate link saved for **{_item_name_aff or _save_cat}**\n"
                    f"↳ Used next time the same product appears.",
                    force=True
                )
            except Exception as _ae:
                logger.warning(f"[AFFILIATE] Auto-save failed (non-fatal): {_ae}")

        else:
            # ── Tier 1: Fuzzy-match stored JSON (product-specific) ────────────────
            try:
                _amz_file = "Monetization_Metrics/Amazon_affliate_link.json"
                if os.path.exists(_amz_file):
                    with open(_amz_file, "r", encoding="utf-8") as _f:
                        _amz_data = json.load(_f)

                    # Find category bucket (case-insensitive)
                    _cat_key = None
                    _cat_lower = _item_category.lower()
                    for _k in _amz_data:
                        if _k.lower() == _cat_lower or _k.lower() in _cat_lower or _cat_lower in _k.lower():
                            _cat_key = _k
                            break

                    if _cat_key:
                        _cat_data = _amz_data[_cat_key]
                        if isinstance(_cat_data, dict) and _cat_data:
                            # Fuzzy word-overlap match against stored product names
                            _query = set(_item_name_key.split()) if _item_name_key else set(_item_category.lower().split())
                            _best_score, _best_link = 0.0, None
                            for _stored_name, _stored_link in _cat_data.items():
                                _stored_words = set(_stored_name.lower().split())
                                _union = _query | _stored_words
                                _score = len(_query & _stored_words) / len(_union) if _union else 0
                                if _score > _best_score:
                                    _best_score, _best_link = _score, _stored_link
                            if _best_score >= 0.45 and _best_link:
                                amazon_in_link = _best_link
                                logger.info(
                                    f"[AFFILIATE] JSON fuzzy match → '{_item_name_key}' "
                                    f"score={_best_score:.2f} → {_best_link}"
                                )
                        elif isinstance(_cat_data, list) and _cat_data:
                            amazon_in_link = _cat_data[0]   # legacy flat format
            except Exception:
                pass

            # ── Tier 2: brain lookup (quota-aware, when JSON has no match) ────────
            if not amazon_in_link:
                try:
                    if _mb_brain:
                        amazon_in_link = _mb_brain.get_monetization_link(
                            target_platform="youtube", item_category=_item_category
                        )
                except Exception:
                    pass

            # ── Tier 3: US link (separate file) ──────────────────────────────────
            try:
                _amz_us_file = "Monetization_Metrics/Amazon_affliate_link_US.json"
                if os.path.exists(_amz_us_file):
                    with open(_amz_us_file, "r", encoding="utf-8") as _f:
                        _amz_us_data = json.load(_f)
                    _us_pool = (
                        _amz_us_data.get(_item_category)
                        or _amz_us_data.get("default", [])
                    )
                    if isinstance(_us_pool, list) and _us_pool:
                        amazon_us_link = _us_pool[0]
            except Exception:
                pass

        # Show IN link in US slot if US is empty
        if not amazon_us_link:
            amazon_us_link = amazon_in_link

        # CPA link (Los Pollos) — adult audience segment
        lp_link = None
        if enable_lp_tele:
            try:
                if _mb_brain:
                    lp_link = _mb_brain.get_monetization_link(target_platform="telegram")
            except Exception:
                pass
            if not lp_link:
                try:
                    _lp_file = "Monetization_Metrics/los_pollos_links.json"
                    if os.path.exists(_lp_file):
                        with open(_lp_file, "r", encoding="utf-8") as _f:
                            _lp_data = json.load(_f)
                        if isinstance(_lp_data, list) and _lp_data:
                            lp_link = random.choice(_lp_data)
                except Exception:
                    pass

        # --- Price psychology block ---
        # [PRICE SYNC] Priority order:
        #   0. User-supplied real_mrp (from Telegram title expansion) ← HIGHEST PRIORITY
        #   1. Precomputed price_data from orchestrator's Gemini estimator
        #   2. Gemini overlay_data hint
        #   3. Random category-tier estimate (last resort)
        _price_data = None
        _price_source_tag = "estimated"  # Tracks whether we have a real vs. estimated price
        try:
            from Text_Modules.smart_price_tag import get_price_display, _premium_price, CATEGORY_PRICE_TIERS
            import re as _re

            # ── Priority 0: User-supplied MRP from Telegram title expansion ────────
            # When the user types "2 https://amzn.to/xyz 23583" the last token is
            # the REAL Amazon MRP they fetched from the product page.
            # This must be the "celebrity price" (original/struck-through price).
            # We generate a believable clone price below it from the category tier.
            _user_real_mrp = session.get("real_mrp")
            if not _user_real_mrp:
                # Also check if it was passed directly (compilation path)
                _user_real_mrp = profile_data.get("real_mrp")

            if _user_real_mrp and int(_user_real_mrp) > 0:
                _user_mrp_int = int(_user_real_mrp)
                # ── real_mrp = actual Amazon selling price = clone_price (gold "deal" number) ──
                # celebrity_price = believable original MRP above it (1.55x–1.75x ratio)
                import random as _tg_rng
                _cel_mult_tg = _tg_rng.uniform(1.55, 1.75)
                _celebrity_price_tg = round(int(_user_mrp_int * _cel_mult_tg) / 100) * 100
                _user_precomputed = {
                    "celebrity_price": _celebrity_price_tg,
                    "clone_price":     _user_mrp_int,
                    "source":          "user_real_mrp",
                }
                _price_data = get_price_display(_item_category, precomputed=_user_precomputed)
                _price_source_tag = "user_real_mrp"  # ← Real, authoritative price
                logger.info(
                    f"[PRICE_SYNC] REAL MRP OVERRIDE (Telegram): "
                    f"Amazon=₹{_user_mrp_int:,} (clone) | Original≈₹{_celebrity_price_tg:,} | "
                    f"(category={_item_category})"
                )
            else:
                # ── Priority 1: Precomputed price_data from orchestrator ──────────
                _precomputed = profile_data.get("price_data")
                if not _precomputed:
                    _precomputed = mon_meta.get("price_data")
                if _precomputed:
                    _price_data = get_price_display(_item_category, precomputed=_precomputed)
                    logger.info(
                        f"[PRICE_SYNC] Using precomputed price: "
                        f"celebrity={_precomputed.get('celebrity_price',0):,} "
                        f"clone={_precomputed.get('clone_price',0):,} "
                        f"source={_precomputed.get('source','?')}"
                    )
                    # ── PRICE SANITY GATE (precomputed path) ─────────────────────────────
                    # Premium-tier estimates still generate ₹15,000+ for basic wear.
                    # Cap to believable Indian market range for non-luxury categories.
                    if _price_data and _item_category.lower() not in ("luxury", "jewellery", "designer"):
                        _cel_raw = _price_data.get("celebrity_price", 0)
                        if isinstance(_cel_raw, (int, float)) and _cel_raw > 7500:
                            import random as _pr2_rand
                            _clone_c2 = _pr2_rand.randint(999, 2499)
                            _cel_c2   = _pr2_rand.randint(2500, 4999)
                            _price_data["celebrity_price"]           = _cel_c2
                            _price_data["clone_price"]               = _clone_c2
                            _price_data["celebrity_price_formatted"] = f"\u20b9{_cel_c2:,}+"
                            _price_data["clone_price_formatted"]     = f"\u20b9{_clone_c2:,}"
                            _savings2 = round((1 - _clone_c2 / _cel_c2) * 100)
                            _price_data["savings_pct"] = f"{_savings2}%"
                            logger.info(
                                f"[PRICE_SANITY] Precomputed price clamped: "
                                f"\u20b9{_cel_raw:,} \u2192 \u20b9{_cel_c2:,} "
                                f"(category={_item_category})"
                            )
                else:
                    # ── Priority 2: Gemini overlay_data hint ─────────────────────
                    _gemini_price = None
                    try:
                        _ov_list = mon_meta.get("overlay_data", [])
                        if isinstance(_ov_list, dict): _ov_list = [_ov_list]
                        _raw_price_str = ""
                        if _ov_list:
                            _raw_price_str = _ov_list[0].get("price_tag", "")
                        if not _raw_price_str:
                            _raw_price_str = profile_data.get("price_tag", "")
                        _raw_price_str = str(_raw_price_str)
                        _price_nums = _re.findall(r'\d+', _raw_price_str.replace(",", ""))
                        if _price_nums:
                            _gemini_price = int(_price_nums[0])
                    except Exception:
                        pass
                    # ── Priority 3: Random category-tier estimate (last resort) ──
                    _price_data = get_price_display(_item_category, _gemini_price)
                    logger.warning(
                        "[PRICE_SYNC] No user MRP or precomputed price_data — using random estimate. "
                        "Video overlay and Telegram prices may differ."
                    )
                    # ── PRICE SANITY GATE ────────────────────────────────────────────────────
                    # Random estimates blow up to ₹15,000+ for basic ethnic wear.
                    # Audience sees real Amazon price → feels deceived → no purchase.
                    # Cap estimated prices to believable Indian market ranges.
                    if _price_data and _item_category.lower() not in ("luxury", "jewellery", "designer"):
                        _cel_raw = _price_data.get("celebrity_price", 0)
                        if isinstance(_cel_raw, (int, float)) and _cel_raw > 7500:
                            import random as _pr_rand
                            _clone_clamped = _pr_rand.randint(999, 2499)
                            _cel_clamped   = _pr_rand.randint(2500, 4999)
                            _price_data["celebrity_price"]           = _cel_clamped
                            _price_data["clone_price"]               = _clone_clamped
                            _price_data["celebrity_price_formatted"] = f"\u20b9{_cel_clamped:,}+"
                            _price_data["clone_price_formatted"]     = f"\u20b9{_clone_clamped:,}"
                            _savings = round((1 - _clone_clamped / _cel_clamped) * 100)
                            _price_data["savings_pct"] = f"{_savings}%"
                            logger.info(
                                f"[PRICE_SANITY] Clamped inflated estimate: "
                                f"\u20b9{_cel_raw:,} \u2192 \u20b9{_cel_clamped:,} "
                                f"(category={_item_category})"
                            )
        except Exception as _pe:
            logger.warning(f"[CAPTION] Price psychology failed (non-fatal): {_pe}")

        # --- Build Telegram & Instagram HIGH-CONVERSION HOOKS (v4) ---
        try:
            # Tier 1: Pull dynamic Gemini hooks from mon_meta (generated during analysis)
            _dynamic_tg_hook = mon_meta.get("telegram_hook")
            _dynamic_ig_hook = mon_meta.get("instagram_hook")
            _dynamic_yt_hook = mon_meta.get("youtube_hook")

            # Tier 1.5: If mon_meta is missing hooks, try the pipeline cache
            # (monetization_brain rescues hooks into smart_price_tag cache even on low-confidence runs)
            if not _dynamic_tg_hook or not _dynamic_ig_hook:
                try:
                    from Text_Modules.smart_price_tag import _read_from_pipeline_cache as _rpc
                    if not _dynamic_tg_hook:
                        _cached_tg = _rpc("telegram_hook")
                        if _cached_tg:
                            _dynamic_tg_hook = _cached_tg
                            logger.info("[HOOK_RESCUE] TG hook recovered from pipeline cache.")
                    if not _dynamic_ig_hook:
                        _cached_ig = _rpc("instagram_hook")
                        if _cached_ig:
                            _dynamic_ig_hook = _cached_ig
                            logger.info("[HOOK_RESCUE] IG hook recovered from pipeline cache.")
                except Exception as _rpc_err:
                    logger.debug(f"[HOOK_RESCUE] Pipeline cache read failed (non-fatal): {_rpc_err}")

            from Intelligence_Modules.money_flow_logic import engine as _mfe
            _offer = _mfe.get_optimized_offer(fashion_category=_item_category)
            _urgency = _offer.get("urgency", "")

            # For Telegram:
            if _dynamic_tg_hook:
                _tg_hook_text = _dynamic_tg_hook
                logger.info("✨ Using Gemini-generated dynamic Telegram hook.")
            else:
                _tg_hook_text = _offer.get("hook", "")
                logger.debug("[HOOK] Gemini TG hook unavailable — using MoneyFlow hardcoded hook.")

            # ── PRICE SANITIZER: strip Gemini-invented prices when real MRP is known ────────
            # Gemini has NO access to the real Amazon price at hook-generation time.
            # If the user supplied a real_mrp via title expansion, Gemini's hook may
            # contain invented numbers (e.g. ₹18,330+ / ₹10,176).  Strip them so
            # the downstream price block can insert the correct figures cleanly.
            _real_mrp_known = _user_real_mrp and int(_user_real_mrp or 0) > 0
            if _tg_hook_text and _real_mrp_known:
                import re as _re_price
                # Remove any ₹NNNN or Rs. NNNN patterns that Gemini invented
                _tg_hook_text = _re_price.sub(
                    r'[₹]\s*[\d,]+(?:\+)?',
                    '',
                    _tg_hook_text
                ).strip()
                # Clean up double spaces left behind
                _tg_hook_text = _re_price.sub(r'  +', ' ', _tg_hook_text).strip()
                logger.info(
                    "[PRICE_SANITIZER] Stripped Gemini-invented prices from TG hook "
                    "(real_mrp=%s is authoritative).", _user_real_mrp
                )

            _tg_hook_block = _tg_hook_text + (f"\n⚡ {_urgency}" if _urgency else "")

            # For Instagram:
            if _dynamic_ig_hook:
                _tg_hook_text_ig = _dynamic_ig_hook
                logger.info("✨ Using Gemini-generated dynamic Instagram hook.")
            else:
                _tg_hook_text_ig = _offer.get("hook", "")
                logger.debug("[HOOK] Gemini IG hook unavailable — using MoneyFlow hardcoded hook.")
            _ig_hook_text  = _tg_hook_text_ig
            _ig_hook_block = _ig_hook_text + (f"\n⚡ {_urgency}" if _urgency else "")
                
            # Store everything including IG and YT hooks so other uploaders can grab them later
            with acquire_session_lock(user_id):
                user_sessions[user_id].setdefault("monetization_report", {})
                user_sessions[user_id]["monetization_report"]["served_hook"] = _tg_hook_text
                if _dynamic_tg_hook: user_sessions[user_id]["monetization_report"]["telegram_hook"] = _dynamic_tg_hook
                if _dynamic_ig_hook: user_sessions[user_id]["monetization_report"]["instagram_hook"] = _dynamic_ig_hook
                if _dynamic_yt_hook: user_sessions[user_id]["monetization_report"]["youtube_hook"] = _dynamic_yt_hook
                save_session(user_id)
                
        except Exception as _mfe_e:
            logger.warning(f"[CAPTION] Hook Generation failed (using fallback): {_mfe_e}")
            _tg_hook_block = mystery_story or raw_cta or "Style breakdown below 👇"
            _ig_hook_block = _tg_hook_block


        # ── Generate / retrieve video UID for this session ─────────────────────
        # Embedded in caption as #vid_XXXXXX so the comment-bot can do exact
        # affiliate link lookup when someone comments "LINK" on the post.
        import uuid as _uuid_mod
        with acquire_session_lock(user_id):
            _video_uid = user_sessions[user_id].get("video_uid")
            if not _video_uid:
                _video_uid = "vid_" + _uuid_mod.uuid4().hex[:6]
                user_sessions[user_id]["video_uid"] = _video_uid
                save_session(user_id)
        logger.info(f"[VIDEO_UID] {_video_uid} assigned to this session")

        # ── HIGH-CONVERSION CAPTION STRUCTURES ──────────────────────────────────
        _primary_link = amazon_in_link or amazon_us_link or ""
        _display_name_esc = _display_name.replace('_', ' ').replace('*', '')

        # ── Build Telegram Caption (tg_caption) ──────────────────────────────
        tg_caption = f"{_display_name_esc}\n\n"
        tg_caption += f"{_tg_hook_block}\n\n"
        
        if _price_data:
            _cel_fmt   = _price_data['celebrity_price_formatted']   # e.g. "₹18,330+"
            _clone_fmt = _price_data['clone_price_formatted']        # e.g. "₹10,176"
            # Using _price_source_tag (set in price resolution block)

            if _price_source_tag == 'user_real_mrp':   # ← matches line 4499 assignment
                # User gave us the REAL price — use authoritative language
                tg_caption += (
                    f"Actual Amazon price: {_clone_fmt} 😱\n"
                    f"Market value piece at {_cel_fmt} — you pay {_clone_fmt} today.\n"
                    f"👑 Click before this deal resets.\n\n"
                )
                logger.info(
                    f"[TG_CAPTION] Real-price block used: MRP={_cel_fmt} deal={_clone_fmt}"
                )
            else:
                # Estimated price — keep curiosity-gap framing
                tg_caption += (
                    f"YT comments guessing {_cel_fmt}... "
                    f"it's actually {_clone_fmt}! 😱\n"
                    f"👑 Amazon deal on the exact market value piece. Click before price resets.\n\n"
                )
                logger.info(
                    f"[TG_CAPTION] Estimated-price block used: MRP={_cel_fmt} deal={_clone_fmt}"
                )
        else:
            tg_caption += "\n"

        if _primary_link:
            # Show link type badge based on how user submitted (1-space=exact, 2-space=alternative)
            with acquire_session_lock(user_id):
                _aff_type_badge = user_sessions.get(user_id, {}).get("affiliate_link_type", "")
            if _aff_type_badge == "exact":
                _shop_label = "🎯 Exact Wear on Amazon\n🔗 Buy here"
            elif _aff_type_badge == "alternative":
                _shop_label = "🔁 Alternative on Amazon\n🔗 Buy here"
            else:
                _shop_label = "🛍️ Shop the Look"
            tg_caption += f"{_shop_label}: {_primary_link}\n\n"

        # ── Build Instagram Caption Base (ig_caption_base) ────────────────────
        # 2026 HIGH-CONVERSION FORMAT:
        # #AD + Hook → Persona pick → 3 category-aware benefits → Price →
        # Scarcity → Comment CTA → Or.. Buy here link →
        # Full hashtags (DEFAULT_HASHTAGS_SHORTS respected) →
        # Copyright disclaimer → Amazon Associate line (always last)

        # ── HASHTAG GENERATION (done first — needed for caption) ───────────────
        import re as _re_mod

        # 1. Brand hashtag
        _brand_raw   = os.getenv("CHANNEL_NAME", os.getenv("BRAND_NAME", ""))
        _brand_clean = _re_mod.sub(r'[^a-zA-Z0-9]', '', _brand_raw)
        _brand_tags  = [f"#{_brand_clean.lower()}"] if _brand_clean else []

        # 2. Item category tag
        _wear_cat_clean = _re_mod.sub(r'[^a-zA-Z0-9]', '', str(_item_category)) if _item_category else ""
        if _wear_cat_clean.lower() == "default":
            _wear_cat_clean = ""
        _wear_tags = [f"#{_wear_cat_clean.lower()}"] if _wear_cat_clean else []

        # 3. Brain / AI generated tags
        _brain_hashtags = mon_meta.get("hashtags", [])
        if isinstance(_brain_hashtags, str):
            _brain_hashtags = _brain_hashtags.split()
        _ai_tags = list(_brain_hashtags) if isinstance(_brain_hashtags, list) else []
        if not _ai_tags and os.getenv("HASHTAG_GEN", "no").lower() == "yes" and hasattr(portal, "hashtag_cache"):
            try:
                _ai_tags = list(portal.hashtag_cache.get_tags(_item_category or ""))[:10]
            except Exception:
                pass

        # ── Full hashtag merge — DEFAULT_HASHTAGS_SHORTS always respected ───────
        _default_tags = os.getenv("DEFAULT_HASHTAGS_SHORTS", "#shorts #viral #trending").split()
        _title_words  = _re_mod.findall(r'[a-zA-Z0-9]+', _display_name)
        _title_tags   = [f"#{w.lower()}" for w in _title_words]
        _seen_ht = set()
        _ordered_tags = []
        for _tag in (_default_tags + _brand_tags + _title_tags + _wear_tags + _ai_tags):
            _t = _tag.lower()
            if _t not in _seen_ht:
                _seen_ht.add(_t)
                _ordered_tags.append(_tag)
        _hashtag_str = " ".join(_ordered_tags)

        # ── Resolve Telegram join line (used in tg_caption only) ─────────────
        _tg_group_id_build = os.getenv("TELEGRAM_GROUP_ID", "")
        _tg_link_build = ""
        if _tg_group_id_build:
            _tg_link_build = f"https://t.me/{_tg_group_id_build.replace('@', '')}"
        else:
            try:
                with open("Credentials/telegram_config.json") as _tcf_b:
                    _tg_link_build = json.load(_tcf_b).get("telegram_link", "")
            except Exception:
                pass
        _tg_join_built = f"Join our Telegram for more: {_tg_link_build}" if _tg_link_build else "Join our Telegram for more"

        if enable_lp_tele and lp_link:
            if isinstance(lp_link, list) and len(lp_link) > 0:
                lp_link = lp_link[0]
            elif isinstance(lp_link, list):
                lp_link = ""
            if lp_link:
                with acquire_session_lock(user_id):
                    user_sessions[user_id].setdefault("monetization_report", {})
                    user_sessions[user_id]["monetization_report"]["lp_link"] = lp_link

        # ── Extract price info for caption ────────────────────────────────────
        _price_orig_str   = ""
        _price_sale_str   = ""
        _discount_pct_str = ""
        _scarcity_line    = "200+ orders in 24h. Selling fast."
        if _price_data:
            # Strip ₹ from formatted strings — template adds it, avoids double ₹₹
            _price_orig_str   = str(_price_data.get("celebrity_price_formatted", "")).replace("₹", "").strip()
            _price_sale_str   = str(_price_data.get("clone_price_formatted", "")).replace("₹", "").strip()
            _discount_pct_str = str(_price_data.get("savings_pct", ""))

        # ── DM trigger keyword ────────────────────────────────────────────────
        _dm_keyword = os.getenv("COMMENT_KEYWORD", "LINK").upper()

        # ── Persona line (own brand name only — no celebrity names) ───────────
        _persona = os.getenv("IG_OWNER_NAME", "").strip()
        _persona_line = f"{_persona} pick: {_display_name_esc.title()}" if _persona else _display_name_esc

        # ── Build bullet benefits from vis_desc or generic fallbacks ─────────
        # vis_desc is the short 2-3 word Gemini label (e.g. "Dark Oval Sunglasses")
        # We do NOT use the long "VIRAL: Process short titled..." paragraph
        _clean_vis = ""
        if vis_desc:
            # Keep only the short garment label — strip everything from "VIRAL:" onward
            _clean_vis = _re_mod.split(r'\s*VIRAL:', vis_desc, maxsplit=1)[0].strip()
            _clean_vis = _re_mod.split(r'\s*Process short', _clean_vis, maxsplit=1)[0].strip()

        _item_label  = _clean_vis or _display_name_esc or _item_category or "this look"
        _cat_lower   = (_item_category or "").lower()
        _label_lower = _item_label.lower()

        # ── Category-aware hook override ──────────────────────────────────
        # The hook from money_flow_logic may not match the product category.
        # Replace with a pool that fits the actual product type.
        import random as _rand_mod
        _hook_pools = {
            "sunglasses": [
                "One frame. Zero effort. All the looks 😎",
                "The sunglasses everyone keeps asking about 👇",
                "She walked in. The room noticed the frame first 👀",
            ],
            "saree": [
                "The saree that stopped the scroll 🌸",
                "She wore this at the function. 12 people asked the brand 🌸",
                "Drape once. Get compliments for a week ✨",
            ],
            "ethnic": [
                "Boutique look. Online price. Same quality 🌸",
                "This sold out twice. Third restock is live 👇",
                "Festive season sorted — and it ships in 2 days 📦",
            ],
            "dress": [
                "Office to dinner without changing — that dress 👗",
                "Her mother-in-law asked for the tailor's number. Here's something better 👇",
                "This almost didn't make it to the feed — too good 😏",
            ],
            "activewear": [
                "Gymshark energy. Indian price 💪",
                "From workout to outing without changing ⚡",
                "Best-seller in fitness right now — already rated 4.8★ 💪",
            ],
            "swimwear": [
                "Beach-ready in under 2 minutes 🏖️",
                "The fit everyone is saving for their next trip 🌊",
                "Supportive AND stylish — rare combo at this price 🏄",
            ],
            "top": [
                "3 compliments before 10am. Same top 👇",
                "The top that pairs with literally everything in your wardrobe ✨",
                "It looks expensive. It really isn't 😏",
            ],
            "bag": [
                "The bag that sold out twice this month 👜",
                "Spacious enough for everything. Cute enough for everywhere 👜",
                "She had 4 bags. Now she only reaches for this one 😏",
            ],
            "footwear": [
                "Comfortable from the first step. That's rare 👟",
                "The shoe that works for every outfit in your wardrobe 👠",
                "Wore these all day. Zero regrets. Zero blisters 🙌",
            ],
            "jewellery": [
                "The piece everyone notices but nobody knows the price of 💎",
                "Lightweight. Tarnish-resistant. Gifts itself 💍",
                "She wore this for a year daily. Still looks new 💎",
            ],
            "luxury": [
                "Old money never announces its source. We just did 😏",
                "Looks ₹20,000. Isn't ₹20,000 😱",
                "The room noticed before anyone spoke 👀",
            ],
            "default": [
                "This almost didn't make it to the feed — too good 😏",
                "Everyone asks where this is from 👇",
                "200+ ordered in 24h. Here's why 👇",
            ],
        }
        def _get_hook_key(label: str, cat: str) -> str:
            l, c = label.lower(), cat.lower()
            if any(k in l or k in c for k in ("sunglass", "eyewear", "goggle", "spectacle")): return "sunglasses"
            if any(k in l or k in c for k in ("saree", "sari")): return "saree"
            if any(k in l or k in c for k in ("kurti", "kurta", "ethnic", "lehenga", "salwar")): return "ethnic"
            if any(k in l or k in c for k in ("midi", "maxi", "mini", "dress", "gown")): return "dress"
            if any(k in l or k in c for k in ("biker short", "legging", "gym", "activewear", "yoga", "sport")): return "activewear"
            if any(k in l or k in c for k in ("bikini", "swimwear", "swimsuit", "beachwear")): return "swimwear"
            if any(k in l or k in c for k in ("top", "blouse", "crop")): return "top"
            if any(k in l or k in c for k in ("bag", "handbag", "tote", "purse", "clutch")): return "bag"
            if any(k in l or k in c for k in ("shoe", "heel", "sandal", "footwear", "sneaker", "flat")): return "footwear"
            if any(k in l or k in c for k in ("jewel", "necklace", "earring", "bracelet", "ring")): return "jewellery"
            if any(k in l or k in c for k in ("luxury", "satin", "silk", "velvet", "bespoke")): return "luxury"
            return "default"
        _hook_key = _get_hook_key(_item_label, _cat_lower)
        # ONLY overwrite with hardcoded fallback if we don't have a dynamic Gemini hook
        if not mon_meta.get("instagram_hook"):
            _ig_hook_block = _rand_mod.choice(_hook_pools[_hook_key])

        # ── Category-aware benefits ─────────────────────────────────────────
        def _pick_benefits(label: str, category: str) -> list:
            l, c = label.lower(), category.lower()
            if any(k in l or k in c for k in ("sunglass", "eyewear", "goggle")):
                return [f"{label} — UV400 protection with a premium finish",
                        "lightweight frame, comfortable for all-day wear",
                        "elevates any outfit — casual, formal, or beach"]
            if any(k in l or k in c for k in ("saree", "sari")):
                return [f"{label} — rich fabric with an effortless drape",
                        "perfect weight — not too heavy, not too sheer",
                        "festive-ready and works for formal occasions too"]
            if any(k in l or k in c for k in ("kurti", "kurta", "ethnic", "lehenga", "salwar")):
                return [f"{label} — intricate detailing at a fraction of boutique price",
                        "comfortable fabric that stays crisp through the day",
                        "festive and casual-ready — one piece, many occasions"]
            if any(k in l or k in c for k in ("bikini", "swimwear", "swimsuit", "beachwear")):
                return [f"{label} — supportive fit that stays in place",
                        "quick-dry fabric, looks great all day at the beach",
                        "compact and lightweight — packs flat in your bag"]
            if any(k in l or k in c for k in ("biker short", "legging", "gym", "activewear", "yoga", "sport")):
                return [f"{label} — high-waist compression that doesn't roll down",
                        "sweat-wicking fabric, stays comfortable during workouts",
                        "gym to street — works outside the gym too"]
            if any(k in l or k in c for k in ("top", "blouse", "crop")):
                return [f"{label} — flattering cut that works for most body types",
                        "lightweight and breathable for all-day comfort",
                        "pairs with jeans, skirts, or trousers — endlessly versatile"]
            if any(k in l or k in c for k in ("midi", "maxi", "mini", "dress", "gown")):
                return [f"{label} — structured silhouette that holds its shape",
                        "premium fabric that drapes beautifully without ironing",
                        "office to dinner without changing — that's the whole point"]
            if any(k in l or k in c for k in ("bag", "handbag", "tote", "purse", "clutch")):
                return [f"{label} — spacious interior with clean, minimal exterior",
                        "sturdy hardware that doesn't tarnish or scratch easily",
                        "day bag to evening bag — works for both"]
            if any(k in l or k in c for k in ("shoe", "heel", "sandal", "footwear", "sneaker", "flat")):
                return [f"{label} — true to size, comfortable from the first wear",
                        "non-slip sole, built to last through daily use",
                        "dresses up or down depending on what you pair it with"]
            if any(k in l or k in c for k in ("jewel", "necklace", "earring", "bracelet", "ring")):
                return [f"{label} — hypoallergenic, tarnish-resistant finish",
                        "lightweight — you forget you're wearing it",
                        "gift-ready packaging included"]
            return [f"{label} — premium quality at this price point is rare",
                    "fits well straight out of the box, no adjustment needed",
                    "photographs beautifully — exactly what you see in the video"]
        _benefit_pool = _pick_benefits(_item_label, _cat_lower)

        # ── Template rotation ─────────────────────────────────────────────
        _CAPTION_ROTATION = os.getenv("CAPTION_TEMPLATE_ROTATION", "sequential").lower()

        # Track rotation index in session
        with acquire_session_lock(user_id):
            _tmpl_idx = user_sessions[user_id].get("ig_template_idx", 0)
            user_sessions[user_id]["ig_template_idx"] = (_tmpl_idx + 1) % 3

        if _CAPTION_ROTATION == "random":
            _tmpl_idx = _rand_mod.randint(0, 2)

        # ── Shared footer: CTA → Or.. link → hashtags → copyright/attribution (last) ──
        _copyright_line = os.getenv("COPYRIGHT_DISCLAIMER", "").strip()
        # Use session link; fall back to DEFAULT_AFFILIATE_LINK so link is never blank
        _footer_link = _primary_link or os.getenv("DEFAULT_AFFILIATE_LINK", "").strip()
        def _build_footer(cta: str) -> str:
            _f  = f"{cta} Or..\n\n"
            if _footer_link:
                _f += f"🔗 Buy here: {_footer_link}\n\n"
            _f += f"{_hashtag_str}\n\n"
            _f += f"{_copyright_line}"
            return _f

        if _tmpl_idx == 0:
            ig_caption_base = (
                f"#AD {_ig_hook_block}\n"
                f"⚡ Selling fast today.\n\n"
                f"{_persona_line}\n"
                f"✦ {_benefit_pool[0]}\n"
                f"✦ {_benefit_pool[1]}\n"
                f"✦ {_benefit_pool[2]}\n\n"
            )
            if _price_orig_str:
                ig_caption_base += (
                    f"This designer look usually costs ₹{_price_orig_str}+. "
                    f"I found it for a fraction. Guess the offer price below! 👇\n\n"
                    f"What do you think the deal price is?\n"
                    f"Reply with your guess — closest one gets the link first 🎯\n"
                )
            ig_caption_base += f"{_scarcity_line}\n\n" + _build_footer(f'Comment your guess or "{_dm_keyword}" and I\'ll DM the real price + link 👇')

        elif _tmpl_idx == 1:
            ig_caption_base = (
                f"#AD — {_ig_hook_block}\n"
                f"⚡ Limited stock today.\n\n"
                f"{_persona_line}\n\n"
                f"What actually makes this worth it:\n"
                f"— {_benefit_pool[0]}\n"
                f"— {_benefit_pool[1]}\n"
                f"— {_benefit_pool[2]}\n\n"
            )
            if _price_orig_str:
                ig_caption_base += (
                    f"She paid ₹{_price_orig_str}+ for this look. "
                    f"I found the alternative. Guess the price 👇\n\n"
                    f"Drop your number in the comments — I'll reveal the price + link to whoever is closest 🎯\n"
                )
            ig_caption_base += f"{_scarcity_line}\n\n" + _build_footer(f'Comment your price guess or "{_dm_keyword}" for the instant link 👇')

        else:
            ig_caption_base = (
                f"#AD | Real talk — {_ig_hook_block}\n"
                f"⚡ Restocked today — won't last.\n\n"
                f"{_persona_line}\n\n"
                f"Here's why it stayed in my cart:\n"
                f"{_benefit_pool[0]}. {_benefit_pool[1]}. {_benefit_pool[2]}.\n\n"
            )
            if _price_orig_str:
                ig_caption_base += (
                    f"Real MRP on this look: ₹{_price_orig_str}+. "
                    f"The alternative is sitting on Amazon right now. "
                    f"What price do you think it is? 🤔\n\n"
                    f"Closest guess wins the DM link first 👇\n"
                )
            ig_caption_base += f"{_scarcity_line}\n\n" + _build_footer(f'Guess below or type "{_dm_keyword}" for the link 👇')


        # Admin warning if all links missing
        if not _primary_link and not lp_link:
            logger.warning(
                "⚠️ [CAPTION] All links are None. "
                "Check Monetization_Metrics/ JSON files exist and are non-empty."
            )



        # Define internal helper for later use
        async def _bg_raw_sync_upload(v_path, caption, channel):
            for attempt in range(1, 4):  # Reduced retries for sync
                try:
                    async with UPLOAD_SEMAPHORE:
                        with ProgressFile(v_path, logger.info) as vf:
                            await locals().get("context").bot.send_video(
                                chat_id=channel,
                                video=vf,
                                caption=caption,
                                parse_mode="Markdown",
                                read_timeout=600,
                                write_timeout=600,
                                connect_timeout=60,
                            )
                    return
                except:
                    await asyncio.sleep(5)

        # FIXED: Use the rich tg_caption for the Public Group
        public_caption = tg_caption

        # Safety Check for default links
        if enable_lp_tele and lp_link and "example.com" in lp_link:
            await safe_reply(
                update,
                "⚠️ WARNING: You are using the default 'example.com' link. Please update 'The_json/los_pollos_links.json'.",
            )

        # ── Strip internal #vid_ tracking tags before any caption is published ──
        # The tag is only used for comment-bot UID lookup; it must never appear
        # in the live Instagram caption.
        ig_caption_base = sanitise_ig_caption(ig_caption_base)
        public_caption  = sanitise_ig_caption(public_caption)

        # ── DRY_RUN guard — prints preview and skips all real output ──────────
        _dry_run = os.getenv("DRY_RUN", "no").lower() in ("yes", "true", "1")
        if _dry_run:
            logger.info("[DRY_RUN] Caption preview (no upload will occur):")
            logger.info(f"\n{'='*60}\n{ig_caption_base}\n{'='*60}")
            await safe_reply(
                update,
                f"🧪 *DRY RUN — no upload*\n\nCaption preview:\n```\n{ig_caption_base[:800]}\n```"
                f"\n\nSet `DRY_RUN=no` in .env when ready to go live.",
            )
            return


        # ── [MILITARY-GRADE OVERRIDE] Affiliate link = Fashion & Style manual route ──
        # When the user provides an affiliate link at title-selection time, replace the
        # auto-generated price-tag caption with a precision 3-beat Gemini copy that
        # drives ManyChat DM conversions. The actual link is NEVER pasted in the caption
        # — it goes via the ManyChat keyword trigger instead.
        try:
            _is_manual_affiliate = bool(
                user_sessions.get(user_id, {}).get("niche_forced_by_affiliate")
                or user_sessions.get(user_id, {}).get("user_affiliate_link")
            )
            if _is_manual_affiliate:
                _aff_link_for_mg = user_sessions.get(user_id, {}).get("user_affiliate_link", "")
                _item_for_mg = (
                    mon_meta.get("item_name")
                    or str(_item_label)[:80]
                    or _display_name_esc
                )
                _actress_for_mg = (
                    str(title).split(":")[0].strip()
                    if ":" in str(title)
                    else str(title).split()[0].strip() if title else ""
                )
                from Uploader_Modules.community_promoter import promoter as _cp
                _mg_caption = _cp.get_instagram_fanpage_caption(
                    base_caption=ig_caption_base[:120],
                    actress_name=_actress_for_mg,
                    affiliate_link=_aff_link_for_mg,
                    item_name=_item_for_mg,
                )
                if _mg_caption and len(_mg_caption.strip()) > 30:
                    ig_caption_base = _mg_caption
                    logger.info(
                        "💪 [MG_COPY] Military-grade caption injected for '%s' "
                        "(item='%s')", _actress_for_mg, _item_for_mg
                    )
        except Exception as _mg_ovr_e:
            logger.warning("⚠️ [MG_COPY] Override failed (non-fatal, using original): %s", _mg_ovr_e)

        # Store public_caption in session so _perform_upload can broadcast it to the Telegram group
        # AND store ig_caption_base for Meta Uploader
        with acquire_session_lock(user_id):
            user_sessions[user_id].setdefault("monetization_report", {})[
                "public_caption"
            ] = public_caption
            user_sessions[user_id]["monetization_report"]["ig_caption_base"] = ig_caption_base
            save_session(user_id)


        # B. Hashtags (Optional - keeping them for discovery if user wants, but request said ONLY attraction msg.
        # User said "in one line". Let's keep it strictly to the CTA + Link.)

        # 2. Construct Admin Debug Report (Stats + Materials for Manual Work)
        # User Request: "🎬 FINAL REVIEW SUMMARY" format

        # ── Build SEO Amazon keyword ──────────────────────────────────────────────
        # Priority: wear_scanner item_name → fashion_scout SEO keywords → item_category
        _seo_item_name  = profile_data.get("wear_scan_item") or ""
        _seo_category   = _item_category.replace("_", " ").title() if _item_category and _item_category != "default" else ""
        _seo_fashion_kw = ""
        _seo_amazon_q   = ""
        if fashion:
            _seo_amazon_q   = fashion.get("search_queries", {}).get("amazon", "")
            _seo_fashion_kw = ", ".join(fashion.get("seo_search_keywords", []))

        # Derive item_name from wm_context/profile_data if wear scanner stored it
        _detected_item = (
            (fashion or {}).get("wear_name")
            or wm_context.get("wear_scan_item")
            or profile_data.get("wear_scan_item")
            or mon_meta.get("item_name")
            or profile_data.get("item_name")
            or _seo_category
            or "Women Outfit"
        )

        # Build keyword: item description + category + market qualifiers
        _kw_parts = []
        if _detected_item:
            _kw_parts.append(_detected_item.strip())
        if _seo_category and _seo_category.lower() not in _detected_item.lower():
            _kw_parts.append(_seo_category)
        _kw_parts += ["Women", "India", "Buy Online"]
        _amazon_keyword = " ".join(_kw_parts)

        # Prefer Gemini's pre-built Amazon query if available (richer)
        if _seo_amazon_q and len(_seo_amazon_q.split()) >= 3:
            _amazon_keyword = _seo_amazon_q

        # Build concise Amazon search URL
        import urllib.parse as _urlparse
        _amazon_search_url = f"https://www.amazon.in/s?k={_urlparse.quote_plus(_amazon_keyword)}"

        # ── Resolve transformation_score from all possible carriers ──────────────
        # Score lives in: profile_data → wm_context → mon_meta (sidecar monetization)
        # It is always a 0.0–1.0 float from unified_intelligence; convert to 0–100 %.
        _raw_ts = (
            profile_data.get("transformation_score")
            or wm_context.get("transformation_score")
            or mon_meta.get("transformation_score")
        )
        try:
            _ts_float = float(_raw_ts) if _raw_ts is not None else None
        except (ValueError, TypeError):
            _ts_float = None

        if _ts_float is None:
            _ts_display = "N/A"
            _ts_label   = "Pending Analysis"
        else:
            # Normalize: values ≤ 1.0 are 0–1 scale; values > 1.0 are already %
            _ts_pct = _ts_float * 100 if _ts_float <= 1.0 else _ts_float
            _ts_pct = round(_ts_pct, 1)
            _ts_display = f"{_ts_pct}"
            if _ts_pct >= 80:
                _ts_label = "🟢 Excellent"
            elif _ts_pct >= 55:
                _ts_label = "🟡 Good"
            elif _ts_pct >= 30:
                _ts_label = "🟠 Needs Work"
            else:
                _ts_label = "🔴 Low"

        admin_report = (
            "--------------------------------\n"
            "🎬 **FINAL REVIEW SUMMARY**\n"
            "--------------------------------\n\n"
            f"🎯 **Title:**\n{title}\n\n"
            f"👗 **Detected Item:**\n`{_detected_item}`\n\n"
            f"🔍 **Amazon Search Keyword:**\n`{_amazon_keyword}`\n\n"
            f"🛒 **Quick Search:**\n{_amazon_search_url}\n\n"
            f"📌 **Caption Generated:**\n{display_caption}\n\n"
            f"🧠 **Watermark Status:**\n{wm_status}\n\n"
            f"💰 **Monetization Status:**\n{'✅ SAFE' if ypp_risk.upper() == 'LOW' else '⚠️ CHECK REQUIRED'}\n\n"
            f"⚠️ **Risk Level:**\n{ypp_risk.upper()}\n\n"
            f"🎨 **Transformation:**\n{_ts_display}% — {_ts_label}\n\n"
            f"📎 **Reason:**\n{reason}\n\n"
        )

        if fashion:
            links   = fashion.get("search_links", {})
            queries = fashion.get("search_queries", {})
            admin_report += "👗 **FASHION SCOUT (FOR AFFILIATE LINKS)**\n"

            if _seo_fashion_kw:
                admin_report += f"🏷️ **SEO Keywords:** `{_seo_fashion_kw}`\n"

            seo_binded = fashion.get("seo_binded_links", [])
            if seo_binded:
                admin_report += "🔗 **Direct Search Links:**\n"
                for item in seo_binded:
                    kw   = item.get("kw")
                    l_in = item.get("link_in")
                    l_us = item.get("link_us")
                    admin_report += f"• {kw}: [🇮🇳]({l_in}) | [🇺🇸]({l_us})\n"
            elif links.get("amazon_in"):
                admin_report += f"🔗 **Amazon IN:** {links.get('amazon_in')}\n"
            if links.get("amazon_us"):
                admin_report += f"🔗 **Amazon US:** {links.get('amazon_us')}\n"

            admin_report += "*(Use these to generate your Affiliate Link)*\n\n"

        tips = mon_meta.get("improvement_tips", [])
        if tips:
            admin_report += "💡 **Improvement Tips:**\n"
            for t in tips[:2]:
                admin_report += f"• {t}\n"
            admin_report += "\n"

        admin_report += f'📜 **Policy:** "{mon_meta.get("policy_citation", "Significant Original Commentary")}"\n\n'
        admin_report += f"🚀 {wm_msg}\n\n"
        admin_report += "--------------------------------\n"
        admin_report += "Reply /approve to upload or /reject."


        # --- BUTTONS (ON VIDEO) ---
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton("\u2705 Clean (Yes)", callback_data="wm_clean"),
                InlineKeyboardButton("\u274c Bad (No)", callback_data="wm_bad"),
            ],
            [
                InlineKeyboardButton("\U0001f680 Approve & Post", callback_data="approve_post"),
                InlineKeyboardButton("\U0001f5d1\ufe0f Reject", callback_data="reject_discard"),
            ],
            [
                # [INFLUENCER] Face Swap & Post — triggers _handle_influencer_refine
                InlineKeyboardButton("\U0001f3ad Face Swap & Post", callback_data="influencer_refine"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            if os.path.getsize(final_str) < 50 * 1024 * 1024:
                # 1. Unified Preview: Admin Report as Video Caption
                # (Ensures all data is in one place for review)
                combined_caption = admin_report
                if len(combined_caption) > 1024:
                    combined_caption = combined_caption[:1021] + "..."

                await safe_video_reply(
                    update,
                    final_str,
                    caption=combined_caption,
                    reply_markup=reply_markup,
                )
            else:
                large_msg = f"⚠️ Video too large for Telegram preview.\n{admin_report}\n\n**Public Caption Preview:**\n{public_caption}"
                await safe_reply(update, large_msg)

        except Exception as e:
            logger.error(f"Error: {e}")
            await safe_reply(update, "❌ Error occurred during preview send.")
        return

    # Case 3.5: WAITING_FOR_COMPILATION_TITLE (New Mandatory Flow)
    if state == "WAITING_FOR_COMPILATION_TITLE":
        user_id = update.effective_user.id

        # Retrieve context
        with acquire_session_lock(user_id):
            session = user_sessions.get(user_id, {})
            merged_path = session.get("pending_compilation_path")
            n_videos = session.get("pending_n_videos")
            hashtags = session.get("pending_hashtags")
            base_title = session.get("pending_base_title", "")

        if not merged_path or not os.path.exists(merged_path):
            await safe_reply(update, "❌ Compilation file lost. Please try again.")
            with acquire_session_lock(user_id):
                user_sessions.pop(user_id, None)
                save_session(user_id)
            return

        # ── Parse inline reply (number + optional affiliate link) ──────────
        try:
            parsed_res = parse_user_input(text)
            if len(parsed_res) == 4:
                title_index, user_affiliate_link, _aff_link_type, _real_mrp = parsed_res
            else:
                title_index, user_affiliate_link = parsed_res[:2]
                _aff_link_type, _real_mrp = None, None
        except Exception as pe:
            logger.error(f"❌ [PARSE_ERR] Failed to parse input: {pe}")
            await safe_reply(update, "❌ Invalid input format.")
            return

        final_title = ""

        # SKIP LOGIC
        if text.strip().lower().startswith("/skip"):
            final_title = base_title if base_title else f"Compilation {n_videos} Videos"
            await safe_reply(
                update, f"⏩ Skipping preset. Using Base Title: {final_title}"
            )
            await finish_compilation_upload(
                update, merged_path, final_title, hashtags,
                affiliate_link=user_affiliate_link,
                affiliate_link_type=_aff_link_type,
                real_mrp=_real_mrp,
                clips=user_sessions.get(user_id, {}).get("compilation_clips")
            )
            return

        # PRESET LOGIC
        try:
            presets = get_presets()
            str_index = str(title_index) if title_index is not None else None

            if str_index and str_index in presets:
                item = presets[str_index]
                suffix = item.get("suffix", "")
                if base_title and base_title != f"Compilation {n_videos} Videos":
                    final_title = f"{base_title}{suffix}"
                else:
                    final_title = f"{item['label']} {suffix}"

                logger.info(f"[TITLE_FLOW] Final title: '{final_title}' | affiliate: {user_affiliate_link}")
            elif title_index is None and user_affiliate_link is None:
                await safe_reply(
                    update,
                    "⚠️ Invalid selection. Reply with a number (e.g. '2') or /skip.",
                )
                return
            else:
                # Should not ideally happen if presets match numbers, but for safety:
                await safe_reply(
                    update,
                    "⚠️ Invalid selection. Please reply with a number (e.g. '2') or /skip.",
                )
                return
        except Exception as e:
            logger.error(f"Preset load error: {e}")
            final_title = base_title or f"Compilation {n_videos} Videos"

        if user_affiliate_link:
            import Monetization_Metrics.fashion_scout as fs
            with acquire_session_lock(user_id):
                _mon_report = user_sessions.get(user_id, {}).get("monetization_report", {})
            _item_cat = _mon_report.get("item_name") or "compilation"
            _uid_for_save = user_sessions.get(user_id, {}).get("video_uid", "")
            fs.save_affiliate_link(_item_cat, _item_cat, user_affiliate_link, video_uid=_uid_for_save)
            await safe_reply(update, f"✅ Title: {final_title}\n🔗 Affiliate link received and saved!")
        else:
            await safe_reply(update, f"✅ Title: {final_title}")

        # Proceed to Finish (pass affiliate link through)
        await finish_compilation_upload(
            update, merged_path, final_title, hashtags,
            affiliate_link=user_affiliate_link,
            affiliate_link_type=_aff_link_type,
            real_mrp=_real_mrp,
            clips=user_sessions.get(user_id, {}).get("compilation_clips")
        )
        return

    # Case 3: Title Expansion Selection (OLD - KEPT FOR BACKWARD COMPAT IF NEEDED or REMOVE?)
    # The user said: "ask user for tittle that from title_expansion_presets.json... but not as optional."
    # The old flow was optional after approval.
    # I will KEEP the old flow for single videos if it exists, but the new flow is for compilations.
    # The old flow state is 'WAITING_FOR_TITLE_EXPANSION', new is 'WAITING_FOR_COMPILATION_TITLE'.

    if state == "WAITING_FOR_TITLE_EXPANSION":
        # ── Parse inline reply (number + optional affiliate link) ──────────
        try:
            parsed_res = parse_user_input(text)
            if len(parsed_res) == 4:
                title_index, user_affiliate_link, _aff_link_type, _real_mrp = parsed_res
            else:
                title_index, user_affiliate_link = parsed_res[:2]
                _aff_link_type, _real_mrp = None, None
        except Exception as pe:
            logger.error(f"❌ [PARSE_ERR] Failed to parse input: {pe}")
            await safe_reply(update, "❌ Invalid input format.")
            return


        if text.strip().startswith("/skip"):
            # Store affiliate link even on skip
            if user_affiliate_link:
                with acquire_session_lock(user_id):
                    user_sessions[user_id]["user_affiliate_link"] = user_affiliate_link
                    if _real_mrp:
                        user_sessions[user_id]["real_mrp"] = _real_mrp
                    save_session(user_id)
            await _perform_upload(update, context)
        elif title_index is not None:
            # Load presets
            try:
                presets = get_presets()
                choice = presets.get(str(title_index))
                if choice:
                    suffix = choice.get("suffix", "")
                    with acquire_session_lock(user_id):
                        current_title = user_sessions[user_id].get("title", "")
                        user_sessions[user_id]["title"] = f"{current_title}{suffix}"
                        # ── Store user-supplied affiliate link if provided ──
                        if user_affiliate_link:
                            user_sessions[user_id]["user_affiliate_link"] = user_affiliate_link
                            # Store link type: 'exact' = found actual celeb product; 'alternative' = similar product
                            if _aff_link_type:
                                user_sessions[user_id]["affiliate_link_type"] = _aff_link_type
                            # ── Store real MRP if provided ──────────────────
                            if _real_mrp:
                                user_sessions[user_id]["real_mrp"] = _real_mrp
                            import Monetization_Metrics.fashion_scout as fs
                            _mon_report = user_sessions[user_id].get("monetization_report", {})
                            _item_cat = _mon_report.get("item_name") or _mon_report.get("fashion_scout", {}).get("attributes", {}).get("classification", {}).get("primary_category", "default")
                            _item_sub = _mon_report.get("fashion_scout", {}).get("attributes", {}).get("classification", {}).get("sub_category", _item_cat)
                            _uid_for_save = user_sessions.get(user_id, {}).get("video_uid", "")
                            fs.save_affiliate_link(_item_cat, _item_sub, user_affiliate_link, video_uid=_uid_for_save)

                            # [NICHE OVERRIDE] Affiliate link = manual Fashion & Style route.
                            # Force the niche sidecar so meta_uploader picks the right
                            # IG account regardless of what Gemini auto-detected.
                            try:
                                _final_for_niche = user_sessions[user_id].get("final_path", "")
                                if _final_for_niche and os.path.exists(_final_for_niche):
                                    from Visual_Refinement_Modules.hybrid_watermark import save_detected_niche
                                    save_detected_niche(_final_for_niche, "Fashion & Style")
                                    user_sessions[user_id]["niche_forced_by_affiliate"] = "Fashion & Style"
                                    logger.info(
                                        "🎯 [NICHE OVERRIDE] Affiliate link → forcing niche='Fashion & Style' "
                                        f"for '{os.path.basename(_final_for_niche)}'"
                                    )
                                    _fs_env = os.path.join("Credentials", "social_media", "Fashion & Style", ".env")
                                    if os.path.exists(_fs_env):
                                        load_dotenv(_fs_env, override=True)
                            except Exception as _ne:
                                logger.warning(f"⚠️ [NICHE OVERRIDE] Sidecar rewrite failed (non-fatal): {_ne}")

                        save_session(user_id)
                    confirm = f"✅ Title Updated: {user_sessions[user_id]['title']}"
                    if user_affiliate_link:
                        _ltype_label = " [Exact Product]✔️" if _aff_link_type == 'exact' else " [Alternative Product]🔁" if _aff_link_type == 'alternative' else ""
                        confirm += f"\n🔗 Affiliate link saved{_ltype_label}\n🎯 Routed → Fashion & Style"
                    await safe_reply(update, confirm)
                    await _perform_upload(update, context)
                else:
                    await safe_reply(
                        update, "⚠️ Invalid selection. Reply number or /skip."
                    )
            except Exception as e:
                logger.error(f"❌ Error applying preset: {e}")
                await _perform_upload(update, context)
        elif title_index is None and user_affiliate_link is None:
            await safe_reply(update, "⚠️ Invalid selection. Use:\n2 OR 2 https://link")
            return
        else:
            await safe_reply(update, "⚠️ Reply with a number to apply preset, or /skip.")
        return

    # Case 4: Approval
    if state == "WAITING_FOR_APPROVAL":
        if text.lower() in ["approve", "/approve"]:
            await approve_upload(update, context)
        elif text.lower() in ["yes", "y"]:
            await verify_watermark(update, context, is_positive=True)
        elif text.lower() in ["no", "n"]:
            await verify_watermark(update, context, is_positive=False)
        elif text.lower() in ["reject", "/reject"]:
            await reject_upload(update, context)
        else:
            await safe_reply(
                update,
                "⚠️ Options:\n• 'yes'/'no' - Verify visual refinement (Training Data)\n• '/approve' - Upload to YouTube\n• '/reject' - Discard Video",
            )
        return

    # Case 5: Initial Local File Path (New Session Fallback)
    possible_path = text.strip('"').strip("'")
    if os.path.exists(possible_path) and os.path.isfile(possible_path):
        file_name = os.path.basename(possible_path)
        file_size = os.path.getsize(possible_path)

        await safe_reply(
            update,
            f"📂 Found Local File: `{file_name}` ({file_size / 1024 / 1024:.1f}MB)",
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_name = _sanitize_title(file_name)
        save_path = os.path.join("downloads", f"local_{clean_name}_{timestamp}.mp4")
        try:
            shutil.copy2(possible_path, save_path)
        except Exception as e:
            await safe_reply(update, f"❌ Failed to copy local file: {e}")
            return

        with acquire_session_lock(user_id):
            user_sessions[user_id] = {
                "state": "WAITING_FOR_TITLE",
                "pending_local_path": str(save_path),
                "pending_url": None,
            }
            save_session(user_id)

        default_hashtags = os.getenv("DEFAULT_HASHTAGS_SHORTS", "#shorts")
        await safe_reply(
            update,
            f"✅ File Staged!\n\n📌 Hashtags:\n{default_hashtags}\n\n✏️ Now send the title to start processing.",
        )
        return

    # Case 6: Catch-all for regular messages (Help)
    if not text.startswith("/"):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        keyboard = [
            [InlineKeyboardButton("\U0001f3ac Autonomous Storytelling", callback_data="autonomous_story")],
            [InlineKeyboardButton("\U0001f4b0 Money Printer (Compilations)", callback_data="money_printer_help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await safe_reply(
            update,
            "🤖 AMTCE Bot Active.\n\nSend me a URL or a Local File Path to start a new job.",
            reply_markup=reply_markup
        )


async def _handle_influencer_refine(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    query,
) -> None:
    """
    Handles the 'Face Swap & Post' Telegram button.

    Guard 1 — .env flag: ENABLE_INFLUENCER_REFINEMENT must be yes.
    Guard 2 — Eligibility: video must pass 4 quality conditions.
    If both pass: swap face, write niche sidecar, upload to face_refinement IG account.
    """
    import asyncio as _asyncio

    # ── Guard 1: .env master switch ───────────────────────────────────────────
    if os.getenv("ENABLE_INFLUENCER_REFINEMENT", "no").lower() not in ("yes", "true", "1"):
        _disabled_msg = (
            "🎭 *Face Swap & Post — Feature Disabled*\n\n"
            "This feature requires a GPU with ≥ 6 GB VRAM and is currently turned off.\n\n"
            "To enable it:\n"
            "1️⃣ Open `Credentials/.env`\n"
            "2️⃣ Set `ENABLE_INFLUENCER_REFINEMENT=yes`\n"
            "3️⃣ Restart the bot on a machine with a capable GPU\n\n"
            "_Everything else (watermark removal, uploads, caption AI) is fully active._"
        )
        # Acknowledge the button click first (clears the spinner on Telegram)
        try:
            await query.answer("Feature disabled — see chat for details.", show_alert=True)
        except Exception:
            pass
        # Send a persistent, visible message in chat so the user cannot miss it
        await safe_reply(update, _disabled_msg)
        return

    # ── Resolve video path from session ──────────────────────────────────────
    with get_session_lock(user_id):
        _session      = user_sessions.get(user_id, {})
        _video_path   = _session.get("final_path") or _session.get("pending_video")

    if not _video_path or not os.path.isfile(_video_path):
        try:
            await query.answer("Session expired. Please upload the video again.", show_alert=True)
        except Exception:
            await safe_reply(update, "Session expired. Please upload the video again.")
        return

    # ── Guard 2: Eligibility scoring ─────────────────────────────────────────
    await safe_reply(update, "Checking video eligibility for face swap...")
    try:
        from Influencer_Modules.face_swap_selector import score_video as _score_video
        _score, _details, _eligible = await _asyncio.to_thread(_score_video, _video_path)
    except Exception as _sel_err:
        await safe_reply(update, f"Eligibility check failed: {_sel_err}")
        logger.error(f"[INFLUENCER_REFINE] Selector error: {_sel_err}", exc_info=True)
        return

    if not _eligible:
        _thr  = _details.get("threshold", 0.60)
        _lines = [
            f"Cannot face-swap this video (score {_score:.2f} / {_thr:.2f} required).",
            "",
        ]
        for _key, _label, _fmt in [
            ("front_face", "Front-face ratio",    lambda v: f"{v*100:.0f}% of frames (need >= 60%)"),
            ("motion",     "Motion score",         lambda v: f"{v:.1f} avg flow (need <= 4.0)"),
            ("lighting",   "Lighting stability",   lambda v: f"brightness std={v:.0f} (need <= 50)"),
            ("expression", "Extreme expressions",  lambda v: f"{v*100:.0f}% frames with open mouth (need < 30%)"),
        ]:
            _d    = _details.get(_key, {})
            _mark = "OK" if _d.get("pass") else "!!"
            _lines.append(f"  [{_mark}] {_label}: {_fmt(_d.get('value', 0.0))}")
        _lines.append("")
        _lines.append("Tip: use a video where the person faces the camera, moves slowly, and has stable lighting.")
        await safe_reply(update, "\n".join(_lines))
        return

    # ── Ask the user to send their face image ─────────────────────────────────
    try:
        await query.answer("Video eligible! Please send your face photo.")
    except Exception:
        pass

    with acquire_session_lock(user_id):
        user_sessions[user_id]["state"]           = "WAITING_FOR_FACE_IMAGE"
        user_sessions[user_id]["face_swap_video"]  = _video_path
        user_sessions[user_id]["face_swap_score"]  = _score
        save_session(user_id)

    await safe_reply(
        update,
        f"✅ Video passed eligibility (score {_score:.2f}).\n\n"
        "📸 *Now send me the face photo you want to swap in.*\n"
        "• Send a clear front-facing photo (JPG or PNG)\n"
        "• One face in the photo works best\n"
        "• Higher resolution = better result",
    )


async def _handle_autonomous_story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggers the 'Movie Director' pipeline.
    Phase 1: Present niche options for storytelling.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    msg = (
        "🎬 **Autonomous Movie Director Mode**\n\n"
        "I will now act as your Director, Scriptwriter, and Editor.\n"
        "Please select a niche to start an autonomous production (16:9 + Shorts):"
    )
    
    # We'll pull niches from the environment or a preset list
    # These will trigger the NarrativeDirector and stock media fetching
    niches = ["Fashion Evolution", "Celebrity Documentary", "Style Breakdown"]
    keyboard = []
    for n in niches:
        keyboard.append([InlineKeyboardButton(f"\U0001f4fd {n}", callback_data=f"story_start_{n.lower().replace(' ', '_')}")])
    
    # Add a back button
    keyboard.append([InlineKeyboardButton("\u2b05\ufe0f Back", callback_data="main_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await safe_reply(update, msg, reply_markup=reply_markup)

async def _handle_money_printer_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Help menu for the 'Money Printer' (Compilations) feature.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    msg = (
        "💰 **Money Printer (Compilations) Active**\n\n"
        "Use these commands to generate monetizable compilations with AI-driven hooks and music:\n\n"
        "1️⃣ `/compile_last 10 [Name]` — Compiles last 10 clips of a subject from `Processed Shorts/` subfolders.\n"
        "2️⃣ `/compile_first 10 [Name]` — Compiles first 10 clips (chronological evolution).\n"
        "3️⃣ `/compile_last 20` — General compilation of the last 20 processed clips.\n\n"
        "✨ **Monetization Features:**\n"
        "• Automatic Music ducking\n"
        "• AI-generated Progression Hooks (Text Overlays)\n"
        "• Natural Key Sorting (Evolution-aware)"
    )
    
    keyboard = [[InlineKeyboardButton("\u2b05\ufe0f Back", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await safe_reply(update, msg, reply_markup=reply_markup)

async def _handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Returns to the main greeting menu.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    keyboard = [
        [InlineKeyboardButton("\U0001f3ac Autonomous Storytelling", callback_data="autonomous_story")],
        [InlineKeyboardButton("\U0001f4b0 Money Printer (Compilations)", callback_data="money_printer_help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg = "🤖 AMTCE Bot Active.\n\nSend me a URL or a Local File Path to start a new job."
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    else:
        await safe_reply(update, msg, reply_markup=reply_markup)


async def approve_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Step 1 of Approval: Ask for Title Expansion.
    """
    user_id = update.effective_user.id
    logger.info(f"📩 [APPROVE] approve_upload called by User {user_id}")

    with get_session_lock(user_id):
        session = user_sessions.get(user_id, {})
        logger.info(f"📊 [APPROVE] Session State: {session.get('state')}")
        
        # Check if video exists before allowing approval
        video_path = session.get("final_path") or session.get("pending_video")
        if not video_path or not os.path.exists(str(video_path)):
            await safe_reply(update, "❌ **Error:** No video found in your current session to approve. Please upload a new video.")
            return

        if session.get("state") == "WAITING_FOR_TITLE_EXPANSION":
            await safe_reply(update, "⏳ **Almost there!** You've already approved the video.\n\n👉 Please **reply with a number** from the list above to expand the title, or send **/skip** to upload it as-is.", force=True)
            return

        if session.get("state") != "WAITING_FOR_APPROVAL":
            await safe_reply(update, "⚠️ No video waiting for approval.")
            return

    # Load Presets
    presets_msg = ""
    try:
        presets = get_presets()

        if presets:
            msg_lines = ["📌 Select title expansion (optional):"]
            for k, v in presets.items():
                msg_lines.append(f"{k}️⃣ {v['label']}")
            msg_lines.append(
                "\nReply format:\n"
                "  <number>              → e.g.  2\n"
                "  <number> <link>       → e.g.  2 https://amzn.to/xyz\n"
                "  (affiliate link is optional)\n"
                "/skip  (or /skip https://amzn.to/xyz)"
            )
            presets_msg = "\n".join(msg_lines)
    except Exception:
        pass

    if presets_msg:
        with acquire_session_lock(user_id):
            user_sessions[user_id]["state"] = "WAITING_FOR_TITLE_EXPANSION"
            save_session(user_id)
        # FORCE REPLY to ensure user sees the menu even if they spammed buttons
        await safe_reply(update, presets_msg, force=True)
    else:
        # No presets, direct upload
        await _perform_upload(update, context)


async def _perform_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Imports provided via Health_handlers portal
    from Health_handlers import get_portal

    portal = get_portal_cached()
    monetization_brain = getattr(portal, "monetization_brain", None)
    uploader = getattr(portal, "uploader", None)
    meta_uploader = getattr(portal, "meta_uploader", None)
    community_promoter = getattr(portal, "community_promoter", None)

    user_id = update.effective_user.id
    logger.info(f"📤 [_perform_upload] Starting for User {user_id}")

    with get_session_lock(user_id):
        session = user_sessions.get(user_id, {})
        final_path = session.get("final_path")
        clean_source_path = session.get(
            "clean_source_path"
        )  # Pre-overlay clean path for Instagram DM mode
        title = session.get("title")
        # existing hashtags from session (if any)
        hashtags = session.get("hashtags")
        # [FIX] Load thumb_path from session for Meta upload
        thumb_path = session.get("thumb_path")

        # [INFLUENCER ENGINE] GPU-conditional IG portrait override
        # When the user has run the Influencer Colab pipeline (Cells 1-7) and
        # downloaded stage3_synced.jpg to Influencer_Output/latest_portrait.jpg,
        # AND the local machine has >= 6 GB GPU, prefer that AI portrait as the
        # Instagram image post instead of the auto-generated sample_thumb.
        try:
            _inf_portrait = os.getenv(
                "INFLUENCER_PORTRAIT_PATH",
                os.path.join("Influencer_Output", "latest_portrait.jpg"),
            )
            if os.path.exists(_inf_portrait) and os.path.getsize(_inf_portrait) > 10_000:
                _use_portrait = False
                try:
                    import torch as _torch
                    _vram = _torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
                    _use_portrait = _vram >= 6.0
                except Exception:
                    _use_portrait = True  # No CUDA? Still prefer portrait if it exists
                if _use_portrait:
                    logger.info(
                        f"[INFLUENCER ENGINE] GPU capable (>= 6 GB VRAM). "
                        f"Using AI portrait as Instagram thumbnail: {_inf_portrait}"
                    )
                    thumb_path = _inf_portrait
                else:
                    logger.info(
                        "[INFLUENCER ENGINE] GPU VRAM < 6 GB. "
                        "Using standard session thumbnail."
                    )
        except Exception as _inf_err:
            logger.debug(f"[INFLUENCER ENGINE] Portrait override skipped: {_inf_err}")

        # Check Reuse Flag
        is_reused_content = session.get("is_reused", False)
        # ── User-supplied affiliate link (optional, pasted at title-selection) ──
        user_affiliate_link = session.get("user_affiliate_link")
        # ── Link type: 'exact' (1 space) or 'alternative' (2 spaces) ──
        _session_link_type = session.get("affiliate_link_type", "exact")
        # ── Real MRP from Amazon (optional, provided by user at title-selection) ──
        _real_mrp = session.get("real_mrp")

    logger.info(f"📁 [_perform_upload] Video Path: {final_path}")

    if not final_path or not os.path.exists(final_path):
        await safe_reply(update, "❌ Video file found missing during upload phase.")
        return

    # --- CHEETAH LOGIC V2: USE BRAIN HASHTAGS ---
    # Strategy: Brain now returns 'hashtags' directly. No separate call needed.
    mon_report = session.get("monetization_report", {})
    brain_hashtags = mon_report.get("hashtags")
    brain_title = mon_report.get("editorial_title")

    # Override title if brain provided a better one
    if brain_title and isinstance(brain_title, str) and len(brain_title) > 5:
        title = brain_title

    # 2. Resolution Strategy
    if brain_hashtags:
        if isinstance(brain_hashtags, list) and len(brain_hashtags) > 0:
            hashtags = " ".join(brain_hashtags)
        elif isinstance(brain_hashtags, str):
            hashtags = brain_hashtags
        await safe_reply(
            update, f"🏷️ Used AI Hashtags (Quota Saver)"
        )
    else:
        if not hashtags:
            hashtags = ""

    # ----- ADDITIONAL HASHTAGS: User Title, Product Name, Channel Name -----
    import re
    _h_list = set(hashtags.split()) if hashtags else set()
    
    # Add Default Shorts Hashtags
    default_shorts = os.getenv("DEFAULT_HASHTAGS_SHORTS", "#shorts #viral #trending")
    for tag in default_shorts.split():
        _h_list.add(tag)

    # 1. User title hashtag
    if title:
        _title_words = re.findall(r'[a-zA-Z0-9]+', title)
        for w in _title_words:
            if len(w) > 1:
                _h_list.add(f"#{w}")
            
    # 2. Product name (wear name) hashtag
    _item_cat = mon_report.get("item_name")
    if not _item_cat:
        _ov = mon_report.get("overlay_data")
        if _ov and isinstance(_ov, dict):
             _item_cat = _ov.get("item_name")
        elif _ov and isinstance(_ov, list) and len(_ov) > 0:
             _item_cat = _ov[0].get("item_name")
             
    if _item_cat:
        _item_clean = re.sub(r'[^a-zA-Z0-9]', '', str(_item_cat))
        if _item_clean:
            _h_list.add(f"#{_item_clean}")

    # 3. Channel name hashtag
    _channel_name = os.getenv("CHANNEL_NAME", os.getenv("BRAND_NAME", "ViralShorts"))
    _channel_clean = re.sub(r'[^a-zA-Z0-9]', '', _channel_name)
    if _channel_clean:
        _h_list.add(f"#{_channel_clean}")
        
    hashtags = " ".join(_h_list)
    # -----------------------------------------------------------------------

    # ── Resolve Affiliate Link for Socials (YouTube & Meta) ──
    _mb2 = getattr(portal, "monetization_brain", None)
    _mb2_brain = getattr(_mb2, "brain", None) if _mb2 else None
    social_affiliate_link = None
    social_cta_var = ""
    if user_affiliate_link:
        social_affiliate_link = user_affiliate_link
        # ── Badge based on 1-space (exact) vs 2-space (alternative) input protocol ──
        if _session_link_type == "alternative":
            _badge = "🔁 Alternative on Amazon"
        else:
            _badge = "🎯 Exact Wear on Amazon"

        # ── Price Hook: use real MRP if provided by user ───────────────────────
        # If real_mrp supplied: show MRP + AI-estimated offer price → compute discount %
        # If NOT supplied: skip price entirely (no fake numbers)
        _price_hook = ""
        if _real_mrp and _real_mrp > 0:
            # Use brain's price_tag_estimate as the estimated "current Amazon offer price"
            _overlay_list = mon_report.get("overlay_data") or [{}]
            _first_overlay = _overlay_list[0] if isinstance(_overlay_list, list) and _overlay_list else {}
            _price_tag_str = _first_overlay.get("price_tag", "") if isinstance(_first_overlay, dict) else ""
            # Extract integer from strings like "₹9,600"
            try:
                _offer_price = int(str(_price_tag_str).lstrip("₹").replace(",", "").strip())
            except (ValueError, AttributeError):
                _offer_price = 0

            if _offer_price > 0 and _offer_price < _real_mrp:
                _pct = int(((_real_mrp - _offer_price) / _real_mrp) * 100)
                _price_hook = f"MRP: ₹{_real_mrp:,}\n🔥 {_pct}% off — Check Amazon 👇"
            else:
                # Offer price unknown/higher — just anchor MRP, tease the deal
                _price_hook = f"MRP: ₹{_real_mrp:,}\n🔥 Big discount on Amazon 👇"
            logger.info(f"[PRICE_HOOK] real_mrp=₹{_real_mrp:,} → {_price_hook!r}")

        social_cta_var = f"{_badge}\n🔗 Buy here"
        if _price_hook:
            social_cta_var = f"{_price_hook}\n{_badge}\n🔗 Buy here"
        logger.info(f"[BRANDING] link_type='{_session_link_type}' mrp={_real_mrp} → CTA built")
    else:
        # [TARGETED] Pass item_name from mon_report to get category-specific link
        _yt_safe_link = _mb2_brain.get_monetization_link(target_platform="youtube", item_category=_item_cat) if _mb2_brain else None
        if _yt_safe_link:
            social_affiliate_link = _yt_safe_link
            import random
            partner_hooks = [
                "Shop this look for your girlfriend",
                "Treat your wife to this look",
                "Get this for your partner",
                "Surprise her with this fit",
                "Shop this style for your girl",
            ]
            social_cta_var = f"🛍️ {random.choice(partner_hooks)}"

    # 3. YouTube Upload (Conditional)
    try:
        send_to_youtube = os.getenv("SEND_TO_YOUTUBE", "on").lower() in [
            "on",
            "yes",
            "true",
        ]
        link = None
        yt_msg = ""  # Initialize here to avoid unbound error

        if send_to_youtube:
            # Extract caption for rich description
            mon_report = session.get("monetization_report", {})
            caption_text = mon_report.get("caption", "")
            rich_desc = mon_report.get("rich_description")

            # Construct Rich Description (Title + Brain Narrative ONLY)
            # Cheetah Logic V2: Use the 3-paragraph humorous/SEO desc if available.
            # CRITICAL SAFETY: Never use 'public_caption' here (contains Telegram CPA links)
            if rich_desc and len(rich_desc) > 50:
                description = f"{title}\n\n{rich_desc}\n\n"
            else:
                # Fallback V1 (Uses the 'Clean' caption_text, NOT the public_caption)
                description = f"{title}\n\n{caption_text}\n\n"

            # ── Affiliate link injection ──────
            if social_affiliate_link:
                description += f"{social_cta_var}: {social_affiliate_link}\n\n"
                logger.info(f"[MONETIZE] Affiliate link injected into YouTube description (single video)")
            # ──────────────────────────────────────────────────────────────

            # Hashtags are passed to uploader.upload_to_youtube directly, no need to append here to avoid duplication.

            await safe_reply(update, "📤 Uploading to YouTube...", force=True)
            logger.info(f"🚀 Calling uploader for: {final_path}")
            # ── ANALYTICS OPTIMIZER: use data-driven publish time when available ──
            try:
                from Intelligence_Modules.analytics_optimizer import optimizer as _ao

                # profile_data is not available in this scope (_perform_upload);
                # use a local dict to hold the scheduled publish time and pass it
                # to the uploader via context.bot_data or just log it.
                if not isinstance(locals().get("profile_data"), dict):
                    profile_data = {}

                _optimal = _ao.get_optimal_upload_time()
                if _optimal and isinstance(_optimal, dict):
                    _day = _optimal.get("day", "")
                    _hour = int(_optimal.get("hour", 18))
                    _iso = _ao.calculate_next_publish_time(_day, _hour)
                    if _iso:
                        profile_data["scheduled_publish_at"] = _iso
                        logger.info(
                            f"📅 [ANALYTICS_OPTIMIZER] Optimal publish: "
                            f"{_day} @ {_hour:02d}:00 → {_iso}"
                        )
            except Exception as _ao_err:
                logger.debug(f"[ANALYTICS_OPTIMIZER] skipped: {_ao_err}")
            # ── Pre-initialize mon_data so it's always defined even on failure ──
            mon_data = session.get("monetization_report", {})

            try:
                # ── NICHE ROUTER: resolve target channel from sidecar ──────────
                _upload_niche = _get_session_niche(final_path)
                logger.info(f"🎯 [NICHE ROUTER] Routing upload → niche='{_upload_niche}'")

                # HARDENING: Retry Network Call
                link = await with_retry(
                    uploader.upload_to_youtube,
                    final_path,
                    title=title,
                    hashtags=hashtags,
                    description=description,
                    niche=_upload_niche,
                )

                if link:
                    yt_msg = f"✅ YouTube: Success ({link})"

                    # Log with strict monetization data
                    mon_data = session.get("monetization_report", {})
                    log_video(
                        final_path,
                        link,
                        title,
                        ypp_risk=mon_data.get("risk", "unknown"),
                        style=mon_data.get(
                            "source", "unknown"
                        ),  # Log Source as Style for visibility
                        action="approved",
                        affiliate_link=user_affiliate_link or "none",
                        video_uid=session.get("video_uid", "none")
                    )  # User clicked approve

                    # [ADAPTIVE v3] Trust Feedback (Success)
                    if getattr(portal, "adaptive_intelligence", None):
                        # Using 'reward' for RL training (simple proxy: High Trust = Reward?)
                        # Actually we update Trust here. RL reward comes from Views later.
                        # For now, immediate upload success counts as small positive.
                        portal.adaptive_intelligence.brain.register_upload_outcome(
                            "success", risk_score=mon_data.get("risk_score", 0)
                        )

                        # Trigger minimal RL update for the selected trigger
                        active_trigger = mon_data.get("active_psychology")
                        if active_trigger:
                            portal.adaptive_intelligence.brain.update_cta_reward(
                                active_trigger, 0.1
                            )  # Small positive reinforcement for successful pipeline execution

                    # --- COMMUNITY PROMOTION ---
                    # Post a comment on this Video pointing to the last Compilation
                    # SMART ROUTE: Shorts = Text Only (No Link), Long/Comp = Clickable Link
                    if (
                        os.getenv("ENABLE_COMMUNITY_POST_COMPILATION", "yes").lower()
                        == "yes"
                    ):
                        is_short_video = "#shorts" in (hashtags or "").lower()
                        logger.info(
                            f"🚀 Triggering Cross-Promotion (Background Task). Video Type: {'Short' if is_short_video else 'Long/Compilation'}"
                        )

                        asyncio.create_task(
                            community_promoter.promoter.promote_on_short_async(
                                uploader.get_authenticated_service(_upload_niche),
                                link,
                                is_short=is_short_video,
                                custom_text=mon_data.get("monetization_cta"),
                                fashion_data=mon_data.get("fashion_scout"),
                            )
                        )


                    # Rating DM fires after Telegram group send (see below).


                    # ── SELF-OPTIMIZING EDITOR: record upload for future learning ──────
                    try:
                        from Core_Modules.self_optimizing_editor import (
                            SelfOptimizingEditor as _SOEClass,
                        )

                        # Extract YouTube video ID from the URL returned by the uploader
                        # YouTube links are either:
                        #   https://youtu.be/VIDEO_ID  OR
                        #   https://www.youtube.com/watch?v=VIDEO_ID  OR
                        #   https://www.youtube.com/shorts/VIDEO_ID
                        _yt_video_id = ""
                        if link and isinstance(link, str):
                            if "youtu.be/" in link:
                                _yt_video_id = (
                                    link.split("youtu.be/")[-1].split("?")[0].strip()
                                )
                            elif "watch?v=" in link:
                                _yt_video_id = (
                                    link.split("watch?v=")[-1].split("&")[0].strip()
                                )
                            elif "/shorts/" in link:
                                _yt_video_id = (
                                    link.split("/shorts/")[-1].split("?")[0].strip()
                                )
                            else:
                                # Fallback: last path segment
                                _yt_video_id = (
                                    link.rstrip("/")
                                    .split("/")[-1]
                                    .split("?")[0]
                                    .strip()
                                )

                        if _yt_video_id:
                            # [TUNED] Use the shared SOE singleton from orchestrator
                            # (real analytics, persistent memory) instead of a throwaway mock.
                            try:
                                from Compiler_Modules.orchestrator import _soe as _soe_shared
                                _soe_shared.record_upload(
                                    video_id=_yt_video_id,
                                    brain_result=mon_data if isinstance(mon_data, dict) else {},
                                    video_duration_s=float(
                                        mon_data.get("duration", 30.0)
                                        if isinstance(mon_data, dict) else 30.0
                                    ),
                                    niche=os.getenv("CHANNEL_NICHE", "fashion"),
                                )
                                logger.info(f"🧠 [SOE] Upload recorded: {_yt_video_id}")

                                # --- PERIODIC LEARNING PASS ---
                                # Trigger a full learning pass every 5 uploads so the
                                # editor memory grows and self-optimizes over time.
                                _soe_upload_count = getattr(
                                    _soe_shared, "_upload_count", 0
                                ) + 1
                                _soe_shared._upload_count = _soe_upload_count
                                if _soe_upload_count % 5 == 0:
                                    logger.info(
                                        f"🧬 [SOE] Running learning pass after "
                                        f"{_soe_upload_count} uploads..."
                                    )
                                    _learn_result = _soe_shared.run_learning_pass()
                                    logger.info(f"🧬 [SOE] Learning pass result: {_learn_result}")
                            except Exception as _soe_shared_e:
                                logger.debug(
                                    f"[SOE] Shared instance unavailable, falling back: {_soe_shared_e}"
                                )
                                # Fallback: standalone instance with real analytics
                                _soe_fallback = _SOEClass(mock_analytics=False)
                                _soe_fallback.record_upload(
                                    video_id=_yt_video_id,
                                    brain_result=mon_data if isinstance(mon_data, dict) else {},
                                    niche=os.getenv("CHANNEL_NICHE", "fashion"),
                                )
                        else:
                            logger.debug(
                                f"[SOE] Could not parse video_id from link: {link}"
                            )
                    except Exception as _soe_rec_err:
                        logger.debug(f"[SOE] record_upload skipped: {_soe_rec_err}")
                else:
                    yt_msg = "❌ YouTube: Failed"
                    # [ADAPTIVE v3] Trust Feedback (Error)
                    if getattr(portal, "adaptive_intelligence", None):
                        portal.adaptive_intelligence.brain.register_upload_outcome(
                            "error", risk_score=mon_data.get("risk_score", 0)
                        )

            except Exception as e:
                logger.error(f"YouTube Upload Failed: {e}")
                yt_msg = f"❌ YouTube Error: {e}"
                # [ADAPTIVE v3] Trust Feedback (Error)
                if getattr(portal, "adaptive_intelligence", None):
                    portal.adaptive_intelligence.brain.register_upload_outcome(
                        "error", risk_score=mon_data.get("risk_score", 0)
                    )
        else:
            logger.info("🚫 SEND_TO_YOUTUBE is OFF. Skipping YouTube upload.")
            await safe_reply(update, "⏭️ YouTube Upload Skipped (Configured OFF).")
            yt_msg = "⏩ YouTube: Skipped"

        # 2. Meta Upload (Runs INDEPENDENTLY of YouTube success/failure/skip)
        # Imports provided via Health_handlers portal
        meta_results = {}
        if os.getenv("ENABLE_META_UPLOAD", "no").lower() in ["yes", "true", "on"]:
            send_to_youtube = os.getenv("SEND_TO_YOUTUBE", "on").lower() in [
                "on",
                "yes",
                "true",
            ]
            await safe_reply(
                update, "📤 Attempting Meta (Instagram/Facebook) Uploads..."
            )
            mon_report = session.get("monetization_report", {})
            caption_text = mon_report.get("caption", "")

            # === INSTAGRAM UPLOAD MODE BRANCH ===
            # [CLEAN CLIP MODE] - SEND_TO_YOUTUBE=off
            # We use the 'final_path' because the orchestrator has already
            # produced the refined clip (color/speed/music) but skipped the text overlays.
            ig_upload_path = final_path

            if not send_to_youtube:
                # DM-BAIT MODE: Caption designed to attract reposting channels
                logger.info(
                    "📸 [IG DM-MODE] SEND_TO_YOUTUBE=off → Uploading refined no-overlay clip to Instagram"
                )
                tg_link = ""
                try:
                    with open("Credentials/telegram_config.json") as _tf:
                        tg_link = json.load(_tf).get("telegram_link", "")
                except:
                    pass
                clean_handle = (
                    tg_link.replace("https://t.me/", "@")
                    if tg_link
                    else "@yourtelegram"
                )
                _actress = (
                    title.split(":")[0].strip()
                    if ":" in title
                    else title.split()[0].strip()
                )

                # Law-bending DM-bait captions
                dm_captions = [
                    f"#{_actress} (link in bio) 🔥\n\nRefined edit. No watermark. No text.\nDM for the original high-quality file 📩\n\nRepost pages welcome — credit appreciated\n#bollywood #actress #fanpage #repost #cleanedits",
                    f"#{_actress} (link in bio) 🎬\n\nRaw refined clip at her best.\nDM if you run a repost page 📩\n\nTelegram (more daily): {clean_handle}\n#actress #fanedits #bollywood #viral #repost",
                    f"#{_actress} (link in bio) ✨\nNo overlay. No text. Just the clip.\n\nPageowners — DM for collab 📩\n(Full archive) → Telegram link in bio\n#bollywood #actress #cleanclip #fanpage #viral",
                ]
                meta_caption = random.choice(dm_captions)
                if hashtags:
                    meta_caption += f"\n\n{hashtags}"
                
                # ── Inject affiliate link into DM-mode Instagram caption ──
                if social_affiliate_link:
                    if "Telegram" in meta_caption:
                        meta_caption = meta_caption.replace("Telegram", f"{social_cta_var}: {social_affiliate_link}\n\nTelegram")
                    elif "#" in meta_caption:
                        _first_hash = meta_caption.find("\n#")
                        if _first_hash != -1:
                            meta_caption = meta_caption[:_first_hash] + f"\n\n{social_cta_var}: {social_affiliate_link}" + meta_caption[_first_hash:]
                        else:
                            meta_caption += f"\n\n{social_cta_var}: {social_affiliate_link}"
                    else:
                        meta_caption += f"\n\n{social_cta_var}: {social_affiliate_link}"
                    logger.info("[MONETIZE] Affiliate link injected into Instagram DM-mode caption")
            else:
                # NORMAL MODE: Use overlaid video + High-Conversion Caption from Telegram
                logger.info(
                    "📸 [IG NORMAL-MODE] SEND_TO_YOUTUBE=on → Uploading fully overlaid clip"
                )

                # ── Use the pre-built high-conversion ig_caption_base directly ──
                # The caption is fully assembled (link → title → hook → price →
                # vis_desc → Telegram join → hashtags) — just strip Telegram markdown.
                _ig_base = mon_report.get("ig_caption_base") or mon_report.get("public_caption") or caption_text or ""
                meta_caption = _ig_base.replace("*", "")

                # ── Inject affiliate link only if it somehow missed the master string ──
                if social_affiliate_link and social_affiliate_link not in meta_caption:
                    if "Join our Telegram" in meta_caption:
                        meta_caption = meta_caption.replace("Join our Telegram", f"{social_cta_var}: {social_affiliate_link}\n\nJoin our Telegram")
                    elif "#" in meta_caption:
                        _first_hash = meta_caption.find("\n#")
                        if _first_hash != -1:
                            meta_caption = meta_caption[:_first_hash] + f"\n\n{social_cta_var}: {social_affiliate_link}" + meta_caption[_first_hash:]
                        else:
                            meta_caption += f"\n\n{social_cta_var}: {social_affiliate_link}"
                    else:
                        meta_caption += f"\n\n{social_cta_var}: {social_affiliate_link}"
                    logger.info("[MONETIZE] Affiliate link fallback injected into Instagram caption")
            # Resolve niche from sidecar (same source used by the YouTube router above)
            _meta_niche = _get_session_niche(final_path)
            logger.info(f"🎯 [META NICHE ROUTER] Routing Meta upload → niche='{_meta_niche}'")

            meta_results = await meta_uploader.AsyncMetaUploader.upload_to_meta(
                ig_upload_path,
                meta_caption,
                upload_type=os.getenv("META_UPLOAD_TYPE", "Reels"),
                skip_facebook=True,  # 🛑 RESTRICT FB TO COMPILATIONS ONLY
                thumbnail_path=thumb_path,
                niche=_meta_niche,
            )

        # 3. Final Report
        report_lines = ["🚀 Upload Summary:", ""]
        report_lines.append(yt_msg)

        if meta_results:
            # Instagram
            ig_res = meta_results.get("instagram", {"status": "skipped"})
            if isinstance(ig_res, str):
                ig_res = {"status": ig_res}
            ig_status = ig_res.get("status", "skipped")
            ig_link = ig_res.get("link", "")
            ig_id = ig_res.get("id", "")
            
            # --- COMMENT-TO-DM FOUNDATION ---
            if ig_status == "success" and ig_id:
                # Find the best link that was pushed (user overrides, or auto from fashion scout)
                _active_link = user_affiliate_link or mon_report.get("fashion_scout", {}).get("affiliate_link", "") or social_affiliate_link
                
                # FINAL SAFETY FALLBACK: If no specific link found, use the global fallback
                if not _active_link:
                    try:
                        with open("Monetization_Metrics/Amazon_affliate_link.json", "r", encoding="utf-8") as f:
                            _active_link = json.load(f).get("general_fallback_link")
                    except:
                        pass

                if _active_link:
                    import time
                    try:
                        aff_link_path = "Monetization_Metrics/Amazon_affliate_link.json"
                        aff_data = {}
                        if os.path.exists(aff_link_path):
                            with open(aff_link_path, "r", encoding="utf-8") as f:
                                try: aff_data = json.load(f)
                                except json.JSONDecodeError: pass
                        
                        if "by_ig_id" not in aff_data:
                            aff_data["by_ig_id"] = {}
                            
                        aff_data["by_ig_id"][str(ig_id)] = {
                            "link": _active_link,
                            "timestamp": int(time.time())
                        }
                        
                        os.makedirs("Monetization_Metrics", exist_ok=True)
                        with open(aff_link_path, "w", encoding="utf-8") as f:
                            json.dump(aff_data, f, indent=4)
                        logger.info(f"💾 [DB_MAP] Saved Instagram Media ID {ig_id} to main database -> {_active_link}")
                    except Exception as e:
                        logger.error(f"❌ [DB_MAP] Failed to update Amazon_affliate_link.json: {e}")
            icon_ig = (
                "✅"
                if ig_status == "success"
                else "❌"
                if "failed" in ig_status
                else "⏩"
            )
            line_ig = f"{icon_ig} Instagram: {ig_status}"
            if ig_link:
                line_ig += f" ({ig_link})"
            report_lines.append(line_ig)

            # Facebook
            fb_res = meta_results.get("facebook", {"status": "skipped"})
            if isinstance(fb_res, str):
                fb_res = {"status": fb_res}
            fb_status = fb_res.get("status", "skipped")
            fb_link = fb_res.get("link", "")
            icon_fb = (
                "✅"
                if fb_status == "success"
                else "❌"
                if "failed" in fb_status
                else "⏩"
            )
            line_fb = f"{icon_fb} Facebook: {fb_status}"
            if fb_link:
                line_fb += f" ({fb_link})"
            report_lines.append(line_fb)

        await safe_reply(update, "\n".join(report_lines))

        # 4. Telegram Group Broadcast — fires after Instagram upload + 5s cooldown
        tg_group_id = os.getenv("TELEGRAM_GROUP_ID", "").strip()
        if tg_group_id:
            try:
                logger.info(
                    "⏳ Waiting 5s before Telegram group broadcast (post-Instagram cooldown)..."
                )
                await asyncio.sleep(5)
                await safe_reply(
                    update, "📤 Sending clip to Telegram group...", force=True
                )
                mon_report_tg = session.get("monetization_report", {})

                # ── [TELEGRAM EXCLUSIVE] Send the processed clip WITHOUT the first-shot intro ──
                # The intro is a YouTube/IG hook only. Telegram members get the raw
                # unfiltered breakdown — exactly what we promise in the YouTube community post.
                _wm_ctx_tg = session.get("wm_context", {})
                _proc_only_path = _wm_ctx_tg.get("processed_only_path", "")
                if _proc_only_path and os.path.exists(_proc_only_path):
                    _tg_video_path = _proc_only_path
                    logger.info(f"🎬 [TG_BROADCAST] Sending processed-only clip (no intro): {_tg_video_path}")
                else:
                    _tg_video_path = final_path
                    logger.info(f"🎬 [TG_BROADCAST] processed_only_path not found — falling back to final_path: {_tg_video_path}")

                # ── Build Telegram caption ────────────────────────────────────────────
                # Keep the Gemini-generated hook as-is (it already has the right tone
                # and curiosity-gap structure). We just do a lightweight word-swap so
                # it clearly says "Telegram Group" or "Telegram Channel" — not a generic
                # handle or "inner circle" without context.
                # NO extra footer lines are appended — the hook IS the CTA.

                # Keep hooks and links exactly as assembled (public_caption has CTA + mon_link)
                public_cap_tg = (
                    mon_report_tg.get("public_caption")
                    or f"{title}\n\n🔥 Exclusive for VIP Members"
                )
                public_cap_tg = public_cap_tg.replace("*", "")

                # ── Smart "Telegram Group/Channel" word-swap ──────────────────────────
                # Rule: upgrade vague phrases to clearly say "Telegram Group" so viewers
                # know exactly WHERE to go — without changing the hook's tone or structure.
                import re as _re_tg
                def _ensure_tg_group_mention(cap: str) -> str:
                    # 1. "join the inner circle here" / "join here" → "join our Telegram Group here"
                    cap = _re_tg.sub(
                        r"join the inner circle here",
                        "join our Telegram Group here",
                        cap, flags=_re_tg.IGNORECASE
                    )
                    cap = _re_tg.sub(
                        r"join the inner circle",
                        "join our Telegram Group",
                        cap, flags=_re_tg.IGNORECASE
                    )
                    # 2. "join here" (standalone, not already preceded by Telegram/channel/group)
                    cap = _re_tg.sub(
                        r"(?<!telegram\s)(?<!channel\s)(?<!group\s)join here",
                        "join our Telegram Group here",
                        cap, flags=_re_tg.IGNORECASE
                    )
                    # 3. Bare "Telegram" not followed by Group/Channel → "Telegram Group"
                    cap = _re_tg.sub(
                        r"\bTelegram\b(?!\s+(Group|Channel|group|channel))",
                        "Telegram Group",
                        cap
                    )
                    # 4. "Join our Telegram for more" → "Join our Telegram Group for more"
                    cap = cap.replace("Join our Telegram for more", "Join our Telegram Group for more")
                    return cap

                public_cap_tg = _ensure_tg_group_mention(public_cap_tg)

                # ── Inject user affiliate link into Telegram group post ──────────────
                # public_caption is built in handle_message BEFORE the user supplies
                # the link, so we must append it here at broadcast time.
                if user_affiliate_link:
                    # ── Badge based on 1-space (exact) vs 2-space (alternative) input protocol ──
                    _tg_shop_label = "🔁 Alternative on Amazon\n🔗 Buy here" if _session_link_type == "alternative" else "🎯 Exact Wear on Amazon\n🔗 Buy here"
                    public_cap_tg += f"\n\n{_tg_shop_label}: {user_affiliate_link}"
                    logger.info(f"[MONETIZE] User affiliate link injected into Telegram group post [{_tg_shop_label}]")
                
                # Append Los Pollos link at the very bottom
                _saved_lp_link = mon_report_tg.get("lp_link")
                if _saved_lp_link:
                    public_cap_tg += f"\n\n💋🔞 The Secret → {_saved_lp_link}"
                # Resolve chat_id: numeric string → int, @username → keep as string
                # [ROBUST] If string and missing '@' or '-', prepend '@' (assumed username)
                _tg_chat = tg_group_id
                if tg_group_id.lstrip("-").isdigit():
                    _tg_chat = int(tg_group_id)
                elif not tg_group_id.startswith(("@", "-")):
                    _tg_chat = f"@{tg_group_id}"
                    logger.info(f"💡 Prepending '@' to Telegram ID: {tg_group_id} -> {_tg_chat}")
                # ── Dual CTA Buttons ────────────────────────────────────────────
                _tg_partner_label = os.getenv("TG_BTN_PARTNER_LABEL", "🔥 Find Your Match")
                _tg_corn_label    = os.getenv("TG_BTN_CORN_LABEL", "🎬 Watch Full Clips")
                # ── Los Pollos link: rotate from los_pollos_links.json (NOT hardcoded env var) ──
                _los_pollos_link = ""
                _lp_tele_enabled = os.getenv("LOS_POLLOS_TELEGRAM", "yes").lower() in ["yes", "true", "on"]
                if _lp_tele_enabled:
                    try:
                        _lp_json_paths = [
                            "The_json/los_pollos_links.json",
                            "Monetization_Metrics/los_pollos_links.json",
                        ]
                        for _lp_path in _lp_json_paths:
                            if os.path.exists(_lp_path):
                                with open(_lp_path, "r", encoding="utf-8") as _lp_f:
                                    _lp_data = json.load(_lp_f)
                                if isinstance(_lp_data, list) and _lp_data:
                                    _los_pollos_link = random.choice(_lp_data)
                                    logger.info("[LP_BTN] Loaded Los Pollos link from %s", _lp_path)
                                break
                    except Exception as _lp_e:
                        logger.warning("[LP_BTN] Failed to load Los Pollos link: %s", _lp_e)
                _tg_invite_link   = os.getenv("TELEGRAM_GROUP_INVITE_LINK", "")
                if not _tg_invite_link:
                    try:
                        _inv = await context.bot.create_chat_invite_link(chat_id=_tg_chat, member_limit=0)
                        _tg_invite_link = _inv.invite_link
                    except Exception:
                        pass
                from telegram import InlineKeyboardButton as _IKB_m, InlineKeyboardMarkup as _IKM_m
                _tg_btns = []
                if _los_pollos_link:
                    _tg_btns.append(_IKB_m(_tg_partner_label, url=_los_pollos_link))
                    logger.info("[LP_BTN] Partner button added: %s", _los_pollos_link[:60])
                else:
                    logger.warning("[LP_BTN] No Los Pollos link available — partner button skipped.")
                if _tg_invite_link:
                    _tg_btns.append(_IKB_m(_tg_corn_label, url=_tg_invite_link))
                _tg_markup = _IKM_m([_tg_btns]) if _tg_btns else None

                async with UPLOAD_SEMAPHORE:
                    with ProgressFile(_tg_video_path, logger.info) as vf:
                        await locals().get("context").bot.send_video(
                            chat_id=_tg_chat,
                            video=vf,
                            caption=public_cap_tg[:1024],
                            reply_markup=_tg_markup,
                            read_timeout=600,
                            write_timeout=600,
                            connect_timeout=60,
                        )
                logger.info(f"✅ Clip sent to Telegram group: {tg_group_id}")
                await safe_reply(update, "✅ Telegram Group: Sent!", force=True)

                # ── OUTFIT SWAP RATING PROMPT (fires RIGHT AFTER TG group send) ──────
                # Admin gets a DM with the thumbnail + ⭐×5 buttons.
                # 5 stars → Higgsfield takes: ai_influencer face + thumbnail outfit → swap.
                try:
                    mon_data_tg = session.get("monetization_report", {})
                    _scout_tg   = mon_data_tg.get("fashion_scout") if isinstance(mon_data_tg, dict) else None
                    _wear_tg    = (_scout_tg or {}).get("wear_name", "") or title or os.path.splitext(os.path.basename(final_path))[0] or "Outfit"
                    _niche_tg   = (_scout_tg or {}).get("attributes", {}).get("classification", {}).get("primary_category", "fashion")
                    _thumb_tg   = session.get("thumb_path") or ""

                    if _wear_tg and ADMIN_IDS and _thumb_tg and os.path.exists(_thumb_tg):
                        from Higgsfield_Modules.outfit_swap_engine import register_frame_for_rating
                        _vid_stem_tg = os.path.splitext(os.path.basename(final_path))[0]
                        _entry_tg    = register_frame_for_rating(
                            frame_path         = _thumb_tg,          # ← actual JPEG thumbnail, not the .mp4
                            wear_name          = _wear_tg,
                            niche              = _niche_tg,
                            video_id           = _vid_stem_tg,
                            outfit_description = (_scout_tg or {}).get("outfit_description", ""),
                        )
                        for _adm in list(ADMIN_IDS)[:1]:
                            asyncio.create_task(
                                send_outfit_rating_prompt(
                                    bot        = context.bot,
                                    admin_id   = _adm,
                                    entry_id   = _entry_tg,
                                    wear_name  = _wear_tg,
                                    frame_path = _thumb_tg,
                                )
                            )
                        logger.info(f"[OUTFIT_SWAP] ⭐ Rating DM sent to admin after TG group post. Outfit: {_wear_tg}")
                    else:
                        logger.debug(f"[OUTFIT_SWAP] Rating DM skipped — thumb missing or no admin: thumb={_thumb_tg}")
                except Exception as _r_err:
                    logger.debug(f"[OUTFIT_SWAP] Rating DM non-fatal error: {_r_err}")

            except Exception as _tg_e:
                logger.error(f"❌ Telegram group send failed: {_tg_e}")
                await safe_reply(update, f"⚠️ Telegram Group Send Failed: {_tg_e}")
        else:
            logger.info("⏭️ TELEGRAM_GROUP_ID not set — skipping group broadcast.")


        # Check for compilation trigger
        if link:  # Only trigger compile if at least youtube worked? Or always?
            # Logic: Compilation usually builds from "Processed Shorts".
            # If upload failed, the file is still in Processed Shorts?
            # Yes. So we can trigger it.
            await maybe_compile_and_upload(update)

    except Exception as e:
        logger.error(f"Upload error: {e}")
        await safe_reply(update, f"❌ Upload error: {e}")

    # Clear session
    with get_session_lock(user_id):
        user_sessions.pop(user_id, None)
        try:
            os.remove(os.path.join(JOB_DIR, f"session_{user_id}.json"))
        except:
            pass

    # --- CASH-MAXIMIZER MEMORY FLUSH ---
    if CASH_MAX_MODE:
        logger.info("🧹 [MEMORY FLUSH] Real-Time loop finishing. Cleaning RAM.")
        gc.collect()
        # Also try to clear asyncio tasks if too many are lingering
        current_tasks = len(asyncio.all_tasks())
        if current_tasks > 50:
            logger.warning(
                f"⚠️ High task count detected: {current_tasks}. Clearing RAM aggressively."
            )
            gc.collect()


async def verify_watermark(update: Update, context: ContextTypes.DEFAULT_TYPE, is_positive: bool = None):
    """
    Handles watermark verification callback (inline buttons) and text commands.
    Routes wm_clean / wm_bad / approve_post / reject_discard callbacks.
    """
    query = update.callback_query

    # Check if this update is too old (> 2 hours) to avoid restart bursts
    # [mkpv-fix] Increased from 60s to 7200s because video review takes longer.
    if query and query.message and query.message.date:
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - query.message.date.replace(tzinfo=timezone.utc)).total_seconds()
        if age > 7200:
            logger.warning(f"⏩ Ignoring old callback query (age: {age:.1f}s)")
            try:
                await query.answer("⌛ This request has expired. Please try again.")
            except:
                pass
            return

    # Check if called via button (query) or command (arg)
    if query:
        try:
            await query.answer()
        except Exception as e:
            if "query is too old" in str(e).lower():
                logger.info("⏳ callback_query expired, ignoring.")
            else:
                logger.warning(f"⚠️ query.answer() failed: {e}")
        user_id = query.from_user.id
        # Use query data mapping if arg is None
        if is_positive is None:
            if query.data == "wm_clean":
                is_positive = True
            elif query.data == "wm_bad":
                is_positive = False
            elif query.data == "approve_post":
                # [STIE] Record transition approval for every gap stored in session
                try:
                    _s = user_sessions.get(user_id, {})
                    _mon = _s.get("monetization_report", {})
                    _t_decisions = _mon.get("transition_decisions", [])
                    if _t_decisions:
                        from Compiler_Modules.smart_transition_engine import engine as _stie_cb
                        for _td in _t_decisions:
                            _stie_cb.record_outcome(
                                context_key=_td.get("context_key", ""),
                                t_type=_td.get("type", "clean"),
                                duration_s=float(_td.get("duration_s", 0.2)),
                                approved=True,
                            )
                        logger.info(f"[STIE_WIRE] Approved {len(_t_decisions)} transition decisions")
                except Exception as _stie_e:
                    logger.debug(f"[STIE_WIRE] approve wiring skipped: {_stie_e}")
                await approve_upload(update, context)
                return
            elif query.data == "reject_discard":
                # [STIE] Record transition rejection
                try:
                    _s2 = user_sessions.get(user_id, {})
                    _t_decisions2 = _s2.get("monetization_report", {}).get("transition_decisions", [])
                    if _t_decisions2:
                        from Compiler_Modules.smart_transition_engine import engine as _stie_rej
                        for _td2 in _t_decisions2:
                            _stie_rej.record_outcome(
                                context_key=_td2.get("context_key", ""),
                                t_type=_td2.get("type", "clean"),
                                duration_s=float(_td2.get("duration_s", 0.2)),
                                approved=False,
                            )
                        logger.info(f"[STIE_WIRE] Rejected {len(_t_decisions2)} transition decisions")
                except Exception as _rej_e:
                    logger.debug(f"[STIE_WIRE] reject wiring skipped: {_rej_e}")
                await reject_upload(update, context)
                return
            elif query.data == "influencer_refine":
                # [INFLUENCER] Face Swap & Post button — two-level guard + swap + upload
                await _handle_influencer_refine(update, context, user_id, query)
                return
            elif query.data == "autonomous_story":
                await _handle_autonomous_story(update, context)
                return
            elif query.data == "money_printer_help":
                await _handle_money_printer_help(update, context)
                return
            elif query.data == "main_menu":
                await _handle_main_menu(update, context)
                return
            elif query.data.startswith("story_start_"):
                niche = query.data.replace("story_start_", "").replace("_", " ").title()
                await query.answer(f"Starting {niche} production...")
                await safe_reply(update, f"\U0001f680 **Production Started: {niche}**\n\nThe Movie Director is now analyzing your past processed clips and fetching stock media for the documentary.\n\n_This autonomous job is running in the background. You will receive a notification when the 16:9 narrative and shorts are ready._")
                # Future: dispatch_director_job(niche)
                return
    else:
        # Called via text command
        user_id = update.effective_user.id

    if is_positive is None:
        # Should not happen if logic is correct, but safety
        return

    # Imports provided via Health_handlers portal

    # Helper for robust editing (Text vs Caption)
    async def smart_edit(text):
        if not query:
            await safe_reply(update, text)
            return

        try:
            if query.message.text:
                await query.edit_message_text(text)
            elif query.message.caption is not None:  # It's a media message
                await query.edit_message_caption(caption=text)
            else:
                # Fallback for weird cases (stickers? types without caption?)
                await safe_reply(update, text)
        except Exception as e:
            logger.warning(f"⚠️ Smart Edit Failed: {e}")
            await safe_reply(update, text)

    with get_session_lock(user_id):
        session = user_sessions.get(user_id, {})
        # Fallback Logic: handle_message sets 'final_path', retry sets 'pending_video'
        video_path = session.get("pending_video") or session.get("final_path")
        if not video_path:
            msg = "❌ Session expired (Video path lost). Please upload again."
            await smart_edit(msg)
            return
        title = session.get("title", "video")
        # Retry Tracker
        retry_count = session.get("retry_count", 0)

        if is_positive:
            # Positive Feedback
            # hybrid_watermark.hybrid_detector.confirm_learning(
            #     session.get("wm_context", {}), is_positive=True
            # )

            msg = f"✅ Watermark Verification Successful! Proceeding to next step..."
            await smart_edit(msg)

            # PROCEED TO APPROVAL FLOW
            try:
                # ensuring state is correct for approve_upload check
                session["state"] = "WAITING_FOR_APPROVAL"
                save_session(user_id)

                await approve_upload(update, context)
            except Exception as e:
                logger.error(f"❌ Error in Approval Flow trigger: {e}", exc_info=True)
                await safe_reply(
                    update,
                    "❌ Error proceeding to upload. Please try /approve manually.",
                    force=True,
                )
            return

        else:
            # Negative Feedback -> RETRY LOOP

            # 1. STRICT DELETION (Soft Reset)
            # We must delete the FAILED artifact to prevent pollution.
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
                    logger.info(f"🗑️ Strict Deletion (Rejected): {video_path}")

                # Try to delete associated JSON
                json_path = os.path.splitext(video_path)[0] + ".json"
                if os.path.exists(json_path):
                    os.remove(json_path)
                    logger.info(f"🗑️ Strict Deletion (Meta): {json_path}")
            except Exception as e:
                logger.warning(f"Deletion warning: {e}")

            # 2. Learning
            # # # hybrid_watermark.hybrid_detector.confirm_learning(
            #     session.get("wm_context", {}), is_positive=False
            # )

            # 3. Increment Level
            retry_count += 1
            session["retry_count"] = retry_count
            save_session(user_id)

            if retry_count > 2:
                # Max Retries Reached -> Give Up
                msg = "❌ Maximum retries reached. I'm sorry I couldn't clean it."
                await smart_edit(msg)
                user_sessions.pop(user_id, None)
                GlobalState.set_busy(False)
                return

            # 4. Trigger Retry
            # Level 1: Aggressive Static
            # Level 2: Better Accurate Patch (Static+6) OR Dynamic (if moving)

            mode_name = "Aggressive" if retry_count == 1 else "Deep Scan"
            status_msg = f"🔄 Retry {retry_count}/2: Activating {mode_name} Correction...\n(This might take longer)"
            await smart_edit(status_msg)

            # --- QUEUE HANDLING FOR RETRY ---
            global QUEUE_SIZE
            is_queued = False
            with QS_LOCK:
                if PROCESSING_LOCK.locked():
                    QUEUE_SIZE += 1
                    is_queued = True
                    pos = QUEUE_SIZE

            if is_queued:
                await safe_reply(
                    update,
                    f"⏳ System Busy. Your retry request is at position #{pos} in the queue...",
                )

            async with PROCESSING_LOCK:
                if is_queued:
                    with QS_LOCK:
                        QUEUE_SIZE = max(0, QUEUE_SIZE - 1)

                # 5. Re-run Compiler
            # We assume pending_video WAS the input or we still have access to original download?
            # Actually, main.py usually keeps 'pending_url' or original download until finished.
            # But compiler overwrites? No, it makes a NEW file.
            # We need the path to the SOURCE video (downloaded raw).
            # Session usually has 'video_path' populated from download, and 'pending_video' populated from compile?
            # Let's use 'video_path' (downloaded) if available, else 'pending_video' (compiled) would be circular if deleted.

            # Wait, `handle_message` download stores path in `video_path` variable, but in SESSION?
            # We need to ensure we have the source.
            # Let's optimistically assume `session['source_path']` exists (I will add it in handle_message next step).
            # Fallback: If not, we might fail.

            source_path = session.get("source_path")

            # If source path is missing, we try to guess from session state or fail
            if not source_path:
                await smart_edit("❌ Error: Original source lost. Cannot retry.")
                return

            try:
                import uuid as _uuid
                from claw_vanguard.vanguard_director import VanguardDirector as _VD_retry

                _retry_dir = os.path.dirname(os.path.abspath(source_path)) or "Processed Shorts"
                _retry_out_path = os.path.join(
                    _retry_dir, f"retry_{retry_count}_{_uuid.uuid4().hex[:6]}.mp4"
                )

                # [CLAW] Retry also goes through the 4-turn Vanguard loop
                _retry_director = _VD_retry()
                _retry_result = await asyncio.to_thread(
                    _retry_director.execute_mission,
                    niche=os.getenv("DEFAULT_NICHE", "viral"),
                    video_request=(
                        f"Retry #{retry_count}: Reprocess '{title}' — fix watermark and quality issues."
                    ),
                    input_paths=[str(source_path)],
                    output_path=_retry_out_path,
                )
                retry_out = _retry_result.output if _retry_result.success else None
                ctx = {}
                if retry_out:
                    _r_sidecar = os.path.splitext(str(retry_out))[0] + ".json"
                    if os.path.exists(_r_sidecar):
                        try:
                            with open(_r_sidecar, "r") as _rsf:
                                _rsc = json.load(_rsf)
                                ctx = _rsc.get("pipeline_metrics", _rsc)
                        except Exception:
                            pass

                if retry_out:
                    # Update Session
                    session["pending_video"] = str(retry_out)
                    session["wm_context"] = ctx
                    save_session(user_id)

                    # Ask Again
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "✅ Perfect (Post It)", callback_data="wm_clean"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ Still Bad (Retry)", callback_data="wm_bad"
                            )
                        ],
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    with ProgressFile(retry_out, logger.info) as vf:
                        await locals().get("context").bot.send_video(
                            chat_id=user_id,
                            video=vf,
                            caption=f"📝 Retry {retry_count} Result ({mode_name}).\nIs the watermark gone?",
                            reply_markup=reply_markup,
                            read_timeout=600,
                            write_timeout=600,
                            connect_timeout=60,
                        )
                else:
                    await smart_edit("❌ Retry failed to produce output.")

            except Exception as e:
                logger.error(f"Retry Error: {e}")
                await smart_edit("❌ Error during retry.")


async def reject_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("❌ [REJECT] reject_upload triggered")

    user_id = update.effective_user.id
    query = update.callback_query
    # Query already answered in verify_watermark

    with get_session_lock(user_id):
        session = user_sessions.get(user_id, {})
        logger.debug(f"DEBUG: Session state: {session.get('state')}")

        if session.get("state") == "WAITING_FOR_APPROVAL":
            final_path = session.get("final_path")

            # User REJECTED: Permanent Delete
            if final_path and os.path.exists(final_path):
                try:
                    os.remove(final_path)
                    logger.info(f"🗑️ Deleted rejected file: {final_path}")
                except Exception as e:
                    logger.error(f"Failed to delete file: {e}")

                # Also delete sibling JSON if exists
                json_sibling = os.path.splitext(final_path)[0] + ".json"
                if os.path.exists(json_sibling):
                    try:
                        os.remove(json_sibling)
                    except Exception:
                        pass

                # ALSO DELETE SNAPPED THUMBS (assets/snapped_thumbs/)
                title = session.get("title")
                if title:
                    try:
                        # Matching compiler.py slug logic for base name
                        safe_base = re.sub(
                            r"[^a-zA-Z0-9_\-]", "", title.replace(" ", "_")
                        )
                        if not safe_base:
                            safe_base = "unnamed_series"

                        snap_dir = "assets/snapped_thumbs"
                        if os.path.exists(snap_dir):
                            # [FIX] Extract Index from Final Path to target SPECIFIC thumb
                            # Final path: .../Avneet_kaur_6.mp4 -> Index 6 -> Avneet_kaur_006.jpg
                            fname = os.path.basename(final_path)
                            name_no_ext = os.path.splitext(fname)[0]

                            # Regex to find trailing number
                            match = re.search(r"_(\d+)$", name_no_ext)
                            if match:
                                idx = int(match.group(1))
                                target_thumb = f"{safe_base}_{idx:03d}.jpg"
                                target_path = os.path.join(snap_dir, target_thumb)

                                if os.path.exists(target_path):
                                    os.remove(target_path)
                                    logger.info(
                                        f"🗑️ Deleted snapped preview: {target_thumb}"
                                    )
                                else:
                                    logger.debug(
                                        f"ℹ️ Thumb not found for rejection: {target_thumb}"
                                    )
                            else:
                                # Fallback: If no index, standard logic might be safer to SKIP than wildcard.
                                logger.debug(
                                    f"ℹ️ Could not extract index from {fname}, skipping thumb deletion to avoid wildcard error."
                                )

                    except Exception as se:
                        logger.warning(f"⚠️ Failed to clean snapped thumbs: {se}")

                # CLEANUP: Delete the sample_thumb copy too
                try:
                    # Logic must match compiler: sample_thumbs/{basename}_thumb.jpg
                    final_name = os.path.basename(
                        final_path
                    )  # e.g. text_2.mp4 or Malavika_1.mp4
                    base_name_no_ext = os.path.splitext(final_name)[0]  # e.g. text_2

                    # Check for both possible patterns (with and without _thumb)
                    possible_thumbs = [
                        f"{base_name_no_ext}_thumb.jpg",
                        f"{base_name_no_ext}.jpg",
                    ]

                    sample_dir = "sample_thumbs"
                    if os.path.exists(sample_dir):
                        for pt in possible_thumbs:
                            thumb_path = os.path.join(sample_dir, pt)
                            if os.path.exists(thumb_path):
                                os.remove(thumb_path)
                                logger.info(f"🗑️ Deleted sample thumb: {thumb_path}")
                except Exception as te:
                    logger.warning(f"⚠️ Failed to clean sample thumb: {te}")

                # AND DELETE THE SPECIFIC THUMBNAIL IN OUTPUT DIR
                thumb_sibling = os.path.splitext(final_path)[0] + "_thumb.jpg"
                if os.path.exists(thumb_sibling):
                    try:
                        os.remove(thumb_sibling)
                    except Exception:
                        pass

                # ── DELETE EXTRACTED AUDIO (Original_audio/) ─────────────────────
                # Use session-stored audio paths written from the sidecar JSON.
                # The old stem-match approach NEVER worked because the audio file
                # is named after the SOURCE (e.g. downloaded_abc.mp3), not the output.
                try:
                    _audio_paths_to_delete = []
                    _bgm_p  = session.get("bgm_audio_path", "")
                    _ext_p  = session.get("extracted_audio_path", "")
                    _all_p  = session.get("all_extracted_audio", [])

                    if _bgm_p:
                        _audio_paths_to_delete.append(_bgm_p)
                    if _ext_p and _ext_p != _bgm_p:
                        _audio_paths_to_delete.append(_ext_p)
                    for _ap in _all_p:
                        if _ap and _ap not in _audio_paths_to_delete:
                            _audio_paths_to_delete.append(_ap)

                    # Also search cooldown/ for the same filenames (pool moves files around)
                    _orig_audio_root = "Original_audio"
                    _audio_sub_dirs = [
                        os.path.join(_orig_audio_root, "active"),
                        os.path.join(_orig_audio_root, "cooldown"),
                        _orig_audio_root,
                    ]
                    for _apath in _audio_paths_to_delete:
                        _abase = os.path.basename(_apath)
                        _deleted = False
                        # Try exact path first
                        if os.path.isfile(_apath):
                            try:
                                os.remove(_apath)
                                logger.info(f"🗑️ Deleted extracted audio: {_apath}")
                                _deleted = True
                            except Exception as _ae:
                                logger.warning(f"⚠️ Could not delete audio {_apath}: {_ae}")
                        # Search all subdirs by basename as fallback
                        if not _deleted:
                            for _adir in _audio_sub_dirs:
                                if not os.path.isdir(_adir):
                                    continue
                                _candidate = os.path.join(_adir, _abase)
                                if os.path.isfile(_candidate):
                                    try:
                                        os.remove(_candidate)
                                        logger.info(f"🗑️ Deleted extracted audio (by name): {_candidate}")
                                    except Exception as _ae2:
                                        logger.warning(f"⚠️ Could not delete audio {_candidate}: {_ae2}")

                    if not _audio_paths_to_delete:
                        logger.debug("[AUDIO_CLEANUP] No audio paths in session — skipping pool cleanup")
                except Exception as _audio_err:
                    logger.warning(f"⚠️ Audio cleanup failed: {_audio_err}")


                # ── DELETE SOURCE DOWNLOAD (downloads/) ──────────────────────
                # The raw downloaded file is no longer needed once rejected.
                _source_path = session.get("source_path") or session.get("pending_local_path")
                if _source_path and os.path.exists(_source_path):
                    try:
                        os.remove(_source_path)
                        logger.info(f"🗑️ Deleted source file: {_source_path}")
                    except Exception as _se:
                        logger.warning(f"⚠️ Could not delete source: {_se}")

                # ── DELETE CLEAN INTERMEDIATE COPY ───────────────────────────
                _clean_path = session.get("clean_source_path")
                if _clean_path and _clean_path != final_path and os.path.exists(_clean_path):
                    try:
                        os.remove(_clean_path)
                        logger.info(f"🗑️ Deleted clean intermediate: {_clean_path}")
                    except Exception as _ce:
                        logger.warning(f"⚠️ Could not delete clean copy: {_ce}")

                await safe_reply(update, "🗑️ Video permanently deleted (output, audio & source cleaned).")
            else:
                await safe_reply(update, "🗑️ Video discarded (File missing).")

            logger.info("Clearing session after reject")
            user_sessions.pop(user_id, None)
            # Remove persistence file
            try:
                os.remove(os.path.join(JOB_DIR, f"session_{user_id}.json"))
            except Exception:
                pass
        else:
            logger.debug("Nothing to reject")
            await safe_reply(update, "⚠️ Nothing to reject.")


import signal
import sys
import uuid
import uuid
import uuid


def signal_handler(sig, frame):
    logger.info("🛑 KeyboardInterrupt received. Force Shutting down...")
    os._exit(0)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning(f"⚠️ Telegram Network/Timeout Error suppressed: {context.error}")
        return

    logger.exception(f"❌ Exception while handling an update:", exc_info=context.error)

    # Try to notify user if possible
    if isinstance(update, Update) and update.effective_message:
        try:
            await safe_reply(
                update, "⚠️ A temporary network error occurred. Please try again."
            )
        except:
            pass


async def cmd_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /learn — Triggers the Self-Optimizing Editor learning pass.
    Fetches analytics for all pending uploaded videos and updates the editor memory.
    Only accessible to admins.
    """
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return

    await safe_reply(
        update, "🧠 **Self-Optimizing Editor**\n\nStarting learning pass..."
    )

    try:
        from Core_Modules.self_optimizing_editor import SelfOptimizingEditor

        _soe = SelfOptimizingEditor(mock_analytics=True)
        status = _soe.status()
        await safe_reply(
            update,
            f"📊 Current Memory:\n"
            f"- Videos learned: {status['total_videos']}\n"
            f"- Patterns stored: {status['total_patterns']}\n"
            f"- Pending analytics: {status['pending_analytics']}\n"
            f"- Top arc: {status['top_arc']}\n"
            f"- Top persona: {status['top_persona']}\n"
            f"- Memory cold: {status['memory_cold']}",
        )

        result = await asyncio.to_thread(_soe.run_learning_pass, 20)
        await safe_reply(
            update,
            f"✅ **Learning Pass Complete**\n\n"
            f"- Processed: {result['processed']} videos\n"
            f"- Skipped: {result['skipped']}\n"
            f"- Patterns added: {result['patterns_added']}\n"
            f"- Total in memory: {result['total_in_memory']}",
        )
    except Exception as e:
        await safe_reply(update, f"❌ Learning pass failed: {e}")


async def cmd_stats_hooks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats_hooks — Shows the MoneyFlowEngine CTR leaderboard.
    Displays which hooks are converting vs. which are failing.
    Admin-only telemetry command.
    """
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return

    try:
        from Intelligence_Modules.money_flow_logic import engine as _mfe_stats
        report = _mfe_stats.get_performance_report()

        if not report:
            await safe_reply(
                update,
                "📊 **Hook CTR Tracker**\n\n"
                "No data yet. Hook impressions accumulate automatically as videos are processed.\n"
                "Click events are recorded when you tap ✅ Approve or ✅ Clean.",
                force=True
            )
            return

        # Sort by CTR descending
        sorted_hooks = sorted(report.items(), key=lambda x: x[1]['ctr'], reverse=True)

        lines = ["📊 **Hook CTR Leaderboard** (Approve/Clean clicks = real engagement)\n"]
        for rank, (hook_text, stats) in enumerate(sorted_hooks[:10], 1):
            ctr = stats['ctr']
            serves = stats['serves']
            clicks = stats['clicks']
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"{rank}."
            short_hook = hook_text[:55] + "..." if len(hook_text) > 55 else hook_text
            lines.append(f"{medal} CTR {ctr:.1f}% ({clicks}/{serves}) — {short_hook}")

        lines.append(f"\n💡 Cold hooks (CTR=0) are randomly picked until enough data accumulates.")
        lines.append(f"🔥 Top hook is promoted automatically on next run.")

        await safe_reply(update, "\n".join(lines), force=True)

    except Exception as e:
        await safe_reply(update, f"❌ Hook stats failed: {e}")


async def cmd_stats_transitions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats_transitions — Shows the SmartTransitionEngine RAG memory leaderboard.
    Displays which transition type+duration is winning per visual context bucket.
    Admin-only telemetry command.
    """
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return

    try:
        from Compiler_Modules.smart_transition_engine import engine as _stie_stats
        memory = _stie_stats.get_report()

        if not memory:
            await safe_reply(
                update,
                "📊 **Transition Memory**\n\n"
                "No data yet. Memory builds automatically as videos are processed.\n"
                "✅ Approve a video → transitions that worked get promoted.\n"
                "❌ Reject a video → transitions that failed get penalised.",
                force=True
            )
            return

        lines = ["🎬 **Transition RAG Leaderboard**\n"]
        total_samples = sum(e.get("samples", 0) for e in memory.values())
        rag_contexts  = len(memory)
        lines.append(f"Contexts learned: {rag_contexts} | Total samples: {total_samples}\n")

        # Sort by total samples (most-used context first)
        for ctx_key, entry in sorted(memory.items(), key=lambda x: x[1].get("samples", 0), reverse=True)[:8]:
            best   = entry.get("best_type", "?")
            dur_ms = int(entry.get("best_duration_s", 0.2) * 1000)
            rate   = entry.get("approval_rate", 0.0)
            n      = entry.get("samples", 0)
            # Shorten key for readability
            parts = ctx_key.split("|")
            short_key = f"{parts[0]}motion|{parts[1]}beat|{parts[3]}color"
            lines.append(
                f"🔹 `{short_key}`\n"
                f"   → {best} @ {dur_ms}ms | ✅ {rate:.0%} ({n} samples)"
            )

        lines.append(f"\n💡 RAG kicks in after {3} approvals per context.")
        lines.append("🔥 More approvals = smarter, personalized transitions.")

        await safe_reply(update, "\n".join(lines), force=True)

    except Exception as e:
        await safe_reply(update, f"❌ Transition stats failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# OUTFIT SWAP RATING SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

async def send_outfit_rating_prompt(
    bot,
    admin_id: int,
    entry_id: str,
    wear_name: str,
    frame_path: str,
):
    """
    Sends admin a star-rating prompt (1-5 stars) after a Telegram group post.
    Called by AMTCE pipeline after group video is successfully posted.
    """
    temp_thumb = None
    try:
        # Build inline keyboard: 5 star buttons
        buttons = [
            InlineKeyboardButton(f"{'⭐' * i}", callback_data=f"outfit_rate:{entry_id}:{i}")
            for i in range(1, 6)
        ]
        # 3 + 2 layout
        keyboard = InlineKeyboardMarkup([
            buttons[:3],
            buttons[3:],
        ])

        caption = (
            f"👗 **Outfit Rating Request**\n\n"
            f"📦 Product: `{wear_name}`\n\n"
            f"Rate the outfit quality for AI swap generation.\n"
            f"⭐⭐⭐⭐⭐ = Generate AI influencer outfit swap with this product image."
        )

        is_video = False
        if frame_path:
            lower_path = frame_path.lower()
            if lower_path.endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
                is_video = True

        send_path = frame_path

        if is_video and frame_path and os.path.exists(frame_path):
            try:
                import subprocess
                # Query video duration via ffprobe to find the exact midpoint and avoid black frames
                seek_time = "00:00:03"  # Default fallback past transitions
                try:
                    probe_cmd = [
                        "ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", frame_path
                    ]
                    probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=5, shell=(os.name == "nt"))
                    if probe_res.returncode == 0 and probe_res.stdout.strip():
                        duration = float(probe_res.stdout.strip())
                        if duration > 2.0:
                            seek_time = str(round(duration / 2, 2))
                except Exception as pe:
                    logger.debug(f"[OUTFIT_SWAP] ffprobe duration check failed: {pe}")

                temp_thumb = frame_path + "_temp_frame.jpg"
                cmd = [
                    "ffmpeg", "-y", "-ss", seek_time, "-i", frame_path,
                    "-vframes", "1", "-q:v", "2", temp_thumb
                ]
                subprocess.run(cmd, capture_output=True, timeout=10, shell=(os.name == "nt"))
                if os.path.exists(temp_thumb) and os.path.getsize(temp_thumb) > 0:
                    send_path = temp_thumb
                    logger.info(f"[OUTFIT_SWAP] Extracted video frame for Telegram rating prompt from midpoint ({seek_time}s): {temp_thumb}")
            except Exception as fe:
                logger.warning(f"[OUTFIT_SWAP] Failed to extract frame with FFmpeg: {fe}")

        # Send the frame image with rating buttons
        if send_path and os.path.exists(send_path):
            with open(send_path, "rb") as img:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=img,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                )
        else:
            # No image — send text only
            await bot.send_message(
                chat_id=admin_id,
                text=caption,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

        logger.info(f"[OUTFIT_SWAP] Rating prompt sent to admin {admin_id} for {entry_id}")

    except Exception as e:
        logger.warning(f"[OUTFIT_SWAP] Failed to send rating prompt: {e}")
    finally:
        if temp_thumb and os.path.exists(temp_thumb):
            try:
                os.remove(temp_thumb)
            except Exception:
                pass


async def handle_outfit_rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the star rating callback from the inline keyboard.
    Parses outfit_rate:<entry_id>:<stars> and records the rating.
    """
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    if not data.startswith("outfit_rate:"):
        return   # not our callback — let other handlers take it

    await query.answer()   # acknowledge immediately

    try:
        _, entry_id, stars_str = data.split(":", 2)
        stars = int(stars_str)
    except Exception:
        await query.edit_message_reply_markup()
        return

    try:
        from Higgsfield_Modules.outfit_swap_engine import record_rating, STAR_MAP
        entry = record_rating(entry_id, stars)
        wear_name = entry.get("wear_name", "Unknown")
        star_str  = STAR_MAP.get(stars, str(stars))

        if stars == 5:
            msg = (
                f"{star_str} **5-Star Rated!**\n\n"
                f"✅ `{wear_name}` has been added to the AI swap queue.\n"
                f"⚡ **Auto-Triggering Outfit Swap now...** Check credits & queue."
            )

            # Start background swap processor task!
            async def run_immediate_swap():
                try:
                    from Higgsfield_Modules.outfit_swap_engine import run_next_swap
                    result = await asyncio.to_thread(run_next_swap)
                    status = result.get("status")
                    if status == "swapped":
                        wear = result.get("wear_name", "outfit")
                        paths = result.get("output_paths", [])
                        bal = result.get("balance_after", "?")

                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=(
                                f"🎉 **AI Outfit Swap Generated Automatically!**\n\n"
                                f"👗 Outfit: `{wear}`\n"
                                f"🖼️ Swaps generated: `{len(paths)}`\n"
                                f"💳 Credits remaining: `{bal}`\n\n"
                                f"📁 Saved to: `Influencer_Output/higgsfield/outfit_swaps/`"
                            ),
                            parse_mode="Markdown"
                        )
                        # Send images back to the user
                        for p in paths:
                            if os.path.exists(str(p)):
                                with open(p, "rb") as img:
                                    await context.bot.send_photo(chat_id=query.message.chat_id, photo=img)
                    elif status == "waiting":
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=(
                                f"⏳ **Outfit Swap Queued**\n\n"
                                f"Credits: `{result.get('balance', 0.0)}/2.0`\n"
                                f"Will run automatically on next credit refresh cycle."
                            ),
                            parse_mode="Markdown"
                        )
                except Exception as ex:
                    logger.error(f"[OUTFIT_SWAP] Immediate swap failed: {ex}")

            asyncio.create_task(run_immediate_swap())
        else:
            msg = (
                f"{star_str} Rated `{stars}` stars\n\n"
                f"📁 `{wear_name}` stored in learning memory.\n"
                f"Only 5-star outfits are queued for AI swap generation."
            )

        await query.edit_message_caption(
            caption=msg,
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"[OUTFIT_SWAP] Callback error: {e}")
        await query.edit_message_reply_markup()


async def cmd_swap_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /swap_status — Shows outfit swap engine status.
    Admin-only: credit balance, pending queue, completed swaps.
    """
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return

    try:
        from Higgsfield_Modules.outfit_swap_engine import get_status_summary
        summary = get_status_summary()
        await safe_reply(update, summary, force=True)
    except Exception as e:
        await safe_reply(update, f"❌ Swap status error: {e}")


async def cmd_run_swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /run_swap — Manually triggers next outfit swap from the 5-star queue.
    Admin-only. Checks credits first, then fires Higgsfield product-photoshoot.
    """
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        return

    await safe_reply(update, "👗 Checking credits and firing outfit swap...")

    try:
        from Higgsfield_Modules.outfit_swap_engine import run_next_swap
        result = await asyncio.to_thread(run_next_swap)

        status = result.get("status")

        if status == "swapped":
            wear   = result.get("wear_name", "outfit")
            paths  = result.get("output_paths", [])
            bal    = result.get("balance_after", "?")

            await safe_reply(
                update,
                f"✅ **Swap Complete!**\n\n"
                f"👗 Outfit: `{wear}`\n"
                f"🖼️ Images generated: `{len(paths)}`\n"
                f"💳 Credits remaining: `{bal}`\n\n"
                f"📁 Saved to: `Influencer_Output/higgsfield/outfit_swaps/`",
                force=True,
            )

            # Send the swapped images back to admin
            for path in paths:
                if os.path.exists(str(path)):
                    try:
                        with open(path, "rb") as img:
                            await context.bot.send_photo(
                                chat_id=update.effective_chat.id,
                                photo=img,
                                caption=f"🤖 AI Swap: {wear}",
                            )
                    except Exception as img_err:
                        logger.warning(f"[OUTFIT_SWAP] Send image error: {img_err}")

        elif status == "waiting":
            await safe_reply(
                update,
                f"⏳ **Waiting for Credits**\n\n"
                f"💳 Balance: `{result.get('balance', 0)}`\n"
                f"🎯 Need: `2` credits for 3-image swap\n\n"
                f"Credits refresh monthly. Check again later.",
                force=True,
            )

        elif status == "empty":
            await safe_reply(
                update,
                "💤 **No items in queue.**\n\n"
                "Rate an outfit ⭐⭐⭐⭐⭐ to add it to the swap queue.",
                force=True,
            )

        else:
            await safe_reply(update, f"❌ Swap failed: {result.get('message', 'Unknown error')}")

    except Exception as e:
        logger.error(f"[OUTFIT_SWAP] cmd_run_swap error: {e}")
        await safe_reply(update, f"❌ Error: {e}")


# --- AUCTION ENGINE HANDLERS ---
from Uploader_Modules.telegram_auction_engine import AuctionState, PaymentVerifier
OCR_COOLDOWN = {}

async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        state = AuctionState()
        with state.safe_lock(timeout=10):
            if not state.state.get("active", False):
                await safe_reply(update, "🛑 The auction is not currently active. Opens at 7 PM IST.")
                return
        
        uid = str(update.effective_user.id)
        if uid not in state.state["bidders"]:
            state.state["bidders"][uid] = {
                "username": update.effective_user.username or "Anonymous",
                "total_paid": 0.0,
                "verified": False,
                "pending_bid": 549.0, # Base deposit
                "bid_timestamp": time.time()
            }
            state.save_state()
            msg = f"🔥 Welcome {state.state['bidders'][uid]['username']}! 🔥\nTo lock your entry, you must pay the ₹549 fully refundable deposit.\n\nSend a screenshot of your UPI payment here within 2 minutes."
            await safe_reply(update, msg)
        else:
            await safe_reply(update, "You are already joined. Use /bid <amount> to raise your bid.")
    except (TimeoutError, Exception) as e:
        logger.error(f"❌ Auction Join Error: {e}")
        await safe_reply(update, "⚠️ Auction system is busy. Please try again in a few seconds.")

async def cmd_bid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(context.args[0])
    except (IndexError, ValueError):
        await safe_reply(update, "Usage: /bid <amount>")
        return
        
    state = AuctionState()
    try:
        with state.safe_lock(timeout=10):
            res = state.register_bid(update.effective_user.id, update.effective_user.username, amount)
            # Need to move the rest of the logic inside the lock or handle results
            if res == "SUCCESS":
                await safe_reply(update, f"💸 Bid of ₹{amount} registered! Send your payment screenshot within 2 minutes to verify.")
            else:
                await safe_reply(update, f"❌ {res}")
    except (TimeoutError, Exception) as e:
        logger.error(f"❌ Auction Bid Error: {e}")
        await safe_reply(update, "⚠️ Auction system is busy. Please try again in a few seconds.")

async def _execute_face_swap_pipeline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    video_path: str,
    face_image_path: str,
    profile_data: dict,
    mon_report: dict,
) -> None:
    """
    Internal helper: runs eligibility-check-passed face swap + upload.
    Called from handle_face_swap_photo after the face image is received.
    """
    import asyncio as _asyncio

    await safe_reply(
        update,
        "🎭 Face swapping in progress... This may take 3-10 minutes depending on GPU.",
    )

    try:
        from Influencer_Modules.refinement_pipeline import run_face_refinement as _run_refinement
        # Temporarily override the env-based face image path with the user-supplied one
        _orig_env = os.environ.get("INFLUENCER_FACE_IMAGE", "")
        os.environ["INFLUENCER_FACE_IMAGE"] = face_image_path
        try:
            _result = await _asyncio.to_thread(_run_refinement, video_path, profile_data)
        finally:
            # Restore original env value regardless of outcome
            os.environ["INFLUENCER_FACE_IMAGE"] = _orig_env
    except Exception as _rfr_err:
        await safe_reply(update, f"Face swap error: {_rfr_err}")
        logger.error(f"[INFLUENCER_REFINE] Pipeline error: {_rfr_err}", exc_info=True)
        return

    if not _result.get("success"):
        await safe_reply(update, f"Face swap failed: {_result.get('message', 'unknown error')}")
        return

    _swapped  = _result["swapped_path"]
    _niche    = _result.get("niche", "face_refinement")
    _portrait = _result.get("portrait_path")
    logger.info(f"[INFLUENCER_REFINE] Swap done: {os.path.basename(_swapped)} niche={_niche}")
    await safe_reply(update, f"✅ Face swap complete! Uploading to influencer account ({_niche})...")

    # ── Upload to face_refinement IG account ──────────────────────────────────
    try:
        import Uploader_Modules.meta_uploader as _meta_mod
        _caption     = mon_report.get("final_caption") or mon_report.get("caption") or ""
        _upload_type = os.getenv("META_UPLOAD_TYPE", "Reels")
        _ig_result   = await _meta_mod.AsyncMetaUploader.upload_to_meta(
            _swapped,
            _caption,
            upload_type=_upload_type,
            skip_facebook=True,
            thumbnail_path=_portrait,
            niche=_niche,
        )
        _ig        = _ig_result.get("instagram", {}) if isinstance(_ig_result, dict) else {}
        _ig_status = _ig.get("status", "unknown") if isinstance(_ig, dict) else str(_ig)
        _ig_link   = _ig.get("link", "")           if isinstance(_ig, dict) else ""
        if _ig_status == "success":
            _msg = f"Face-swapped post is live!\n{_ig_link}" if _ig_link else "Face-swapped post is live!"
        else:
            _msg = (
                f"Upload status: {_ig_status}.\n"
                "Check face_refinement/meta_config.json credentials."
            )
        await safe_reply(update, _msg)
    except Exception as _up_err:
        await safe_reply(
            update,
            f"Upload error (swap file saved locally): {_up_err}\n"
            f"Swapped video: {_swapped}",
        )
        logger.error(f"[INFLUENCER_REFINE] Upload error: {_up_err}", exc_info=True)


async def handle_face_swap_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Photo handler for the WAITING_FOR_FACE_IMAGE state.
    Downloads the user's face photo, stores it, then kicks off the swap pipeline.
    Routes to handle_auction_screenshot for all other photo messages.
    """
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return

    with get_session_lock(user_id):
        _state = user_sessions.get(user_id, {}).get("state", "")

    # If NOT waiting for a face image, fall through to the auction screenshot handler
    if _state != "WAITING_FOR_FACE_IMAGE":
        await handle_auction_screenshot(update, context)
        return

    if not update.message or not update.message.photo:
        await safe_reply(update, "❌ No photo detected. Please send a face photo (JPG/PNG).")
        return

    # ── Download the face photo ───────────────────────────────────────────────
    os.makedirs("temp", exist_ok=True)
    _photo_file = await update.message.photo[-1].get_file()  # largest size
    _face_save_path = os.path.join(
        "temp", f"face_swap_input_{user_id}_{int(time.time())}.jpg"
    )
    try:
        await _photo_file.download_to_drive(_face_save_path)
    except Exception as _dl_err:
        await safe_reply(update, f"❌ Failed to download your photo: {_dl_err}")
        logger.error(f"[FACE_SWAP_PHOTO] Download error for user {user_id}: {_dl_err}")
        return

    if not os.path.isfile(_face_save_path) or os.path.getsize(_face_save_path) == 0:
        await safe_reply(update, "❌ Photo download resulted in an empty file. Please try again.")
        return

    logger.info(
        f"[FACE_SWAP_PHOTO] Face image received from user {user_id}: {_face_save_path} "
        f"({os.path.getsize(_face_save_path) / 1024:.1f} KB)"
    )
    await safe_reply(update, "📥 Face photo received! Starting face swap pipeline...")

    # ── Pull stored session data & reset state ────────────────────────────────
    with acquire_session_lock(user_id):
        _sess         = user_sessions.get(user_id, {})
        _video_path   = _sess.get("face_swap_video", "")
        _profile_data = _sess.get("profile_data", {})
        _mon_report   = _sess.get("monetization_report", {})
        # Clear the waiting state so the user can send other messages
        user_sessions[user_id]["state"] = _sess.get("_pre_faceswap_state", "IDLE")
        user_sessions[user_id].pop("face_swap_video", None)
        user_sessions[user_id].pop("face_swap_score", None)
        save_session(user_id)

    if not _video_path or not os.path.isfile(_video_path):
        await safe_reply(
            update,
            "❌ Session video is no longer available. Please process a new video and try face swap again.",
        )
        return

    # ── Execute the swap pipeline ─────────────────────────────────────────────
    await _execute_face_swap_pipeline(
        update, context, user_id,
        video_path=_video_path,
        face_image_path=_face_save_path,
        profile_data=_profile_data,
        mon_report=_mon_report,
    )


async def handle_auction_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    try:
        state = AuctionState()
        with state.safe_lock(timeout=10):
            if not state.state.get("active", False):
                return 
            uid = str(update.effective_user.id)
            if uid not in state.state["bidders"]:
                return 
            
            user_data = state.state["bidders"][uid]
            if user_data.get("verified", False) and user_data.get("pending_bid", 0) == 0:
                return 
    except TimeoutError:
        logger.warning(f"⚠️ Lock timeout for user {update.effective_user.id} in screenshot handler.")
        return

    # Cooldown Check
    last_ocr = OCR_COOLDOWN.get(uid, 0)
    if time.time() - last_ocr < 60:
        await safe_reply(update, "⏳ Processing previous receipt. Please wait 60s.")
        return 
        
    OCR_COOLDOWN[uid] = time.time()
    await safe_reply(update, "🔍 Verifying payment screenshot (Thread Safely)...")
    
    # Download photo
    photo_file = await update.message.photo[-1].get_file()
    temp_path = os.path.join(tempfile.gettempdir(), f"payment_{uid}_{int(time.time())}.jpg")
    await photo_file.download_to_drive(temp_path)
    
    # Offload OCR to avoid blocking the bot
    try:
        data = await asyncio.to_thread(PaymentVerifier.verify_screenshot, temp_path)
    except Exception as e:
        await safe_reply(update, f"OCR Error: {e}")
        return 
        
    if data.get("status") == "success":
        utr = data.get("utr_number", "Unknown")
        amt = data.get("amount", "Unknown")
        name = data.get("payer_name", "Unknown")
        msg = f"Receipt Read ✅\nUTR: {utr}\nAmount: ₹{amt}\nName: {name}\n\nSending to Admin for final approval."
        await safe_reply(update, msg)
        
        # Send to admin
        admin_id = os.getenv("TELEGRAM_ADMIN_ID")
        if admin_id:
            try:
                admin_msg = f"🚨 PENDING BID VERIFICATION 🚨\nUser: @{update.effective_user.username}\nID: {uid}\nOCR Amount: ₹{amt}\nUTR: {utr}\nUse /confirm {uid} to approve."
                await context.bot.send_photo(chat_id=admin_id, photo=open(temp_path, 'rb'), caption=admin_msg)
            except Exception as e:
                logger.error(f"Failed to send to admin: {e}")
    else:
        await safe_reply(update, "❌ Could not read screenshot. Ensure UTR and amount are clearly visible.")

async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from dotenv import dotenv_values
        fashion_env = dotenv_values("Credentials/social_media/Fashion & Style/.env")
        fashion_admin_ids = [int(x) for x in fashion_env.get("ADMIN_IDS", "").split(",") if x]
    except:
        fashion_admin_ids = []
        
    if update.effective_user.id not in ADMIN_IDS and update.effective_user.id not in fashion_admin_ids:
        return
    try:
        target_uid = context.args[0]
    except IndexError:
        await safe_reply(update, "Usage: /confirm <user_id>")
        return
        
    state = AuctionState()
    try:
        with state.safe_lock(timeout=10):
            if target_uid in state.state["bidders"]:
                user_data = state.state["bidders"][target_uid]
                pending = user_data.get("pending_bid", 0)
                user_data["total_paid"] += pending
                user_data["pending_bid"] = 0
                user_data["verified"] = True
                username = user_data["username"]
                state.save_state_locked()
            else:
                await safe_reply(update, "User ID not found in ledger.")
                return
        
        # Recalculate pot after releasing lock
        state.update_pot_total()
    except (TimeoutError, Exception) as e:
        logger.error(f"❌ Auction Confirm Error: {e}")
        await safe_reply(update, "⚠️ Auction system is busy or user ID invalid.")
        return
    await safe_reply(update, f"✅ Verified user for ₹{pending}.")
    try:
        await context.bot.send_message(chat_id=target_uid, text="🎉 Your payment was verified by admin! You are locked in.")
    except: pass

async def cmd_auction_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from dotenv import dotenv_values
        fashion_env = dotenv_values("Credentials/social_media/Fashion & Style/.env")
        fashion_admin_ids = [int(x) for x in fashion_env.get("ADMIN_IDS", "").split(",") if x]
    except:
        fashion_admin_ids = []
        
    if update.effective_user.id not in ADMIN_IDS and update.effective_user.id not in fashion_admin_ids:
        return
    from Uploader_Modules.telegram_auction_engine import SchedulerDaemon
    SchedulerDaemon.job_open_auction()
    await safe_reply(update, "✅ Auction manually STARTED! Users can now /join.")

async def cmd_auction_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from dotenv import dotenv_values
        fashion_env = dotenv_values("Credentials/social_media/Fashion & Style/.env")
        fashion_admin_ids = [int(x) for x in fashion_env.get("ADMIN_IDS", "").split(",") if x]
    except:
        fashion_admin_ids = []
        
    if update.effective_user.id not in ADMIN_IDS and update.effective_user.id not in fashion_admin_ids:
        return
    from Uploader_Modules.telegram_auction_engine import SchedulerDaemon
    SchedulerDaemon.job_close_auction()
    await safe_reply(update, "✅ Auction manually STOPPED! Winner calculation complete.")

async def cmd_tg_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: show current Telegram group routing configuration."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        from Uploader_Modules.telegram_router import get_router_status
        status = get_router_status()
        lines = ["📡 <b>Telegram Group Router Status</b>", ""]
        for cat, info in status.items():
            icon = {"fashion": "👗", "nsfw": "🔞", "general": "📰"}.get(cat, "📡")
            lines.append(f"{icon} <b>{cat.upper()}</b>")
            lines.append(f"   Env prefix: <code>{info['prefix']}</code>")
            if info["groups"]:
                for i, g in enumerate(info["groups"]):
                    marker = "▶" if i == info["next_index"] else " "
                    lines.append(f"   {marker} [{i}] {g}")
            else:
                lines.append("   ⚠️ <i>No groups configured — will fall back to general</i>")
            lines.append("")
        lines.append("💡 Add secrets: <code>TELEGRAM_CONFIG_FASHION</code> / <code>TELEGRAM_CONFIG_ADULT</code>")
        await safe_reply(update, "\n".join(lines))
    except Exception as e:
        await safe_reply(update, f"❌ tg_status error: {e}")

# --- END AUCTION ENGINE HANDLERS ---


def main():
    # Register Signal Handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)

    # HARDENED TIMEOUTS (Total patience for slow uploads)
    from telegram.request import HTTPXRequest

    # Setting read/write to None enables infinite timeout for large file streaming
    request_config = HTTPXRequest(
        connect_timeout=600,
        read_timeout=None,
        write_timeout=None,
        pool_timeout=600,
        connection_pool_size=50,  # Fix for "Pool timeout" under load
    )

    # Check if Local Bot API is configured
    local_api_url = os.getenv("LOCAL_BOT_API_URL")

    app_builder = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .request(request_config)
    )

    if local_api_url:
        logger.info(f"🚀 Using LOCAL_BOT_API_URL: {local_api_url}")
        app_builder = app_builder.base_url(local_api_url).local_mode(True)

    app = app_builder.build()

    app.add_error_handler(error_handler)
    
    # REGISTER WATCHER IN GROUP -1 (Runs before all other handlers)
    app.add_handler(TypeHandler(Update, global_debug_logger), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", cmd_status))      # Queue & job status
    app.add_handler(CommandHandler("getid", cmd_getid))  # Helper: get chat ID of any group
    app.add_handler(CommandHandler("ytcode", cmd_ytcode))  # YouTube Headless Auth code receiver
    app.add_handler(CommandHandler("getbatch", getbatch))
    app.add_handler(CommandHandler("setbatch", setbatch))
    app.add_handler(CommandHandler("compile_last", compile_last))
    app.add_handler(CommandHandler("compile_first", compile_first))
    app.add_handler(CommandHandler("versus", cmd_versus))
    app.add_handler(CommandHandler("approve", approve_upload))
    app.add_handler(CommandHandler("reject", reject_upload))
    app.add_handler(CommandHandler("register_promo", register_promo))  # New Command
    app.add_handler(CommandHandler("compile", cmd_compile))
    app.add_handler(CommandHandler("learn", cmd_learn))
    app.add_handler(CommandHandler("stats_hooks", cmd_stats_hooks))          # Hook CTR leaderboard
    app.add_handler(CommandHandler("stats_transitions", cmd_stats_transitions))  # STIE RAG leaderboard
    app.add_handler(CommandHandler("swap_status", cmd_swap_status))                # Outfit swap queue status
    app.add_handler(CommandHandler("run_swap", cmd_run_swap))                      # Fire next outfit swap
    # Outfit Swap Rating Callback (must be before generic verify_watermark)
    app.add_handler(CallbackQueryHandler(handle_outfit_rating_callback, pattern=r"^outfit_rate:"))
    app.add_handler(CallbackQueryHandler(verify_watermark))  # FIXED: Register Handler

    # Auction Engine Handlers
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("bid", cmd_bid))
    app.add_handler(CommandHandler("confirm", cmd_confirm))
    app.add_handler(CommandHandler("auction_start", cmd_auction_start))
    app.add_handler(CommandHandler("auction_stop", cmd_auction_stop))
    app.add_handler(CommandHandler("tg_status", cmd_tg_status))     # Telegram routing status
    # Face-swap photo handler must come FIRST so it intercepts WAITING_FOR_FACE_IMAGE
    # state before the auction screenshot handler can claim the photo.
    app.add_handler(MessageHandler(filters.PHOTO, handle_face_swap_photo))

    # Direct Video Upload Handler
    app.add_handler(
        MessageHandler(filters.VIDEO | filters.Document.ALL, handle_attachment)
    )

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    logger.info("🤖 Bot is running...")

    # Load Sessions
    load_sessions()

    # Check Env
    check_and_update_env()

    # Start AutoCleanup (Checks every 60 minutes, deletes files > 2 days old)
    cleanup = AutoCleanup(interval_minutes=60, age_days=2)
    cleanup.start()

    # ── INSTAGRAM INSTANT AUTOMATIONS ─────────────────────────────────────────
    # Three background daemon threads — zero impact on main bot loop.
    # Each gate-checks its own env vars and exits silently if disabled.
    # Legacy IG_DM_Listener removed. IGCommentLinkResponder starts below.

    try:
        from Uploader_Modules.ig_story_dm_listener import IGStoryDMListener
        IGStoryDMListener.start_background_polling()       # Automation 2: Story → DM
    except Exception as _a2_err:
        logger.warning(f"⚠️ Story DM Listener failed to start: {_a2_err}")

    try:
        from Uploader_Modules.ig_auto_dm_responder import IGAutoDMResponder
        IGAutoDMResponder.start_background_polling()       # Automation 3: Gemini DM replies
    except Exception as _a3_err:
        logger.warning(f"⚠️ Auto DM Responder failed to start: {_a3_err}")

    try:
        from Uploader_Modules.ig_comment_link_responder import IGCommentLinkResponder
        IGCommentLinkResponder.start_background_polling()  # Automation 4: Comment-to-DM exact link
    except Exception as _a4_err:
        logger.warning(f"⚠️ Comment Link Responder failed to start: {_a4_err}")
        
    try:
        from Uploader_Modules.telegram_auction_engine import SchedulerDaemon
        SchedulerDaemon.start_background_polling()         # Automation 5: Flash Deal Auction Engine
    except Exception as _a5_err:
        logger.warning(f"⚠️ Telegram Auction Engine failed to start: {_a5_err}")

    try:
        from Actress_Modules.actress_scheduler import start_scheduler
        start_scheduler()                                  # Automation 6: Actress Account Auto-Scraper
    except Exception as _a6_err:
        logger.warning(f"⚠️ Actress Scheduler failed to start: {_a6_err}")

    # ── Higgsfield Outfit Swap Background Processor ────────────────────────────
    # Daemon thread: checks swap queue every 30 min, auto-fires swaps when credits allow,
    # and DMs output images to admin automatically.
    try:
        from Higgsfield_Modules.outfit_swap_engine import start_background_swap_processor
        start_background_swap_processor(interval_minutes=30)   # Automation 7: AI Outfit Swap
        logger.info("👗 [OUTFIT_SWAP] Background swap processor started (30min cycle).")
    except Exception as _a7_err:
        logger.warning(f"⚠️ Outfit Swap Processor failed to start: {_a7_err}")
    # ──────────────────────────────────────────────────────────────────────────

    # Run polling
    # stop_signals=None prevents it from overwriting our signal handler (unlikely, but safe)
    # Run resilient polling
    logger.info("🤖 Bot starting polling loop...")

    last_crash_time = 0
    retry_delay = 10

    while True:
        try:
            # If running successfully for >60s, reset the backoff
            if time.time() - last_crash_time > 60:
                retry_delay = 10

            # Standard polling (handles stop signals automatically)
            app.run_polling(close_loop=True)
            break
        except (KeyboardInterrupt, SystemExit):
            logger.info("🛑 Bot shutdown initiated by user.")
            break
        except Exception as e:
            last_crash_time = time.time()
            err_str = str(e).lower()
            if (
                "httpx.connecterror" in err_str
                or "getaddrinfo" in err_str
                or "network" in err_str
                or "timeout" in err_str
            ):
                logger.warning(
                    f"⚠️ Network issue intercepted: {e}. Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 300)  # Max 5 mins
            else:
                logger.error(f"❌ Polling CRASH: {e}")
                logger.warning("⚠️ Unexpected crash. Retrying in 5s...")
                time.sleep(5)
                retry_delay = 10

    # Safe Shutdown
    logger.info("🛑 Shutting down executor...")
    executor.shutdown(wait=True)
    logger.info("👋 Bot stopped gracefully.")


# ==================== AUTO-TRAINING ====================
# ==================== AUTO-TRAINING ====================


class AutoCleanup(threading.Thread):
    def __init__(self, interval_minutes=60, age_days=2):
        super().__init__()
        self.interval = interval_minutes * 60
        self.age_days = age_days
        self.daemon = True
        self.running = True
        # Expanded Cleanup Targets (Including Telegram Bot API local caches)
        self.target_dirs = [
            "downloads",
            "temp",
            "final_compilations",
            "Original_audio",
            "telegram-bot-api",
            "telegram-bot-api-Windows",
            "Processed Shorts",
            # ── new purge targets ──
            "debug_frames",
            "_karaoke_tmp",
            "intelligence_cache",
        ]
        self.state_file = "The_json/cleanup_state.json"
        self.last_run = self._load_state()

    def _load_state(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    return data.get("last_run", 0)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load cleanup state: {e}")
        return 0

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({"last_run": self.last_run}, f)
        except Exception as e:
            logger.error(f"❌ Failed to save cleanup state: {e}")

    def run(self):
        logger.info("🧹 AutoCleanup started (Persistent Mode).")

        while self.running:
            # Calculate time since last run
            elapsed = time.time() - self.last_run
            wait_time = max(0, self.interval - elapsed)

            if wait_time > 0:
                logger.info(
                    f"⏳ Next cleanup in {int(wait_time / 60)} minutes ({int(wait_time)}s)..."
                )
                # Sleep in chunks to allow faster shutdown if needed (though daemon thread handles kill)
                # But for simplicity, simple sleep is fine as it's a daemon thread.
                time.sleep(wait_time)

            # Perform cleanup
            self._cleanup()

            # Update state
            self.last_run = time.time()
            self._save_state()

            # Wait for next interval (full interval now)
            # Actually, the loop logic above handles this naturally:
            # Next iteration: elapsed will be ~0, so wait_time will be ~interval.
            # So we don't need an extra sleep here.

    def _cleanup(self):
        try:
            # 1. RUN AUDIO DEDUPLICATION (Prioritize checking Original_audio)
            audio_deduplicator.scan_and_clean_duplicates("Original_audio")
            
            # 2. RUN AUDIO POOL MAINTENANCE (Rotation & Cleanup)
            try:
                from Audio_Modules.audio_pool_manager import pool_manager
                pool_manager.maintenance()
            except Exception as _pme:
                logger.warning(f"⚠️ Audio pool maintenance failed: {_pme}")

            cutoff = time.time() - (self.age_days * 86400)

            for target_dir in self.target_dirs:
                if not os.path.exists(target_dir):
                    continue

                # Custom Retention Policy per folder
                # Default: self.age_days (2 days)
                effective_cutoff = cutoff
                if "Original_audio" in target_dir:
                    effective_cutoff = time.time() - (2 * 86400)
                    logger.info("🧹 Cleanup: Applying 2-Day policy to Original_audio")
                elif "downloads" in target_dir:
                    effective_cutoff = time.time() - (DOWNLOADS_RETENTION_DAYS * 86400)
                    logger.info(f"🧹 Cleanup: Applying {DOWNLOADS_RETENTION_DAYS}-Day policy to downloads")
                elif "Processed Shorts" in target_dir:
                    effective_cutoff = time.time() - (PROCESSED_SHORTS_RETENTION_DAYS * 86400)
                    logger.info(f"🧹 Cleanup: Applying {PROCESSED_SHORTS_RETENTION_DAYS}-Day policy to Processed Shorts")
                elif "final_compilations" in target_dir:
                    effective_cutoff = time.time() - (FINAL_COMPILATIONS_RETENTION_DAYS * 86400)
                    logger.info(f"🧹 Cleanup: Applying {FINAL_COMPILATIONS_RETENTION_DAYS}-Day policy to final_compilations")
                elif target_dir in ("temp",):
                    effective_cutoff = time.time() - (TEMP_RETENTION_HOURS * 3600)
                    logger.info(f"🧹 Cleanup: Applying {TEMP_RETENTION_HOURS}h policy to temp/")
                elif target_dir == "debug_frames":
                    effective_cutoff = time.time() - (DEBUG_FRAMES_RETENTION_HOURS * 3600)
                    logger.info(f"🧹 Cleanup: Applying {DEBUG_FRAMES_RETENTION_HOURS}h policy to debug_frames/")
                elif target_dir == "_karaoke_tmp":
                    effective_cutoff = time.time() - (KARAOKE_TMP_RETENTION_HOURS * 3600)
                    logger.info(f"🧹 Cleanup: Applying {KARAOKE_TMP_RETENTION_HOURS}h policy to _karaoke_tmp/")
                elif target_dir == "intelligence_cache":
                    # ── SIZE-CAP: evict oldest files until under INTELLIGENCE_CACHE_MAX_MB ──
                    _ic_max_bytes = INTELLIGENCE_CACHE_MAX_MB * 1024 * 1024
                    try:
                        _ic_files = []
                        for _ic_root, _ic_dirs, _ic_names in os.walk(target_dir):
                            for _ic_name in _ic_names:
                                _ic_fp = os.path.join(_ic_root, _ic_name)
                                try:
                                    _ic_files.append((_ic_fp, os.path.getmtime(_ic_fp), os.path.getsize(_ic_fp)))
                                except OSError:
                                    pass
                        _ic_total = sum(s for _, _, s in _ic_files)
                        if _ic_total > _ic_max_bytes:
                            logger.info(f"🧹 intelligence_cache: {_ic_total/1024/1024:.1f} MB > cap {INTELLIGENCE_CACHE_MAX_MB} MB — evicting oldest")
                            _ic_files.sort(key=lambda x: x[1])  # oldest first
                            for _ic_fp, _, _ic_sz in _ic_files:
                                if _ic_total <= _ic_max_bytes:
                                    break
                                try:
                                    os.remove(_ic_fp)
                                    _ic_total -= _ic_sz
                                    logger.info(f"🗑️ Evicted cache: {os.path.basename(_ic_fp)}")
                                except Exception as _ice:
                                    logger.debug(f"Cache evict failed: {_ice}")
                    except Exception as _ic_err:
                        logger.warning(f"⚠️ intelligence_cache size-cap failed: {_ic_err}")
                    continue  # skip the standard age-based loop for this dir

                for item in os.listdir(target_dir):
                    item_path = os.path.join(target_dir, item)

                    if "keep" in item.lower() or "compile" in item.lower():
                        continue

                    try:
                        mtime = os.path.getmtime(item_path)
                        if mtime < effective_cutoff:
                            if os.path.isfile(item_path):
                                os.remove(item_path)
                                logger.info(f"🗑️ Cleaned file: {item} in {target_dir}")
                            elif os.path.isdir(item_path):
                                shutil.rmtree(item_path, ignore_errors=True)
                                logger.info(f"🗑️ Cleaned dir: {item} in {target_dir}")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to clean {item}: {e}")

        except Exception as e:
            logger.error(f"❌ AutoCleanup Error: {e}")


def run_cli_mode(args):
    """
    Direct CLI Entrypoint.
    Bypasses Telegram Bot and runs the compiler pipeline directly.
    """
    print(f"🚀 CLI Mode Active: Standard Pipeline")
    input_source = args.input

    # 1. Handle URL vs File
    if isinstance(input_source, tuple):
        input_source = input_source[0]

    video_path = input_source
    if input_source.startswith("http"):
        print(f"📥 Downloading URL: {input_source}")
        # Use portal downloader
        from Download_Modules import downloader

        dl_res = downloader.download_video(input_source)
        if dl_res and dl_res[0]:
            video_path, is_cached = dl_res
            print(f"✅ Downloaded: {video_path} (Cached: {is_cached})")
        else:
            print(f"❌ Download Failed.")
            return

    if not os.path.exists(video_path):
        print(f"❌ Input file not found: {video_path}")
        return

    # 2. Run Compiler
    print(f"🎬 Compiling: {video_path}")
    output_path = f"cli_output_{int(time.time())}.mp4"

    # [CLAW] CLI routed through Vanguard 4-turn loop — zero bypass
    try:
        # Pass enhance flag from CLI args
        enhance_mode = getattr(args, "enhance", False)
        title_val = getattr(args, "title", None) or "CLI Mission"
        print(f"🔬 Enhance Mode: {enhance_mode}")
        print(f"🏷️ Title: {title_val}")

        # [CLAW] CLI routed through Vanguard 4-turn loop — synchronous for instant Ctrl+C
        from claw_vanguard import VanguardDirector as _VD_cli

        _cli_director = _VD_cli()
        _cli_result = _cli_director.execute_mission(
            niche=os.getenv("DEFAULT_NICHE", "viral"),
            video_request=(
                f"CLI: Process '{title_val}'"
                + (" with enhanced quality." if enhance_mode else ".")
            ),
            input_paths=[video_path],
        )
        res_path = _cli_result.output if _cli_result.success else None

        if res_path and os.path.exists(res_path):
            print(f"✅ SUCCESS! Output: {os.path.abspath(res_path)}")
        else:
            print(f"❌ Compilation Failed.")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Final Summary for CLI Mode
        try:
            from Diagnostics_Modules.gemini_trace import GeminiTrace
            GeminiTrace.print_summary()
        except Exception:
            pass


def process_clip(video_path: str, actress_title: str) -> str | None:
    """
    Direct callable entry point for actress_publisher.
    Returns the final processed video path, or None on failure.
    """
    try:
        from claw_vanguard import VanguardDirector
        director = VanguardDirector()
        title_val = actress_title or "CLI Mission"
        video_request = f"CLI: Process '{title_val}'"
        
        result = director.execute_mission(
            niche=os.getenv("DEFAULT_NICHE", "viral"),
            video_request=video_request,
            input_paths=[video_path],
        )
        
        if result and result.success and result.output and os.path.exists(result.output):
            return result.output
        return None
    except Exception as exc:
        logger.error("Pipeline error: %s", exc)
        return None



def run_ci_mode():
    logger.info("🤖 [CI] Running AMTCE in One-Shot CI Mode...")

    # 1. Check and update env / heal json files
    check_and_update_env()

    github_event = os.getenv("GITHUB_EVENT_NAME", "").lower()
    github_cron  = os.getenv("GITHUB_CRON", "")

    logger.info(f"📊 [CI Trigger Info] Event: '{github_event}' | Cron: '{github_cron}'")

    is_scheduled = (github_event == "schedule")

    # ── Bot Lock: scheduled runs yield to a running persistent bot ──────────
    # When a manual (workflow_dispatch) run starts main() it writes bot_lock.json
    # with the expected expiry timestamp. Any scheduled cron that starts while
    # the lock is still valid skips all work and exits immediately to prevent
    # two runners sharing the same Telegram bot token.
    _BOT_LOCK_FILE = "The_json/bot_lock.json"
    if is_scheduled:
        try:
            if os.path.exists(_BOT_LOCK_FILE):
                import json as _jl
                with open(_BOT_LOCK_FILE, "r") as _f:
                    _lock = _jl.load(_f)
                _expires = _lock.get("expires_at", 0)
                if time.time() < _expires:
                    _mins_left = int((_expires - time.time()) / 60)
                    logger.info(
                        "🔒 [CI] Bot lock active — persistent bot running for ~%d more min. "
                        "Scheduled cron '%s' will skip to avoid token conflict.",
                        _mins_left, github_cron or 'unknown'
                    )
                    return  # ← Scheduled run exits immediately, bot keeps running
                else:
                    logger.info("🔓 [CI] Bot lock expired — scheduled cron proceeding normally.")
        except Exception as _le:
            logger.warning("⚠️ [CI] Could not read bot_lock.json: %s", _le)

    # Define Harvester vs Publisher crons precisely
    harvester_crons = {
        '0 2 * * *',
        '0 6 * * *',
        '0 11 * * *',
        '0 13 * * *',
        '0 15 * * *'
    }

    publisher_crons = {
        '30 2 * * *',
        '30 6 * * *',
        '30 11 * * *',
        '30 13 * * *',
        '30 14 * * *'
    }

    should_harvest = False
    should_publish = False

    if is_scheduled:
        # Standardize cron string (strip extra whitespace)
        cron_key = " ".join(github_cron.split())
        if cron_key in harvester_crons:
            should_harvest = True
            logger.info(f"🚜 [CI] Scheduled Harvester Slot Fired ('{cron_key}')")
        elif cron_key in publisher_crons:
            should_publish = True
            logger.info(f"📤 [CI] Scheduled Publisher Slot Fired ('{cron_key}')")
        elif cron_key == '0 12 * * 1-6':
            should_harvest = True
            should_publish = True
            logger.info(f"🏷️ [CI] Scheduled Auction Pre-Post Fired ('{cron_key}'): Harvest + Publish enabled!")
        elif cron_key == '30 15 * * 1-6':
            logger.info(f"🎬 [CI] Scheduled Auction Close Slot Fired ('{cron_key}').")
        else:
            # Fallback if a cron is not in the set, run harvest/publish based on minute heuristic
            try:
                minute = int(cron_key.split()[0])
                if minute in (30, 0):
                    should_harvest = True
                    logger.info(f"🚜 [CI] Scheduled Harvester (Fallback by minute: {minute})")
                else:
                    should_publish = True
                    logger.info(f"📤 [CI] Scheduled Publisher (Fallback by minute: {minute})")
            except Exception:
                should_harvest = True
                logger.info("🚜 [CI] Scheduled Trigger (Defaulting to Harvester)")
    else:
        # Manual run / push / workflow_dispatch
        # Check overrides in .env
        from dotenv import load_dotenv
        for p in ["Credentials/.env", ".env"]:
            if os.path.exists(p):
                load_dotenv(p, override=True)
                break

        if github_event == "workflow_dispatch":
            dispatch_mode = os.getenv("DISPATCH_MODE", "both").lower()
            should_harvest = dispatch_mode in ("harvest", "both")
            should_publish = dispatch_mode in ("publish", "both")
            logger.info(f"🔧 [CI] Manual dispatch — mode='{dispatch_mode}' harvest={should_harvest} publish={should_publish}")
        else:
            force_harvest = os.getenv("FORCE_HARVEST", "no").lower() in ("yes", "true", "1")
            force_publish = os.getenv("FORCE_NEXT_BATCH", "no").lower() in ("yes", "true", "1")

            if force_harvest:
                should_harvest = True
                logger.info("🔥 [CI] Manual Override: FORCE_HARVEST=yes detected.")
            if force_publish:
                should_publish = True
                logger.info("🔥 [CI] Manual Override: FORCE_NEXT_BATCH=yes detected.")

        if not should_harvest and not should_publish:
            logger.info("⏭️ [CI] Skipping all automations. Manual trigger runs in dry/setup-only mode.")

    # --- 1. HARVEST PHASE ---
    if should_harvest:
        # Run post timing analysis to optimize schedule dynamically
        try:
            from Actress_Modules.posting_time_analyzer import get_recommendations, patch_env
            recs = get_recommendations()
            patch_env(recs)
            logger.info("✅ [CI] Successfully optimized posting times based on ledger.")
        except Exception as e:
            logger.warning(f"⚠️ [CI] Failed to run posting time analyzer: {e}")

        try:
            from Actress_Modules.actress_scheduler import run_daily_cycle
            logger.info("🚜 [CI] Starting daily harvest cycle...")
            run_daily_cycle()
            logger.info("✅ [CI] Harvest cycle complete.")
        except Exception as e:
            logger.error(f"❌ [CI] Harvest cycle failed: {e}")

    # --- 2. PUBLISH PHASE ---
    if should_publish:
        try:
            from Actress_Modules.actress_publisher import PublishQueue, _process_queue_item, _auto_fill_queue_from_downloads
            import time, random

            # Pull any un-queued clips from downloads/ into the queue first
            _auto_fill_queue_from_downloads()

            queue = PublishQueue.load()
            if queue:
                logger.info(f"📤 [CI] Found {len(queue)} queued clip(s). Processing EXACTLY ONE queued item to avoid posting like a maniac...")
                
                # Setup trackers for pop_one if necessary
                last_folder = None
                last_gender = None
                
                item = PublishQueue.pop_one(last_folder, last_gender)
                if item:
                    video_path     = item['video_path']
                    actress_title  = item['actress_title']
                    actress_folder = item['actress_folder']
                    
                    f_lower = actress_folder.lower()
                    if f_lower.startswith("paparazzi"): last_gender = "men"
                    elif f_lower.startswith("fashion"): last_gender = "women_fashion"
                    else: last_gender = "women_general"
                    
                    logger.info(f"🎬 Popped video for processing: {os.path.basename(video_path)} (Gender: {last_gender})")
                    
                    final_video_path = video_path
                    try:
                        result_path = process_clip(video_path, actress_title)
                        if result_path:
                            final_video_path = result_path
                            logger.info(f"✅ AMTCE PROCESS SUCCESS: output → {final_video_path}")
                    except Exception as e:
                        logger.error(f"⚠️ Failed to run AMTCE PROCESS: {e}")

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
                        except Exception:
                            pass

                    from Actress_Modules.actress_scheduler import _auto_publish_clip
                    _auto_publish_clip(final_video_path, actress_title, actress_folder)
                    
                    # Cleanup .mp4
                    if os.path.exists(final_video_path):
                        try: os.remove(final_video_path)
                        except Exception: pass
                    if os.path.exists(video_path) and final_video_path != video_path:
                        try: os.remove(video_path)
                        except Exception: pass
                        
                logger.info("✅ [CI] Queue item processing phase complete.")
            else:
                logger.info("📭 [CI] Publish queue is empty.")
        except Exception as e:
            logger.error(f"❌ [CI] Queue processing failed: {e}")

    # 5. Precise Auction triggers based on cron schedule / time of day
    try:
        from Uploader_Modules.telegram_auction_engine import SchedulerDaemon
        import datetime as _dt
        
        now_local = _dt.datetime.now() # TZ is Asia/Kolkata, so this is IST
        weekday = now_local.isoweekday() # 1-7 (Mon-Sun)
        cron_key = " ".join(github_cron.split())
        
        # Determine auction action
        auction_action = None
        
        if is_scheduled:
            if cron_key == '0 12 * * 1-6':
                auction_action = "announce"
            elif cron_key == '30 13 * * *':
                auction_action = "open"
            elif cron_key == '30 15 * * 1-6':
                auction_action = "close"
        else:
            # Manual / test trigger fallback: check current IST time (Asia/Kolkata)
            # Announce: 17:00 - 18:29 IST (Mon-Sat)
            if weekday <= 6 and (now_local.hour == 17 or (now_local.hour == 18 and now_local.minute < 30)):
                auction_action = "announce"
            # Open: 19:00 - 19:59 IST (everyday)
            elif now_local.hour == 19:
                auction_action = "open"
            # Close: 21:00 - 21:59 IST (Mon-Sat)
            elif weekday <= 6 and now_local.hour == 21:
                auction_action = "close"

        if auction_action == "announce":
            logger.info("🎬 [CI Auction] Firing deal announcement...")
            SchedulerDaemon.job_announce_deal()
            logger.info("✅ [CI Auction] Deal announcement complete.")
        elif auction_action == "open":
            logger.info("🎬 [CI Auction] Firing auction open...")
            SchedulerDaemon.job_open_auction()
            logger.info("✅ [CI Auction] Auction open complete.")
        elif auction_action == "close":
            logger.info("🎬 [CI Auction] Firing auction close...")
            SchedulerDaemon.job_close_auction()
            logger.info("✅ [CI Auction] Auction close complete.")
        else:
            logger.info(f"⏰ [CI Auction] No auction action triggered for current schedule/time (IST: {now_local.strftime('%H:%M')}).")
            
    except Exception as e:
        logger.error(f"❌ [CI Auction] Trigger failed: {e}")

    logger.info("👋 [CI] One-Shot CI Execution Finished successfully. Exiting.")


if __name__ == "__main__":
    lazy_load_genai_trace()
    import argparse

    parser = argparse.ArgumentParser(description="AMTCE Bot & CLI")
    parser.add_argument("--input", help="Direct input URL or File Path to process")
    parser.add_argument(
        "--enhance", action="store_true", help="Enable Heavy AI Enhancement/Upscaling"
    )
    parser.add_argument(
        "--title", type=str, help="Optional title for the compilation"
    )
    args, unknown = parser.parse_known_args()

    if args.input:
        run_cli_mode(args)
    elif os.getenv("GITHUB_ACTIONS") == "true" and os.getenv("GITHUB_EVENT_NAME", "").lower() != "workflow_dispatch":
        run_ci_mode()
    else:
        # Write bot lock so scheduled crons know to stand down while we run
        _BOT_LOCK_FILE = "The_json/bot_lock.json"
        _BOT_TIMEOUT_HOURS = 5.5  # GitHub Actions hard-kills at 6h; we expire at 5.5h
        _bot_expires = time.time() + (_BOT_TIMEOUT_HOURS * 3600)
        try:
            os.makedirs("The_json", exist_ok=True)
            import json as _jl
            with open(_BOT_LOCK_FILE, "w") as _f:
                _jl.dump({
                    "started_at": time.time(),
                    "expires_at": _bot_expires,
                    "mode": "workflow_dispatch",
                }, _f)
            logger.info(
                "🔒 [BOT LOCK] Written — bot holds lock until %s IST",
                __import__('datetime').datetime.fromtimestamp(_bot_expires).strftime('%H:%M')
            )
        except Exception as _le:
            logger.warning("⚠️ Could not write bot_lock.json: %s", _le)

        # Run Persistent Bot & Schedulers
        logger.info("🤖 Starting AMTCE in Persistent Server Mode (Schedulers Active)")
        main()