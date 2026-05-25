"""
Frame Extractor — Influencer Modules
======================================
Extracts the single best frame from a reference video (e.g., a Pinterest reel)
using the ffmpeg infrastructure already present in AMTCE.

Two strategies:
  1. Scene-change detection  — picks the first perceptually distinct frame
  2. Mid-point fallback      — extracts frame at 30% of total duration

No new dependencies — uses only ffmpeg/ffprobe (standard in AMTCE).

License: Apache 2.0 (ffmpeg via LGPL, usage wrapper is Apache 2.0)
"""

import os
import subprocess
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger("influencer.frame_extractor")

# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def extract_best_frame(video_path: str, output_dir: str = None) -> str:
    """
    Extract the single most visually distinct frame from a video.

    Uses ffmpeg `select='gt(scene,0.4)'` to find scene-change frames.
    Falls back to 30 % of total duration if scene detection yields nothing.

    Parameters
    ----------
    video_path  : Path to the input video (any ffmpeg-supported format).
    output_dir  : Directory to write the frame JPG.
                  Defaults to a system temp directory.

    Returns
    -------
    str  : Absolute path to the extracted frame JPG.

    Raises
    ------
    FileNotFoundError  : If video_path does not exist.
    RuntimeError       : If all extraction strategies fail.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="amtce_inf_frame_")
    os.makedirs(output_dir, exist_ok=True)

    stem = Path(video_path).stem
    scene_path    = os.path.join(output_dir, f"{stem}_scene.jpg")
    fallback_path = os.path.join(output_dir, f"{stem}_midpoint.jpg")

    # ── Strategy 1: scene-change detection ─────────────────────────────────
    logger.info(f"🎬 Extracting best frame (scene detection): {video_path}")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", "select='gt(scene,0.4)',scale=1080:-2",
            "-vsync", "vfr",
            "-frames:v", "1",
            "-q:v", "2",
            scene_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and _valid_image(scene_path):
            logger.info(f"✅ Scene frame saved: {scene_path}")
            return scene_path
        logger.warning("⚠️  Scene detection found nothing — trying fallback.")
    except Exception as exc:
        logger.warning(f"⚠️  Scene detection error: {exc}")

    # ── Strategy 2: 30 % duration fallback ─────────────────────────────────
    try:
        duration = _probe_duration(video_path)
        seek_sec = duration * 0.30

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{seek_sec:.3f}",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            "-vf", "scale=1080:-2",
            fallback_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and _valid_image(fallback_path):
            logger.info(f"✅ Fallback frame saved at {seek_sec:.1f}s: {fallback_path}")
            return fallback_path
    except Exception as exc:
        logger.error(f"❌ Fallback extraction failed: {exc}")

    raise RuntimeError(f"Could not extract any frame from: {video_path}")


def extract_multiple_frames(
    video_path: str,
    count: int = 5,
    output_dir: str = None,
) -> list:
    """
    Extract *count* evenly-spaced frames for manual best-frame selection.

    Parameters
    ----------
    video_path : Path to the input video.
    count      : Number of frames to extract.
    output_dir : Output directory (auto-created temp dir if None).

    Returns
    -------
    list[str] : Sorted list of frame JPG paths.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="amtce_inf_frames_")
    os.makedirs(output_dir, exist_ok=True)

    stem           = Path(video_path).stem
    output_pattern = os.path.join(output_dir, f"{stem}_%04d.jpg")

    try:
        duration   = _probe_duration(video_path)
        fps_target = count / duration

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps={fps_target:.6f},scale=1080:-2",
            "-q:v", "2",
            output_pattern,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=True)

        frames = sorted(
            str(Path(output_dir) / f)
            for f in os.listdir(output_dir)
            if f.startswith(stem) and f.endswith(".jpg")
        )
        logger.info(f"✅ Extracted {len(frames)} frames for selection.")
        return frames

    except Exception as exc:
        logger.error(f"❌ Multi-frame extraction failed: {exc}")
        return []


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------

def _probe_duration(video_path: str) -> float:
    """Return duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "csv",
        "-show_entries", "format=duration",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    raw = result.stdout.strip().split(",")
    return float(raw[-1])


def _valid_image(path: str, min_bytes: int = 5000) -> bool:
    """Return True if *path* is a non-trivial image file."""
    return os.path.exists(path) and os.path.getsize(path) >= min_bytes
