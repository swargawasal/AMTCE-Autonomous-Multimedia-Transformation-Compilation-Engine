"""
Reaction_Engine/audio_interleave_scheduler.py
----------------------------------------------
Interleaved Audio Scheduler — treats the Reactor and Narrator as a
two-person conversation where only ONE speaks at a time.

Instead of dumping the full narration audio track over the top of
reactor speech (causing cognitive overload + viewer scroll-away),
this module:

  1. Maps all reactor "busy" windows from reaction_lines timestamps.
  2. Finds the silence gaps between those windows.
  3. Slices the narration TTS audio into chunks that fill those gaps.
  4. Returns a unified schedule consumed by reaction_compositor.py.

.env flags:
    ENABLE_AUDIO_INTERLEAVE=yes  (default: yes)
    NARR_REACTOR_GAP=0.3         silence buffer around reactor windows (s)
    NARR_MIN_SLOT=1.0            minimum gap duration for a narration chunk (s)
"""

import os
import logging
import subprocess
import tempfile
import uuid
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("audio_interleave_scheduler")

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
_REACTOR_GAP = float(os.getenv("NARR_REACTOR_GAP", "0.3"))
_MIN_SLOT     = float(os.getenv("NARR_MIN_SLOT", "1.0"))


def is_interleave_enabled() -> bool:
    return os.getenv("ENABLE_AUDIO_INTERLEAVE", "yes").lower() in ("yes", "true", "1")


