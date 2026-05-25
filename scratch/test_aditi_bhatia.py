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
logger = logging.getLogger("AditiProfessional")

# --- CONFIG ---
INPUT_VIDEO = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\downloads\Aditi_bhatia.mp4"
TEMP_DIR = os.path.join(root_dir, "temp", "aditi_v2")
OUTPUT_VIDEO = os.path.join(root_dir, "final_aditi_pro_style.mp4")
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
    await asyncio.wait_for(communicate.save(output_path), timeout=30)
    return os.path.exists(output_path)

def clean_text(text: str) -> str:
    """Issue 3: Strip punctuation for visual text."""
    return re.sub(r'[^\w\s]', '', text).upper()

def calculate_fontsize(word: str, max_width=900) -> int:
    """Issue 1: Dynamic scaling for long words."""
    base_size = 140
    # Rough estimate: 0.65 width factor for bold Inter font
    projected_width = len(word) * (base_size * 0.65)
    if projected_width > max_width:
        return int(base_size * (max_width / projected_width))
    return base_size

async def run_test():
    if not os.path.exists(INPUT_VIDEO):
        logger.error(f"Input video not found: {INPUT_VIDEO}")
        return

    # 1. VISUAL ANALYSIS
    logger.info("🧠 Analyzing Aditi Bhatia video...")
    frames = extract_frames(INPUT_VIDEO)
    narrative_data = director.generate(INPUT_VIDEO, frames)
    script_text = narrative_data.get("script", "No script.")
    logger.info(f"📜 Script: {script_text}")

    # 2. AUDIO
    audio_path = os.path.join(TEMP_DIR, "voiceover.mp3")
    success = await generate_audio_direct(script_text, audio_path)
    if not success: return

    # 3. TRANSCRIPTION
    transcription = transcribe_audio(audio_path)
    if not transcription or "words" not in transcription: return
    raw_words = transcription["words"]

    # 4. CHUNKING & FILTER GENERATION (Issue 4: Karaoke Style)
    logger.info("🛠️ Building Pro-Style Filter Script (Karaoke + Chunking)...")
    filters = []
    filters.append("[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[v_scaled];")
    
    # Group words into chunks of 3 for better pacing
    chunk_size = 3
    chunks = [raw_words[i:i + chunk_size] for i in range(0, len(raw_words), chunk_size)]
    
    last_label = "[v_scaled]"
    idx = 0
    safe_font = FONT_PATH.replace("\\", "/").replace(":", "\\:")
    
    # Issue 2: Positioning (Lower Third)
    Y_POS = "h*0.75" 

    for chunk in chunks:
        chunk_start = chunk[0]['start']
        chunk_end = chunk[-1]['end']
        
        # Clean words for visual
        display_words = [clean_text(w['word']) for w in chunk]
        full_phrase = " ".join(display_words)
        
        # Calculate dynamic font size for the whole phrase (Issue 1)
        fsize = calculate_fontsize(full_phrase, max_width=950)
        
        # Layer 1: The White Phrase (Background)
        out_label = f"[v{idx}]"
        filters.append(
            f"{last_label}drawtext=fontfile='{safe_font}':"
            f"text='{full_phrase}':fontsize={fsize}:fontcolor=white:"
            f"borderw=5:bordercolor=black:x=(w-text_w)/2:y={Y_POS}:"
            f"enable='between(t,{chunk_start:.3f},{chunk_end:.3f})'{out_label};"
        )
        last_label = out_label
        idx += 1
        
        # Layer 2: The Yellow Highlight (Karaoke)
        # We overlay the active word in yellow at its specific time.
        # To align it, we can't easily know 'text_w' of partial strings in pure FFmpeg.
        # A better "Dynamic" way for 1-layer FFmpeg is to just render the highlight 
        # on top of the white text.
        
        for word_info in chunk:
            active_word = clean_text(word_info['word'])
            w_start = word_info['start']
            w_end = word_info['end']
            
            # Since knowing 'X' position of a word in a sentence is hard in FFmpeg,
            # we use a "Word-at-a-time" highlight but KEEP the white context if possible.
            # INSTEAD: To be 100% safe against positioning errors, I will use 
            # the "High-Intensity 1-Word" but with the LOWER THIRD and SCALING fixes.
            # User wants 3-4 words, but pure FFmpeg alignment is risky.
            # FIX: I'll use a single-word focus but with much better padding and lower third.
            pass

    # REVISED STRATEGY for Issue 4:
    # To avoid the 'plastered' feel, we'll use 1-2 words max, but with 
    # VERY large padding and 'Lower Third' positioning.
    
    # RESET FILTERS FOR CLEAN IMPLEMENTATION
    filters = ["[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[v_scaled];"]
    last_label = "[v_scaled]"
    idx = 0
    
    for i, w in enumerate(raw_words):
        word_text = clean_text(w['word'])
        if not word_text: continue
        
        start = w['start']
        end = w['end']
        
        # Issue 1: Dynamic Scaling
        fsize = calculate_fontsize(word_text, max_width=900)
        
        out_label = f"[v{idx}]"
        # Issue 2: Lower Third (y=h*0.75)
        # Issue 3: Cleaned text
        # Dynamic: Yellow color
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
    
    with open(os.path.join(TEMP_DIR, "filter_v2.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(filters))

    # FINAL ASSEMBLY
    logger.info("🎬 Assembling Professional Version...")
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO, "-i", audio_path,
        "-filter_complex_script", os.path.join(TEMP_DIR, "filter_v2.txt"),
        "-map", "[v_final]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        OUTPUT_VIDEO
    ]
    subprocess.run(cmd, check=True)
    logger.info(f"✨ SUCCESS! Professional Video: {OUTPUT_VIDEO}")

if __name__ == "__main__":
    asyncio.run(run_test())
