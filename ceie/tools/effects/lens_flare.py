"""
ceie/tools/effects/lens_flare.py
--------------------------------
Implements a warm lens flare / light leak overlay effect procedurally in FFmpeg.
"""

import subprocess
import logging

logger = logging.getLogger("ceie.tools.effects.lens_flare")

def apply_lens_flare(video_path: str, output_path: str, start_sec: float, duration_sec: float, color: str = "orange@0.25") -> bool:
    """
    Applies a warm, glowing light-leak / lens flare overlay effect.
    """
    end_sec = start_sec + duration_sec
    
    # FFmpeg filter: Generate a warm color layer, apply vignette to soft-edge it, and blend/overlay it
    filter_complex = (
        f"color=c={color}:s=1080x1920:d={duration_sec:.3f},vignette=PI/4[flare]; "
        f"[0:v][flare]overlay=x=0:y=0:enable='between(t,{start_sec:.3f},{end_sec:.3f})'[vout]"
    )
    
    # Check if video has audio
    try:
        has_audio = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path]
        ).decode().strip() != ""
    except:
        has_audio = False
        
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]"
    ]
    if has_audio:
        cmd += ["-map", "0:a", "-c:a", "copy"]
        
    cmd += [
        "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to apply lens flare effect: {e.stderr.decode()}")
        return False
