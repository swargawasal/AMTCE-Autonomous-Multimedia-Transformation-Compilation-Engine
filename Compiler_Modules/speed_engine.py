"""
Speed Engine — Intelligent Speed Factor Decisioning
=====================================================
Analyses a video clip and returns the optimal playback speed factor for:
1. Content ID visual fingerprint breaking (always apply at least 1.03x)
2. Slow-motion normalisation (if clip is 60fps+, speed it back up to look natural)
3. Pacing optimisation (very slow clips get a minor speed boost for Shorts engagement)

Logic:
  FPS > 50      → Slow-motion clip         → speed = 1.5 – 2.0x (normalise to ~30fps feel)
  Duration > 25 → Padding/filler clip       → speed = 1.25x (cut the dead air)
  Normal clip   → Content ID bypass only   → speed = 1.04x (inperceptible, but breaks fingerprint)

Returns: float speed_factor ready to pass into render_pipeline()
"""

import os
import logging
import subprocess
import json
import random


def _safe_parse_fps(raw_fps: str) -> float:
    """Safely parse fractional FPS strings like '60000/1001' without using eval()."""
    try:
        if "/" in raw_fps:
            num, den = raw_fps.split("/", 1)
            return float(num) / float(den)
        return float(raw_fps)
    except Exception:
        return 30.0

logger = logging.getLogger("speed_engine")

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

# ── Tuning knobs (can be overridden via .env) ──────────────────────────────
_SLOW_MO_THRESHOLD_FPS  = float(os.getenv("SPEED_SLOWMO_FPS_THRESHOLD", "48"))   # fps above this = slow-mo
_SLOW_MO_FACTOR         = float(os.getenv("SPEED_SLOWMO_FACTOR",        "1.6"))  # how fast to restore slow-mo
_LONG_CLIP_THRESHOLD_S  = float(os.getenv("SPEED_LONG_CLIP_SEC",        "22"))   # clips longer than this get boosted
_LONG_CLIP_FACTOR       = float(os.getenv("SPEED_LONG_CLIP_FACTOR",     "1.2"))  # slight boost for long clips
_CONTENT_ID_FACTOR      = float(os.getenv("SPEED_CONTENT_ID_FACTOR",    "1.04")) # baseline anti-fingerprint shift
_MAX_FACTOR             = float(os.getenv("SPEED_MAX_FACTOR",           "2.0"))  # hard cap


def probe_video(video_path: str) -> dict:
    """Return fps and duration from ffprobe. Lightweight, no OpenCV needed."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,duration",
            "-of", "json",
            video_path
        ]
        ffprobe_bin = FFMPEG_BIN.replace("ffmpeg", "ffprobe")
        cmd[0] = ffprobe_bin
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0]
        
        # Parse fractional FPS e.g. "60000/1001" — using safe math, NOT eval()
        raw_fps = stream.get("r_frame_rate", "30/1")
        fps = _safe_parse_fps(raw_fps)
        
        duration = float(stream.get("duration", 0) or 0)
        return {"fps": fps, "duration": duration}
    except Exception as e:
        logger.warning(f"⚠️ SpeedEngine probe failed: {e}")
        return {"fps": 30.0, "duration": 15.0}


def decide_speed_factor(video_path: str) -> float:
    """
    Analyse the clip and return the best speed_factor.

    Decision tree:
      1. Slow-motion (fps > threshold)  → restore to natural pace
      2. Long/padded clip               → slight pace boost
      3. Any clip                       → minimum Content ID bypass shift
    """
    info = probe_video(video_path)
    fps      = info["fps"]
    duration = info["duration"]

    label = "normal"
    factor = _CONTENT_ID_FACTOR  # always at minimum

    if fps >= _SLOW_MO_THRESHOLD_FPS:
        # Slow-motion clip — bring it back to standard pace
        # Target output fps ≈ 30. Ratio = fps / 30 capped at MAX.
        natural_factor = min(fps / 30.0, _MAX_FACTOR)
        # Blend: use half the natural ratio so it still looks cinematic
        factor = max(_SLOW_MO_FACTOR, min(natural_factor * 0.85, _MAX_FACTOR))
        label = f"slow-mo ({fps:.0f}fps)"

    elif duration >= _LONG_CLIP_THRESHOLD_S:
        # Long clip — tighten the pacing for Shorts
        factor = max(factor, _LONG_CLIP_FACTOR)
        label = f"long-clip ({duration:.1f}s)"

    # Add a tiny randomised jitter (±0.01) so every clip has a unique fingerprint
    jitter = random.uniform(-0.01, 0.01)
    factor = round(min(factor + jitter, _MAX_FACTOR), 4)

    logger.info(
        f"⚡ SpeedEngine: {label} | fps={fps:.1f} dur={duration:.1f}s → speed_factor={factor}x"
    )
    return factor
