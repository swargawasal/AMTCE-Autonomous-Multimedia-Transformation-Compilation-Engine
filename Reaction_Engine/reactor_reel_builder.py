"""
Reaction_Engine/reactor_reel_builder.py
----------------------------------------
Builds a single assembled "reactor reel" that:
  1. Maps the source video timeline to emotion-appropriate reactor clips
  2. Auto-detects and crops black bars from each reactor clip
  3. Loops/trims clips to fill each time window precisely
  4. Concatenates everything into ONE mp4 matching the source video duration

This replaces the old single-clip approach.

Output:
    A single .mp4 file (VISUAL ONLY, no audio) matching source_duration.
    The reel is segmented like:
        [neutral loop] [shocked trim] [neutral loop] [hype trim] ...

Integration:
    Called by reaction_engine._run_pipeline() at Step 3.
    The returned path goes straight to face_swap → lip_sync → compositor.
"""

import logging
import os
import subprocess
import tempfile
from typing import List, Dict, Optional, Any

logger = logging.getLogger("reactor_reel_builder")

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")

# Duration of each emotion reaction window in the assembled reel (seconds)
# The neutral clip fills all gaps between these windows
_REACTION_WINDOW_S = float(os.getenv("REACTION_WINDOW_SECONDS", "4.0"))

# Min gap between reaction windows — below this, merge into one
_MIN_SEGMENT_GAP_S = 2.0

# Framerate for generated PiP video (24 saves 20% computation vs 30)
_FPS = os.getenv("REACTION_FPS", "24")


def build_reactor_reel(
    reaction_lines: List[Dict[str, Any]],
    library,                       # ReactorLibraryManager instance
    source_duration: float,        # seconds
    output_dir: str,
) -> Optional[str]:
    """
    Build a single assembled reactor reel matching source_duration.

    Args:
        reaction_lines:   List of ReactionLine dicts from generate_reaction_script().
        library:          ReactorLibraryManager loaded instance.
        source_duration:  Duration of the source video in seconds.
        output_dir:       Directory to write intermediate and final files.

    Returns:
        Path to assembled_reel.mp4 (visual only), or None on failure.
    """
    if not reaction_lines:
        logger.warning("[REEL_BUILDER] No reaction lines. Cannot build reel.")
        return None
    if source_duration <= 0:
        logger.warning("[REEL_BUILDER] source_duration is 0. Cannot build reel.")
        return None

    os.makedirs(output_dir, exist_ok=True)

    # ── 1. Build timeline windows ──────────────────────────────────────────────
    windows = _build_timeline_windows(reaction_lines, source_duration)
    logger.info(f"[REEL_BUILDER] Timeline: {len(windows)} segments over {source_duration:.1f}s")

    # ── 2. Generate each segment as a trimmed/looped clip ─────────────────────
    segment_paths = []
    for i, window in enumerate(windows):
        emotion   = window["emotion"]
        duration  = window["duration"]
        seg_path  = os.path.join(output_dir, f"seg_{i:03d}_{emotion}.mp4")

        clip_raw = library.get_clip(emotion)
        if not clip_raw:
            logger.warning(f"[REEL_BUILDER] No clip for '{emotion}' — using neutral fallback.")
            clip_raw = library.get_clip("neutral")

        if not clip_raw:
            logger.error("[REEL_BUILDER] No neutral clip available. Aborting reel build.")
            return None

        # Auto-crop black bars → trim/loop to segment duration
        ok = _make_segment(
            clip_path=clip_raw,
            duration=duration,
            output_path=seg_path,
            emotion=emotion,
            segment_idx=i,
        )
        if ok:
            segment_paths.append(seg_path)
        else:
            logger.warning(f"[REEL_BUILDER] Segment {i} failed. Skipping.")

    if not segment_paths:
        logger.error("[REEL_BUILDER] All segments failed. No reel produced.")
        return None

    # ── 3. Concatenate all segments ───────────────────────────────────────────
    reel_path = os.path.join(output_dir, "assembled_reel.mp4")
    concat_ok = _concat_segments(segment_paths, reel_path)

    if concat_ok and os.path.isfile(reel_path):
        size_mb = os.path.getsize(reel_path) / 1024 / 1024
        logger.info(
            f"✅ [REEL_BUILDER] Assembled reel: {os.path.basename(reel_path)} "
            f"({size_mb:.1f}MB, {len(segment_paths)} segments)"
        )
        return reel_path
    else:
        logger.error("[REEL_BUILDER] Concat failed. Reel not built.")
        return None


