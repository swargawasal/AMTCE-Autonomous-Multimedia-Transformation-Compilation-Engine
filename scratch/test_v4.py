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
logger = logging.getLogger("KaraokeV4")

# --- CONFIG ---
INPUT_VIDEO = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\downloads\Sakshi_malik_1.mp4"
TEMP_DIR = os.path.join(root_dir, "temp", "karaoke_v4")
OUTPUT_VIDEO = os.path.join(root_dir, "final_sakshi_v4.mp4")
FONT_PATH = os.path.join(root_dir, "assets", "fonts", "Inter-Bold.ttf")

os.makedirs(TEMP_DIR, exist_ok=True)

def clean_word(word: str) -> str:
    """Strip punctuation and whitespace."""
    return re.sub(r'[^\w]', '', word).upper()

def bridge_timestamps(words: List[Dict]) -> List[Dict]:
    """Eliminate black frames between words."""
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

    logger.info(f"🧠 Running V4 'Zero-Jitter' Engine for Sakshi Malik...")
    frames = extract_frames(INPUT_VIDEO)
    narrative_data = director.generate(INPUT_VIDEO, frames)
    script_text = narrative_data.get("script", "No script.")
    
    audio_path = os.path.join(TEMP_DIR, "voiceover.mp3")
    await generate_audio_direct(script_text, audio_path)
    
    transcription = transcribe_audio(audio_path)
    raw_words = bridge_timestamps(transcription["words"])

    logger.info("🛠️ Building Karaoke Master Complex V4...")
    chunk_size = 3
    chunks = [raw_words[i:i + chunk_size] for i in range(0, len(raw_words), chunk_size)]
    
    filters = ["[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[v_scaled];"]
    last_label = "[v_scaled]"
    idx = 0
    safe_font = FONT_PATH.replace("\\", "/").replace(":", "\\:")
    
    Y_POS = "h*0.72"
    BASE_FONT_SIZE = 120
    # Refined Width Factor for Inter Bold
    WIDTH_FACTOR = 0.65 
    SPACE_FACTOR = 0.45 # Issue 1: Larger space to prevent collapsing

    for chunk in chunks:
        # Pre-process words
        cleaned_words = [clean_word(w['word']) for w in chunk]
        
        # 1. Calculate Total Phrase Width for Centering
        total_word_width = sum(len(w) * (BASE_FONT_SIZE * WIDTH_FACTOR) for w in cleaned_words)
        total_space_width = (len(cleaned_words) - 1) * (BASE_FONT_SIZE * SPACE_FACTOR)
        total_phrase_width = total_word_width + total_space_width
        
        # 2. Handle Overflow
        current_fsize = BASE_FONT_SIZE
        if total_phrase_width > 950:
            scale = 950 / total_phrase_width
            current_fsize = int(BASE_FONT_SIZE * scale)
            total_phrase_width = 950
            
        # 3. Absolute Phrase Start X
        phrase_start_x = f"(w-{total_phrase_width})/2"
        current_offset = 0
        
        chunk_start = chunk[0]['start']
        chunk_end = chunk[-1]['end']

        for word_idx, word_info in enumerate(chunk):
            word_text = cleaned_words[word_idx]
            if not word_text: continue
            
            w_start = word_info['start']
            w_end = word_info['end']
            
            # Precise Word X relative to phrase start
            # Using absolute string for X to avoid label-chain drift
            abs_x = f"{phrase_start_x}+{current_offset}"
            
            # White Base Layer
            out_label_white = f"[v{idx}w]"
            filters.append(
                f"{last_label}drawtext=fontfile='{safe_font}':"
                f"text='{word_text}':fontsize={current_fsize}:fontcolor=white:"
                f"borderw=4:bordercolor=black:shadowx=2:shadowy=2:"
                f"x={abs_x}:y={Y_POS}:"
                f"enable='between(t,{chunk_start:.3f},{chunk_end:.3f})'{out_label_white};"
            )
            
            # Yellow Highlight Overlay (Issue 2: Locked to the exact same X)
            out_label_yellow = f"[v{idx}y]"
            filters.append(
                f"{out_label_white}drawtext=fontfile='{safe_font}':"
                f"text='{word_text}':fontsize={current_fsize}:fontcolor=yellow:"
                f"borderw=5:bordercolor=black:shadowx=3:shadowy=3:"
                f"x={abs_x}:y={Y_POS}:"
                f"enable='between(t,{w_start:.3f},{w_end:.3f})'{out_label_yellow};"
            )
            
            last_label = out_label_yellow
            
            # Increment offset for next word in phrase
            word_w = len(word_text) * (current_fsize * WIDTH_FACTOR)
            space_w = current_fsize * SPACE_FACTOR
            current_offset += word_w + space_w
            idx += 1

    filters.append(f"{last_label}null[v_final]")
    
    with open(os.path.join(TEMP_DIR, "karaoke_v4.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(filters))

    logger.info("🎬 Rendering Sakshi V4 (Zero-Jitter)...")
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO, "-i", audio_path,
        "-filter_complex_script", os.path.join(TEMP_DIR, "karaoke_v4.txt"),
        "-map", "[v_final]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        OUTPUT_VIDEO
    ]
    subprocess.run(cmd, check=True)
    logger.info(f"✨ SUCCESS! Final Video: {OUTPUT_VIDEO}")

if __name__ == "__main__":
    asyncio.run(run_test())
