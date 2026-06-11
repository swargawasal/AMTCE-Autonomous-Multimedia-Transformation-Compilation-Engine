"""
ceie/tools/effects/shake.py
---------------------------
Implements a camera shake effect using dynamic crop and translate math in FFmpeg.
"""

import subprocess
import logging

logger = logging.getLogger("ceie.tools.effects.shake")

def apply_shake(video_path: str, output_path: str, start_sec: float, duration_sec: float, intensity: float = 1.0) -> bool:
    """
    Applies a dynamic camera shake effect for the specified interval.
    intensity: scales the pixel offsets of the shake (default 1.0 -> 15px max offset)
    """
    end_sec = start_sec + duration_sec
    amplitude = int(15 * intensity)
    
    # FFmpeg filter: Crop slightly, then translate coordinates with high-frequency sine/cosine waves
    shake_filter = (
        f"crop=w=iw-40:h=ih-40:"
        f"x='(iw-ow)/2+{amplitude}*sin(2*PI*t*12)':"
        f"y='(ih-oh)/2+{amplitude}*cos(2*PI*t*15)'"
    )
    
    filter_complex = f"[0:v]{shake_filter}[shaken]; [0:v][shaken]overlay=enable='between(t,{start_sec:.3f},{end_sec:.3f})'[vout]"
    
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
        logger.error(f"Failed to apply shake effect: {e.stderr.decode()}")
        return False
