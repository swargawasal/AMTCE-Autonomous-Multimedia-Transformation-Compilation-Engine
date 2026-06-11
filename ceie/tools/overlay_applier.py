"""
ceie/tools/overlay_applier.py
----------------------------
Adapter for Compiler_Modules/overlay_engine.
Applies text overlays (titles, lower thirds, analysis notes) to the video using FFmpeg.
"""

import os
import subprocess
import logging
from typing import List, Dict, Any

from Compiler_Modules.overlay_engine import engine as overlay_engine

logger = logging.getLogger("ceie.tools.overlay_applier")

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

def apply_overlays(video_path: str, output_path: str, overlays: List[Dict[str, Any]]) -> bool:
    """
    Translates TextOverlay schema instances to the overlay engine format,
    generates drawtext filter graphs, and applies them to video_path.
    """
    if not overlays:
        # Just copy file if no overlays
        import shutil
        shutil.copy(video_path, output_path)
        return True
        
    duration = get_video_duration(video_path)
    logger.info(f"Applying {len(overlays)} text overlays over video of duration {duration:.2f}s")
    
    # Map the model overlays to the dictionary format expected by OverlayEngine
    mapped_overlays = []
    for o in overlays:
        mapped_overlays.append({
            "text": o["text"],
            "lane": o["lane"],
            "start": float(o["at_sec"]),
            "duration": float(o["duration_sec"])
        })
        
    # Generate the stack filters
    filter_complex = overlay_engine.generate_stack_filter(mapped_overlays, duration)
    
    if not filter_complex:
        logger.info("No drawtext filters generated, copying source.")
        import shutil
        shutil.copy(video_path, output_path)
        return True
        
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", filter_complex,
        "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
        "-c:a", "copy",
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"✅ Text overlays successfully applied -> {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed applying overlays: {e.stderr.decode()}")
        return False
