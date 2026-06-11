"""
ceie/tools/cutter.py
--------------------
Adapter for Video_Modules/timeline_cutter.
Converts a list of trims (intervals to remove) into keep ranges (intervals to keep)
and extracts them as separate video clips.
"""

import os
import subprocess
import logging
from typing import List, Dict, Any

logger = logging.getLogger("ceie.tools.cutter")

def trims_to_keep_ranges(total_duration: float, trims: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    """
    Inverts a list of trim ranges (which are excluded) into keep ranges (which are included).
    Merges overlapping trims and clamps boundaries between 0.0 and total_duration.
    """
    if not trims:
        return [{"start": 0.0, "end": total_duration}]
        
    # Sort trims chronologically
    sorted_trims = sorted(trims, key=lambda x: x["start_sec"])
    
    # Merge overlapping or touching trim intervals
    merged_trims = []
    for trim in sorted_trims:
        t_start = max(0.0, float(trim["start_sec"]))
        t_end = min(total_duration, float(trim["end_sec"]))
        
        if t_start >= t_end:
            continue
            
        if not merged_trims:
            merged_trims.append([t_start, t_end])
        else:
            prev_start, prev_end = merged_trims[-1]
            if t_start <= prev_end:
                merged_trims[-1][1] = max(prev_end, t_end)
            else:
                merged_trims.append([t_start, t_end])
                
    # Generate keep ranges
    keep_ranges = []
    last_end = 0.0
    for t_start, t_end in merged_trims:
        if t_start > last_end + 0.05:  # Keep segments greater than 50ms
            keep_ranges.append({"start": last_end, "end": t_start})
        last_end = t_end
        
    if last_end < total_duration - 0.05:
        keep_ranges.append({"start": last_end, "end": total_duration})
        
    if not keep_ranges:
        logger.warning("Trims covered the entire video! Defaulting to keeping the whole video.")
        keep_ranges.append({"start": 0.0, "end": total_duration})
        
    return keep_ranges

def extract_keep_segments(video_path: str, keep_ranges: List[Dict[str, float]], temp_dir: str) -> List[str]:
    """
    Extracts each keep range from the video into a separate mp4 file in temp_dir.
    Returns a list of paths to the extracted clips.
    """
    os.makedirs(temp_dir, exist_ok=True)
    clips = []
    
    for idx, r in enumerate(keep_ranges):
        start = r["start"]
        end = r["end"]
        duration = end - start
        
        clip_path = os.path.join(temp_dir, f"keep_seg_{idx:03d}.mp4")
        logger.info(f"Extracting sub-clip #{idx}: {start:.2f}s to {end:.2f}s (duration: {duration:.2f}s)")
        
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-i", video_path,
            "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            clip_path
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            clips.append(clip_path)
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed extracting sub-clip #{idx}: {e.stderr.decode()}")
            raise RuntimeError(f"FFmpeg sub-clip extraction failed: {e}")
            
    return clips
