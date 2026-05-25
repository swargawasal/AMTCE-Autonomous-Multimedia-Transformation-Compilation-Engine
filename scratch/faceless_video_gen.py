import os
import subprocess
import asyncio
import json
import logging
import tempfile
import textwrap
import time
from typing import List, Dict

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("faceless_gen")

# --- CONFIGURATION ---
FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"
FONT_PATH = os.path.abspath("assets/fonts/Inter-Bold.ttf") # Standard in this repo
DEFAULT_BG = "bg.mp4"
OUTPUT_FILE = "final_short.mp4"

# Ensure directories
os.makedirs("temp", exist_ok=True)

async def generate_audio(text: str, output_path: str, voice: str = "en-US-ChristopherNeural"):
    """Generates audio using edge-tts."""
    import edge_tts
    logger.info(f"🎙️ Generating voiceover: {voice}")
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    return output_path

def transcribe_audio(audio_path: str):
    """Transcribes audio using faster-whisper and returns word-level timestamps."""
    from faster_whisper import WhisperModel
    logger.info("🎙️ Transcribing with faster-whisper (base model)...")
    
    # Load model on CPU
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, word_timestamps=True)
    
    words = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                words.append({
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end
                })
    return words

def generate_filter_script(words: List[Dict], video_width: int = 1080, video_height: int = 1920):
    """
    Constructs a robust FFmpeg filter script for dynamic captions.
    Solves the 'Length Limit' by writing to a file.
    Implements 'OpusClip' style word-at-a-time highlighting.
    """
    filters = []
    
    # 1. Base Scaling and Vertical Padding (9:16)
    # This ensures the background is always centered and vertical
    base_filter = (
        f"[0:v]scale={video_width}:{video_height}:force_original_aspect_ratio=decrease,"
        f"pad={video_width}:{video_height}:(ow-iw)/2:(oh-ih)/2:black[v_scaled];"
    )
    filters.append(base_filter)
    
    last_label = "[v_scaled]"
    
    # 2. Add individual word filters
    for i, w in enumerate(words):
        word_text = w['word'].upper()
        # Escape for FFmpeg
        safe_word = word_text.replace("'", "").replace(":", "\\:").replace(",", "\\,")
        
        start = w['start']
        end = w['end']
        duration = end - start
        
        # Dynamic Styling:
        # - Yellow color
        # - Black border
        # - Centered
        # - Slight 'Pop' effect (font size grows slightly)
        font_size = 120
        pop_expr = f"min({font_size}, {font_size-20} + (t-{start})*100)"
        
        out_label = f"[v{i}]"
        
        # drawtext filter
        dt = (
            f"{last_label}drawtext=fontfile='{FONT_PATH.replace(':', '\\:')}':"
            f"text='{safe_word}':fontsize={font_size}:fontcolor=yellow:"
            f"borderw=4:bordercolor=black:shadowx=2:shadowy=2:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:"
            f"enable='between(t,{start:.3f},{end:.3f})'{out_label}"
        )
        filters.append(dt + ";")
        last_label = out_label

    # Final label map
    filters.append(f"{last_label}null[v_final]")
    
    script_content = "\n".join(filters)
    script_path = os.path.join("temp", f"filter_script_{int(time.time())}.txt")
    
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_content)
    
    return script_path

def get_video_duration(path: str):
    cmd = [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    return float(subprocess.check_output(cmd).decode().strip())

def assemble_video(bg_path: str, audio_path: str, script_path: str, output_path: str):
    """Executes the FFmpeg command using the filter script."""
    audio_dur = get_video_duration(audio_path)
    
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", bg_path,
        "-i", audio_path,
        "-filter_complex_script", script_path,
        "-map", "[v_final]",
        "-map", "1:a",
        "-t", str(audio_dur),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-shortests", # Ensure it stops when audio ends
        output_path
    ]
    
    logger.info("🎬 Running final FFmpeg assembly...")
    subprocess.run(cmd, check=True)
    logger.info(f"✅ Video generated: {output_path}")

async def main(script_text: str, bg_video: str):
    audio_file = os.path.join("temp", "audio.mp3")
    
    # 1. Generate Audio
    await generate_audio(script_text, audio_file)
    
    # 2. Transcribe
    words = transcribe_audio(audio_file)
    
    # 3. Generate Filter Script
    script_path = generate_filter_script(words)
    
    # 4. Assemble
    assemble_video(bg_video, audio_file, script_path, OUTPUT_FILE)
    
    # Cleanup
    # os.remove(audio_file)
    # os.remove(script_path)

if __name__ == "__main__":
    # Example usage
    SCRIPT = "This is a test of the dynamic highlighting system. It works just like OpusClip by showing one word at a time with a yellow highlight."
    BG = "bg.mp4" # User must provide this
    
    if not os.path.exists(BG):
        logger.error(f"❌ Background video {BG} not found. Please provide a file named {BG}.")
    else:
        asyncio.run(main(SCRIPT, BG))
