"""
ceie/tools/voiceover_applier.py
------------------------------
Generates audio files for voiceover segments and mixes them into the video.
Uses FFmpeg with dynamic background volume ducking during voiceover segments.
"""

import os
import subprocess
import logging
from typing import List, Dict, Any

from Audio_Modules.voiceover import generate_voiceover

logger = logging.getLogger("ceie.tools.voiceover_applier")

def get_audio_duration(path: str) -> float:
    """Gets audio duration using ffprobe."""
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
        logger.error(f"Failed to get audio duration for {path}: {e}")
        return 0.0

def apply_voiceovers(
    video_path: str,
    output_path: str,
    voiceover_segments: List[Dict[str, Any]],
    temp_dir: str
) -> tuple[bool, List[Dict[str, Any]]]:
    """
    Generates TTS clips for all voiceover segments and mixes them into the video.
    Returns (success_status, list_of_generated_vo_metadata).
    """
    if not voiceover_segments:
        # Just copy video
        import shutil
        shutil.copy(video_path, output_path)
        return True, []
        
    os.makedirs(temp_dir, exist_ok=True)
    logger.info(f"Generating TTS clips for {len(voiceover_segments)} voiceover segments...")
    
    generated_vos = []
    
    # 1. Generate TTS audio files
    for idx, seg in enumerate(voiceover_segments):
        script = seg["script"]
        insert_at = float(seg["insert_at_sec"])
        replace_audio = seg.get("replace_original_audio", False)
        
        vo_path = os.path.join(temp_dir, f"vo_seg_{idx:03d}.mp3")
        
        logger.info(f"Generating voiceover #{idx} -> '{script[:30]}...' to insert at {insert_at:.2f}s")
        # Force=True to ensure TTS is generated regardless of main project toggle
        success = generate_voiceover(script, vo_path, force=True)
        
        if success and os.path.exists(vo_path):
            dur = get_audio_duration(vo_path)
            generated_vos.append({
                "audio_path": vo_path,
                "start": insert_at,
                "end": insert_at + dur,
                "duration": dur,
                "script": script,
                "replace_audio": replace_audio
            })
        else:
            logger.warning(f"Failed to generate TTS clip #{idx}")
            
    if not generated_vos:
        logger.warning("No voiceover clips were successfully generated, copying video.")
        import shutil
        shutil.copy(video_path, output_path)
        return True, []
        
    # 2. Build FFmpeg command to mix audio and apply background ducking
    # Map inputs
    inputs = ["-i", video_path]
    for vo in generated_vos:
        inputs += ["-i", vo["audio_path"]]
        
    # Build filter complex
    filter_complex = ""
    
    # Background audio volume ducking filter
    duck_conditions = []
    for vo in generated_vos:
        if not vo["replace_audio"]:
            # Duck background volume to 15% during voiceover playback
            duck_conditions.append(f"between(t,{vo['start']:.3f},{vo['end']:.3f})")
            
    if duck_conditions:
        # Build volume envelope: if voiceover is playing, volume is 0.15, else 1.0
        cond_str = " + ".join(duck_conditions)
        filter_complex += f"[0:a]volume='if({cond_str}, 0.15, 1.0)':eval=frame[bg_ducked]; "
        prev_mix_label = "[bg_ducked]"
    else:
        # If replace_audio is True for all, or no ducking is needed
        filter_complex += "[0:a]volume=1.0[bg_ducked]; "
        prev_mix_label = "[bg_ducked]"
        
    # Mix each voiceover clip at its offset
    for idx, vo in enumerate(generated_vos):
        input_label = f"[{idx+1}:a]"
        delay_label = f"[delay_{idx}]"
        mix_label = f"[mix_{idx}]" if idx < len(generated_vos) - 1 else "[aout]"
        
        # Delay the voiceover audio to start at the requested offset (in milliseconds)
        delay_ms = int(vo["start"] * 1000)
        filter_complex += f"{input_label}adelay={delay_ms}|{delay_ms}{delay_label}; "
        
        # Mix delayed voiceover into the accumulated mix
        filter_complex += f"{prev_mix_label}{delay_label}amix=inputs=2:duration=longest{mix_label}"
        if idx < len(generated_vos) - 1:
            filter_complex += "; "
        prev_mix_label = mix_label
        
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",  # Fast: copy video stream, only transcode mixed audio
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]
    
    logger.info(f"Mixing {len(generated_vos)} voiceovers into video -> {output_path}")
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"✅ Voiceover mixing complete -> {output_path}")
        return True, generated_vos
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed mixing voiceovers: {e.stderr.decode()}")
        return False, []
