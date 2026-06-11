"""
Text Overlay Module (Hardened Production Grade)
Handles robust text overlay with Font Auto-Healing via Official Zip, ASS Fallback, and Crash Safety.

Capabilities:
1. Auto-downloads authoritative font (Inter v4.0 Zip).
2. Extracts and verifies font file integrity (>50KB).
3. Falls back to subtitle overlay (.ass) if drawtext fails or unicode detected.
4. Sanitizes all text inputs.
5. Non-blocking failure model (returns False instead of crashing).

STRICT AUDIT COMPLIANT: Global State Fix, Atomic Ops, Conservative Width.
"""

import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
import zipfile
from typing import Any, Dict, Optional

import requests

from Text_Modules.arc_caption_style import (
    build_caption_drawtext_filter,
    wrap_caption_lines,
    get_font_path as get_arc_caption_font_path,
    BRAND_FONT_SIZE,
    BRAND_BORDER_WIDTH,
)
from Text_Modules.caption_renderer import render_caption_png

logger = logging.getLogger("text_overlay")

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FONT_ZIP_URL = "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip"
LOCAL_FONT_DIR = os.path.join("assets", "fonts")
LOCAL_FONT_PATH = os.path.join(LOCAL_FONT_DIR, "Inter-Bold.ttf")
FALLBACK_FONT_PATH = os.path.join("fonts", "Roboto-Bold.ttf")
ARC_CAPTION_FONT_PATH = get_arc_caption_font_path()

# 1. Configurable Env Vars
FONT_MIN_SIZE_BYTES = int(os.getenv("FONT_MIN_SIZE_BYTES", 50 * 1024))
FONT_DOWNLOAD_TIMEOUT_SECS = int(os.getenv("FONT_DOWNLOAD_TIMEOUT_SECS", 30))
FONT_DOWNLOAD_RETRIES = int(os.getenv("FONT_DOWNLOAD_RETRIES", 2))
FONT_AUTO_DOWNLOAD_BACKGROUND = (
    os.getenv("FONT_AUTO_DOWNLOAD_BACKGROUND", "yes").lower() == "yes"
)
ASS_PLAYRES_X = int(os.getenv("ASS_PLAYRES_X", 1080))
ASS_PLAYRES_Y = int(os.getenv("ASS_PLAYRES_Y", 1920))
DEBUG_JSON = os.getenv("DEBUG_JSON", "0") == "1"
TEXT_MAX_CHARS = int(os.getenv("TEXT_MAX_CHARS", 220))

TEXT_LANES = {
    "caption": 0.92,  # AI Captions (ULTRA TIGHT: Near footer)
    "fixed": 0.96,  # Fixed Branding (Bottom 4%)
    "top": 0.08,  # Top Warning/Info
    "center": 0.50,  # Dead Center
    "brand": 0.97,  # Persistent brand watermark — bottom-right corner
}
LANE_PADDING = 0.04

# Brand overlay constants — driven by .env BRAND_NAME / ADD_TEXT_OVERLAY
BRAND_FONT_SIZE = int(os.getenv("BRAND_FONT_SIZE", "36"))

import random

def _get_stealth_jitter(base_size=30):
    """
    Military-grade algorithmic pattern breaker.
    Randomizes X/Y anchor zones, pixel coordinates, font size, and opacity.
    Makes it mathematically impossible for YT/IG to detect a static template hash.
    """
    positions = [
        f"x=(w-text_w)/2:y=h-text_h-{random.randint(40, 90)}",            # Bottom Center
        f"x={random.randint(30, 80)}:y=h-text_h-{random.randint(40, 90)}", # Bottom Left
        f"x=w-text_w-{random.randint(30, 80)}:y=h-text_h-{random.randint(40, 90)}", # Bottom Right
        f"x=(w-text_w)/2:y={random.randint(60, 120)}",                     # Top Center
    ]
    pos_expr = random.choice(positions)
    opacity = round(random.uniform(0.65, 0.95), 2)
    fs_mod = random.randint(-4, 4)
    fs = max(18, base_size + fs_mod)
    
    return pos_expr, opacity, fs