def _get_audio_duration(audio_path: str) -> float:
    """Get duration of an audio file using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _find_narration_slots(
    reaction_lines: List[Dict],
    video_duration: float,
    gap: float = _REACTOR_GAP,
    min_slot: float = _MIN_SLOT,
) -> List[Tuple[float, float]]:
    """
    Find the silence windows between reactor speaking moments.
    Each slot is a (start, end) tuple in seconds.

    The reactor is treated as occupying [ts - gap, ts + duration + gap]
    to leave breathing room around each spoken line.
    """
    if not reaction_lines:
        # No reactor speech — full video is a valid narration slot
        return [(0.0, video_duration)]

    # Build and sort busy windows
    busy_windows = []
    for line in reaction_lines:
        ts = float(line.get("ts", line.get("time", 0.0)))
        dur = float(line.get("duration", 2.0))
        busy_start = max(0.0, ts - gap)
        busy_end = min(video_duration, ts + dur + gap)
        busy_windows.append((busy_start, busy_end))

    # Merge overlapping windows (important when gap causes two windows to touch)
    busy_windows.sort(key=lambda w: w[0])
    merged = [busy_windows[0]]
    for b_start, b_end in busy_windows[1:]:
        if b_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b_end))
        else:
            merged.append((b_start, b_end))

    # Narration slots are the gaps BETWEEN busy windows
    slots = []
    cursor = 0.0
    for b_start, b_end in merged:
        if b_start - cursor >= min_slot:
            slots.append((round(cursor, 3), round(b_start, 3)))
        cursor = b_end

    # Slot after last reactor window
    if video_duration - cursor >= min_slot:
        slots.append((round(cursor, 3), round(video_duration, 3)))

    logger.info(
        f"[INTERLEAVE] Found {len(slots)} narration slot(s) "
        f"from {len(reaction_lines)} reactor windows. "
        f"Slots: {[(round(s,1), round(e,1)) for s,e in slots]}"
    )
    return slots


def _slice_audio_segment(
    audio_path: str,
    src_start: float,
    src_end: float,
    output_path: str,
) -> bool:
    """Extract a slice [src_start, src_end] from audio_path to output_path."""
    duration = max(0.0, src_end - src_start)
    if duration <= 0.01:
        return False
    cmd = [
        FFMPEG_BIN, "-y",
        "-ss", str(src_start),
        "-t", str(duration),
        "-i", audio_path,
        "-c:a", "aac", "-b:a", "192k",
        output_path
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, timeout=60
        )
        return result.returncode == 0 and os.path.isfile(output_path)
    except Exception as e:
        logger.error(f"[INTERLEAVE] Slice failed: {e}")
        return False


def build_interleaved_schedule(
    reaction_lines: List[Dict],
    narration_audio: Optional[str],
    video_duration: float,
    job_dir: str,
) -> List[Dict]:
    """
    Build a unified audio schedule where reactor and narrator never overlap.

    Returns a list of schedule items:
        {
            "audio_path": str,       # path to audio chunk
            "start_ts":   float,     # when to start playing in the output video (seconds)
            "end_ts":     float,     # when it ends
            "track":      str,       # "reactor" | "narration"
            "duration":   float,
        }

    The caller (reaction_compositor.py) uses this schedule to build an
    FFmpeg filter chain that places each chunk at the correct timestamp
    with silence between them.
    """
    schedule: List[Dict] = []

    # ── Reactor items (always present — their timestamps are already final) ──
    for line in reaction_lines:
        ts  = float(line.get("ts", line.get("time", 0.0)))
        dur = float(line.get("duration", 2.0))
        # Reactor audio is embedded in the reactor_reel video — no separate file needed
        schedule.append({
            "audio_path": None,   # placeholder — handled by reactor_reel
            "start_ts":   ts,
            "end_ts":     min(video_duration, ts + dur),
            "duration":   dur,
            "track":      "reactor",
            "text":       line.get("text", ""),
        })

    # ── Narration items (sliced into gap slots) ──────────────────────────────
    if not narration_audio or not os.path.isfile(narration_audio):
        logger.info("[INTERLEAVE] No narration audio — schedule is reactor-only.")
        return sorted(schedule, key=lambda x: x["start_ts"])

    narr_duration = _get_audio_duration(narration_audio)
    if narr_duration <= 0.0:
        logger.warning("[INTERLEAVE] Could not read narration duration.")
        return sorted(schedule, key=lambda x: x["start_ts"])

    slots = _find_narration_slots(reaction_lines, video_duration)

    # Distribute narration audio across the available slots
    narr_cursor = 0.0  # position into the narration audio file
    os.makedirs(job_dir, exist_ok=True)

    for slot_start, slot_end in slots:
        if narr_cursor >= narr_duration:
            break  # all narration has been scheduled

        slot_dur     = slot_end - slot_start
        available    = min(slot_dur, narr_duration - narr_cursor)
        if available < 0.3:
            continue

        chunk_path = os.path.join(job_dir, f"narr_chunk_{uuid.uuid4().hex[:6]}.m4a")
        ok = _slice_audio_segment(
            audio_path=narration_audio,
            src_start=narr_cursor,
            src_end=narr_cursor + available,
            output_path=chunk_path,
        )
        if ok:
            schedule.append({
                "audio_path": chunk_path,
                "start_ts":   slot_start,
                "end_ts":     slot_start + available,
                "duration":   available,
                "track":      "narration",
            })
            logger.info(
                f"[INTERLEAVE] Narration chunk: "
                f"video={slot_start:.2f}→{slot_start+available:.2f}s | "
                f"audio={narr_cursor:.2f}→{narr_cursor+available:.2f}s"
            )
            narr_cursor += available

    schedule.sort(key=lambda x: x["start_ts"])
    n_rx = sum(1 for s in schedule if s["track"] == "reactor")
    n_nr = sum(1 for s in schedule if s["track"] == "narration")
    logger.info(
        f"✅ [INTERLEAVE] Schedule complete: {n_rx} reactor + {n_nr} narration items. "
        f"Narration coverage: {narr_cursor:.1f}/{narr_duration:.1f}s"
    )
    return schedule


def build_interleaved_audio_filter(
    schedule: List[Dict],
    source_audio_idx: int = 0,
    first_chunk_idx: int = 2,
    duck_level: float = 0.25,
) -> Tuple[List[str], str, List[str]]:
    """
    Converts the schedule into FFmpeg filter_complex fragments and extra input args.

    Returns:
        (extra_inputs, filter_str, map_args)

    extra_inputs: list of ["-i", path] pairs for narration chunks
    filter_str:   the filter_complex audio section
    map_args:     ["-map", "[aout]"]

    The caller must slot extra_inputs AFTER the video inputs and BEFORE
    encoding flags in the ffmpeg command.

    Audio strategy:
    - Source audio is ducked to duck_level at all times (consistent behaviour).
    - Narration chunks are placed at their exact start_ts using adelay.
    - All streams are mixed with amix.
    """
    narration_chunks = [s for s in schedule if s["track"] == "narration" and s.get("audio_path")]

    if not narration_chunks:
        # Simple: just duck the source
        return [], f"[{source_audio_idx}:a]volume={duck_level}[aout]", ["-map", "[aout]"]

    extra_inputs: List[str] = []
    delays: List[str] = []

    for i, chunk in enumerate(narration_chunks):
        extra_inputs.extend(["-i", chunk["audio_path"]])
        # FFmpeg adelay takes milliseconds
        delay_ms = int(chunk["start_ts"] * 1000)
        stream_idx = i + first_chunk_idx  # index exactly where it appears in command
        delays.append(
            f"[{stream_idx}:a]adelay={delay_ms}|{delay_ms},volume=2.0[nr{i}]"
        )

    n = len(narration_chunks)
    delayed_labels = "".join(f"[nr{i}]" for i in range(n))
    filter_parts = [
        f"[{source_audio_idx}:a]volume={duck_level}[aduck]",
    ] + delays + [
        f"[aduck]{delayed_labels}amix=inputs={n+1}:duration=first:dropout_transition=1[aout]"
    ]

    filter_str = ";".join(filter_parts)
    return extra_inputs, filter_str, ["-map", "[aout]"]
