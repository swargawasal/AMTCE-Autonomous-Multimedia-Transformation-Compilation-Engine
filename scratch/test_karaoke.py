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
logger = logging.getLogger("ProfessionalEngineV3")

# --- CONFIG ---
INPUT_VIDEO = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\downloads\Aishwarya_Laxmi.mp4"
TEMP_DIR = os.path.join(root_dir, "temp", "pro_v3")
OUTPUT_VIDEO = os.path.join(root_dir, "final_karaoke_pro.mp4")
FONT_PATH = os.path.join(root_dir, "assets", "fonts", "Inter-Bold.ttf")

os.makedirs(TEMP_DIR, exist_ok=True)

def clean_word(word: str) -> str:
    """Issue 3: Strip punctuation."""
    return re.sub(r'[^\w\s]', '', word).upper()

def bridge_timestamps(words: List[Dict]) -> List[Dict]:
    """Issue 1: Timestamp Bridging to fix the flicker bug."""
    for i in range(len(words) - 1):
        words[i]["end"] = words[i+1]["start"]
    return words

async def generate_audio_direct(text: str, output_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
    await asyncio.wait_for(communicate.save(output_path), timeout=45)
    return os.path.exists(output_path)

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

async def run_test():
    if not os.path.exists(INPUT_VIDEO):
        logger.error(f"Input video not found: {INPUT_VIDEO}")
        return

    # 1. ANALYSIS & AUDIO
    logger.info("🧠 Running Professional Pipeline V3 (Karaoke Mode)...")
    frames = extract_frames(INPUT_VIDEO)
    narrative_data = director.generate(INPUT_VIDEO, frames)
    script_text = narrative_data.get("script", "No script.")
    
    audio_path = os.path.join(TEMP_DIR, "voiceover.mp3")
    await generate_audio_direct(script_text, audio_path)
    
    # 2. TRANSCRIPTION & BRIDGING
    transcription = transcribe_audio(audio_path)
    raw_words = bridge_timestamps(transcription["words"])

    # 3. PHRASE GROUPING (Issue 2: Karaoke Logic)
    logger.info("🛠️ Building Karaoke-Style Filter Complex...")
    chunk_size = 3  # Grouping words in 3s for better cognitive load
    chunks = [raw_words[i:i + chunk_size] for i in range(0, len(raw_words), chunk_size)]
    
    filters = ["[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[v_scaled];"]
    last_label = "[v_scaled]"
    idx = 0
    safe_font = FONT_PATH.replace("\\", "/").replace(":", "\\:")
    
    # Layout Params
    Y_POS = "h*0.72" # Issue 2: Lower Third
    BASE_FONT_SIZE = 120
    PIXELS_PER_CHAR = BASE_FONT_SIZE * 0.55 # Rough width of bold Inter character
    SPACE_WIDTH = BASE_FONT_SIZE * 0.3

    for chunk in chunks:
        # Calculate Total Width of Phrase for centering
        cleaned_words = [clean_word(w['word']) for w in chunk]
        total_chars = sum(len(w) for w in cleaned_words)
        total_width = (total_chars * PIXELS_PER_CHAR) + ((len(cleaned_words) - 1) * SPACE_WIDTH)
        
        # Issue 1: Dynamic Scaling if phrase too long
        current_fsize = BASE_FONT_SIZE
        if total_width > 950:
            current_fsize = int(BASE_FONT_SIZE * (950 / total_width))
            total_width = 950
            
        start_x = f"(w-{total_width})/2"
        current_x_offset = 0
        
        chunk_start = chunk[0]['start']
        chunk_end = chunk[-1]['end']

        for word_idx, word_info in enumerate(chunk):
            word_text = cleaned_words[word_idx]
            w_start = word_info['start']
            w_end = word_info['end']
            
            # Word Width
            word_w = len(word_text) * (current_fsize * 0.55)
            
            # X Position for this specific word
            word_x = f"{start_x}+{current_x_offset}"
            
            # 1. RENDER WHITE (STATIC BASE)
            out_label_white = f"[v{idx}w]"
            filters.append(
                f"{last_label}drawtext=fontfile='{safe_font}':"
                f"text='{word_text}':fontsize={current_fsize}:fontcolor=white:"
                f"borderw=4:bordercolor=black:shadowx=2:shadowy=2:"
                f"x={word_x}:y={Y_POS}:"
                f"enable='between(t,{chunk_start:.3f},{chunk_end:.3f})'{out_label_white};"
            )
            
            # 2. RENDER YELLOW (HIGHLIGHT LAYER)
            out_label_yellow = f"[v{idx}y]"
            filters.append(
                f"{out_label_white}drawtext=fontfile='{safe_font}':"
                f"text='{word_text}':fontsize={current_fsize}:fontcolor=yellow:"
                f"borderw=5:bordercolor=black:shadowx=3:shadowy=3:"
                f"x={word_x}:y={Y_POS}:"
                f"enable='between(t,{w_start:.3f},{w_end:.3f})'{out_label_yellow};"
            )
            
            last_label = out_label_yellow
            current_x_offset += word_w + (current_fsize * 0.3)
            idx += 1

    filters.append(f"{last_label}null[v_final]")
    
    filter_path = os.path.join(TEMP_DIR, "karaoke_v3.txt")
    with open(filter_path, "w", encoding="utf-8") as f:
        f.write("\n".join(filters))

    # FINAL ASSEMBLY
    logger.info("🎬 Rendering Professional Karaoke Masterpiece...")
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO, "-i", audio_path,
        "-filter_complex_script", filter_path,
        "-map", "[v_final]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", # Even higher quality
        "-c:a", "aac", "-b:a", "192k",
        OUTPUT_VIDEO
    ]
    subprocess.run(cmd, check=True)
    logger.info(f"✨ SUCCESS! Professional Karaoke Video: {OUTPUT_VIDEO}")

if __name__ == "__main__":
    asyncio.run(run_test())