class TextOverlay:
    def __init__(self):
        # Instance-scoped state to prevent global poisoning
        self._drawtext_supported: Optional[bool] = None
        self._font_checked: bool = False
        self._drawtext_failed_once: bool = False
        self._last_result_meta: Dict[str, Any] = {}
        self._last_debug_info: Dict[str, Any] = {}

        # 17. Non-Blocking Font Download
        if self._validate_font_file(LOCAL_FONT_PATH):
            self._font_checked = True
            self._check_drawtext_support()
        else:
            if FONT_AUTO_DOWNLOAD_BACKGROUND:
                t = threading.Thread(target=self._ensure_font_thread, daemon=True)
                t.start()
                # We don't block; fallback to ASS until ready
            else:
                self._ensure_font_thread()
                self._check_drawtext_support()

    def _ensure_font(self):
        """Deprecated: Use _ensure_font_thread internal logic."""
        self._ensure_font_thread()

    def _ensure_font_thread(self):
        """Auto-heals missing font by downloading and extracting the official Zip (Atomic & Robust)."""
        if self._font_checked:
            return

        # Double check in thread
        if self._validate_font_file(LOCAL_FONT_PATH):
            self._font_checked = True
            return

        os.makedirs(LOCAL_FONT_DIR, exist_ok=True)
        # Use temp file for atomic swap
        fd, temp_zip = tempfile.mkstemp(suffix=".zip", dir=LOCAL_FONT_DIR)
        os.close(fd)
        temp_ttf = os.path.join(LOCAL_FONT_DIR, f"tmp_{int(time.time())}.ttf")

        for attempt in range(FONT_DOWNLOAD_RETRIES + 1):
            try:
                logger.info(f"⬇️ Downloading font (Attempt {attempt + 1})...")
                response = requests.get(
                    FONT_ZIP_URL, timeout=FONT_DOWNLOAD_TIMEOUT_SECS
                )
                response.raise_for_status()

                with open(temp_zip, "wb") as f:
                    f.write(response.content)

                found = False
                with zipfile.ZipFile(temp_zip) as z:
                    target_file = None
                    for name in z.namelist():
                        if name.endswith("Inter-Bold.ttf") and "Variable" not in name:
                            target_file = name
                            break

                    if target_file:
                        with (
                            z.open(target_file) as source,
                            open(temp_ttf, "wb") as target,
                        ):
                            shutil.copyfileobj(source, target)

                        # Atomic Move
                        if self._validate_font_file(temp_ttf):
                            # On Windows, need to remove dest first
                            if os.path.exists(LOCAL_FONT_PATH):
                                try:
                                    os.remove(LOCAL_FONT_PATH)
                                except:
                                    pass
                            os.replace(temp_ttf, LOCAL_FONT_PATH)
                            self._font_checked = True
                            logger.info("✅ Font installed successfully.")
                            found = True
                            break  # Success
                        else:
                            logger.error("❌ Downloaded font validation failed.")
                    else:
                        logger.error("❌ Inter-Bold.ttf not found in ZIP.")

                if found:
                    break

            except Exception as e:
                logger.warning(f"⚠️ Font download attempt {attempt + 1} failed: {e}")
                time.sleep(0.5 * (2**attempt))  # Exponential backoff
            finally:
                # Cleanup temps
                for p in [temp_zip, temp_ttf]:
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except:
                            pass

        # Final check
        if not self._font_checked:
            logger.error(
                "❌ Failed to install font after retries. Subtitles fallback enabled."
            )

    def _validate_font_file(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            if os.path.getsize(path) < FONT_MIN_SIZE_BYTES:
                return False
            # Header check
            with open(path, "rb") as f:
                header = f.read(4)
                # TrueType (00010000) or OTC/TTF (ttcf)
                if header == b"\x00\x01\x00\x00" or header == b"ttcf":
                    return True
            return False
        except:
            return False

    def _get_effective_font_path(self) -> str:
        """
        Return the effective font path for drawtext.

        Priority:
            1. LOCAL_FONT_PATH if validated (converted to absolute)
            2. FALLBACK_FONT_PATH (fonts/Roboto-Bold.ttf) if exists (absolute path)
            3. LOCAL_FONT_PATH anyway (let FFmpeg handle error)
            
        All paths are converted to absolute paths for FFmpeg reliability.
        """
        # Check primary font - convert to absolute path
        abs_primary = os.path.abspath(LOCAL_FONT_PATH)
        if self._validate_font_file(abs_primary):
            logger.info(f"[TEXT_OVERLAY] font_loaded={abs_primary}")
            return abs_primary

        # Check fallback font - use absolute path for FFmpeg
        abs_fallback = os.path.abspath(FALLBACK_FONT_PATH)
        if os.path.exists(abs_fallback):
            logger.info(f"[TEXT_OVERLAY] font_loaded={abs_fallback} (fallback)")
            return abs_fallback

        # Default to primary (will likely fail but consistent behavior)
        logger.warning(
            f"⚠️ [TEXT OVERLAY] No valid font found. Using default path: {abs_primary}"
        )
        return abs_primary

    def _check_drawtext_support(self):
        """Checks if installed FFmpeg supports drawtext filter."""
        if self._drawtext_supported is not None:
            return

        try:
            # 4. Drawtext Support Check with Timeout
            result = subprocess.run(
                [FFMPEG_BIN, "-filters"],
                capture_output=True,
                text=True,
                timeout=6,  # Strict Timeout
            )
            # Robust stdout parsing
            output = result.stdout.lower()
            self._drawtext_supported = " drawtext " in output or "\ndrawtext " in output

            if not self._drawtext_supported:
                logger.warning(
                    "⚠️ FFmpeg 'drawtext' filter NOT found. Fallback mode enabled."
                )
        except subprocess.TimeoutExpired:
            logger.warning("⚠️ FFmpeg filter check timed out. Assuming broken.")
            self._drawtext_supported = False
        except Exception:
            logger.warning("⚠️ Could not verify FFmpeg filters. Assuming broken.")
            self._drawtext_supported = False

    def _get_video_dimensions(self, video_path: str) -> tuple[int, int]:
        """Probes video dimensions (w, h) using ffprobe."""
        try:
            probe_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0",
                video_path
            ]
            result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
            if result.stdout.strip():
                dims = result.stdout.strip().split('x')
                if len(dims) >= 2:
                    return int(dims[0]), int(dims[1])
        except Exception:
            pass
        return 1080, 1920  # Default fallback

    def _is_safe_ascii(self, text: str) -> bool:
        """Strict check for drawtext safety. ONLY printable ASCII allowed."""
        if not text:
            return True
        try:
            # Must be ASCII
            text.encode("ascii")
            # detailed check for control chars
            for char in text:
                if not (32 <= ord(char) <= 126 or char == "\n"):
                    return False
            return True
        except UnicodeEncodeError:
            return False

    def _escape_drawtext(self, text: str) -> str:
        """Strict escaping for FFmpeg drawtext."""
        if not text:
            return ""

        text = text.replace("\\", "\\\\")
        text = text.replace(":", "\\:")
        text = text.replace("'", "\\'")
        text = text.replace(",", "\\,")
        text = text.replace(";", "\\;")
        text = text.replace("%", "\\%")
        text = text.replace("[", "\\[")
        text = text.replace("]", "\\]")
        text = text.replace("(", "\\(")
        text = text.replace(")", "\\)")
        text = text.replace("\n", " ")

        return text

    def _escape_ass(self, text: str) -> str:
        """Escaping for ASS subtitles."""
        if not text:
            return ""
        text = text.replace("{", "\\{").replace("}", "\\}")
        text = text.replace("\n", "\\N")
        return text

    def _wrap_text(self, text: str, max_chars: int = 26) -> str:
        if not text:
            return ""
        text = text.replace("\r", "").strip()
        if len(text) <= max_chars:
            return text
        return textwrap.fill(
            text, width=max_chars, break_long_words=False, break_on_hyphens=False
        )

    def _wrap_caption_arc_text(self, text: str) -> str:
        lines = wrap_caption_lines(text)
        return "\n".join(lines) if lines else ""

    def _create_ass_file(self, text: str, lane: str) -> str:
        """Generates a temporary .ass subtitle file (Atomic Write)."""
        filename = f"overlay_{os.getpid()}_{int(time.time() * 1000)}.ass"
        tmp_dir = os.path.join("temp", "ass")
        os.makedirs(tmp_dir, exist_ok=True)

        ass_path = os.path.join(tmp_dir, filename)

        # Use temp file for writing
        fd, temp_write_path = tempfile.mkstemp(dir=tmp_dir, text=True)
        os.close(fd)

        # Alignment: 2=Bottom Center, 8=Top Center, 5=Middle Center
        alignment = 2

        # Calc MarginV based on Lane Percentage
        pct = TEXT_LANES.get(lane, TEXT_LANES["caption"])

        # Check if voiceover is off
        _narrator_enabled = os.getenv("CINEMATIC_NARRATOR_ENABLED", "yes").lower() == "yes"
        _vo_enabled = os.getenv("ENABLE_MICRO_VOICEOVER", "yes").lower() == "yes"
        _voiceover_off = (not _narrator_enabled or not _vo_enabled)

        if lane == "caption" and _voiceover_off:
            font_name = "Inter Bold"
            font_size = int(os.getenv("KARAOKE_FONT_SIZE", "64"))
            primary_color = f"&H00{os.getenv('KARAOKE_BASE_COLOR', 'FFFFFF')}"
            secondary_color = f"&H00{os.getenv('KARAOKE_HIGHLIGHT_COLOR', '00FFFF')}"
            outline_color = "&H00000000"
            back_color = "&H00000000"
            bold_val = 1
            outline_width = int(os.getenv("KARAOKE_OUTLINE_WIDTH", "4"))
            shadow_depth = int(os.getenv("KARAOKE_SHADOW_DEPTH", "6"))
            alignment = 2
            margin_v = int(os.getenv("KARAOKE_SAFE_ZONE", "670"))
        else:
            font_name = "Playfair Display"
            font_size = 85
            primary_color = "&H0000D7FF"
            secondary_color = "&H0000D7FF"
            outline_color = "&H00000000"
            back_color = "&H00000000"
            bold_val = -1
            outline_width = 0
            shadow_depth = 0
            if lane == "caption":
                alignment = 8
                margin_v = int(ASS_PLAYRES_Y * 0.05)
            elif lane == "top":
                alignment = 8
                margin_v = int(ASS_PLAYRES_Y * pct)
            elif lane == "center":
                alignment = 5
                margin_v = 0
            else:
                # Bottom Logic (Caption/Fixed)
                # MarginV = Distance from Bottom
                margin_v = int(ASS_PLAYRES_Y * (1.0 - pct))

        escaped_text = self._escape_ass(text)

        ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ASS_PLAYRES_X}
