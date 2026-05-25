import os
import sys
import json
import logging
import asyncio
import subprocess
import time
import re
from typing import List, Dict
from dotenv import load_dotenv

# Ensure the project root is in sys.path
root_dir = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
if root_dir not in sys.path:
    sys.path.append(root_dir)

# Load credentials
load_dotenv(os.path.join(root_dir, "Credentials", ".env"), override=True)

# Import our project modules
from Intelligence_Modules.narrative_brain import director
from Audio_Modules.voiceover import voice_engine
from Audio_Modules.speech_to_text import transcribe_audio

# FORCE ENABLE Voiceover for this test
voice_engine.enabled = True

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("AishwaryaLaxmi")

# --- CONFIG ---
INPUT_VIDEO = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\downloads\Aishwarya_Laxmi.mp4"
TEMP_DIR = os.path.join(root_dir, "temp", "aishwarya_test")
OUTPUT_VIDEO = os.path.join(root_dir, "final_aishwarya_laxmi.mp4")
FONT_PATH = os.path.join(root_dir, "assets", "fonts", "Inter-Bold.ttf")

os.makedirs(TEMP_DIR, exist_ok=True)

def extract_frames(video_path: str, count: int = 5) -> list:
    frame_paths = []
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
        duration = float(subprocess.check_output(cmd).decode().strip())
        frames_dir = os.path.join(TEMP_DIR, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        for i in range(count):
            ts = (duration / count) * i + 0.1
            out = os.path.join(frames_dir, f"frame_{i}.jpg")
            subprocess.run(["ffmpeg", "-y", "-ss", str(ts), "-i", video_path, "-vframes", "1", "-q:v", "3", out], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(out):
                frame_paths.append(out)
    except Exception as e:
        logger.error(f"Frame extraction failed: {e}")
    return frame_paths

async def generate_audio_direct(text: str, output_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
    await asyncio.wait_for(communicate.save(output_path), timeout=45)
    return os.path.exists(output_path)

def clean_text(text: str) -> str:
    return re.sub(r'[^\w\s]', '', text).upper()

def calculate_fontsize(word: str, max_width=900) -> int:
    base_size = 140
    projected_width = len(word) * (base_size * 0.65)
    if projected_width > max_width:
        return int(base_size * (max_width / projected_width))
    return base_size

async def run_test():
    if not os.path.exists(INPUT_VIDEO):
        logger.error(f"Input video not found: {INPUT_VIDEO}")
        return

    logger.info(f"🧠 Analyzing Aishwarya Laxmi video...")
    frames = extract_frames(INPUT_VIDEO)
    narrative_data = director.generate(INPUT_VIDEO, frames)
    script_text = narrative_data.get("script", "No script.")
    logger.info(f"📜 Script: {script_text}")

    audio_path = os.path.join(TEMP_DIR, "voiceover.mp3")
    success = await generate_audio_direct(script_text, audio_path)
    if not success: return

    transcription = transcribe_audio(audio_path)
    if not transcription or "words" not in transcription: return
    raw_words = transcription["words"]

    logger.info("🛠️ Building Professional Filters...")
    filters = ["[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[v_scaled];"]
    last_label = "[v_scaled]"
    idx = 0
    safe_font = FONT_PATH.replace("\\", "/").replace(":", "\\:")
    
    for i, w in enumerate(raw_words):
        word_text = clean_text(w['word'])
        if not word_text: continue
        start = w['start']
        end = w['end']
        fsize = calculate_fontsize(word_text, max_width=900)
        out_label = f"[v{idx}]"
        dt = (
            f"{last_label}drawtext=fontfile='{safe_font}':"
            f"text='{word_text}':fontsize={fsize}:fontcolor=yellow:"
            f"borderw=6:bordercolor=black:shadowx=4:shadowy=4:"
            f"x=(w-text_w)/2:y=h*0.72:"
            f"enable='between(t,{start:.3f},{end:.3f})'{out_label}"
        )
        filters.append(dt + ";")
        last_label = out_label
        idx += 1

    filters.append(f"{last_label}null[v_final]")
    with open(os.path.join(TEMP_DIR, "filter.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(filters))

    logger.info("🎬 Assembling Final Video...")
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO, "-i", audio_path,
        "-filter_complex_script", os.path.join(TEMP_DIR, "filter.txt"),
        "-map", "[v_final]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        OUTPUT_VIDEO
    ]
    subprocess.run(cmd, check=True)
    logger.info(f"✨ SUCCESS! Professional Video: {OUTPUT_VIDEO}")

if __name__ == "__main__":
    asyncio.run(run_test())