# ── Timeline window builder ────────────────────────────────────────────────────

def _build_timeline_windows(
    reaction_lines: List[Dict],
    source_duration: float,
) -> List[Dict]:
    """
    Convert reaction_lines timestamps into a full segmented timeline.

    Each reaction moment becomes a short window of its emotion.
    Everything between moments → neutral.
    Covers [0, source_duration] completely.

    Returns list of dicts: {"emotion": str, "duration": float, "start": float}
    """
    # Sort by timestamp
    sorted_lines = sorted(reaction_lines, key=lambda l: l.get("ts", 0.0))
    windows = []
    cursor = 0.0

    for line in sorted_lines:
        ts       = line.get("ts", 0.0)
        emotion  = line.get("emotion", "neutral")

        # If timestamp is before current cursor, skip (merged into previous)
        if ts <= cursor:
            continue

        # Gap before this reaction → neutral window
        gap = ts - cursor
        if gap >= _MIN_SEGMENT_GAP_S:
            windows.append({
                "emotion":  "neutral",
                "start":    cursor,
                "duration": gap,
            })
        elif gap > 0:
            # Small gap — extend the previous neutral or just advance cursor
            if windows and windows[-1]["emotion"] == "neutral":
                windows[-1]["duration"] += gap
            else:
                windows.append({"emotion": "neutral", "start": cursor, "duration": gap})

        cursor = ts

        # Reaction window — fixed duration per emotion moment
        win_dur = min(_REACTION_WINDOW_S, source_duration - cursor)
        if win_dur > 0:
            windows.append({
                "emotion":  emotion,
                "start":    cursor,
                "duration": win_dur,
            })
            cursor += win_dur

    # Fill remaining time with neutral
    remainder = source_duration - cursor
    if remainder > 0.1:
        windows.append({
            "emotion":  "neutral",
            "start":    cursor,
            "duration": remainder,
        })

    return windows


# ── Black bar crop detection ───────────────────────────────────────────────────

