"""
ceie/chunker.py
---------------
Splits the raw input video into ~60-second clips aligned with shot boundaries.
Utilizes pyscenedetect / OpenCV fallback to find natural split points.
"""

import os
import subprocess
import logging
from typing import List, Dict, Any

from Intelligence_Modules.shot_detector import detect_shots

logger = logging.getLogger("ceie.chunker")

def determine_chunk_boundaries(shots: List[Dict[str, Any]], total_duration: float, target_duration: float = 60.0) -> List[tuple[float, float]]:
    """
    Groups shot boundaries to form chunks close to target_duration.
    If shots list is empty or doesn't cover the video, fallback to uniform splits.
    """
    if not shots:
        # Uniform fallback
        boundaries = []
        curr = 0.0
        while curr < total_duration:
            nxt = min(curr + target_duration, total_duration)
            boundaries.append((curr, nxt))
            curr = nxt
        return boundaries

    # Sort shots chronologically
    sorted_shots = sorted(shots, key=lambda x: x["start"])
    
    # We want to find split points. The start of each chunk will be 0.0 or the end of the previous chunk.
    split_points = [0.0]
    
    current_chunk_dur = 0.0
    for i, shot in enumerate(sorted_shots):
        shot_end = shot["end"]
        current_chunk_dur = shot_end - split_points[-1]
        
        # If the current accumulated duration is close to target_duration
        if current_chunk_dur >= target_duration - 5.0:  # Allow slight early splitting if a shot boundary exists
            # We split at shot_end unless it's too close to the total duration
            if total_duration - shot_end > 10.0:
                split_points.append(shot_end)
                current_chunk_dur = 0.0

    # Ensure the final duration is covered
    if split_points[-1] < total_duration:
        split_points.append(total_duration)
    else:
        split_points[-1] = total_duration

    # Create boundaries
    boundaries = []
    for i in range(len(split_points) - 1):
        boundaries.append((split_points[i], split_points[i+1]))
        
    return boundaries

def get_video_duration(video_path: str) -> float:
    """Gets the total duration of a video using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Error getting duration for {video_path}: {e}")
        # Return fallback value or raise
        raise e

def chunk_video(video_path: str, output_dir: str, target_chunk_duration: float = 60.0) -> List[Dict[str, Any]]:
    """
    Splits the video at video_path into chunks in output_dir.
    Returns metadata for each chunk.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Source video not found: {video_path}")
        
    os.makedirs(output_dir, exist_ok=True)
    
    total_duration = get_video_duration(video_path)
    logger.info(f"Analyzing shot boundaries for video: {video_path} (Duration: {total_duration:.2f}s)")
    
    try:
        shots = detect_shots(video_path)
    except Exception as e:
        logger.warning(f"Shot detection failed, falling back to uniform chunks: {e}")
        shots = []
        
    boundaries = determine_chunk_boundaries(shots, total_duration, target_chunk_duration)
    logger.info(f"Determined {len(boundaries)} chunks based on shot boundaries: {boundaries}")
    
    chunks = []
    for idx, (start, end) in enumerate(boundaries):
        chunk_name = f"chunk_{idx:03d}_{start:.1f}_{end:.1f}.mp4"
        chunk_path = os.path.join(output_dir, chunk_name)
        
        duration = end - start
        logger.info(f"Cutting chunk #{idx}: {start:.2f}s to {end:.2f}s (duration: {duration:.2f}s)")
        
        # Use precise FFmpeg cutting (transcoding to ensure keyframe alignment)
        # Avoid copy-mode because it causes visual glitches at cut points
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-i", video_path,
            "-c:v", "libx264",
            "-preset", "superfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            chunk_path
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            chunks.append({
                "chunk_index": idx,
                "video_path": chunk_path,
                "start_sec": start,
                "end_sec": end,
                "duration": duration
            })
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed slicing chunk #{idx}: {e.stderr.decode()}")
            raise RuntimeError(f"FFmpeg chunk slicing failed: {e}")
            
    return chunks
