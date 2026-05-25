import os
import tempfile
import subprocess
import logging
import math
from typing import List, Dict

logger = logging.getLogger("timeline_cutter")

def generate_srt_from_words(words_data: List[Dict], srt_path: str):
    """
    Converts Whisper word-level timestamps into an SRT file using pysrt.
    """
    try:
        import pysrt
    except ImportError:
        logger.error("❌ pysrt not installed. Run: pip install pysrt")
        return False

    try:
        subs = pysrt.SubRipFile()
        
        # Group words into short chunks (e.g., 3-5 words) for punchy captions
        current_text = ""
        chunk_start = None
        word_count = 0
        sub_index = 1
        
        for i, word_obj in enumerate(words_data):
            word = word_obj["word"].strip()
            if not word: continue
                
            if chunk_start is None:
                chunk_start = word_obj["start"]
                
            current_text += word + " "
            word_count += 1
            
            # Create a subtitle entry every 4 words or at punctuation
            if word_count >= 4 or word[-1:] in ".!?" or i == len(words_data) - 1:
                end_time = word_obj["end"]
                
                # Convert seconds to pysrt SubRipTime
                start_h = int(chunk_start // 3600)
                start_m = int((chunk_start % 3600) // 60)
                start_s = int(chunk_start % 60)
                start_ms = int((chunk_start % 1) * 1000)
                
                end_h = int(end_time // 3600)
                end_m = int((end_time % 3600) // 60)
                end_s = int(end_time % 60)
                end_ms = int((end_time % 1) * 1000)
                
                sub = pysrt.SubRipItem(
                    index=sub_index,
                    start=pysrt.SubRipTime(start_h, start_m, start_s, start_ms),
                    end=pysrt.SubRipTime(end_h, end_m, end_s, end_ms),
                    text=current_text.strip().upper()
                )
                subs.append(sub)
                
                # Reset
                sub_index += 1
                current_text = ""
                chunk_start = None
                word_count = 0
                
        subs.save(srt_path, encoding='utf-8')
        logger.info(f"✅ SRT generated at {srt_path}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to generate SRT: {e}")
        return False


def generate_ass_from_words(words_data: List[Dict], ass_path: str, video_width: int = 1080, video_height: int = 1920):
    """
    Converts Whisper word-level timestamps into an ASS file with {\\kf} karaoke tags.
    """
    try:
        def time_to_ass(t):
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = int(t % 60)
            cs = int((t % 1) * 100) # centiseconds
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        # Setup standard styles and PlayRes
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat,72,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,20,20,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        
        events = []
        
        # Group into 3-4 word chunks
        chunk_words = []
        chunk_start = 0
        
        for i, word_obj in enumerate(words_data):
            word = word_obj["word"].strip()
            if not word: continue
                
            if not chunk_words:
                chunk_start = word_obj["start"]
                
            # duration in seconds, ensure minimum 100ms (0.1s)
            dur_s = max(0.1, word_obj["end"] - word_obj["start"])
            dur_cs = int(dur_s * 100) # Centiseconds for \kf
            
            chunk_words.append({
                "text": word.upper(),
                "kf": dur_cs,
                "end": word_obj["end"]
            })
            
            if len(chunk_words) >= 4 or word[-1:] in ".!?" or i == len(words_data) - 1:
                # Build the line
                chunk_end = chunk_words[-1]["end"]
                
                ass_start = time_to_ass(chunk_start)
                ass_end = time_to_ass(chunk_end)
                
                # Format with \kf tags
                line_text = ""
                for cw in chunk_words:
                    line_text += f"{{\\kf{cw['kf']}}}{cw['text']} "
                line_text = line_text.strip()
                
                events.append(f"Dialogue: 0,{ass_start},{ass_end},Default,,0,0,0,,{line_text}")
                
                # reset
                chunk_words = []

        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(events) + "\n")
            
        logger.info(f"✅ ASS generated at {ass_path}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to generate ASS: {e}")
        return False

def cut_and_burn_video(input_path: str, output_path: str, keep_ranges: List[Dict], subs_path: str = None) -> bool:
    """
    Uses pure FFmpeg to trim the video according to `keep_ranges` and optionally burns an SRT or ASS file.
    Since we are cutting AND burning subtitles, we use a filter_complex graph.
    """
    if not keep_ranges:
        logger.error("❌ No keep ranges provided to timeline cutter.")
        return False
        
    logger.info(f"🎬 Cutting {len(keep_ranges)} segments via FFmpeg...")
    
    # We build a complex filter to trim and concatenate
    filter_complex = ""
    concat_video = ""
    concat_audio = ""
    
    for i, r in enumerate(keep_ranges):
        start = r["start"]
        end = r["end"]
        
        # Video Trim
        filter_complex += f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]; "
        # Audio Trim
        filter_complex += f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]; "
        
        concat_video += f"[v{i}]"
        concat_audio += f"[a{i}]"
        
    filter_complex += f"{concat_video}concat=n={len(keep_ranges)}:v=1:a=0[vout_raw]; "
    filter_complex += f"{concat_audio}concat=n={len(keep_ranges)}:v=0:a=1[aout]; "
    
    # Trim concat output label (no subtitles yet)
    vout_label = "[vout_raw]"

    # Add subtitles if provided
    if subs_path and os.path.exists(subs_path):
        # Escape path for FFmpeg filter (Windows drive-letter colon must be escaped)
        safe_subs_path = subs_path.replace("\\", "/").replace(":", "\\:")

        if subs_path.endswith(".ass"):
            # ASS uses embedded styles; pass fontsdir so libass finds Montserrat
            try:
                import Text_Modules.font_manager as fm
                fonts_dir = os.path.abspath(fm.LOCAL_FONT_DIR).replace("\\", "/").replace(":", "\\:")
                sub_filter = f"subtitles='{safe_subs_path}':fontsdir='{fonts_dir}'"
            except Exception:
                sub_filter = f"subtitles='{safe_subs_path}'"
        else:
            sub_filter = (
                f"subtitles='{safe_subs_path}':force_style="
                "'Fontname=Arial,Fontsize=24,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,BorderStyle=1,Outline=2'"
            )

        filter_complex += f"[vout_raw]{sub_filter}[vout_final]"
        vout_label = "[vout_final]"

    # Build Final Command
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", vout_label,
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        logger.info(f"✅ Auto-Edit complete! Output saved to: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ FFmpeg execution failed: {e.stderr.decode()}")
        return False