def _detect_crop(clip_path: str) -> Optional[str]:
    """
    Run ffprobe-style cropdetect to find the effective content area.

    Returns a crop filter string like "crop=640:480:220:100", or None if
    detection fails (caller should skip cropping).
    """
    try:
        result = subprocess.run(
            [
                FFMPEG_BIN, "-y",
                "-ss", "0.5",          # skip first 0.5s (title cards)
                "-t", "3",             # sample 3 seconds
                "-i", clip_path,
                "-vf", "cropdetect=24:16:0",
                "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        # Parse last cropdetect output line
        crop_val = None
        for line in result.stderr.splitlines():
            if "crop=" in line:
                # e.g. "... crop=640:480:220:100 ..."
                for token in line.split():
                    if token.startswith("crop="):
                        crop_val = token.strip()
                        break

        if crop_val:
            logger.debug(f"[REEL_BUILDER] Detected: {crop_val} on {os.path.basename(clip_path)}")
            return crop_val
    except Exception as e:
        logger.debug(f"[REEL_BUILDER] cropdetect failed (non-fatal): {e}")

    return None


def _has_audio_stream(path: str) -> bool:
    """Return True if the media file contains at least one audio stream."""
    try:
        result = subprocess.run(
            [
                FFPROBE_BIN, "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=index", "-of", "csv=p=0", path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


# ── Segment generator ─────────────────────────────────────────────────────────

def _make_segment(
    clip_path: str,
    duration: float,
    output_path: str,
    emotion: str,
    segment_idx: int,
) -> bool:
    """
    Generate a single segment clip at exact `duration` seconds.

    Steps:
      1. Detect and apply crop (remove black bars)
      2. Scale to standard PiP dimensions (270×480 — portrait face)
      3. Loop the clip if it's shorter than needed
      4. Trim exactly to `duration`
      5. Strip audio (visual only)
    """
    if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        logger.debug(f"[REEL_BUILDER] Segment {segment_idx} cached: {output_path}")
        return True

    # Detect crop
    crop_filter = _detect_crop(clip_path)

    # Target: Match the REACTION_PIP_SIZE (default 378x672)
    # We build the reel at the target resolution directly, without black bars.
    PIP_W = int(os.getenv("REACTION_PIP_SIZE", "378"))
    PIP_W = PIP_W - (PIP_W % 2)
    PIP_H = int(PIP_W * 16 / 9)
    PIP_H = PIP_H - (PIP_H % 2)

    vf_steps = []
    if crop_filter:
        # crop= comes from cropdetect (format: crop=W:H:X:Y)
        vf_steps.append(crop_filter)
        
    # CSS 'object-fit: cover' logic: scale UP to fill, then crop overflow.
    vf_steps.append(
        f"scale=w={PIP_W}:h={PIP_H}:force_original_aspect_ratio=increase,"
        f"crop={PIP_W}:{PIP_H},"
        f"setsar=1,fps={_FPS},format=yuv420p"
    )
    vf = ",".join(vf_steps)

    # Audio handling: check if we should keep clip audio
    use_clip_audio = os.getenv("REACTION_USE_CLIP_AUDIO", "no").lower() in ("yes", "true", "1")
    
    audio_inputs = []
    audio_flags = []
    
    if use_clip_audio:
        has_audio = _has_audio_stream(clip_path)
        if has_audio:
            # Normalize to standard aac 44100Hz stereo to ensure concat doesn't fail
            audio_flags = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
        else:
            # Inject silent track if no audio exists
            audio_inputs = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
            audio_flags = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
            # We have 2 inputs now (clip_path and anullsrc). 
            # We must map video from input 0 and audio from input 1
            audio_flags += ["-map", "0:v", "-map", "1:a"]
    else:
        # Visual only (original default behaviour)
        audio_flags = ["-an"]

    # If we map explicitly in the audio_flags (for anullsrc), we must ensure 
    # the normal case maps correctly too if we are adding maps.
    # Actually, FFmpeg default behavior maps one video and one audio if no -map is provided.
    # So if we provide -map for anullsrc, we are good.

    cmd = [
        FFMPEG_BIN, "-y",
        "-stream_loop", "-1",
        "-i", clip_path,
    ] + audio_inputs + [
        "-t", str(duration),
        "-vf", vf,
    ] + audio_flags + [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning(
                f"[REEL_BUILDER] Segment {segment_idx} ({emotion}) failed: "
                f"{result.stderr[-300:]}"
            )
            return False
        return os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    except subprocess.TimeoutExpired:
        logger.warning(f"[REEL_BUILDER] Segment {segment_idx} timed out.")
        return False
    except Exception as e:
        logger.warning(f"[REEL_BUILDER] Segment {segment_idx} exception: {e}")
        return False


# ── Segment concatenation ─────────────────────────────────────────────────────

def _concat_segments(segment_paths: List[str], output_path: str) -> bool:
    """
    Concatenate segments using FFmpeg concat demuxer (frame-accurate, no re-encode).
    Uses a temp filelist.txt.
    """
    # Write concat list
    list_path = output_path.replace(".mp4", "_concat_list.txt")
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for seg in segment_paths:
                # Use forward slashes for ffmpeg on Windows, ensure absolute path
                abs_seg = os.path.abspath(seg).replace(chr(92), '/')
                f.write(f"file '{abs_seg}'\n")
    except Exception as e:
        logger.error(f"[REEL_BUILDER] Failed to write concat list: {e}")
        return False

    cmd = [
        FFMPEG_BIN, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c:v", "libx264",      # re-encode to ensure consistent stream
        "-preset", "ultrafast",
        "-crf", "23",
    ]
    
    use_clip_audio = os.getenv("REACTION_USE_CLIP_AUDIO", "no").lower() in ("yes", "true", "1")
    if use_clip_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
    else:
        cmd += ["-an"]

    cmd += [output_path]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.error(
                f"[REEL_BUILDER] Concat failed (rc={result.returncode}): "
                f"{result.stderr[-400:]}"
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("[REEL_BUILDER] Concat timed out (>300s).")
        return False
    except Exception as e:
        logger.error(f"[REEL_BUILDER] Concat exception: {e}")
        return False
