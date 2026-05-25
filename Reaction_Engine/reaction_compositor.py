"""
Reaction_Engine/reaction_compositor.py
---------------------------------------
FFmpeg-based compositor for reaction videos.

Layouts:
    pip (DEFAULT — top-right corner PiP, YouTube Shorts optimised):
        ┌──────────────────────────┐  1080×1920
        │     ┌──────────┐        │
        │     │ REACTOR  │← 270px │  top-right, 20px margin
        │     │  FACE 😮  │        │
        │     └──────────┘        │
        │                        │
        │    SOURCE VIDEO         │  Full screen, untouched
        │    (1080×1920)          │
        │                        │
        └──────────────────────────┘
        Reactor PiP: 270×480px (portrait face), top-right corner
        This is the WhatsApp/FaceTime call overlay aesthetic.

    stacked (9:16 — legacy, top panel):
        ┌────────────────┐
        │  REACTOR FACE  │  top 35%  (1080×672)
        ├────────────────┤
        │  SOURCE REEL   │  bottom 65% (1080×1248)
        └────────────────┘

    side_by_side (landscape):
        [SOURCE 65%] [REACTOR 35%]

.env flags:
    REACTION_LAYOUT=pip|stacked|side_by_side  (default: pip)
    REACTION_PIP_SIZE=270           reactor PiP size in px (square base)
    REACTION_PIP_MARGIN=20          px margin from corner
    REACTION_PIP_CORNER=top_right   top_right|top_left|top_center
    REACTION_SOURCE_RATIO=0.65      used by stacked/side_by_side only
    REACTION_DUCK_SOURCE_AUDIO=yes  duck source under reactor narration
    REACTION_DUCK_LEVEL=0.25
"""

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger("reaction_compositor")

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

_LAYOUT       = os.getenv("REACTION_LAYOUT", "pip").lower()
_SOURCE_RATIO = float(os.getenv("REACTION_SOURCE_RATIO", "0.65"))
_DUCK_SOURCE  = os.getenv("REACTION_DUCK_SOURCE_AUDIO", "yes").lower() in ("yes", "true", "1")
_DUCK_LEVEL   = float(os.getenv("REACTION_DUCK_LEVEL", "0.25"))

_PIP_SIZE     = int(os.getenv("REACTION_PIP_SIZE", "270"))    # base width of the PiP
_PIP_MARGIN   = int(os.getenv("REACTION_PIP_MARGIN", "20"))   # px from edge
_PIP_CORNER   = os.getenv("REACTION_PIP_CORNER", "top_right").lower()

# Standard Shorts output dimensions
_OUT_W = 1080
_OUT_H = 1920


def _calc_dimensions() -> tuple:
    """
    Calculate pixel dimensions for source and reactor panels.

    Returns:
        (source_w, source_h, reactor_w, reactor_h)
    """
    if _LAYOUT == "side_by_side":
        source_w  = int(_OUT_W * _SOURCE_RATIO)
        source_h  = _OUT_H
        reactor_w = _OUT_W - source_w
        reactor_h = _OUT_H
    else:  # stacked (default)
        source_w  = _OUT_W
        source_h  = int(_OUT_H * _SOURCE_RATIO)
        reactor_w = _OUT_W
        reactor_h = _OUT_H - source_h

    # Force even numbers (required by libx264)
    source_h  = source_h  - (source_h  % 2)
    reactor_h = reactor_h - (reactor_h % 2)
    source_w  = source_w  - (source_w  % 2)
    reactor_w = reactor_w - (reactor_w % 2)

    return source_w, source_h, reactor_w, reactor_h


