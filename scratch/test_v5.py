import os
import sys
import json
import logging
import asyncio
import subprocess
import time
import re
import textwrap
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
logger = logging.getLogger("IndustrialV5")

# --- CONFIG ---
INPUT_VIDEO = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\downloads\Sakshi_malik_1.mp4"
TEMP_DIR = os.path.join(root_dir, "temp", "karaoke_v5")
OUTPUT_VIDEO = os.path.join(root_dir, "final_sakshi_v5.mp4")
FONT_PATH = os.path.join(root_dir, "assets", "fonts", "Inter-Bold.ttf")

os.makedirs(TEMP_DIR, exist_ok=True)

def clean_word(word: str) -> str:
    return re.sub(r'[^\w]', '', word).upper()

def bridge_timestamps(words: List[Dict]) -> List[Dict]:
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

    logger.info(f"🧠 Running V5.1 'Multi-Line Stable' Engine...")
    frames = extract_frames(INPUT_VIDEO)
    narrative_data = director.generate(INPUT_VIDEO, frames)
    script_text = narrative_data.get("script", "No script.")
    
    audio_path = os.path.join(TEMP_DIR, "voiceover.mp3")
    await generate_audio_direct(script_text, audio_path)
    
    transcription = transcribe_audio(audio_path)
    raw_words = bridge_timestamps(transcription["words"])

    # --- V5 ENGINE SETTINGS ---
    STABLE_FONT_SIZE = 95 # Issue 1: Permanent Ceiling
    MAX_CHARS_PER_LINE = 18 # Issue 2: Wrapping threshold
    WIDTH_FACTOR = 0.65
    SPACE_FACTOR = 0.45
    LINE_HEIGHT = STABLE_FONT_SIZE * 1.2
    Y_BASE = 0.72 # Safe Lower Third

    logger.info("🛠️ Building Multi-Line Karaoke Filter Complex V5.1...")
    
    # Phrase Grouping
    chunk_size = 5 
    chunks = [raw_words[i:i + chunk_size] for i in range(0, len(raw_words), chunk_size)]
    
    filters = ["[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[v_scaled];"]
    last_label = "[v_scaled]"
    idx = 0
    safe_font = FONT_PATH.replace("\\", "/").replace(":", "\\:")

    for chunk in chunks:
        chunk_start = chunk[0]['start']
        chunk_end = chunk[-1]['end']
        
        # Build the lines
        phrase_list = [clean_word(w['word']) for w in chunk]
        full_phrase = " ".join(phrase_list)
        wrapped_lines = textwrap.wrap(full_phrase, width=MAX_CHARS_PER_LINE, break_long_words=False)
        num_lines = len(wrapped_lines)
        
        # FIXED: Dynamic Y calculation for N lines
        total_stack_height = (num_lines - 1) * LINE_HEIGHT
        start_y_offset = -total_stack_height / 2
        line_y_coords = []
        for i in range(num_lines):
            offset = start_y_offset + (i * LINE_HEIGHT)
            line_y_coords.append(f"h*{Y_BASE}{offset:+}")

        # Map words to lines correctly
        words_on_lines = []
        # Since textwrap might change capitalization or spacing, we track manually
        remaining_chunk_words = [clean_word(w['word']) for w in chunk]
        chunk_word_objs = list(chunk)
        
        for line_idx, line_text in enumerate(wrapped_lines):
            line_words_in_text = line_text.split()
            current_x_offset = 0
            
            line_width = (sum(len(lw) for lw in line_words_in_text) * STABLE_FONT_SIZE * WIDTH_FACTOR) + \
                         ((len(line_words_in_text) - 1) * STABLE_FONT_SIZE * SPACE_FACTOR)
            start_x = f"(w-{line_width})/2"
            
            for lw in line_words_in_text:
                if chunk_word_objs and clean_word(chunk_word_objs[0]['word']) == lw:
                    w_obj = chunk_word_objs.pop(0)
                    abs_x = f"{start_x}+{current_x_offset}"
                    words_on_lines.append((w_obj, line_idx, abs_x))
                    current_x_offset += (len(lw) * STABLE_FONT_SIZE * WIDTH_FACTOR) + (STABLE_FONT_SIZE * SPACE_FACTOR)

        # RENDER BASE LAYERS
        for line_idx, line_text in enumerate(wrapped_lines):
            line_words_in_text = line_text.split()
            line_width = (sum(len(lw) for lw in line_words_in_text) * STABLE_FONT_SIZE * WIDTH_FACTOR) + \
                         ((len(line_words_in_text) - 1) * STABLE_FONT_SIZE * SPACE_FACTOR)
            start_x = f"(w-{line_width})/2"
            y_pos = line_y_coords[line_idx]
            
            out_label_base = f"[v{idx}b{line_idx}]"
            filters.append(
                f"{last_label}drawtext=fontfile='{safe_font}':"
                f"text='{line_text}':fontsize={STABLE_FONT_SIZE}:fontcolor=white:"
                f"borderw=5:bordercolor=black:shadowx=2:shadowy=2:"
                f"x={start_x}:y={y_pos}:"
                f"enable='between(t,{chunk_start:.3f},{chunk_end:.3f})'{out_label_base};"
            )
            last_label = out_label_base

        # RENDER HIGHLIGHT LAYERS
        for w_info, line_idx, abs_x in words_on_lines:
            word_text = clean_word(w_info['word'])
            w_start = w_info['start']
            w_end = w_info['end']
            y_pos = line_y_coords[line_idx]
            
            out_label_highlight = f"[v{idx}h]"
            filters.append(
                f"{last_label}drawtext=fontfile='{safe_font}':"
                f"text='{word_text}':fontsize={STABLE_FONT_SIZE}:fontcolor=yellow:"
                f"borderw=6:bordercolor=black:shadowx=3:shadowy=3:"
                f"x={abs_x}:y={y_pos}:"
                f"enable='between(t,{w_start:.3f},{w_end:.3f})'{out_label_highlight};"
            )
            last_label = out_label_highlight
            idx += 1

    filters.append(f"{last_label}null[v_final]")
    
    with open(os.path.join(TEMP_DIR, "karaoke_v5.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(filters))

    logger.info("🎬 Rendering V5.1 Final Master...")
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO, "-i", audio_path,
        "-filter_complex_script", os.path.join(TEMP_DIR, "karaoke_v5.txt"),
        "-map", "[v_final]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        OUTPUT_VIDEO
    ]
    subprocess.run(cmd, check=True)
    logger.info(f"✨ SUCCESS! Final Video: {OUTPUT_VIDEO}")

if __name__ == "__main__":
    asyncio.run(run_test())
