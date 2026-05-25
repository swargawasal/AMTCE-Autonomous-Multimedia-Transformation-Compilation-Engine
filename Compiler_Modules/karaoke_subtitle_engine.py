"""
Compiler_Modules/karaoke_subtitle_engine.py

V7 Cinema-Grade .ASS Karaoke Subtitle Engine
=============================================
Industry-standard karaoke subtitle renderer using Advanced SubStation Alpha (.ass).
Eliminates all ghosting, jitter, and scaling bugs from drawtext-based approaches.

ALL settings are controlled via .env — no code changes required to tune output.

ENV CONTROLS (Credentials/.env):
─────────────────────────────────────────────────────────────────────────────────
  KARAOKE_ENABLED          = true   # Master toggle (true/false)
  KARAOKE_FONT_SIZE        = 64     # Base font size in ASS units (1pt ≈ 1.5px)
  KARAOKE_SAFE_ZONE        = 670    # MarginV from bottom. 670 = Y≈1250 on 1920p
  KARAOKE_MARGIN_SIDE      = 120    # Left/Right margin (safe-zone padding in px)
  KARAOKE_SHADOW_DEPTH     = 6      # Drop shadow depth (0 = none, 6 = strong pop)
  KARAOKE_OUTLINE_WIDTH    = 4      # Outline stroke width (0 = none)
  KARAOKE_CHUNK_SIZE       = 4      # Words per subtitle phrase (3-6 recommended)
  KARAOKE_HIGHLIGHT_COLOR  = 00FFFF # Active word color (BGR hex, no #)
  KARAOKE_BASE_COLOR       = FFFFFF # Inactive word color (BGR hex, no #)
─────────────────────────────────────────────────────────────────────────────────
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
from datetime import timedelta
from typing import Dict, List, Optional
from Audio_Modules.voiceover import generate_voiceover as generate_hybrid_voiceover

logger = logging.getLogger("karaoke_subtitle_engine")


# ────────────────────────────────────────────────────────────────────────────────
# ENV CONFIG LOADER — reads ALL settings from .env at import time
# ────────────────────────────────────────────────────────────────────────────────

def _env_bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("true", "1", "yes", "on")

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).strip())
    except (ValueError, TypeError):
        return default

def _env_str(key: str, default: str) -> str:
    return os.getenv(key, default).strip()


class KaraokeConfig:
    """Live-loaded config from .env. Re-read on each engine call for hot-reload."""

    @classmethod
    def load(cls) -> "KaraokeConfig":
        cfg = cls()
        cfg.enabled          = _env_bool("CINEMATIC_NARRATOR_ENABLED", _env_bool("KARAOKE_ENABLED", True))
        cfg.font_size        = _env_int("KARAOKE_FONT_SIZE", 64)
        cfg.safe_zone_margin = _env_int("KARAOKE_SAFE_ZONE", 670)
        cfg.side_margin      = _env_int("KARAOKE_MARGIN_SIDE", 120)
        cfg.shadow_depth     = _env_int("KARAOKE_SHADOW_DEPTH", 6)
        cfg.outline_width    = _env_int("KARAOKE_OUTLINE_WIDTH", 4)
        cfg.chunk_size       = _env_int("KARAOKE_CHUNK_SIZE", 4)
        cfg.highlight_color  = _env_str("KARAOKE_HIGHLIGHT_COLOR", "00FFFF")
        cfg.base_color       = _env_str("KARAOKE_BASE_COLOR", "FFFFFF")
        return cfg

    def log(self):
        logger.info(
            f"🎬 [KARAOKE_CFG] enabled={self.enabled} | "
            f"font={self.font_size}pt | safe_zone={self.safe_zone_margin}px | "
            f"outline={self.outline_width}pt | shadow={self.shadow_depth}pt | "
            f"chunk={self.chunk_size} words | "
            f"colors=#{self.highlight_color}/#{self.base_color}"
        )


# ────────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ────────────────────────────────────────────────────────────────────────────────

def _format_ass_time(seconds: float) -> str:
    """Convert float seconds → ASS time format H:MM:SS.CC"""
    total_cs = int(round(seconds * 100))
    h = total_cs // 360000
    total_cs %= 360000
    m = total_cs // 6000
    total_cs %= 6000
    s = total_cs // 100
    cs = total_cs % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean_word(word: str) -> str:
    """Strip punctuation and uppercase for display."""
    return re.sub(r'[^\w]', '', word).upper()


def _bridge_timestamps(words: List[Dict]) -> List[Dict]:
    """
    Timestamp Bridging: Force word[i].end == word[i+1].start.
    Eliminates Whisper's 20-50ms gaps that cause black-frame flicker.
    """
    for i in range(len(words) - 1):
        words[i]["end"] = words[i + 1]["start"]
    return words



def _build_ass_content(words: List[Dict], cfg: KaraokeConfig) -> str:
    """
    Build the full .ASS subtitle file content with per-word karaoke highlighting.

    Architecture:
    - One Dialogue event per active word (per chunk timing window)
    - Active word gets HIGHLIGHT color via inline tag {\\1c&Hcolor&}
    - All other words stay in BASE color
    - Word-level timestamps prevent any flickering gaps
    """
    YELLOW_TAG = r"{\1c&H" + cfg.highlight_color + r"&}"
    WHITE_TAG  = r"{\1c&H" + cfg.base_color + r"&}"

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        # Format: Name, Font, Size, PrimaryColor, Secondary, Outline, Back,
        #         Bold, Italic, Underline, Strike, ScaleX, ScaleY, Spacing, Angle,
        #         BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Inter Bold,{cfg.font_size},&H00{cfg.base_color},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{cfg.outline_width},{cfg.shadow_depth},2,{cfg.side_margin},{cfg.side_margin},{cfg.safe_zone_margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Group words into phrase chunks
    chunks = [words[i:i + cfg.chunk_size] for i in range(0, len(words), cfg.chunk_size)]

    for chunk in chunks:
        # For each active word in the chunk, emit one Dialogue line
        for active_idx, active_word in enumerate(chunk):
            w_start = _format_ass_time(active_word["start"])
            w_end   = _format_ass_time(active_word["end"])

            # Build the line: highlight active word, white for rest
            parts = []
            for j, w in enumerate(chunk):
                cleaned = _clean_word(w["word"])
                if not cleaned:
                    continue
                if j == active_idx:
                    parts.append(f"{YELLOW_TAG}{cleaned}{WHITE_TAG}")
                else:
                    parts.append(cleaned)

            if parts:
                full_line = " ".join(parts)
                lines.append(f"Dialogue: 0,{w_start},{w_end},Default,,0,0,0,,{full_line}")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────────
# MAIN ENGINE FUNCTION
# ────────────────────────────────────────────────────────────────────────────────

def apply_karaoke_subtitles(
    input_video: str,
    output_video: str,
    script_text: str,
    temp_dir: Optional[str] = None,
) -> bool:
    """
    Master entry point. Applies V7 Cinema-Grade karaoke subtitles to a video.

    This function is the production replacement for all drawtext-based caption
    rendering. It operates as a POST-PROCESSING step after the main render.

    Args:
        input_video:  Path to the already-rendered video (with visual overlays)
        output_video: Path to write the final captioned video
        script_text:  The Gemini-generated narration script (used for voiceover
                      AND as Whisper initial_prompt to prevent hallucinations)
        temp_dir:     Optional temp directory (auto-created if None)

    Returns:
        True on success, False on any failure (original video remains untouched)
    """
    cfg = KaraokeConfig.load()

    if not cfg.enabled:
        logger.info("🔕 [KARAOKE] KARAOKE_ENABLED=false — skipping subtitle injection.")
        # Copy input to output as-is
        try:
            import shutil
            shutil.copy2(input_video, output_video)
            return True
        except Exception as e:
            logger.error(f"❌ [KARAOKE] Copy fallback failed: {e}")
            return False

    cfg.log()

    if not os.path.exists(input_video):
        logger.error(f"❌ [KARAOKE] Input video not found: {input_video}")
        return False

    # ── TEMP DIR SETUP ────────────────────────────────────────────────────────
    _temp_created = False
    if temp_dir is None:
        temp_dir = os.path.join(os.path.dirname(input_video), "_karaoke_tmp")
        _temp_created = True
    os.makedirs(temp_dir, exist_ok=True)

    audio_path = os.path.join(temp_dir, "karaoke_voice.mp3")
    ass_path   = os.path.join(temp_dir, "karaoke_captions.ass")

    try:
        # ── STEP 1: HYBRID VOICEOVER GENERATION ─────────────────────────────────────
        logger.info("🎙️ [KARAOKE] Generating Hybrid voiceover via Global VoiceEngine...")
        # Force=True bypasses the micro-VO check so the Master Switch controls this
        success = generate_hybrid_voiceover(script_text, audio_path, force=True)

        if not success:
            logger.error("❌ [KARAOKE] Voiceover generation failed — aborting.")
            return False

        logger.info(f"✅ [KARAOKE] Voiceover saved: {audio_path}")

        # ── STEP 2: TRANSCRIPTION with HALLUCINATION SHIELD ──────────────────
        logger.info("🎙️ [KARAOKE] Transcribing with Hallucination Shield...")
        try:
            from Audio_Modules.speech_to_text import transcribe_audio
            transcription = transcribe_audio(
                audio_path,
                initial_prompt=script_text  # V7: Inject script as Whisper cheat-sheet
            )
        except ImportError:
            logger.error("❌ [KARAOKE] Cannot import speech_to_text module")
            return False

        if not transcription or not transcription.get("words"):
            logger.error("❌ [KARAOKE] Transcription returned no words — aborting.")
            return False

        words = _bridge_timestamps(transcription["words"])
        logger.info(f"✅ [KARAOKE] Transcribed {len(words)} words with bridged timestamps.")

        # ── STEP 3: GENERATE .ASS SUBTITLE FILE ──────────────────────────────
        ass_content = _build_ass_content(words, cfg)
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)
        logger.info(f"✅ [KARAOKE] ASS file written: {ass_path}")

        # ── STEP 4: PROBE SOURCE DURATION ────────────────────────────────────
        try:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input_video
            ]
            duration = float(subprocess.check_output(probe_cmd, stderr=subprocess.DEVNULL).decode().strip())
        except Exception as e:
            logger.warning(f"⚠️ [KARAOKE] Could not probe duration: {e} — using 30s fallback")
            duration = 30.0

        # ── STEP 5: PROBE AUDIO & FINAL RENDER ──────────────────────────────────────────────
        try:
            has_audio = subprocess.check_output(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", input_video]
            ).decode().strip() != ""
        except:
            has_audio = False

        # Use relative path to avoid FFmpeg Windows drive letter colon parsing issues
        rel_ass = os.path.relpath(ass_path, os.getcwd())
        safe_ass = rel_ass.replace("\\", "/").replace(":", "\\\\:")
        logger.info("🎬 [KARAOKE] Starting final render with Cinema-Grade .ASS subtitles...")

        if has_audio:
            filter_chain = f"[0:v]format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles='{safe_ass}'[v];[0:a][1:a]amix=inputs=2:duration=longest[a]"
            audio_map = "[a]"
        else:
            filter_chain = f"[0:v]format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles='{safe_ass}'[v]"
            audio_map = "1:a"

        cmd = [
            "ffmpeg", "-y",
            "-i", input_video,
            "-i", audio_path,
            "-filter_complex", filter_chain,
            "-map", "[v]",
            "-map", audio_map,
            "-c:v", "libx264",
            "-preset", "fast" if os.getenv("RENDER_TARGET", "quality").strip().lower() == "speed" else "medium",
            "-crf", "26" if os.getenv("RENDER_TARGET", "quality").strip().lower() == "speed" else "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", str(duration),
            output_video
        ]

        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            err = result.stderr.decode(errors="ignore")[-1000:]
            logger.error(f"❌ [KARAOKE] FFmpeg render failed:\n{err}")
            return False

        logger.info(f"✨ [KARAOKE] Cinema-Grade render complete: {output_video}")
        return True

    except Exception as e:
        logger.exception(f"❌ [KARAOKE] Unexpected error in apply_karaoke_subtitles: {e}")
        return False

    finally:
        # Clean up temp files (keep the .ass for debugging if DEBUG_JSON=1)
        if os.getenv("DEBUG_JSON", "0") != "1":
            for f in [audio_path]:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception:
                    pass


# Convenience singleton check
def is_karaoke_enabled() -> bool:
    """Quick check without loading full config."""
    return _env_bool("CINEMATIC_NARRATOR_ENABLED", _env_bool("KARAOKE_ENABLED", True))
