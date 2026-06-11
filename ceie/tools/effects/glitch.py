"""
ceie/tools/effects/glitch.py
----------------------------
Implements an RGB split glitch effect via FFmpeg.
Splits color channels, shifts them temporally/spacially, and merges them.
"""

import subprocess
import logging

logger = logging.getLogger("ceie.tools.effects.glitch")

def apply_glitch(video_path: str, output_path: str, start_sec: float, duration_sec: float) -> bool:
    """
    Applies an RGB split glitch effect for the specified interval.
    """
    end_sec = start_sec + duration_sec
    
    # FFmpeg filter: Split video into three streams, filter to R, G, B, shift green/blue, and screen merge them back
    filter_complex = (
        f"[0:v]split=3[vr][vg][vb]; "
        f"[vr]colorchannelmixer=rr=1:rg=0:rb=0:gr=0:gg=0:gb=0:br=0:bg=0:bb=0[red]; "
        f"[vg]colorchannelmixer=rr=0:rg=0:rb=0:gr=0:gg=1:gb=0:br=0:bg=0:bb=0,crop=iw-10:ih-10:10:10,scale=iw+10:ih+10[green]; "
        f"[vb]colorchannelmixer=rr=0:rg=0:rb=0:gr=0:gg=0:gb=1:br=0:bg=0:bb=0,crop=iw-15:ih-15:0:0,scale=iw+15:ih+15[blue]; "
        f"[red][green]blend=all_mode='screen'[rg]; "
        f"[rg][blue]blend=all_mode='screen'[glitched]; "
        f"[0:v][glitched]overlay=enable='between(t,{start_sec:.3f},{end_sec:.3f})'[vout]"
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
        logger.error(f"Failed to apply glitch effect: {e.stderr.decode()}")
        return False