def composite_reaction_video(
    source_video: str,
    reactor_video: str,
    reactor_audio: Optional[str],
    output_path: str,
    reaction_start_ts: float = 0.0,
    reaction_duration: float = None,
    audio_schedule: list = None,  # NEW: interleaved audio schedule
) -> bool:
    """
    Create a reaction video by compositing source + reactor.

    Layout is determined by REACTION_LAYOUT env var:
      - pip         → reactor as top-right PiP (default)
      - stacked     → reactor on top 35%, source bottom 65%
      - side_by_side → reactor right 35%, source left 65%

    Returns True on success, False on failure.
    """
    if not os.path.isfile(source_video):
        logger.error(f"[COMPOSITOR] Source video not found: {source_video}")
        return False
    if not os.path.isfile(reactor_video):
        logger.error(f"[COMPOSITOR] Reactor video not found: {reactor_video}")
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if _LAYOUT == "pip":
        logger.info(f"[COMPOSITOR] Layout=pip | corner={_PIP_CORNER} | size={_PIP_SIZE}px")
        _build_pip(source_video, reactor_video, reactor_audio, output_path, audio_schedule=audio_schedule)
    elif _LAYOUT == "side_by_side":
        src_w, src_h, rx_w, rx_h = _calc_dimensions()
        logger.info(f"[COMPOSITOR] Layout=side_by_side | Source={src_w}x{src_h} | Reactor={rx_w}x{rx_h}")
        _build_side_by_side(source_video, reactor_video, reactor_audio, output_path, src_w, src_h, rx_w, rx_h)
    else:  # stacked
        src_w, src_h, rx_w, rx_h = _calc_dimensions()
        logger.info(f"[COMPOSITOR] Layout=stacked | Source={src_w}x{src_h} | Reactor={rx_w}x{rx_h}")
        _build_stacked(
            source_video, reactor_video, reactor_audio,
            output_path, src_w, src_h, rx_w, rx_h,
            reaction_start_ts, reaction_duration,
        )

    success = os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    if success:
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(f"✅ [COMPOSITOR] Done: {os.path.basename(output_path)} ({size_mb:.1f}MB)")
    else:
        logger.error(f"[COMPOSITOR] Output file missing or empty: {output_path}")
    return success


def _get_video_duration(path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _has_audio_stream(path: str) -> bool:
    """Return True if the media file contains at least one audio stream."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _run_ffmpeg(cmd: list, label: str, timeout: int = 120) -> bool:
    """Helper that runs an FFmpeg command and logs the result."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error(
                f"[COMPOSITOR] FFmpeg {label} failed (rc={result.returncode}): "
                f"{result.stderr[-600:]}"
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"[COMPOSITOR] FFmpeg {label} timed out (>{timeout}s).")
        return False
    except Exception as e:
        logger.error(f"[COMPOSITOR] FFmpeg {label} exception: {e}")
        return False


# ── PiP Layout (DEFAULT) ──────────────────────────────────────────────────────

def _pip_position(pip_w: int, pip_h: int) -> str:
    """
    Return FFmpeg overlay position string for the PiP.
    'W' = background width, 'w' = overlay width.
    'H' = background height, 'h' = overlay height.
    """
    margin = _PIP_MARGIN
    if _PIP_CORNER == "top_left":
        return f"{margin}:{margin}"
    elif _PIP_CORNER == "top_center":
        return f"(W-w)/2:{margin}"
    else:  # top_right (default)
        return f"W-w-{margin}:{margin}"


