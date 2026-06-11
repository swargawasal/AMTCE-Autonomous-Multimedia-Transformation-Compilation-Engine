"""
ceie/tools/transition_applier.py
--------------------------------
Orchestrates transition application between sequential video segments.
Matches transitions from the plan to segment boundaries, applying xfade or smart engine.
Supports custom glitch, camera shake, and lens flare effects at boundaries.
"""

import os
import subprocess
import logging
from typing import List, Dict, Any

from ceie.tools.xfade_transitions import engine as xfade_engine
from ceie.tools.effects.glitch import apply_glitch
from ceie.tools.effects.shake import apply_shake
from ceie.tools.effects.lens_flare import apply_lens_flare

logger = logging.getLogger("ceie.tools.transition_applier")

# Smart transition filter maps (FFmpeg filters to apply to boundaries)
SMART_FILTERS = {
    "whip_pan": "boxblur=lr=12:lp=1:cr=12:cp=1",
    "blur_cut": "boxblur=lr=5:lp=1:cr=5:cp=1",
    "punch_cut": "eq=contrast=1.8:saturation=1.3",
    "zoom_pop": "eq=contrast=1.6:saturation=1.2",
    "glitch_pop": "colorchannelmixer=rr=2.0:gg=2.0:bb=2.0,format=yuv420p",
    "glow_fade": "boxblur=lr=8:lp=1:cr=8:cp=1,eq=brightness=0.12",
    "slow_fade": "boxblur=lr=16:lp=2:cr=16:cp=2",
    "dip_black": "eq=brightness=-1.0"
}

def get_clip_duration(path: str) -> float:
    """Uses ffprobe to get video duration."""
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

def apply_smart_transition(clip_a: str, clip_b: str, trans_type: str, duration_sec: float, output_path: str) -> bool:
    """
    Applies a local filter transition (e.g. whip_pan) to the boundary of clip_a and clip_b.
    The filter is enabled at the end of clip_a and start of clip_b, then they are concatenated.
    """
    dur_a = get_clip_duration(clip_a)
    
    # Define temporary files for processed clips
    temp_dir = os.path.dirname(output_path)
    temp_a = os.path.join(temp_dir, f"temp_trans_a_{os.path.basename(clip_a)}")
    temp_b = os.path.join(temp_dir, f"temp_trans_b_{os.path.basename(clip_b)}")
    
    filter_expr = SMART_FILTERS.get(trans_type, "null")
    
    # 1. Process Clip A (end of clip)
    trans_start_a = max(0.0, dur_a - duration_sec)
    vf_a = f"{filter_expr}:enable='gt(t,{trans_start_a:.3f})'" if filter_expr != "null" else "null"
    cmd_a = [
        "ffmpeg", "-y",
        "-i", clip_a,
        "-vf", vf_a,
        "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
        "-c:a", "copy",
        temp_a
    ]
    
    # 2. Process Clip B (start of clip)
    vf_b = f"{filter_expr}:enable='lt(t,{duration_sec:.3f})'" if filter_expr != "null" else "null"
    cmd_b = [
        "ffmpeg", "-y",
        "-i", clip_b,
        "-vf", vf_b,
        "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
        "-c:a", "copy",
        temp_b
    ]
    
    try:
        subprocess.run(cmd_a, check=True, capture_output=True)
        subprocess.run(cmd_b, check=True, capture_output=True)
        
        # 3. Concatenate processed Clip A and Clip B
        cmd_concat = [
            "ffmpeg", "-y",
            "-i", temp_a,
            "-i", temp_b,
            "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            output_path
        ]
        subprocess.run(cmd_concat, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to apply smart transition {trans_type}: {e.stderr.decode()}")
        return False
    finally:
        # Cleanup temp files
        for f in [temp_a, temp_b]:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

def apply_transitions_sequential(input_clips: List[str], gap_transitions: List[Dict[str, Any]], output_path: str) -> bool:
    """
    Sequentially stitches a list of clips together applying transitions at each gap.
    """
    if not input_clips:
        return False
    if len(input_clips) == 1:
        # Just copy the single clip to output
        import shutil
        shutil.copy(input_clips[0], output_path)
        return True
        
    temp_dir = os.path.dirname(output_path)
    current_clip = input_clips[0]
    
    for idx in range(1, len(input_clips)):
        next_clip = input_clips[idx]
        trans = gap_transitions[idx - 1]
        
        trans_type = trans.get("type", "clean").lower()
        trans_engine = trans.get("engine", "smart").lower()
        duration_sec = float(trans.get("duration_ms", 400)) / 1000.0
        
        step_output = os.path.join(temp_dir, f"stitch_step_{idx}.mp4")
        logger.info(f"Stitching clip {idx-1} to {idx} using {trans_type} ({trans_engine}) -> {step_output}")
        
        success = False
        
        # Check special overlay-based boundary effects
        if trans_type in ("glitch", "shake", "lens_flare"):
            # 1. Straight concatenate them first
            temp_concat = os.path.join(temp_dir, f"temp_concat_step_{idx}.mp4")
            cmd_concat = [
                "ffmpeg", "-y",
                "-i", current_clip,
                "-i", next_clip,
                "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                temp_concat
            ]
            try:
                subprocess.run(cmd_concat, check=True, capture_output=True)
                dur_a = get_clip_duration(current_clip)
                start_sec = max(0.0, dur_a - (duration_sec / 2.0))
                
                # 2. Apply special effect centered at the cut point
                if trans_type == "glitch":
                    success = apply_glitch(temp_concat, step_output, start_sec, duration_sec)
                elif trans_type == "shake":
                    success = apply_shake(temp_concat, step_output, start_sec, duration_sec)
                elif trans_type == "lens_flare":
                    success = apply_lens_flare(temp_concat, step_output, start_sec, duration_sec)
            except Exception as e:
                logger.error(f"Failed to apply boundary effect {trans_type}: {e}")
            finally:
                if os.path.exists(temp_concat):
                    try: os.remove(temp_concat)
                    except: pass
                    
        elif trans_type in ("clean", "hard_cut", "match_cut"):
            # Straight concat
            cmd = [
                "ffmpeg", "-y",
                "-i", current_clip,
                "-i", next_clip,
                "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                step_output
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                success = True
            except subprocess.CalledProcessError as e:
                logger.error(f"Concat failed at step {idx}: {e.stderr.decode()}")
                
        elif trans_engine == "xfade" or trans_type in xfade_engine.resolve(trans_type)[0]:
            # Use XfadeEngine
            dur_a = get_clip_duration(current_clip)
            success = xfade_engine.apply(
                input_clips=[current_clip, next_clip],
                output_path=step_output,
                transition=trans_type,
                duration=duration_sec,
                clip_duration=dur_a,
                audio_concat=True
            )
        else:
            # Use Smart transition (boundary boxblur/eq filters)
            success = apply_smart_transition(
                clip_a=current_clip,
                clip_b=next_clip,
                trans_type=trans_type,
                duration_sec=duration_sec,
                output_path=step_output
            )
            
        if not success:
            logger.error("Failed to stitch clips at transition boundary. Aborting.")
            return False
            
        # Clean up previous step files
        if current_clip.startswith(os.path.join(temp_dir, "stitch_step_")):
            try: os.remove(current_clip)
            except: pass
            
        current_clip = step_output
        
    # Move final stitched file to destination
    if os.path.exists(output_path):
        os.remove(output_path)
    os.rename(current_clip, output_path)
    logger.info(f"✅ Transitions applied. Final stitched video saved to: {output_path}")
    return True
