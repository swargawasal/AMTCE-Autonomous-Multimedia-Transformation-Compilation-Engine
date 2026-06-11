"""
ceie/tools/speed_applier.py
--------------------------
Applies speed ramping (time remapping) to specific segments of the video.
Partitions the video timeline, applies setpts (video) and atempo (audio) filters,
and stitches the segments back together.
"""

import os
import subprocess
import logging
from typing import List, Dict, Any

logger = logging.getLogger("ceie.tools.speed_applier")

def get_video_duration(path: str) -> float:
    """Gets total duration via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to get duration for {path}: {e}")
        return 0.0

def build_atempo_filter(factor: float) -> str:
    """Builds a chain of atempo filters since FFmpeg atempo only supports 0.5 to 2.0."""
    spd = factor
    parts = []
    while spd > 2.0:
        parts.append("atempo=2.0")
        spd /= 2.0
    while spd < 0.5:
        parts.append("atempo=0.5")
        spd /= 0.5
    parts.append(f"atempo={spd:.4f}")
    return ",".join(parts)

def apply_speed_ramps(video_path: str, output_path: str, ramps: List[Dict[str, Any]]) -> bool:
    """
    Partitions the video timeline into speed-ramped and normal segments,
    applies FFmpeg speed filters, and stitches them.
    """
    if not ramps:
        # Just copy video
        import shutil
        shutil.copy(video_path, output_path)
        return True
        
    duration = get_video_duration(video_path)
    if duration <= 0:
        logger.error("Could not determine video duration for speed ramping.")
        return False
        
    logger.info(f"Applying {len(ramps)} speed ramps to video of duration {duration:.2f}s")
    
    # 1. Sort and clean ramps, merging overlapping ones (first one wins)
    sorted_ramps = sorted(ramps, key=lambda x: x["start_sec"])
    cleaned_ramps = []
    
    for r in sorted_ramps:
        r_start = max(0.0, float(r["start_sec"]))
        r_end = min(duration, float(r["end_sec"]))
        factor = float(r.get("factor", 1.0))
        
        if r_start >= r_end or abs(factor - 1.0) < 0.05:
            continue
            
        # Check overlap with existing cleaned ramps
        overlap = False
        for cr in cleaned_ramps:
            if r_start < cr["end"] and r_end > cr["start"]:
                overlap = True
                break
        if not overlap:
            cleaned_ramps.append({
                "start": r_start,
                "end": r_end,
                "factor": factor
            })
            
    if not cleaned_ramps:
        logger.info("No active speed ramps to apply. Copying video.")
        import shutil
        shutil.copy(video_path, output_path)
        return True
        
    # 2. Partition timeline into sub-segments
    partitions = []
    last_t = 0.0
    
    for r in cleaned_ramps:
        if r["start"] > last_t + 0.05:
            partitions.append({
                "start": last_t,
                "end": r["start"],
                "factor": 1.0
            })
        partitions.append(r)
        last_t = r["end"]
        
    if last_t < duration - 0.05:
        partitions.append({
            "start": last_t,
            "end": duration,
            "factor": 1.0
        })
        
    # 3. Build FFmpeg filter complex
    filter_complex = ""
    concat_video = ""
    concat_audio = ""
    
    # Check if video has audio
    try:
        has_audio = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path]
        ).decode().strip() != ""
    except:
        has_audio = False
        
    for i, part in enumerate(partitions):
        start = part["start"]
        end = part["end"]
        factor = part["factor"]
        
        # Video stream trim + speed
        video_speed = 1.0 / factor
        filter_complex += f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts='{video_speed:.6f}*(PTS-STARTPTS)'[v{i}]; "
        concat_video += f"[v{i}]"
        
        if has_audio:
            # Audio stream trim + speed
            atempo_filter = build_atempo_filter(factor)
            filter_complex += f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts='PTS-STARTPTS',{atempo_filter}[a{i}]; "
            concat_audio += f"[a{i}]"
            
    # Concat filters
    filter_complex += f"{concat_video}concat=n={len(partitions)}:v=1:a=0[vout]"
    if has_audio:
        filter_complex += f"; {concat_audio}concat=n={len(partitions)}:v=0:a=1[aout]"
        
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]"
    ]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        
    cmd += [
        "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info("✅ Speed ramping applied successfully.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed applying speed ramps: {e.stderr.decode()}")
        return False