def _build_pip(
    source_video: str,
    reactor_video: str,
    reactor_audio: Optional[str],
    output_path: str,
    audio_schedule: list = None,
) -> None:
    """
    PiP overlay: reactor face floats in top-right corner over full-screen source.

    Source video is 1080×1920, completely untouched in composition.
    Reactor PiP is _PIP_SIZE × (_PIP_SIZE * 16/9) portrait, placed at corner.

    Audio: source audio is the base. If reactor_audio (narration TTS) is provided,
    it is mixed in. Source audio is ducked while narrator speaks.
    """
    pip_w  = _PIP_SIZE - (_PIP_SIZE % 2)           # ensure even
    pip_h  = int(pip_w * 16 / 9)                   # portrait aspect (9:16)
    pip_h  = pip_h - (pip_h % 2)
    pos    = _pip_position(pip_w, pip_h)

    # Scale source to full output canvas (cover-fill, no black bars, then crop centre)
    vf_source = (
        f"[0:v]scale={_OUT_W}:{_OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={_OUT_W}:{_OUT_H},"
        f"setsar=1,fps=24,format=yuv420p[vsrc]"
    )

    # Scale reactor to PiP — CSS 'object-fit: cover':
    # 1. Scale UP so the SMALLEST dimension fills the PiP box (no black ever touches the box)
    # 2. Crop the overflow to exact pip_w x pip_h
    # This works for ANY source aspect ratio (landscape webcam, portrait phone, etc.)
    vf_reactor = (
        f"[1:v]scale=w={pip_w}:h={pip_h}:force_original_aspect_ratio=increase,"
        f"crop={pip_w}:{pip_h},"
        f"setsar=1,fps=24,format=yuv420p[vrx]"
    )

    # Overlay PiP on top of source
    vf_overlay = f"[vsrc][vrx]overlay={pos}[vout]"

    # Audio: choose between interleaved schedule or simple duck-and-mix
    extra_audio_inputs: list = []

    # Detect base/source audio; inject silent track if missing so FFmpeg graph never fails.
    has_src_audio = _has_audio_stream(source_video)
    base_audio_idx = 0
    if not has_src_audio:
        logger.warning("[COMPOSITOR] Source has no audio — injecting silent bed (anullsrc).")
        extra_audio_inputs.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])
        base_audio_idx = 2  # after source (0) and reactor video (1)

    reactor_audio_idx = None

    if audio_schedule:
        _pre_audio_inputs = list(extra_audio_inputs)
        # Import here to avoid circular dependency at module level
        try:
            from Reaction_Engine.audio_interleave_scheduler import build_interleaved_audio_filter
            extra_audio_inputs, af, audio_map = build_interleaved_audio_filter(
                schedule=audio_schedule,
                source_audio_idx=base_audio_idx,
                duck_level=_DUCK_LEVEL,
            )
            extra_audio_inputs = _pre_audio_inputs + extra_audio_inputs
            logger.info("[COMPOSITOR] Using INTERLEAVED audio schedule.")
        except Exception as e:
            logger.warning(f"[COMPOSITOR] Interleave filter failed ({e}) — falling back to simple mix.")
            audio_schedule = None  # trigger fallback below

    if not audio_schedule:
        use_clip_audio = os.getenv("REACTION_USE_CLIP_AUDIO", "no").lower() in ("yes", "true", "1")
        dynamic_duck = os.getenv("REACTION_DYNAMIC_DUCK", "no").lower() in ("yes", "true", "1")
        
        if reactor_audio and os.path.isfile(reactor_audio):
            reactor_audio_idx = 2 if has_src_audio else 3
            if dynamic_duck:
                af = (
                    f"[{reactor_audio_idx}:a]asplit=2[arx_duck][arx_mix];"
                    f"[{base_audio_idx}:a][arx_duck]sidechaincompress=threshold=0.015:ratio=4:attack=50:release=300[aduck];"
                    f"[aduck][arx_mix]amix=inputs=2:duration=first:dropout_transition=1[aout]"
                )
            elif _DUCK_SOURCE:
                af = (
                    f"[{base_audio_idx}:a]volume={_DUCK_LEVEL}[aduck];"
                    f"[{reactor_audio_idx}:a]volume=2.0[arx];"
                    f"[aduck][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
                )
            else:
                af = (
                    f"[{base_audio_idx}:a]volume=1.0[asrc];"
                    f"[{reactor_audio_idx}:a]volume=2.0[arx];"
                    f"[asrc][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
                )
            extra_audio_inputs += ["-stream_loop", "-1", "-i", reactor_audio]
            audio_map = ["-map", "[aout]"]
        elif use_clip_audio:
            if dynamic_duck:
                af = (
                    f"[1:a]asplit=2[arx_duck][arx_mix];"
                    f"[{base_audio_idx}:a][arx_duck]sidechaincompress=threshold=0.015:ratio=4:attack=50:release=300[aduck];"
                    f"[aduck][arx_mix]amix=inputs=2:duration=first:dropout_transition=1[aout]"
                )
            elif _DUCK_SOURCE:
                af = (
                    f"[{base_audio_idx}:a]volume={_DUCK_LEVEL}[aduck];"
                    f"[1:a]volume=2.0[arx];"
                    f"[aduck][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
                )
            else:
                af = (
                    f"[{base_audio_idx}:a]volume=1.0[asrc];"
                    f"[1:a]volume=2.0[arx];"
                    f"[asrc][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
                )
            audio_map = ["-map", "[aout]"]
        else:
            af = f"[{base_audio_idx}:a]volume=1.0[aout]"
            audio_map = ["-map", "[aout]"]

    filter_complex = f"{vf_source};{vf_reactor};{vf_overlay};{af}"

    source_duration = _get_video_duration(source_video)
    duration_args   = ["-t", str(source_duration)] if source_duration > 0 else []
    if source_duration > 0:
        logger.info(f"[COMPOSITOR] PiP: source={source_duration:.2f}s | pip={pip_w}x{pip_h} @ {_PIP_CORNER}")

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", source_video,
        "-stream_loop", "-1",
        "-i", reactor_video,
    ] + extra_audio_inputs + duration_args + [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
    ] + audio_map + [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]

    _run_ffmpeg(cmd, "pip_compositor", timeout=180)

