"""
ceie/tools/karaoke_applier.py
-----------------------------
Transcribes voiceover clips, generates word-level karaoke subtitle timings,
and burns ASS subtitles onto the video.
"""

import os
import subprocess
import logging
from typing import List, Dict, Any

from Audio_Modules.speech_to_text import transcribe_audio
from Compiler_Modules.karaoke_subtitle_engine import _build_ass_content, KaraokeConfig, _bridge_timestamps

logger = logging.getLogger("ceie.tools.karaoke_applier")

def apply_karaoke(
    video_path: str,
    output_path: str,
    voiceover_clips: List[Dict[str, Any]],
    temp_dir: str
) -> bool:
    """
    Transcribes voiceover clips, shifts timestamps to align globally,
    builds an ASS subtitle file, and renders it onto the video.
    """
    if not voiceover_clips:
        # Just copy input to output if no voiceovers
        import shutil
        shutil.copy(video_path, output_path)
        return True
        
    os.makedirs(temp_dir, exist_ok=True)
    ass_path = os.path.join(temp_dir, "karaoke_captions.ass")
    
    global_words = []
    
    logger.info(f"Transcribing {len(voiceover_clips)} voiceover clips for karaoke subtitles...")
    
    for idx, vo in enumerate(voiceover_clips):
        audio_path = vo["audio_path"]
        script = vo["script"]
        start_offset = vo["start"]
        
        try:
            # Transcribe audio using Whisper
            transcription = transcribe_audio(audio_path, initial_prompt=script)
            
            if not transcription or not transcription.get("words"):
                logger.warning(f"No words transcribed for voiceover clip #{idx}")
                continue
                
            words = transcription["words"]
            # Shift the start/end times by the insertion offset
            for w in words:
                w["start"] += start_offset
                w["end"] += start_offset
                
            global_words.extend(words)
            
        except Exception as e:
            logger.error(f"Error transcribing clip #{idx}: {e}")
            
    if not global_words:
        logger.warning("No words were transcribed from voiceover clips. Copying video without subtitles.")
        import shutil
        shutil.copy(video_path, output_path)
        return True
        
    # Bridge timestamps to eliminate gaps between words
    bridged_words = _bridge_timestamps(global_words)
    
    # Generate ASS subtitle content
    cfg = KaraokeConfig.load()
    ass_content = _build_ass_content(bridged_words, cfg)
    
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    logger.info(f"✅ ASS file generated: {ass_path}")
    
    # Burn subtitles onto the video using FFmpeg
    rel_ass = os.path.relpath(ass_path, os.getcwd())
    safe_ass = rel_ass.replace("\\", "/").replace(":", "\\\\:")
    
    # Check if video has an audio stream to copy
    try:
        has_audio = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path]
        ).decode().strip() != ""
    except:
        has_audio = False
        
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"subtitles='{safe_ass}'",
        "-c:v", "libx264", "-preset", "superfast", "-crf", "20",
        "-c:a", "copy" if has_audio else "aac",
        output_path
    ]
    
    logger.info(f"Burning subtitles onto video -> {output_path}")
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"✅ Subtitles burned successfully -> {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg failed burning subtitles: {e.stderr.decode()}")
        return False