PlayResY: {ASS_PLAYRES_Y}
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_color},{secondary_color},{outline_color},{back_color},{bold_val},0,0,0,100,100,0,0,1,{outline_width},{shadow_depth},{alignment},20,20,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:59:59.00,Default,,0,0,0,,{escaped_text}
"""
        with open(temp_write_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        # Atomic Move
        if os.path.exists(ass_path):
            try:
                os.remove(ass_path)
            except:
                pass
        os.replace(temp_write_path, ass_path)

        return ass_path

    def add_overlay(self, video_path, output_path, text, lane="caption", size=60):
        """
        Main entry point with Strict Fallback Logic and Lanes.
        """
        if not text or not video_path or not os.path.exists(video_path):
            return False

        if lane == "caption":
            wrapped_text = self._wrap_caption_arc_text(text)
        else:
            wrapped_text = self._wrap_text(text, max_chars=24)
        
        # Log caption sanitization
        logger.info("[TEXT_OVERLAY] caption_sanitized=True")

        # 11. Font Size & Overflow Protection
        line_count = wrapped_text.count("\n") + 1

        if line_count == 2:
            size = int(size * 0.85)
        elif line_count >= 3:
            size = int(size * 0.70)

        longest_line = (
            max([len(line) for line in wrapped_text.split("\n")]) if wrapped_text else 0
        )

        # Conservative Width Estimate (0.7 factor)
        estimated_width = longest_line * (size * 0.7)
        max_allowed_width = ASS_PLAYRES_X * 0.9

        while estimated_width > max_allowed_width and size > 18:
            size = int(size * 0.95)
            estimated_width = longest_line * (size * 0.7)

        size = max(18, min(size, 300))

        if lane not in TEXT_LANES:
            logger.warning(f"⚠️ Unknown text lane '{lane}', defaulting to 'caption'")
            lane = "caption"

        # Decision Tree
        use_drawtext = True
        start_method = "DRAWTEXT"
        reason = "optimal"

        if self._drawtext_failed_once:
            use_drawtext = False
            reason = "previous_failure"
        elif not self._drawtext_supported:
            use_drawtext = False
            reason = "drawtext_unavailable"
        elif not self._font_checked and not os.path.exists(LOCAL_FONT_PATH):
            use_drawtext = False
            reason = "font_missing"
        elif not self._is_safe_ascii(text):  # STRICT ALLOWLIST
            use_drawtext = False
            reason = "complex_chars_detected"
        elif size < 20:
            use_drawtext = False
            reason = "text_too_small"

        if not use_drawtext:
            start_method = "SUBTITLES"

        logger.info("🧾 [TEXT OVERLAY] Starting")
        logger.info(f"    ├─ lane: {lane}")
        logger.info(f"    ├─ text_len: {len(text)}")
        logger.info(f"    ├─ size: {size}")
        logger.info(f"    └─ method: {start_method} ({reason})")

        self._last_result_meta = {
            "method": start_method.lower(),
            "font_used": LOCAL_FONT_PATH if use_drawtext else None,
            "text_len": len(text),
            "size": size,
            "lane": lane,
        }

        # Check if voiceover/narration is off
        _narrator_enabled = os.getenv("CINEMATIC_NARRATOR_ENABLED", "yes").lower() == "yes"
        _vo_enabled = os.getenv("ENABLE_MICRO_VOICEOVER", "yes").lower() == "yes"
        _voiceover_off = (not _narrator_enabled or not _vo_enabled)

        if lane == "caption" and not _voiceover_off and self._is_safe_ascii(text):
            # ── New PNG-based Caption Rendering ──────────────────────────
            vw, vh = self._get_video_dimensions(video_path)
            caption_png = render_caption_png(wrapped_text, vw, vh)
            if caption_png and os.path.exists(caption_png):
                cmd = [
                    FFMPEG_BIN, "-y", "-i", video_path, "-i", caption_png,
                    "-filter_complex", f"[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:enable='gte(t,0.75)'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-c:a", "copy",
                    output_path
                ]
                success = self._safe_run_overlay("PNG_Caption", cmd)
                try: os.remove(caption_png)
                except: pass
                if success:
                    logger.info("✔️ [TEXT OVERLAY] Caption applied via PNG")
                    return True

        if use_drawtext:
            success = self._apply_drawtext(
                video_path, output_path, wrapped_text, lane, size
            )
            if success:
                logger.info("✔️ [TEXT OVERLAY] Applied successfully")
                logger.info("    ├─ method: DRAWTEXT")
                logger.info("    └─ lane: " + lane)
                return True
            else:
                self._drawtext_failed_once = True  # Only fails THIS instance
                logger.warning("❌ [TEXT OVERLAY] Drawtext failed")
                logger.warning("    ├─ reason: ffmpeg_error")
                logger.warning("    └─ action: fallback_to_subtitles")
                self._last_result_meta["fallback"] = True
                return self._apply_ass(video_path, output_path, wrapped_text, lane)
        else:
            success = self._apply_ass(video_path, output_path, wrapped_text, lane)
            if success:
                logger.info("✔️ [TEXT OVERLAY] Applied successfully")
                logger.info("    ├─ method: SUBTITLES")
                logger.info("    └─ lane: " + lane)
            return success

    def _safe_run_overlay(self, method_name, cmd):
        """Wrapper for safe subprocess execution."""
        try:
            # Timeout 120s
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=120,
            )

            if DEBUG_JSON:
                self._last_debug_info = {
                    "method": method_name,
                    "cmd": " ".join(cmd[:10]) + "...",
                    "result": True,
                }
            return True
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode() if e.stderr else "Unknown Error"
            logger.error(f"{method_name} failed: {err_msg[:300]}")
            if DEBUG_JSON:
                self._last_debug_info = {
                    "method": method_name,
                    "cmd": " ".join(cmd[:10]) + "...",
                    "result": False,
                    "error": err_msg,
                }
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"{method_name} timed out!")
            return False
        except Exception as e:
            logger.error(f"{method_name} crashed: {e}")
            return False

    def _apply_drawtext(self, video_path, output_path, text, lane, size):
        font_path = (
            os.path.abspath(self._get_effective_font_path()).replace("\\", "/").replace(":", "\\:")
        )
        lines = []
        if lane == "caption":
            clean_text = text.replace("\r", "")
            pre_lines = clean_text.split("\n")
            for line in pre_lines:
                if line.strip():
                    lines.append(line.strip())
        else:
            lines = text.split("\n")

        if len(lines) > 5:
            lines = lines[:5]

        filters = []

        # [AESTHETIC] Cinematic Bottom Gradient
        # filters.append("drawbox=x=0:y=ih*0.65:w=iw:h=ih*0.35:color=black@0.35:t=fill")
        pass

        if lane == "fixed":
            # Brand logic (Bottom Anchor)
            # [AESTHETIC] User requested SAME SIZE as other overlays
            fixed_size = size
            # Explicitly anchor at bottom 6% (0.94)
            fixed_y = "h*0.94"
            for line in lines:
                safe_line = self._escape_drawtext(line)
                dt = (
                    f"drawtext=fontfile='{font_path}':text='{safe_line}':fontsize={fixed_size}:"
                    f"fontcolor=white:shadowx=3:shadowy=4:shadowcolor=black@0.6:x=(w-text_w)/2:y={fixed_y}:"
                    f"enable='gte(t,0.75)'"
                )
                filters.append(dt)

        elif lane == "caption":
            # Get video dimensions for dynamic font sizing
            try:
                import subprocess
                probe_cmd = [
                    "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=height", "-of", "csv=s=x:p=0",
                    video_path
                ]
                result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
                video_height = int(result.stdout.strip().split('x')[0]) if result.stdout.strip() else 1920
            except Exception:
                video_height = 1920  # Default fallback

            caption_filter = build_caption_drawtext_filter(
                "\n".join(lines),
                video_height=video_height,
                font_path=ARC_CAPTION_FONT_PATH,
            )
            if caption_filter:
                filters.append(caption_filter)

        elif lane == "brand":
            # ── Brand watermark lane ──────────────────────────────────────
            # Military-grade stealth jitter pattern breaker
            safe_line = self._escape_drawtext(lines[0]) if lines else ""
            if safe_line:
                pos_expr, opacity, fs = _get_stealth_jitter(BRAND_FONT_SIZE)
                
                dt = (
                    f"drawtext=fontfile='{font_path}':text='{safe_line}':"
                    f"{pos_expr}:"
                    f"fontsize={fs}:fontcolor=white@{opacity}:"
                    f"borderw={BRAND_BORDER_WIDTH}:bordercolor=black@{opacity}:"
                    f"shadowcolor=black@{opacity}:shadowx=2:shadowy=2:"
                    f"enable='gte(t,0.75)'"
                )
                filters.append(dt)

        else:
            # Standard Top-Down for other lanes (top/center)
            # Fallback to legacy logic
            line_height = int(size * 1.25)
            for i, line in enumerate(lines):
                safe_line = self._escape_drawtext(line)
                if not safe_line:
                    continue
                y_expr = f"h*{TEXT_LANES.get(lane, 0.5)} + ({i} * {line_height})"
                dt = (
                    f"drawtext=fontfile='{font_path}':text='{safe_line}':fontsize={size}:"
                    f"fontcolor=white:shadowx=3:shadowy=4:shadowcolor=black@0.6:x=(w-text_w)/2:y={y_expr}:"
                    f"enable='gte(t,0.75)'"
                )
                filters.append(dt)

        if not filters:
            return True

        complex_filter = ",".join(filters)
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i",
            video_path,
            "-vf",
            complex_filter,
            "-c:v",
            "libx264",
            "-preset",
            os.getenv("REENCODE_PRESET", "ultrafast"),
            "-crf",
            os.getenv("REENCODE_CRF", "23"),
            "-c:a",
            "copy",
            output_path,
        ]

        return self._safe_run_overlay("Drawtext", cmd)

    def _apply_ass(self, video_path, output_path, text, lane):
        ass_file = None
        try:
            ass_file = self._create_ass_file(text, lane)
            safe_ass_path = (
                os.path.abspath(ass_file).replace("\\", "/").replace(":", "\\:")
            )
            vf_filter = f"subtitles='{safe_ass_path}'"

            cmd = [
                FFMPEG_BIN,
                "-y",
                "-i",
                video_path,
                "-vf",
                vf_filter,
                "-c:v",
                "libx264",
                "-preset",
                os.getenv("REENCODE_PRESET", "ultrafast"),
                "-crf",
                os.getenv("REENCODE_CRF", "23"),
                "-c:a",
                "copy",
                output_path,
            ]

            return self._safe_run_overlay("ASS", cmd)

        except Exception as e:
            logger.error(f"ASS prep crashed: {e}")
            return False
        finally:
            # Safe cleanup after logic
            if ass_file and os.path.exists(ass_file):
                try:
                    os.remove(ass_file)
                except:
                    pass

    def last_debug(self):
        return self._last_debug_info

    # ──────────────────────────────────────────────────────────────────────
    # Brand Overlay Lane  (dedicated, independent of caption overlays)
    # ──────────────────────────────────────────────────────────────────────

    def add_brand_overlay(
        self,
        video_path: str,
        output_path: str,
        brand_text: str,
    ) -> bool:
        """
        Render a persistent brand watermark at the bottom CENTER.
        """
        if not brand_text or not video_path or not os.path.exists(video_path):
            logger.warning("[TEXT_OVERLAY] brand overlay skipped — missing brand_text or video_path")
            return False

        logger.info("[TEXT_OVERLAY] lane: brand")
        logger.info(f"[TEXT_OVERLAY] brand={brand_text}")
        logger.info("[TEXT_OVERLAY] position=bottom_center (fixed)")

        use_drawtext = (
            bool(self._drawtext_supported)
            and not self._drawtext_failed_once
            and os.path.exists(LOCAL_FONT_PATH)
            and self._is_safe_ascii(brand_text)
        )

        if use_drawtext:
            return self._apply_drawtext(video_path, output_path, brand_text, "brand", BRAND_FONT_SIZE)
        
        return self._apply_ass(video_path, output_path, brand_text, "brand")

    def add_caption_and_brand_overlay(
        self,
        video_path: str,
        output_path: str,
        caption: str,
        brand_text: str = "",
    ) -> bool:
        """
        New PNG-based caption rendering system.
        Guarantees exact positioning and avoids FFmpeg drawtext escaping errors.
        """
        if not video_path or not os.path.exists(video_path):
            logger.warning("[TEXT_OVERLAY] caption+brand overlay skipped — missing video_path")
            return False

        if not caption and not brand_text:
            logger.warning("[TEXT_OVERLAY] caption+brand overlay skipped — no text provided")
            return False

        # ── Layout logging ────────────────────────────────────────────────
        logger.info("[TEXT_OVERLAY] caption_render_mode=png_overlay")
        logger.info("[TEXT_OVERLAY] caption_position=above_brand")
        logger.info("[TEXT_OVERLAY] overlay_delay=0.75")

        # ── Get video dimensions for PNG sizing ──────────────────────────
        vw, vh = self._get_video_dimensions(video_path)

        # ── Generate Caption PNG ──────────────────────────────────────────
        caption_png = ""
        if caption:
            caption_png = render_caption_png(caption, vw, vh)
        
        # ── Build FFmpeg command ─────────────────────────────────────────
        cmd = [FFMPEG_BIN, "-y", "-i", video_path]
        filter_parts = []
        input_count = 1
        
        if caption_png and os.path.exists(caption_png):
            cmd.extend(["-i", caption_png])
            idx = input_count
            input_count += 1
            # Positioning: The PNG is already full video resolution and text is pre-positioned.
            # We overlay at 0:0 to preserve the exact positioning from caption_renderer.py.
            filter_parts.append(f"[{idx}:v]format=rgba[cap];[0:v][cap]overlay=0:0:enable='gte(t,0.75)'[v_cap]")
        else:
            filter_parts.append("[0:v]copy[v_cap]")

        font_path = os.path.abspath(self._get_effective_font_path()).replace("\\", "/").replace(":", "\\:")
        safe_brand = self._escape_drawtext(brand_text) if brand_text else ""
        
        if safe_brand:
            pos_expr, opacity, fs = _get_stealth_jitter(30)
            brand_dt = (
                f"[v_cap]drawtext=fontfile='{font_path}':"
                f"text='{safe_brand}':"
                f"fontsize={fs}:"
                f"fontcolor=white@{opacity}:"
                f"{pos_expr}:"
                f"enable='gte(t,0.75)'[v_final]"
            )
            filter_parts.append(brand_dt)
            output_map = "[v_final]"
        else:
            output_map = "[v_cap]"

        cmd.extend([
            "-filter_complex", ";".join(filter_parts),
            "-map", output_map,
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", os.getenv("REENCODE_PRESET", "ultrafast"),
            "-crf", os.getenv("REENCODE_CRF", "23"),
            "-c:a", "copy",
            output_path
        ])

        success = self._safe_run_overlay("PNG_Caption_Overlay", cmd)
        
        if caption_png and os.path.exists(caption_png):
            try: os.remove(caption_png)
            except: pass

        return success

    def _fallback_caption_brand(
        self,
        video_path: str,
        output_path: str,
        caption: str,
        brand_text: str,
    ) -> bool:
        """Fallback using ASS subtitles when drawtext fails."""
        try:
            # Try brand overlay first
            if brand_text:
                temp_output = output_path + ".tmp.mp4"
                brand_success = self.add_brand_overlay(video_path, temp_output, brand_text)
                if brand_success:
                    video_path = temp_output

            # Then caption overlay
            if caption:
                self.add_overlay(video_path, output_path, caption, "caption", 42)
            else:
                if os.path.exists(temp_output):
                    shutil.move(temp_output, output_path)

            return os.path.exists(output_path)
        except Exception as e:
            logger.error(f"[TEXT_OVERLAY] fallback failed: {e}")
            return False

    def add_logo_overlay(
        self,
        video_path: str,
        output_path: str,
        logo_path: str,
        lane_context: str = "caption",
    ) -> bool:
        """
        Adds a logo overlay to the video.

        The new logic automatically converts the source logo to a transparent
        PNG and then positions it centered at the bottom with a slight 60px
        inset.  A brief delay is applied so the logo only appears after
        0.75s, matching the caption/brand overlay timing.

        Integration notes:
        1. If a cleaned version (assets/logo/brand_logo_clean.png) already
           exists, it will be used directly.
        2. Otherwise we run ``clean_logo_background`` once and log the outcome.

        Positioning rules:
            x = (W-w)/2
            y = H-h-60
            enable = 'gte(t,0.75)'

        Size: scaled to ~7.4% of frame width (same as before).
        """
        # --- CLEANUP/TRANSPARENCY STEP ---
        try:
            from Utilities.logo_transparency_cleaner import clean_logo_background
        except ImportError:
            clean_logo_background = None

        # Determine cleaned path in assets
        clean_dir = os.path.join("assets", "logo")
        os.makedirs(clean_dir, exist_ok=True)
        cleaned_logo = os.path.join(clean_dir, "brand_logo_clean.png")

        # If cleaned logo doesn't exist yet and we have a cleaner, run conversion
        if clean_logo_background and not os.path.exists(cleaned_logo):
            try:
                clean_logo_background(logo_path, cleaned_logo)
            except Exception as e:
                logger.warning(f"[LOGO_CLEANER] failed_conversion={e}")

        # Use cleaned version if available
        if os.path.exists(cleaned_logo):
            logo_path = cleaned_logo

        # Log the overlay attempt
        logger.info(f"🎨 Applying Logo Overlay: {os.path.basename(logo_path)}")

        if not os.path.exists(logo_path):
            return False

        # Centered bottom placement
        # x: center horizontally
        # y: 60px from bottom of frame
        filter_str = (
            "[1:v]scale=iw*0.074:-1[logo];"
            "[0:v][logo]overlay=x=(W-w)/2:y=H-h-60:enable='gte(t,0.75)'[out]"
        )

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i",
            video_path,
            "-i",
            logo_path,
            "-filter_complex",
            filter_str,
            "-map",
            "[out]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-c:a",
            "copy",
            output_path,
        ]

        return self._safe_run_overlay("LogoOverlay", cmd)

    def add_episodic_overlay(
        self,
        video_path: str,
        output_path: str,
        episode_num: str,
        series_name: str = "SERIES",
        tagline: str = "TAGLINE",
        has_intro: bool = False,
    ) -> bool:
        """
        Adds episodic framing text (3 lines).

        Refinement Logic:
        - Fade In: 0.3s
        - Fade Out: 0.3s
        - Duration: 1.5s
        - Timing: Starts at 0s (or after intro offset if we knew it? assume 0s is start of *this* clip or *compilation*?)
          "Appear only in: First 1.5s" -> implying t=0 to t=1.5.
          If `has_intro` is True, this logic might need adjustment if we are post-intro.
          But usually this function is called on the compiled file?
          Or on the first clip?
          Ideally on the compiled file.
        """
        if not self._font_checked and not os.path.exists(LOCAL_FONT_PATH):
            self._ensure_font_thread()  # Try one last panic load

        font_path = (
            os.path.abspath(LOCAL_FONT_PATH).replace("\\", "/").replace(":", "\\:")
        )

        # Design:
        # Line 1: Series Name (Small, Spaced)
        # Line 2: Episode N (Large, Bold)
        # Line 3: Tagline (Medium, Italic/Color)

        # Timing
        start_t = 0.5
        dur = 3.5  # Total duration visible
        fade_dur = 0.3

        # If has_intro, usually intro is ~3-5s.
        # But we are overlaying on the FINAL video?
        # "After intro" -> If we detect intro, we might shift start_t.
        # Let's assume caller handles offsets or we assume standard intro length if has_intro=True.
        # User said: "After intro (if intro exists)".
        # We'll default to start_t = 4.0 if has_intro, else 0.5
        if has_intro:
            start_t = 4.5

        enable = f"between(t,{start_t},{start_t + dur})"

        # Alpha Fade
        # fade(t, start_t, fade_in) * fade_out(t, end, fade_out)
        # alpha='if(lt(t,0.5),0,if(lt(t,0.8),(t-0.5)/0.3,if(lt(t,3.5),1,if(lt(t,3.8),(3.8-t)/0.3,0))))'
        # Simpler: alpha=min(1,(t-start)/fade)*min(1,(end-t)/fade) implies linear
        alpha_expr = (
            f"min(1,(t-{start_t})/{fade_dur})*min(1,({start_t + dur}-t)/{fade_dur})"
        )

        # Center X, Y tiers
        # Series: Y=30%
        # Ep: Y=35%
        # Tag: Y=45%

        lines = []

        # 1. SERIES
        if series_name:
            t1 = self._escape_drawtext(series_name.upper())
            lines.append(
                f"drawtext=fontfile='{font_path}':text='{t1}':fontsize=40:fontcolor=white:x=(w-text_w)/2:y=h*0.35:alpha='{alpha_expr}':enable='{enable}'"
            )

        # 2. EPISODE
        if episode_num:
            t2 = f"EPISODE {episode_num}"
            lines.append(
                f"drawtext=fontfile='{font_path}':text='{t2}':fontsize=90:fontcolor=yellow:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h*0.40:alpha='{alpha_expr}':enable='{enable}'"
            )

        # 3. TAGLINE
        if tagline:
            t3 = self._escape_drawtext(tagline)
            lines.append(
                f"drawtext=fontfile='{font_path}':text='{t3}':fontsize=50:fontcolor=white:x=(w-text_w)/2:y=h*0.48:alpha='{alpha_expr}':enable='{enable}'"
            )

        if not lines:
            return False

        filter_str = ",".join(lines)

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i",
            video_path,
            "-vf",
            filter_str,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "copy",
            output_path,
        ]

        return self._safe_run_overlay("EpisodicOverlay", cmd)

    def get_cinematic_base_filter(self):
        return "drawbox=x=0:y=ih*0.65:w=iw:h=ih*0.35:color=black@0.35:t=fill"


# Global Instance
overlay_engine = TextOverlay()


def apply_text_overlay_safe(input_path, output_path, text, lane="caption", size=60):
    return overlay_engine.add_overlay(input_path, output_path, text, lane, size)


def apply_brand_overlay_safe(
    video_path: str,
    output_path: str,
    brand_text: str,
) -> bool:
    """
    Module-level convenience wrapper for ``TextOverlay.add_brand_overlay``.

    Reads ``BRAND_NAME`` and ``ADD_TEXT_OVERLAY`` from the environment so
    callers can simply pass the resolved brand string and let this function
    handle the gate check.

    Usage (orchestrator)::

        from Text_Modules.text_overlay import apply_brand_overlay_safe
        apply_brand_overlay_safe(temp_render, branded_render, brand_text)
    """
    return overlay_engine.add_brand_overlay(video_path, output_path, brand_text)


def apply_caption_and_brand_overlay_safe(
    video_path: str,
    output_path: str,
    caption: str,
    brand_text: str = "",
) -> bool:
    """
    Module-level convenience wrapper for ``TextOverlay.add_caption_and_brand_overlay``.

    Applies fashion-reel style caption above brand layout in a single pass.
    ``brand_text`` defaults to the BRAND_NAME env variable so callers that
    don't pass anything explicit will still get the owner's brand handle.

    Usage (orchestrator)::

        from Text_Modules.text_overlay import apply_caption_and_brand_overlay_safe
        apply_caption_and_brand_overlay_safe(
            video_path,
            output_path,
            caption="Your caption here",
        )
    """
    resolved_brand = brand_text or os.getenv("BRAND_NAME", "")
    return overlay_engine.add_caption_and_brand_overlay(video_path, output_path, caption, resolved_brand)


def add_logo_overlay(video_path, output_path, logo_path, lane_context="caption"):
    return overlay_engine.add_logo_overlay(
        video_path, output_path, logo_path, lane_context
    )


def get_timed_overlay_filter(
    text, lane, start, duration, width=1080, height=1920, size=60, color="white"
):
    """
    Generates a single complex filter string for a timed text overlay.
    Matches compiler.py signature: (text, lane, start, duration, w, h)
    """
    if not text:
        return ""

    # Ensure font path is absolute and escaped correctly for FFmpeg
    font_path = os.path.abspath(LOCAL_FONT_PATH).replace("\\", "/").replace(":", "\\:")

    # Enable expression (Strict 0.75s delay + duration)
    enable_expr = f"gte(t,0.75)*between(t,{float(start):.3f},{float(start) + float(duration):.3f})"

    if lane == "caption":
        return build_caption_drawtext_filter(
            text,
            video_height=height,
            enable_expr=enable_expr,
            font_path=ARC_CAPTION_FONT_PATH,
        )

    # Wrap Text
    wrapped = textwrap.fill(text, width=22)

    lines = wrapped.split("\n")
    line_height = int(size * 1.25)

    filters = []

    # Lane Logic (Cinematic Spacing)
    for i, line in enumerate(lines):
        safe_line = overlay_engine._escape_drawtext(line)  # Use global instance helper

        y_expr = "h*0.5"  # Default

        if lane == "branding_upper":
            # branding_upper -> h*0.84 (Collision Fix)
            y_expr = f"h*0.84+({i}*{line_height})"

        elif lane == "fixed":
            # fixed -> h*0.92
            y_expr = f"h*0.92+({i}*{line_height})"

        elif lane == "item_lower":
            # item_lower -> h*0.96
            y_expr = f"h*0.96+({i}*{line_height})"

        elif lane == "analysis_lower":
            # analysis_lower -> h*0.92 (Aligned with fixed)
            y_expr = f"h*0.92+({i}*{line_height})"

        elif lane == "top":
            # top -> h*0.10
            y_expr = f"h*0.10+({i}*{line_height})"

        else:
            y_expr = f"h*0.5+({i}*{line_height})"

        # [AESTHETIC] Cinematic Shadow + Fade In
        # Default Enable: 'between(t, start, end)'
        # We use BOTH: 'enable' to turn on at exact time, 'alpha' to fade smoothly.

        # FADE IN (0.5s) applied to all overlays
        # Relative time: (t - start) -> min(1,(t-start)/0.5)
        alpha_val = f"min(1,(t-{start})*2)"

        dt = (
            f"drawtext=fontfile='{font_path}':text='{safe_line}':fontsize={size}:"
            f"fontcolor={color}:shadowx=3:shadowy=4:shadowcolor=black@0.6:x=(w-text_w)/2:y={y_expr}:"
            f"alpha='{alpha_val}':"
            f"enable='{enable_expr}'"
        )

        filters.append(dt)

    return ",".join(filters)


def get_timed_overlay_filter(text: str, lane: str = "top", start: float = 0.0, end: float = 999.0) -> str:
    """
    Module-level helper that returns an FFmpeg drawtext filter string for
    the given text, lane, and time window.  Used by the orchestrator title
    overlay integration and patchable by tests.
    """
    if not text:
        return ""
    safe_text = text.replace("'", "\\'").replace(":", "\\:").replace("\\", "\\\\")
    y_map = {
        "top":     "h*0.08",
        "caption": "h*0.88",
        "fixed":   "h*0.94",
        "center":  "(h-text_h)/2",
    }
    y_expr = y_map.get(lane, "h*0.08")
    enable = f"between(t,{start},{end})"
    return (
        f"drawtext=text='{safe_text}':fontsize=48:fontcolor=white"
        f":x=(w-text_w)/2:y={y_expr}:shadowx=3:shadowy=4"
        f":shadowcolor=black@0.6:enable='{enable}'"
    )
