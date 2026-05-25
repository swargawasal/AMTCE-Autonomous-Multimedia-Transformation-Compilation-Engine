import os
import sys
import json
import logging
import asyncio
import subprocess
import time
import re
from typing import List, Dict
from datetime import timedelta
from dotenv import load_dotenv

# Ensure project root is in sys.path
root_dir = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
if root_dir not in sys.path:
    sys.path.append(root_dir)

# Load credentials
load_dotenv(os.path.join(root_dir, "Credentials", ".env"), override=True)

# Import project modules
from Intelligence_Modules.narrative_brain import director
from Audio_Modules.voiceover import voice_engine
from Audio_Modules.speech_to_text import transcribe_audio

# FORCE ENABLE Voiceover for this test
voice_engine.enabled = True

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CinemaGradeV6")

# --- CONFIG ---
INPUT_VIDEO = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\downloads\Akanksha_puri.mp4"
TEMP_DIR = os.path.join(root_dir, "temp", "cinema_v6")
OUTPUT_VIDEO = os.path.join(root_dir, "final_akanksha_v6_cinema.mp4")
FONT_PATH = os.path.join(root_dir, "assets", "fonts", "Inter-Bold.ttf")

os.makedirs(TEMP_DIR, exist_ok=True)

def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS time format H:MM:SS.CC"""
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    centiseconds = int(round((seconds - total_seconds) * 100))
    if centiseconds == 100:
        secs += 1
        centiseconds = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"

def clean_word(word: str) -> str:
    return re.sub(r'[^\w]', '', word).upper()

async def generate_audio_direct(text: str, output_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
    await asyncio.wait_for(communicate.save(output_path), timeout=45)
    return os.path.exists(output_path)

async def run_test():
    if not os.path.exists(INPUT_VIDEO):
        logger.error(f"Input video not found: {INPUT_VIDEO}")
        return

    logger.info(f"🧠 Running V6 Cinema-Grade (.ASS) Engine...")
    
    # 1. VISUAL ANALYSIS
    cmd_probe = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", INPUT_VIDEO]
    duration = float(subprocess.check_output(cmd_probe).decode().strip())
    
    # Extract frames for Gemini
    frames_dir = os.path.join(TEMP_DIR, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    frame_paths = []
    for i in range(5):
        ts = (duration / 5) * i + 0.1
        out = os.path.join(frames_dir, f"frame_{i}.jpg")
        subprocess.run(["ffmpeg", "-y", "-ss", str(ts), "-i", INPUT_VIDEO, "-vframes", "1", "-q:v", "3", out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        frame_paths.append(out)

    narrative_data = director.generate(INPUT_VIDEO, frame_paths)
    script_text = narrative_data.get("script", "No script.")
    
    # 2. AUDIO & TRANSCRIPTION
    audio_path = os.path.join(TEMP_DIR, "voiceover.mp3")
    await generate_audio_direct(script_text, audio_path)
    transcription = transcribe_audio(audio_path)
    words = transcription["words"]

    # 3. GENERATE .ASS SUBTITLE FILE
    ass_path = os.path.join(TEMP_DIR, "captions.ass")
    
    # Header & Styles
    # MarginV 600 anchors it to the lower third (1920 - 600 = 1320 from top)
    # Alignment 2 = Bottom-Center
    ass_content = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Inter Bold,62,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,3,2,100,100,600,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    # Phrase Grouping (3-4 words)
    chunk_size = 3
    chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]
    
    # Yellow Color Tag: {\1c&H00D7FF&} (Actually BGR, so &H00FFFF& for pure yellow, &H00D7FF& for a warmer yellow)
    YELLOW = r"{\1c&H00FFFF&}"
    WHITE = r"{\1c&HFFFFFF&}"

    for chunk in chunks:
        start_t = format_ass_time(chunk[0]['start'])
        end_t = format_ass_time(chunk[-1]['end'])
        
        # For each word in chunk, we need a separate event to handle the highlighting
        # Or even better: One event per WORD highlighting phase
        for i, active_w in enumerate(chunk):
            w_start = format_ass_time(active_w['start'])
            w_end = format_ass_time(active_w['end'])
            
            # Construct the line: all white except the active word
            line_parts = []
            for j, w in enumerate(chunk):
                cleaned = clean_word(w['word'])
                if i == j:
                    line_parts.append(f"{YELLOW}{cleaned}{WHITE}")
                else:
                    line_parts.append(cleaned)
            
            full_line = " ".join(line_parts)
            ass_content.append(f"Dialogue: 0,{w_start},{w_end},Default,,0,0,0,,{full_line}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_content))

    # 4. FINAL RENDER
    logger.info("🎬 Rendering Cinema-Grade V6 with .ASS Subtitles...")
    # Escape path for FFmpeg subtitles filter
    # On Windows, the path must be correctly escaped (double backslashes or forward slashes)
    safe_ass_path = ass_path.replace("\\", "/").replace(":", "\\:")
    
    cmd_render = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO, "-i", audio_path,
        "-vf", f"scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,subtitles='{safe_ass_path}'",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration), # Ensure output isn't longer than source
        OUTPUT_VIDEO
    ]
    
    subprocess.run(cmd_render, check=True)
    logger.info(f"✨ SUCCESS! Cinema-Grade Video: {OUTPUT_VIDEO}")

if __name__ == "__main__":
    asyncio.run(run_test())