def _build_stacked(
    source_video, reactor_video, reactor_audio,
    output_path, src_w, src_h, rx_w, rx_h,
    reaction_start_ts, reaction_duration,
):
    """
    Build stacked (9:16) layout:
        [reactor_face | top    rx_h  px]  ← reactor cam on top
        [source_reel  | bottom src_h px]  ← main content on bottom

    The reactor clip is looped to fill the full source duration.
    Audio: source at full volume + reactor audio (ducked if REACTION_DUCK_SOURCE_AUDIO=yes).
    """
    # Build filter_complex
    # Input 0: source video
    # Input 1: reactor video (looped)
    # Input 2: reactor audio (if provided)

    # Scale source to fit BOTTOM panel
    vf_source = (
        f"[0:v]scale={src_w}:{src_h}:force_original_aspect_ratio=decrease,"
        f"pad={src_w}:{src_h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=30,format=yuv420p[vsrc]"
    )

    # Scale reactor to fit TOP panel (loop with stream_loop)
    vf_reactor = (
        f"[1:v]scale={rx_w}:{rx_h}:force_original_aspect_ratio=decrease,"
        f"pad={rx_w}:{rx_h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=30,format=yuv420p[vrx]"
    )

    # Stack: reactor TOP, source BOTTOM
    vf_stack = "[vrx][vsrc]vstack=inputs=2[vout]"

    # Audio mixing (robust to missing source audio)
    has_src_audio = _has_audio_stream(source_video)
    base_audio_idx = 0
    audio_input: list = []
    if not has_src_audio:
        logger.warning("[COMPOSITOR] Source has no audio — injecting silent bed (anullsrc).")
        audio_input.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])
        base_audio_idx = 2  # after source and reactor video inputs

    use_clip_audio = os.getenv("REACTION_USE_CLIP_AUDIO", "no").lower() in ("yes", "true", "1")
    dynamic_duck = os.getenv("REACTION_DYNAMIC_DUCK", "no").lower() in ("yes", "true", "1")

    if reactor_audio and os.path.isfile(reactor_audio):
        reactor_audio_idx = 2 if has_src_audio else 3
        if dynamic_duck:
            af = (
                f"[{reactor_audio_idx}:a]asplit=2[arx_duck][arx_mix];"
                f"[{base_audio_idx}:a][arx_duck]sidechaincompress=threshold=0.015:ratio=4:attack=50:release=300[aduck];"
                f"[aduck][arx_mix]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        elif _DUCK_SOURCE:
            af = (
                f"[{base_audio_idx}:a]volume={_DUCK_LEVEL}[aduck];"
                f"[{reactor_audio_idx}:a]volume=2.0[arx];"
                f"[aduck][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        else:
            af = (
                f"[{base_audio_idx}:a]volume=1.0[asrc];"
                f"[{reactor_audio_idx}:a]volume=2.0[arx];"
                f"[asrc][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        audio_input += ["-stream_loop", "-1", "-i", reactor_audio]
        audio_map = ["-map", "[aout]"]
    elif use_clip_audio:
        if dynamic_duck:
            af = (
                f"[1:a]asplit=2[arx_duck][arx_mix];"
                f"[{base_audio_idx}:a][arx_duck]sidechaincompress=threshold=0.015:ratio=4:attack=50:release=300[aduck];"
                f"[aduck][arx_mix]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        elif _DUCK_SOURCE:
            af = (
                f"[{base_audio_idx}:a]volume={_DUCK_LEVEL}[aduck];"
                f"[1:a]volume=2.0[arx];"
                f"[aduck][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        else:
            af = (
                f"[{base_audio_idx}:a]volume=1.0[asrc];"
                f"[1:a]volume=2.0[arx];"
                f"[asrc][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        audio_map = ["-map", "[aout]"]
    else:
        af = f"[{base_audio_idx}:a]volume=1.0[aout]"
        audio_map = ["-map", "[aout]"]

    filter_complex = f"{vf_source};{vf_reactor};{vf_stack};{af}"

    # Determine source duration to cap output length (prevents infinite loop hang)
    source_duration = _get_video_duration(source_video)
    duration_args = ["-t", str(source_duration)] if source_duration > 0 else []
    if source_duration > 0:
        logger.info(f"[COMPOSITOR] Source duration={source_duration:.2f}s — capping output.")

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", source_video,          # input 0: source (plays ONCE, no loop)
        "-stream_loop", "-1",        # loop reactor to match source length
        "-i", reactor_video,
    ] + audio_input + duration_args + [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
    ] + audio_map + [
        "-c:v", "libx264",
        "-preset", "ultrafast",      # faster for reaction clips
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]

    _run_ffmpeg(cmd, "stacked_compositor", timeout=180)


def _build_side_by_side(
    source_video, reactor_video, reactor_audio,
    output_path, src_w, src_h, rx_w, rx_h,
):
    """
    Build side-by-side (landscape) layout:
        [source_reel left] [reactor_face right]
    """
    vf_source  = (
        f"[0:v]scale={src_w}:{src_h}:force_original_aspect_ratio=decrease,"
        f"pad={src_w}:{src_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30[vsrc]"
    )
    vf_reactor = (
        f"[1:v]scale={rx_w}:{rx_h}:force_original_aspect_ratio=decrease,"
        f"pad={rx_w}:{rx_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30[vrx]"
    )
    vf_hstack  = "[vsrc][vrx]hstack=inputs=2[vout]"

    has_src_audio = _has_audio_stream(source_video)
    base_audio_idx = 0
    audio_input: list = []
    if not has_src_audio:
        logger.warning("[COMPOSITOR] Source has no audio — injecting silent bed (anullsrc).")
        audio_input.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])
        base_audio_idx = 2

    use_clip_audio = os.getenv("REACTION_USE_CLIP_AUDIO", "no").lower() in ("yes", "true", "1")
    dynamic_duck = os.getenv("REACTION_DYNAMIC_DUCK", "no").lower() in ("yes", "true", "1")

    if reactor_audio and os.path.isfile(reactor_audio):
        reactor_audio_idx = 2 if has_src_audio else 3
        if dynamic_duck:
            af = (
                f"[{reactor_audio_idx}:a]asplit=2[arx_duck][arx_mix];"
                f"[{base_audio_idx}:a][arx_duck]sidechaincompress=threshold=0.015:ratio=4:attack=50:release=300[aduck];"
                f"[aduck][arx_mix]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        else:
            af = (
                f"[{base_audio_idx}:a]volume={_DUCK_LEVEL if _DUCK_SOURCE else 1.0}[asrc];"
                f"[{reactor_audio_idx}:a]volume=2.0[arx];"
                f"[asrc][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        audio_input += ["-stream_loop", "-1", "-i", reactor_audio]
        audio_map   = ["-map", "[aout]"]
    elif use_clip_audio:
        if dynamic_duck:
            af = (
                f"[1:a]asplit=2[arx_duck][arx_mix];"
                f"[{base_audio_idx}:a][arx_duck]sidechaincompress=threshold=0.015:ratio=4:attack=50:release=300[aduck];"
                f"[aduck][arx_mix]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        else:
            af = (
                f"[{base_audio_idx}:a]volume={_DUCK_LEVEL if _DUCK_SOURCE else 1.0}[asrc];"
                f"[1:a]volume=2.0[arx];"
                f"[asrc][arx]amix=inputs=2:duration=first:dropout_transition=1[aout]"
            )
        audio_map   = ["-map", "[aout]"]
    else:
        af = f"[{base_audio_idx}:a]volume=1.0[aout]"
        audio_map   = ["-map", "[aout]"]

    filter_complex = f"{vf_source};{vf_reactor};{vf_hstack};{af}"

    source_duration = _get_video_duration(source_video)
    duration_args = ["-t", str(source_duration)] if source_duration > 0 else []

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", source_video,
        "-stream_loop", "-1",
        "-i", reactor_video,
    ] + audio_input + duration_args + [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
    ] + audio_map + [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]

    _run_ffmpeg(cmd, "side_by_side_compositor", timeout=180)
