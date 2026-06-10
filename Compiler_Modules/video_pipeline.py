import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any


def _safe_parse_fps(raw_fps: str) -> float:
    """Safely parse fractional FPS strings like '60000/1001' without using eval()."""
    try:
        if "/" in raw_fps:
            num, den = raw_fps.split("/", 1)
            return float(num) / float(den)
        return float(raw_fps)
    except Exception:
        return 30.0


def _safe_temp_path(prefix: str = "fc_script_") -> str:
    """
    Create a temp file in a path that FFmpeg can open on Windows.

    tempfile.mkstemp() may return an 8.3 short-name path like
    C:/Users/MIDHUN~1/... which FFmpeg's internal fopen() cannot resolve.
    This helper expands the temp dir to its long-form path first via the
    Windows kernel API, then builds the temp file inside it.
    """
    raw_tmp = tempfile.gettempdir()
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        ret = ctypes.windll.kernel32.GetLongPathNameW(raw_tmp, buf, 32768)
        long_tmp = buf.value if ret > 0 else raw_tmp
    except Exception:
        long_tmp = raw_tmp

    fd, path = tempfile.mkstemp(suffix=".txt", prefix=prefix, dir=long_tmp)
    os.close(fd)  # close raw fd; caller will open the file by name
    return path.replace("\\", "/")

logger = logging.getLogger("video_pipeline")

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

# ── Env-driven safety thresholds (configure in Credentials/.env) ─────────────
# Max video file size before pipeline rejects input (default: 2 GB)
MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", "2000"))
# Max seconds to allow a single ffmpeg render job before killing it (default: 30 min)
FFMPEG_TIMEOUT_SEC = int(os.getenv("FFMPEG_TIMEOUT_SEC", "1800"))

# --- DYNAMIC RENDER SETTINGS ---
RENDER_TARGET = os.getenv("RENDER_TARGET", "quality").strip().lower()

if RENDER_TARGET == "speed":
    logger.info(
        "⚡ RENDER_TARGET = speed. Using `-crf 26` and `-preset fast` for faster uploads."
    )
    REENCODE_PRESET = "fast"
    REENCODE_CRF = "26"
else:
    logger.info(
        "🎥 RENDER_TARGET = quality. Using `-crf 20` and `-preset medium` for maximum detail."
    )
    REENCODE_PRESET = "medium"
    REENCODE_CRF = "20"  # High quality (Default)


def get_video_info(path):
    """Probe video metadata."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration,r_frame_rate",
            "-of",
            "json",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            logger.warning(
                f"Probe: no video stream found in '{path}' — returning empty info"
            )
            return {}
        stream = streams[0]
        return {
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "duration": float(stream.get("duration", 0)),
            "fps": _safe_parse_fps(stream.get("r_frame_rate", "30/1")),
        }
    except subprocess.TimeoutExpired:
        logger.error(f"Probe timed out for '{path}'")
        return {}
    except Exception as e:
        logger.error(f"Probe failed: {e}")
        return {}


def validate_video_input(file_path: str) -> None:
    """
    Pre-flight security guard: runs BEFORE any codec processing.
    Raises ValueError immediately on oversized or corrupt inputs.
    Env vars: MAX_VIDEO_SIZE_MB, FFMPEG_TIMEOUT_SEC
    """
    # Guard 1: File existence
    if not os.path.exists(file_path):
        raise ValueError(f"Input file not found: {file_path}")

    # Guard 2: File size (zip-bomb / oversize protection)
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if size_mb > MAX_VIDEO_SIZE_MB:
        raise ValueError(
            f"🚫 Input rejected: {size_mb:.0f} MB exceeds MAX_VIDEO_SIZE_MB={MAX_VIDEO_SIZE_MB} MB. "
            "Increase MAX_VIDEO_SIZE_MB in .env if intentional."
        )
    logger.info(f"✅ [PREFLIGHT] Size check passed: {size_mb:.1f} MB / {MAX_VIDEO_SIZE_MB} MB limit")

    # Guard 2: Magic byte check (extension spoofing prevention)
    # MP4/MOV containers always have the ASCII string 'ftyp' at bytes 4–7.
    # Reading 12 bytes is sufficient and near-zero cost (no codec involved).
    # Reference: ISO 14496-12 §4.3 — File Type Box
    try:
        with open(file_path, "rb") as _f:
            _header = _f.read(12)
        if len(_header) < 8 or _header[4:8] != b"ftyp":
            raise ValueError(
                f"🚫 Input rejected: not a valid MP4 container "
                f"(bytes 4-7: {_header[4:8].hex() if len(_header) >= 8 else 'too short'}). "
                "Extension spoofing suspected."
            )
        logger.info(
            f"✅ [PREFLIGHT] Magic byte check passed: "
            f"ftyp={_header[8:12].decode('ascii', errors='replace')} "
            f"(brand confirmed)"
        )
    except ValueError:
        raise  # re-raise our own rejection
    except Exception as _e:
        raise ValueError(f"🚫 Input rejected: could not read file header — {_e}")

    # Guard 3: Container integrity (ffprobe error-mode scan)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", file_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if probe.returncode != 0 or probe.stderr.strip():
            raise ValueError(
                f"🚫 Input rejected: corrupt or invalid container — "
                f"{probe.stderr.strip()[:120]}"
            )
        logger.info("✅ [PREFLIGHT] Container integrity check passed.")
    except subprocess.TimeoutExpired:
        raise ValueError("🚫 Input rejected: ffprobe integrity check timed out (corrupt file suspected).")



def render_pipeline(
    input_path: str,
    output_path: str,
    filters: list = [],
    speed_factor: float = 1.0,
    color_intensity: float = 0.0,
    filter_type: str = "cinematic",
    mirror_mode: bool = False,
    trim_duration: float = None,
    price_tag_images: list = None,
    price_tag_time: float = 2.0,
    timeline_instructions: dict = None,
) -> bool:
    """
    Core Rendering Pipeline (The "Ferrari Engine")
    executes ALL visual transformations (Trim, Crop, Color, Text, Image Overlays) in ONE PASS.
    """
    # ── Pre-Flight Security Guard ────────────────────────────────────────────
    # Validates: file existence → size limit → container integrity
    # Thresholds configurable in Credentials/.env:
    #   MAX_VIDEO_SIZE_MB  (default: 2000)
    #   FFMPEG_TIMEOUT_SEC (default: 1800)
    try:
        validate_video_input(input_path)
    except ValueError as e:
        logger.error(f"❌ [PREFLIGHT REJECTED] {e}")
        return False

    # 1. Inputs Construction (Opt: Hardware Decode if available)
    inputs = ["-hwaccel", "auto", "-i", input_path]

    overlay_tag_paths = []
    if price_tag_images:
        if isinstance(price_tag_images, str):
            price_tag_images = [price_tag_images]
        for tag in price_tag_images:
            if tag and os.path.exists(tag):
                inputs.extend(["-i", tag])
                overlay_tag_paths.append(tag)

    # 2. Build Filter Graph
    # We use stream labels to pass the video through multiple independent stages
    vid_node = "[0:v]"
    aud_node = "[0:a]"
    graph_nodes = []

    # --- VIDEO STAGES ---
    # Stage A: Trim (Start at 1s, duration=trim_duration) & Reset Timestamps
    if trim_duration and trim_duration > 0:
        graph_nodes.append(
            f"{vid_node}trim=start=1:duration={trim_duration},setpts=PTS-STARTPTS[v_trim]"
        )
        vid_node = "[v_trim]"

        # Sync Audio Trim
        graph_nodes.append(
            f"{aud_node}atrim=start=1:duration={trim_duration},asetpts=PTS-STARTPTS[a_trim]"
        )
        aud_node = "[a_trim]"

    # -------------------------------------------------------------------
    # Stage A2: SMART SCENE EDITOR (Visual Effects, Transitions, Zoom)
    # -------------------------------------------------------------------
    if timeline_instructions is None:
        logger.info("SmartSceneEditor inactive — running original render pipeline")
    else:
        cuts = timeline_instructions.get("cuts", [])
        transitions = timeline_instructions.get("transitions", [])
        zoom_effects = timeline_instructions.get("zoom_effects", [])
        smart_reframe = timeline_instructions.get("smart_reframe", [])

        # --- Sub-Stage A20: Smart Reframe (Subject Tracking) ---
        if smart_reframe:
            # Single crop filter with nested IFs because crop doesn't support :enable
            x_expr = "(iw-1080)/2"
            for rf in reversed(smart_reframe):
                r_start = round(float(rf.get("start", 0.0)), 4)
                r_end = round(float(rf.get("end", 0.0)), 4)
                off_x = int(rf.get("offset_x", 0))
                x_expr = (
                    f"if(between(t,{r_start},{r_end}),(iw-1080)/2+({off_x}),{x_expr})"
                )

            graph_nodes.append(
                f"{vid_node}crop=w=1080:h=1920:x='{x_expr}':y='(ih-1920)/2'[v_reframe]"
            )
            vid_node = "[v_reframe]"
            logger.info(
                f"🎯 Applied {len(smart_reframe)} smart reframe track(s) via dynamic expression"
            )

        # --- Sub-Stage A2a: Transition Effects (stateless overlays) ---
        scene_vfilters = []
        for trans in transitions:
            t_type = trans.get("type", "hard_cut")
            between_idx = trans.get("between", [1, 2])
            t_dur = trans.get("duration", 0.2)

            if len(cuts) >= between_idx[0]:
                t_time = cuts[between_idx[0] - 1]
                start_trans = round(max(0.0, t_time - t_dur / 2.0), 4)
                end_trans = round(t_time + t_dur / 2.0, 4)

                if t_type in ("flash_transition", "glitch_pop", "beat_cut"):
                    # Flash white — stateless colorchannelmixer (capped at 2.0 per FFmpeg specs)
                    scene_vfilters.append(
                        f"colorchannelmixer=rr=2.0:gg=2.0:bb=2.0"
                        f":enable='between(t,{start_trans},{end_trans})'"
                    )
                elif t_type in (
                    "whip_pan",
                    "swipe_transition",
                    "motion_blur_transition",
                ):
                    # Motion blur simulation — stateless boxblur
                    scene_vfilters.append(
                        f"boxblur=lr=15:lp=1:cr=15:cp=1"
                        f":enable='between(t,{start_trans},{end_trans})'"
                    )
                elif t_type in ("speed_ramp_cut", "snap_transition", "camera_push"):
                    # [mkpv-fix] Softened contrast/saturation to prevent unwanted "flash lighting"
                    # was: eq=contrast=1.6:saturation=1.2
                    scene_vfilters.append(
                        f"eq=contrast=1.2:saturation=1.1"
                        f":enable='between(t,{start_trans},{end_trans})'"
                    )

        if scene_vfilters:
            graph_nodes.append(f"{vid_node}{','.join(scene_vfilters)}[v_scene]")
            vid_node = "[v_scene]"
            logger.info(f"🎞️ Applied Smart Scene transitions: {len(scene_vfilters)} effects")

        # --- Sub-Stage A2b: Zoom Effects (crop+scale, video-native) ---
        # IMPORTANT: zoompan is designed for STILL IMAGES and causes green/corrupt
        # artifacts on video. Instead, we use crop+scale which is video-native.
        # Easing: zoom ramps from 1.0x → target over the first 400ms using
        #   zoom(t) = base + (max_zoom * min((t - z_start) / 0.4, 1.0))
        # which gives a smooth camera push-in without any frame discontinuity.
        if zoom_effects:
            # Build a single nested-if expression for the zoom factor.
            # We iterate reversed so the first effect in the list wins (last written = outermost).
            z_expr = "1.0"
            for zfx in reversed(zoom_effects):
                z_type = zfx.get("type", "slow_zoom_in")
                z_start = round(float(zfx.get("start", 0.0)), 4)
                z_end   = round(float(zfx.get("end",   z_start + 2.0)), 4)

                # max_zoom determines final magnification above 1.0x
                if z_type == "punch_zoom":
                    max_zoom = 0.25
                elif z_type == "slow_zoom_in":
                    max_zoom = 0.15
                elif z_type == "slow_zoom_out":
                    max_zoom = 0.15   # same magnitude, applied inverted below
                else:
                    max_zoom = 0.12

                # t_local = time elapsed since this effect started (clamped [0,1] over 400ms)
                ease_in = f"min((t-{z_start})/0.4,1.0)"

                # slow_zoom_out: start at max, ease back toward 1.0
                if z_type == "slow_zoom_out":
                    inner_expr = f"(1.0+{max_zoom}*(1.0-{ease_in}))"
                else:
                    inner_expr = f"(1.0+{max_zoom}*{ease_in})"

                # Nest: if we are in this window, use inner_expr, else fall through
                z_expr = f"if(between(t,{z_start},{z_end}),{inner_expr},{z_expr})"

            # Crop center at iw/Z x ih/Z, then scale back to target size
            z_w = f"'iw/({z_expr})'"
            z_h = f"'ih/({z_expr})'"
            z_x = f"'(iw-iw/({z_expr}))/2'"
            z_y = f"'(ih-ih/({z_expr}))/2'"
            z_filter = (
                f"crop=w={z_w}:h={z_h}:x={z_x}:y={z_y},"
                f"scale=1080:1920:flags=bilinear,setsar=1"
            )
            graph_nodes.append(f"{vid_node}{z_filter}[v_zoom]")
            vid_node = "[v_zoom]"
            logger.info(
                f"🔍 Applied {len(zoom_effects)} zoom effects (ease-in crop+scale, video-native)"
            )

        # --- Sub-Stage A2c: Micro Speed Ramps (VIDEO ONLY) ---
        # Applies stateless setpts=PTS/speed on video for each ramp window.
        # NOTE: Audio ramps via atempo are SKIPPED because FFmpeg's atempo filter
        # does NOT support the :enable timeline option. The micro speed ramps are
        # very short (0.2-0.3s windows) so audio desync is imperceptible.
        speed_ramps = timeline_instructions.get("speed_ramps", [])
        if speed_ramps:
            ramp_vfilters = []
            for ramp in speed_ramps:
                r_start = round(float(ramp.get("start", 0.0)), 4)
                r_end = round(float(ramp.get("end", r_start + 0.3)), 4)
                r_speed = float(ramp.get("speed", 1.1))
                # Clamp speed to safe range [0.5, 2.0]
                r_speed = max(0.5, min(2.0, r_speed))

                # Video: setpts=PTS/(speed) inside the time window
                # Outside the window the filter is bypassed — stateless
                # NOTE: setpts uses uppercase 'T' for timestamp (not lowercase 't')
                ramp_vfilters.append(
                    f"setpts='if(between(T,{r_start},{r_end}),PTS/{r_speed:.4f},PTS)'"
                )

            if ramp_vfilters:
                graph_nodes.append(f"{vid_node}{','.join(ramp_vfilters)}[v_ramps]")
                vid_node = "[v_ramps]"

            logger.info(
                f"⚡ Applied {len(speed_ramps)} micro speed ramp(s) to filter graph (video-only)"
            )

    # Stage B: Core Visuals (Mirror, Speed, Scale/Pad, Color)
    base_vfilters = []
    if mirror_mode:
        base_vfilters.append("hflip")

    if abs(speed_factor - 1.0) > 0.01:  # Lower threshold to catch 1.04x
        base_vfilters.append(f"setpts={1 / speed_factor}*PTS")

        # Sync Audio Speed (atempo allows 0.5 to 2.0)
        # Handle speed_factor if it's out of atempo range (not expected here but safe)
        temp_speed = speed_factor
        a_speed_filters = []
        while temp_speed > 2.0:
            a_speed_filters.append("atempo=2.0")
            temp_speed /= 2.0
        while temp_speed < 0.5:
            a_speed_filters.append("atempo=0.5")
            temp_speed /= 0.5
        a_speed_filters.append(f"atempo={temp_speed}")

        graph_nodes.append(f"{aud_node}{','.join(a_speed_filters)}[a_speed]")
        aud_node = "[a_speed]"

    # --- Stage B: Smart Reframe / Aspect Ratio Handling ---
    # Default: Letterbox (safe but boring)
    v_aspect_filter = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
    
    # [V5] Smart Reframe (9:16 Crop)
    if timeline_instructions and timeline_instructions.get("smart_reframe"):
        try:
            v_info = get_video_info(input_path)
            iw, ih = v_info.get("width", 1920), v_info.get("height", 1080)
            
            # If video is landscape, we want to crop
            if iw > ih:
                reframe_pts = timeline_instructions["smart_reframe"]
                # Calculate median target_x (0.0 - 1.0)
                # Note: SmartSceneEditor currently returns pixel x or normalized x.
                # We'll normalize here.
                target_vals = []
                for pt in reframe_pts:
                    tx = pt.get("target_x")
                    if tx is not None:
                        # Normalize pixel coordinates (assuming 1920 context if > 1.0)
                        if tx > 1.0: tx /= iw
                        target_vals.append(max(0.1, min(0.9, tx))) # Clamp to avoid edge jitter
                
                if target_vals:
                    # Use median for maximum stability
                    target_vals.sort()
                    median_x = target_vals[len(target_vals)//2]
                    
                    # FFmpeg logic: scale so height matches target (1920), then crop width (1080)
                    # scaled_iw = iw * (1920/ih)
                    # crop_x = (median_x * scaled_iw) - 540
                    # Clamped between 0 and (scaled_iw - 1080)
                    
                    scaled_iw_expr = f"iw*(1920/ih)"
                    cx_expr = f"({median_x}*{scaled_iw_expr})-540"
                    clamp_x = f"min(max(0,{cx_expr}),{scaled_iw_expr}-1080)"
                    
                    v_aspect_filter = f"scale=-1:1920:force_original_aspect_ratio=increase,crop=1080:1920:{clamp_x}:0,setsar=1"
                    logger.info(f"🎯 [SMART_CROP] Applied subject-centered crop at x_pct={median_x:.2f}")
        except Exception as _sc_e:
            logger.warning(f"⚠️ Smart crop calculation failed: {_sc_e}")

    base_vfilters.append(v_aspect_filter)

    if color_intensity > 0:
        if filter_type == "cinematic":
            c = 1.0 + (0.1 * color_intensity)
            s = 1.0 + (0.3 * color_intensity)
            base_vfilters.append(f"eq=contrast={c}:saturation={s}")
        elif filter_type == "fashion":
            # Warm skin tone, punchy saturation — vignette removed (was overriding transitions visually)
            c = round(max(0.0, min(2.0, 1.0 + (0.12 * color_intensity))), 4)
            s = round(max(0.0, min(3.0, 1.0 + (0.35 * color_intensity))), 4)
            b = round(max(-1.0, min(1.0, 0.0 + (0.02 * color_intensity))), 4)
            base_vfilters.append(f"eq=contrast={c}:saturation={s}:brightness={b}")
            base_vfilters.append("hue=h=3:s=1.05")
        elif filter_type == "noir":
            base_vfilters.append("hue=s=0,eq=contrast=1.2")
        elif filter_type == "vibrant":
            base_vfilters.append("eq=saturation=1.5:brightness=0.05")

    # Stage B2: timeline_instructions effects — merged into base_vfilters so they
    # land inside the single [v_core] chain (no intermediate labels, no [v_text]).
    # Supported effect tokens: "cinematic" → eq=contrast/saturation
    if timeline_instructions:
        tl_effects = timeline_instructions.get("effects", [])
        tl_text_filters = timeline_instructions.get("text_filters", [])

        for effect in tl_effects:
            if effect == "cinematic":
                # Apply a cinematic colour grade (contrast + saturation boost).
                # Uses fixed values so the filter is always predictable in tests.
                base_vfilters.append("eq=contrast=1.1:saturation=1.3")
            elif effect == "fashion":
                base_vfilters.append("eq=contrast=1.12:saturation=1.35:brightness=0.02")
                base_vfilters.append("hue=h=3:s=1.05")
            elif effect == "vibrant":
                base_vfilters.append("eq=saturation=1.5:brightness=0.05")
            elif effect == "noir":
                base_vfilters.append("hue=s=0,eq=contrast=1.2")

        # Merge text_filters from timeline_instructions directly into [v_core]
        # (avoids creating a separate [v_text] intermediate label)
        if tl_text_filters:
            base_vfilters.extend(tl_text_filters)

    # Stage C: Text Overlays (Merged into Stage B base_vfilters for optimization)
    if filters:
        if isinstance(filters, list):
            base_vfilters.extend(filters)
        else:
            base_vfilters.append(filters)

    if base_vfilters:
        graph_nodes.append(f"{vid_node}{','.join(base_vfilters)}[v_core]")
        vid_node = "[v_core]"

    # Stage D: Image/Video Overlay (Price Tags)
    if overlay_tag_paths:
        for i, tag_path in enumerate(overlay_tag_paths):
            tag_stream = f"[{i + 1}:v]"
            is_tracking_video = tag_path.lower().endswith((".mov", ".webm", ".mp4"))

            if is_tracking_video:
                # CRITICAL: format=rgba preserves alpha channel. Without it, pad fills
                # empty space with green (uninitialized YUV chroma) causing green screen.
                # pad color 0x00000000 = fully transparent black.
                scale_pad = "format=rgba,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x00000000"
                graph_nodes.append(f"{tag_stream}{scale_pad}[tag_scaled_{i}]")
                graph_nodes.append(
                    f"{vid_node}[tag_scaled_{i}]overlay=0:0:format=auto[v_out_{i}]"
                )
            else:
                # Use Gemini-decided timing for price tags
                t_start = max(0.0, price_tag_time)
                t_end = min(5.0, t_start + 4.25)
                graph_nodes.append(
                    f"{vid_node}{tag_stream}overlay=0:0:enable='between(t,{t_start:.2f},{t_end:.2f})'[v_out_{i}]"
                )

            vid_node = f"[v_out_{i}]"

    # 3. Render Command
    cmd = [FFMPEG_BIN, "-y", *inputs]

    fc_script_path = None
    if graph_nodes:
        fc_graph = ";".join(graph_nodes)
        import time, tempfile

        # [WIN_FIX] Use -filter_complex inline instead of -filter_complex_script.
        # The script file approach causes 'Invalid argument' on Windows because
        # FFmpeg's internal fopen() fails to resolve temp paths reliably.
        cmd.extend(["-filter_complex", fc_graph])
        # [mkpv-fix] Sanitize map specifiers: Remove brackets if they refer to raw input streams
        # because -map [0:a] is invalid, while -map 0:a is correct.
        vid_map = vid_node.strip("[]") if vid_node in ["[0:v]", "[0:a]"] else vid_node
        aud_map = aud_node.strip("[]") if aud_node in ["[0:v]", "[0:a]"] else aud_node
        # Append '?' to gracefully ignore if the input video lacks an audio stream
        if aud_map == "0:a":
            aud_map = "0:a?"
    else:
        vid_map = "0:v"
        aud_map = "0:a?"

    cmd.extend(
        [
            "-map",
            vid_map,
            "-map",
            aud_map,  # Map audio stream
            "-c:v",
            "libx264",
            "-preset",
            REENCODE_PRESET,
            "-crf",
            REENCODE_CRF,
            "-pix_fmt",
            "yuv420p",  # Force stable pixel format (prevents green screen)
            "-c:a",
            "aac",
            "-b:a",
            "192k",  # Re-encode audio to maintain sync with speed/trim
            output_path,
        ]
    )

    logger.info("[VIDEO_PIPELINE] pixel_format_forced=yuv420p")

    logger.info(
        f"🏎️ Single-Pass Pipeline Engine: {len(graph_nodes)} stages | Tag: {bool(price_tag_images)}"
    )
    render_t0 = time.time()
    res = None
    success = False
    try:
        res = subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT_SEC,
        )
        success = True
    except subprocess.TimeoutExpired:
        logger.error(
            f"⏱️ Render TIMEOUT after {FFMPEG_TIMEOUT_SEC}s — job killed. "
            "Increase FFMPEG_TIMEOUT_SEC in .env for long videos."
        )
        return False
    except subprocess.CalledProcessError as e:
        err_tail = e.stderr.decode(errors="ignore")[-800:]
        logger.warning(f"⚠️ Single-Pass Render Failed:\n{err_tail}")
        logger.info("🔄 Effects/zoom likely caused failure. Retrying with stripped pass-through render...")

        # Clean up the failed script file
        if fc_script_path and os.path.exists(fc_script_path):
            try:
                os.remove(fc_script_path)
            except Exception:
                pass
            fc_script_path = None  # Signal not to try removing again below

        # Build a clean, minimal fallback command.
        # No filter_complex, no zooms, just re-encode raw streams. 
        # Use 0:v and 0:a? (raw stream indices, NOT bracket labels)
        cmd_fallback = [
            FFMPEG_BIN, "-y",
            "-i", input_path,
            "-map", "0:v",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", REENCODE_PRESET,
            "-crf", REENCODE_CRF,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path,
        ]
        try:
            res = subprocess.run(
                cmd_fallback, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=FFMPEG_TIMEOUT_SEC,
            )
            success = True
            logger.info("✅ Fallback pass-through render succeeded.")
        except subprocess.TimeoutExpired:
            logger.error(
                f"⏱️ Fallback Render TIMEOUT after {FFMPEG_TIMEOUT_SEC}s — job killed."
            )
        except subprocess.CalledProcessError as e2:
            logger.error(f"❌ Fallback Render also failed:\n{e2.stderr.decode(errors='ignore')[-800:]}")
            try:
                with open("last_failed_command.txt", "w", encoding="utf-8") as f:
                    f.write(" ".join(str(x) for x in cmd_fallback))
            except Exception:
                pass

    render_elapsed = time.time() - render_t0

    if fc_script_path and os.path.exists(fc_script_path):
        try:
            os.remove(fc_script_path)
        except Exception:
            pass

    if not success:
        return False

    # Parse frames from stderr to calculate FPS
    stderr_text = res.stderr.decode(errors="ignore") if res else ""
    frame_count = 0
    matches = re.findall(r"frame=\s*(\d+)", stderr_text)
    if matches:
        frame_count = int(matches[-1])

    fps_measured = round(frame_count / render_elapsed, 1) if render_elapsed > 0 else 0.0
    logger.info(
        f"⚡ Render complete — {frame_count} frames processed at {fps_measured} fps "
        f"in {render_elapsed:.2f}s"
    )
    return True



# ═══════════════════════════════════════════════════════════════════════════════
# SCENE RECONSTRUCTION PIPELINE (trim → effects → concat → render)
# ═══════════════════════════════════════════════════════════════════════════════


def build_trim_segments(
    segments: list,
    input_paths: list = None,
    speed_factor: float = 1.0,
    color_intensity: float = 0.7,   # [FIX] was 1.2 — caused saturation flash between segments
    filter_type: str = "fashion",
    mirror_mode: bool = False,
) -> dict:
    """
    Step 1: Build per-segment FFmpeg trim + visual transform filters.

    For each segment generates:
      [0:v]trim=start=X:end=Y,setpts=PTS-STARTPTS,<scale>,<color>[v_segN]
      [0:a]atrim=start=X:end=Y,asetpts=PTS-STARTPTS[a_segN]

    Returns:
      {
        "graph_parts":  [str],   # FFmpeg filter_complex fragments
        "vid_labels":  [str],   # [v_seg0], [v_seg1], ...
        "aud_labels":  [str],   # [a_seg0], [a_seg1], ...
        "valid_segments": [dict], # segments that passed validation
        "durations":     [float], # per-segment duration (after trim)
      }
    """
    # [mkpv-fix] Robustness: Handle if segments is a list of timestamps (cuts) instead of dicts
    if segments and isinstance(segments[0], (int, float)):
        logger.info("🛠 Converting cut timestamps to reconstruction segments")
        new_segs = []
        last_t = 0.0
        for cut in sorted(segments):
            if cut > last_t:
                new_segs.append({"start": last_t, "end": cut})
                last_t = cut
        segments = new_segs

    # [MULTI_CLIP FIX] Build a per-clip duration map so we can use the MAXIMUM
    # clip duration as the canonical sentinel passed to sanitize_segments.
    # The old code used only input_paths[0]'s duration, which caused every
    # segment from clip 1 / clip 2 whose end > clip-0-duration to be silently
    # dropped by sanitize_segments before the per-segment capping loop even ran.
    _clip_dur_map = {}
    try:
        if input_paths:
            for _ci, _cp in enumerate(input_paths):
                try:
                    _clip_dur_map[_ci] = get_video_info(_cp).get("duration", 30.0)
                except Exception:
                    _clip_dur_map[_ci] = 30.0
            # Use the longest clip as the sentinel so no segment is discarded too early.
            # The per-segment loop below will cap each segment to its own clip's length.
            max_clip_dur = max(_clip_dur_map.values()) if _clip_dur_map else 30.0
        else:
            max_clip_dur = 30.0
    except Exception:
        max_clip_dur = 30.0

    # [mkpv-fix] Avoid returning empty segments if already cleaned upstream
    # render_scene_reconstruction already clamped and preserved order
    valid_segs = segments

    if not valid_segs:
        return {
            "graph_parts": [],
            "vid_labels": [],
            "aud_labels": [],
            "valid_segments": [],
            "durations": [],
        }

    graph_parts = []
    vid_labels = []
    aud_labels = []
    durations = []

    for i, seg in enumerate(valid_segs):
        c_id = int(seg.get("clip_id", 0))

        # [mkpv-fix] Get clip duration to cap t_end (Prevents FFmpeg -22 errors)
        try:
            info = get_video_info(input_paths[c_id])
            clip_duration = info.get("duration", 0)
        except:
            clip_duration = 0

        t_start = round(float(seg["start"]), 4)
        t_end = round(float(seg["end"]), 4)

        if clip_duration > 0 and t_end > clip_duration:
            logger.warning(
                f"⚠️ Segment {i}: t_end ({t_end}) exceeds duration ({clip_duration}). Capping."
            )
            t_end = clip_duration

        dur = round(t_end - t_start, 4)
        if dur < 0.1:
            continue  # Skip if capped too short

        durations.append(dur)
        
        # [mkpv-fix] Production-Ready Hard Guard (Clamping)
        max_index = max(0, len(input_paths) - 1)
        clip_id = seg.get("clip_id", 0)

        if not isinstance(clip_id, int) or clip_id < 0 or clip_id > max_index:
            logger.warning(f"⚠️ [CLIP_GUARD] Invalid clip_id={clip_id}, forcing 0 (max_index={max_index})")
            clip_id = 0  # force safe fallback

        # Stream label construction
        v_in = f"[{clip_id}:v]"
        a_in = f"[{clip_id}:a]"

        v_out = f"[v_seg{i}]"
        a_out = f"[a_seg{i}]"

        # ── Video: trim → reset PTS ──────────────────────────────────────
        vf = f"{v_in}trim=start={t_start}:end={t_end},setpts=PTS-STARTPTS"

        if mirror_mode:
            vf += ",hflip"

        if abs(speed_factor - 1.0) > 0.01:
            vf += f",setpts={1 / speed_factor:.6f}*PTS"

        # Scale + pad to 1080×1920 (vertical shorts format)
        vf += ",scale=1080:1920:force_original_aspect_ratio=decrease"
        vf += ",pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1"

        # Color grading intentionally removed from per-segment chain.
        # All eq/hue/vignette is applied once as a master grade in
        # build_concat_pipeline so every segment looks uniformly graded
        # and there are no inter-cut saturation or brightness flashes.

        # Guarantee format and frame rate at the absolute end of the segment filter chain
        # (This prevents color-space corruptions causing the full green screen)
        vf += ",fps=30,format=yuv420p"

        seg_label = f"[v_seg{i}]"
        graph_parts.append(f"{vf}{seg_label}")
        vid_labels.append(seg_label)

        # ── Audio: trim → reset PTS ──────────────────────────────────────
        a_label = f"[a_seg{i}]"
        # [MULTI_CLIP FIX] Probe the actual audio stream — NOT video duration.
        # get_video_info() selects v:0 only, so its "duration" key is always the
        # video duration and tells us nothing about audio. Using it as has_audio
        # caused silent clips (e.g. OpenCV-inpainted Watermark_Buffer files) to
        # emit an audio filter label that referenced a non-existent [N:a] stream,
        # crashing FFmpeg with "Stream specifier… matches no streams".
        try:
            import json as _json
            _aprobe_cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "json",
                input_paths[c_id],
            ]
            _aprobe = subprocess.run(_aprobe_cmd, capture_output=True, text=True, timeout=8)
            _adata = _json.loads(_aprobe.stdout)
            has_audio = bool(_adata.get("streams"))
        except Exception:
            has_audio = True  # Assume audio exists if probe fails

        if has_audio:
            a_speed_part = ""
            if abs(speed_factor - 1.0) > 0.01:
                spd = speed_factor
                a_parts = []
                while spd > 2.0:
                    a_parts.append("atempo=2.0")
                    spd /= 2.0
                while spd < 0.5:
                    a_parts.append("atempo=0.5")
                    spd /= 0.5
                a_parts.append(f"atempo={spd:.4f}")
                a_speed_part = "," + ",".join(a_parts)

            # [mkpv-fix] Guard against zero-duration or invalid audio trims
            safe_t_end = max(t_start + 0.1, t_end)
            graph_parts.append(
                f"{a_in}atrim=start={t_start}:end={safe_t_end},asetpts=PTS-STARTPTS"
                f"{a_speed_part},aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo{a_label}"
            )
            aud_labels.append(a_label)
        else:
            logger.warning(
                f"🔇 Segment {i} (clip {c_id}) has no audio stream — skipping audio trim"
            )

    return {
        "graph_parts": graph_parts,
        "vid_labels": vid_labels,
        "aud_labels": aud_labels,
        "valid_segments": valid_segs,
        "durations": durations,
    }


def build_transition_graph(
    trim_data: dict,
    timeline_instructions: dict = None,
) -> list:
    """
    Step 2: Append per-segment effects (transitions, zoom, speed ramps)
    to the filter graph AFTER the trim stage.

    Modifies v_seg labels in-place:
      [v_seg0] → transition/zoom → [v_fx0]
      [v_seg1] → (no fx)         → [v_fx1]  (just null)

    Returns the updated graph_parts list. Also mutates trim_data vid_labels.
    """
    segments = trim_data["valid_segments"]
    durations = trim_data["durations"]
    n = len(segments)
    effect_nodes = []
    new_labels = []

    tl = timeline_instructions or {}
    zoom_effects = tl.get("zoom_effects", [])
    speed_ramps = tl.get("speed_ramps", [])

    for i, seg in enumerate(segments):
        old_label = trim_data["vid_labels"][i]
        dur = durations[i]
        seg_fx = []

        # ── Per-segment Zoom Effect ──────────────────────────────────────
        # Check if any zoom effect from timeline_instructions applies here
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)
        zoom_applied = False
        for zf in zoom_effects:
            # Match by scene index (1-based from smart_scene_editor) OR overlapping timestamps
            matches_scene = zf.get("scene") == i + 1
            z_start = float(zf.get("start", -1))
            z_end = float(zf.get("end", -1))
            matches_time = (z_start >= 0 and z_end > 0) and (
                z_end > seg_start and z_start < seg_end
            )

            if matches_scene or matches_time:
                z_type = zf.get("type", "slow_zoom_in")
                # Dynamic crop math with linear interpolation ease-in over 400ms
                max_zoom = 0.18 if "punch" in z_type else 0.12
                z_expr = f"(1.0+{max_zoom}*min(t/0.4\\,1))"

                z_w = f"iw/{z_expr}"
                z_h = f"ih/{z_expr}"

                # Subject-Aware Center Calculation
                # [QUOTE-FIX] wrap crop values in '' so inner commas (from min/max)
                # are not misinterpreted as filter chain separators by FFmpeg.
                bbox = seg.get("bbox")
                if (
                    bbox
                    and isinstance(bbox, dict)
                    and "x" in bbox
                    and "y" in bbox
                    and "w" in bbox
                    and "h" in bbox
                ):
                    cx = int(bbox["x"] + bbox["w"] / 2)
                    cy = int(bbox["y"] + bbox["h"] / 2)

                    # Ensure we don't go out of bounds
                    z_x = f"'max(0\\,min(iw-{z_w}\\,{cx}-{z_w}/2))'"
                    z_y = f"'max(0\\,min(ih-{z_h}\\,{cy}-{z_h}/2))'"
                else:
                    z_x = f"'(iw-{z_w})/2'"
                    z_y = f"'(ih-{z_h})/2'"

                seg_fx.append(
                    f"crop=w='{z_w}':h='{z_h}':x={z_x}:y={z_y},scale=1080:1920:flags=bilinear,setsar=1"
                )
                zoom_applied = True
                break

        # Fallback: Apply a subtle slow zoom to high-importance segments if no Gemini zoom
        importance = seg.get("importance", 0)
        if not zoom_applied and importance > 0.6 and dur >= 2.5:
            # Dynamic crop math with linear interpolation ease-in over 400ms
            max_zoom = 0.10
            z_expr = f"(1.0+{max_zoom}*min(t/0.4\\,1))"
            
            z_w = f"iw/{z_expr}"
            z_h = f"ih/{z_expr}"

            # Subject-Aware Center Calculation
            # [QUOTE-FIX] wrap crop values in '' so inner commas (from min/max)
            # are not misinterpreted as filter chain separators by FFmpeg.
            bbox = seg.get("bbox")
            if (
                bbox
                and isinstance(bbox, dict)
                and "x" in bbox
                and "y" in bbox
                and "w" in bbox
                and "h" in bbox
            ):
                cx = int(bbox["x"] + bbox["w"] / 2)
                cy = int(bbox["y"] + bbox["h"] / 2)

                # Ensure we don't go out of bounds
                z_x = f"'max(0\\,min(iw-{z_w}\\,{cx}-{z_w}/2))'"
                z_y = f"'max(0\\,min(ih-{z_h}\\,{cy}-{z_h}/2))'"
            else:
                z_x = f"'(iw-{z_w})/2'"
                
                # [FOCUS_ON_WEAR] Dynamic Y-axis bias based on category
                _focus_on_wear = tl.get("focus_on_wear", False)
                _wear_cat = tl.get("wear_category", "").lower()
                
                if _focus_on_wear and _wear_cat:
                    if _wear_cat in ("skirt", "pants", "cargo", "shorts", "jeans", "trousers", "leggings", "joggers", "footwear"):
                        # Bias downwards (75% down)
                        z_y = f"'(ih-{z_h})*0.75'"
                    elif _wear_cat in ("top", "shirt", "blouse", "hoodie", "jacket", "blazer", "t-shirt", "polo", "vest", "saree-blouse"):
                        # Bias upwards (25% down)
                        z_y = f"'(ih-{z_h})*0.25'"
                    else:
                        z_y = f"'(ih-{z_h})/2'"
                else:
                    z_y = f"'(ih-{z_h})/2'"

            seg_fx.append(
                f"crop=w='{z_w}':h='{z_h}':x={z_x}:y={z_y},scale=1080:1920:flags=bilinear,setsar=1"
            )

        # ── Per-segment Speed Ramp ───────────────────────────────────────
        # Check if any speed ramp falls within this segment's original time range
        for ramp in speed_ramps:
            matches_scene = ramp.get("scene") == i + 1
            r_start = float(ramp.get("start", -1))
            r_end = (
                float(ramp.get("end", -1))
                if "end" in ramp
                else (r_start + 0.3 if r_start >= 0 else -1)
            )
            matches_time = (r_start >= 0 and r_end > 0) and (
                r_end > seg_start and r_start < seg_end
            )

            if matches_scene or matches_time:
                r_speed = max(0.5, min(2.0, float(ramp.get("speed", 1.1))))
                # Map to segment-local time
                local_start = max(0.0, r_start - seg_start) if r_start >= 0 else 0.0
                local_end = min(dur, r_end - seg_start) if r_end >= 0 else min(dur, 0.3)
                seg_fx.append(
                    f"setpts='if(between(T,{local_start:.3f},{local_end:.3f}),PTS/{r_speed:.4f},PTS)'"
                )
                break  # Only one ramp per segment

        # ── Inter-segment Transition ─────────────────────────────────────
        # Prioritize explicit "transition" key injected by bridge, fallback to style
        style = seg.get(
            "transition", seg.get("style", seg.get("transition_after", "clean"))
        )

        if style not in ("clean", "cut", "match_cut") and i < n - 1:
            # [STIE & TIE] Use precomputed transition_duration.
            _stie_dur = seg.get("transition_duration")
            
            # [TIE] Extract alignment and easing if available
            tie = seg.get("tie_decision", {})
            tie_exec = tie.get("execution", {})
            alignment = tie_exec.get("alignment", "End")
            easing = tie_exec.get("easing", "Ease-In-Out")
            
            if _stie_dur and float(_stie_dur) > 0:
                trans_duration = round(max(0.04, min(0.60, float(_stie_dur))), 3)
                logger.debug(
                    f"[STIE_RENDER] seg={i} style={style} "
                    f"dur={trans_duration*1000:.0f}ms "
                    f"align={alignment} easing={easing} "
                    f"rag={seg.get('transition_rag_hit', False)}"
                )
            else:
                # Legacy beat-interval formula
                _beat_interval = seg.get("beat_interval")
                if _beat_interval and float(_beat_interval) > 0:
                    trans_duration = round(
                        min(0.35, max(0.10, float(_beat_interval) * 0.20)), 3
                    )
                else:
                    trans_duration = 0.2  # safe fallback

            # [BEAT-SYNC / ALIGNMENT] Cut point offset.
            _frame_offset = seg.get("beat_offset", 0.033)
            
            if alignment == "Start":
                # Start at cut: the transition effect happens mostly on the NEXT clip,
                # but since we render on the outgoing clip, we push it to the very end
                trans_start = max(0.0, dur - (trans_duration * 0.2) - _frame_offset)
                trans_end = max(trans_start + 0.033, dur - _frame_offset)
            elif alignment == "Center":
                # Straddle the cut: half on outgoing, half on incoming
                trans_start = max(0.0, dur - (trans_duration * 0.5) - _frame_offset)
                trans_end = max(trans_start + 0.033, dur - _frame_offset)
            else:
                # "End": standard behaviour — effect completes exactly at the cut
                trans_start = max(0.0, dur - trans_duration - _frame_offset)
                trans_end = max(trans_start + 0.033, dur - _frame_offset)

            if style in ("whip_pan", "zoom_blur"):
                seg_fx.append(
                    f"boxblur=lr=12:lp=1:cr=12:cp=1"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )
            elif style == "fade":
                # Basic fade simulator via blur for strong beats
                seg_fx.append(
                    f"boxblur=lr=10:lp=1:cr=10:cp=1"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )
            elif style == "blur_cut":
                seg_fx.append(
                    f"boxblur=lr=5:lp=1:cr=5:cp=1"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )
            elif style == "punch_cut":
                # crop does not support :enable timeline — use contrast punch via eq instead
                seg_fx.append(
                    f"eq=contrast=1.8:saturation=1.3"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )
            elif style == "zoom_pop":
                # crop does not support :enable timeline — use contrast+blur pop instead
                seg_fx.append(
                    f"eq=contrast=1.6:saturation=1.2"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )
            elif style in ("glitch_pop", "flash"):
                # "flash" (drop beat) → sudden overexposure
                # MUST convert back to yuv420p because colorchannelmixer outputs RGB
                seg_fx.append(
                    f"colorchannelmixer=rr=2.0:gg=2.0:bb=2.0"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})',format=yuv420p"
                )

            # ── New STIE transition types ─────────────────────────────────
            elif style == "glow_fade":
                # Warm breath — boxblur + gentle brightness lift
                seg_fx.append(
                    f"boxblur=lr=8:lp=1:cr=8:cp=1,eq=brightness=0.12"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )
            elif style == "slow_fade":
                # Long dramatic fade — heavier blur radius
                seg_fx.append(
                    f"boxblur=lr=16:lp=2:cr=16:cp=2"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )
            elif style == "zoom_blur_fade":
                # Spin/twirl entry — boxblur (crop applied globally; imperceptible outside window)
                seg_fx.append(
                    f"boxblur=lr=10:lp=1:cr=10:cp=1"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )
                # 1.12x static zoom centred for the whole segment
                z_val = 1.12
                seg_fx.append(
                    f"crop=w=iw/{z_val}:h=ih/{z_val}:x=(iw-iw/{z_val})/2:y=(ih-ih/{z_val})/2,"
                    f"scale=1080:1920:flags=bilinear,setsar=1"
                )
            elif style == "dip_black":
                # Dip to black — crush brightness at cut point
                seg_fx.append(
                    f"eq=brightness=-1.0"
                    f":enable='between(t,{trans_start:.3f},{trans_end:.3f})'"
                )

        # ── Chain result ─────────────────────────────────────────────────
        new_label = f"[v_fx{i}]"
        if seg_fx:
            effect_nodes.append(f"{old_label}{','.join(seg_fx)}{new_label}")
        else:
            # No effects → rename via null filter
            effect_nodes.append(f"{old_label}null{new_label}")
        new_labels.append(new_label)

    # Update video labels to the new post-fx labels
    trim_data["vid_labels"] = new_labels
    return effect_nodes


def sanitize_segments(segments: list, duration: float, preserve_order: bool = False) -> list:
    """
    Validates, clamps, and filters scene reconstruction segments to prevent FFmpeg crashes.

    preserve_order=True: keeps the creative non-chronological order produced by
    RhythmTimelineBuilder / TimelineReconstructor. Only deduplicates and clamps —
    does NOT sort or merge overlapping source-time windows, because two segments
    that appear to overlap in SOURCE time (e.g. 11s→14s then 3s→6s) are intentional
    non-chronological cuts and must be kept as-is.
    """
    sanitized = []

    # 1. Deduplicate (keep first occurrence, preserve insertion order)
    unique_segs = []
    seen = set()
    for s in segments:
        key = (s.get("start"), s.get("end"), s.get("clip_id"))
        if key not in seen:
            seen.add(key)
            unique_segs.append(s)

    # Only sort when order doesn't matter (non-AI / fallback path)
    if not preserve_order:
        unique_segs = sorted(unique_segs, key=lambda x: x.get("start", 0))

    for idx, seg in enumerate(unique_segs):
        st = float(seg.get("start", 0))
        en = float(seg.get("end", 0))
        c_id = int(seg.get("clip_id", 0))
        reason = seg.get("reason", "ai_cut")

        # Clamp against actual video duration
        original_en = en
        en = min(en, duration - 0.05) if duration > 0 else en
        st = max(0.0, st)

        if original_en != en:
            logger.warning(
                f"⚠️ [SEGMENT_CLAMPED] Segment {idx} end clamped: {original_en:.2f} -> {en:.2f}"
            )

        if en <= st:
            logger.warning(
                f"🚫 [SEGMENT_REMOVED] Segment {idx} dropped: End ({en}) <= Start ({st})"
            )
            continue

        seg_dur = en - st
        if seg_dur < 0.4:
            logger.warning(
                f"🚫 [SEGMENT_REMOVED] Segment {idx} dropped: Duration ({seg_dur:.2f}) < 0.4s limit"
            )
            continue

        # Overlap prevention — ONLY for chronological (non-creative) paths
        # Creative paths may intentionally revisit the same source time window
        if not preserve_order:
            if sanitized and sanitized[-1]["clip_id"] == c_id and sanitized[-1]["end"] > st:
                prev = sanitized[-1]
                logger.warning(
                    f"🔄 [SEGMENT_SANITIZED] Merged overlapping segment {idx} with previous."
                )
                if en > prev["end"]:
                    prev["end"] = en
                continue

        # Preserve all fields from original segment (color_mode, style, transition, etc.)
        out_seg = dict(seg)
        out_seg.update({
            "clip_id": c_id,
            "start": round(st, 3),
            "end": round(en, 3),
            "reason": reason,
        })
        sanitized.append(out_seg)

    return sanitized


# ── CapCut-grade xfade transition library (inline, no ceie import needed) ──
# Custom pixel-math expressions — identical to ceie/tools/xfade_transitions.py
_XFADE_CUSTOM: dict = {
    "slide_left":     "if(gt(X,W*P),A,B)",
    "slide_right":    "if(lt(X,W*(1-P)),A,B)",
    "slide_up":       "if(gt(Y,H*P),A,B)",
    "slide_down":     "if(lt(Y,H*(1-P)),A,B)",
    "circle_reveal":  "if(lte(hypot(X-W/2,Y-H/2),hypot(W/2,H/2)*P),B,A)",
    "diagonal_tl":    "if(lt((X/W+Y/H)/2,P),B,A)",
    "diagonal_tr":    "if(lt((1-X/W+Y/H)/2,P),B,A)",
    "zoom_in":        "if(between(X,W/2*(1-P),W-W/2*(1-P))*between(Y,H/2*(1-P),H-H/2*(1-P)),B,A)",
    "wipe_soft":      "if(gt(X/(W*P),1.1),A,if(lt(X/(W*P),0.9),B,A*(1-(X/(W*P)-0.9)/0.2)+B*((X/(W*P)-0.9)/0.2)))",
    "crossfade":      "A*(1-P)+B*P",
    "pixel_dissolve": "if(gt(random(0),1-P),B,A)",
    "flash_white":    "if(lt(P,0.15),A*(1-P*6)+P*6,if(gt(P,0.85),(1-(P-0.85)*6)+B*((P-0.85)*6),B))",
    "dip_black":      "if(lt(P,0.5),mul(A,1-P*2),mul(B,(P-0.5)*2))",
    "punch":          "A*(1-pow(P,3))+B*pow(P,3)",
}

# FFmpeg native xfade names (no expr= needed)
_XFADE_BUILTIN: set = {
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite",
    "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "vertopen", "vertclose",
    "horzopen", "horzclose", "dissolve", "pixelize",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice",
    "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr",
    "squeezeh", "squeezev", "zoomin",
}

# Style-name aliases that map old AMTCE style names to xfade names
_STYLE_ALIAS: dict = {
    "whip_pan":      "slide_left",
    "zoom_blur":     "zoom_in",
    "zoom_pop":      "zoom_in",
    "blur_cut":      "crossfade",
    "punch_cut":     "punch",
    "glitch_pop":    "pixel_dissolve",
    "flash":         "flash_white",
    "glow_fade":     "crossfade",
    "slow_fade":     "crossfade",
    "zoom_blur_fade":"zoom_in",
    "fade":          "fade",          # native
}


def _resolve_xfade(name: str) -> str:
    """
    Returns the xfade filter fragment (transition=... part only — no duration/offset).
    Resolves custom expr transitions AND FFmpeg native builtins.
    """
    # Normalise via alias map first
    name = _STYLE_ALIAS.get(name, name)
    if name in _XFADE_CUSTOM:
        return f"transition=custom:expr='{_XFADE_CUSTOM[name]}'"
    if name in _XFADE_BUILTIN:
        return f"transition={name}"
    # Unknown → safe default
    logger.debug(f"[xfade] Unknown transition '{name}' — falling back to crossfade")
    return f"transition=custom:expr='{_XFADE_CUSTOM['crossfade']}'"


def build_concat_pipeline(
    trim_data: dict,
    text_filters: list = None,
    filter_type: str = "fashion",
    color_intensity: float = 0.7,
) -> list:
    """
    Step 3: Build the xfade transition chain, master color grade, and optional text overlays.

    Pipeline order (≥2 segments):
      xfade/acrossfade chain → master grade (eq + hue + vignette) → [text overlays] → [v_out]

    For a single segment, skips the chain entirely and just grades directly.

    Transitions are resolved per boundary from trim_data["valid_segments"][i]["transition"].
    Unrecognised or hard-cut styles fall back to a short crossfade (d=0.20 s).
    The xfade offset is computed from cumulative real segment durations minus
    the overlap window, preventing timebase drift across the chain.

    filter_type controls the grade preset:
      "fashion"   → warm skin tones, punchy saturation, soft vignette
      "vibrant"   → higher saturation, higher contrast, vignette
      "cinematic" → subtle contrast lift, cool tones, vignette

    Returns list of graph_parts to append to the filter_complex.
    """
    v_labels = trim_data["vid_labels"]
    a_labels = trim_data["aud_labels"]
    durations = trim_data.get("durations", [])
    valid_segs = trim_data.get("valid_segments", [])

    has_audio_track = len(a_labels) > 0

    if has_audio_track:
        paired = list(zip(v_labels, a_labels))
        if len(paired) < len(v_labels):
            logger.warning(
                f"[MULTI_CLIP] {len(v_labels) - len(paired)} video segment(s) had no audio partner "
                f"and were excluded from the A/V chain to prevent stream-count mismatch."
            )
        n = len(paired)
        paired_v = [p[0] for p in paired]
        paired_a = [p[1] for p in paired]
    else:
        n = len(v_labels)
        paired_v = list(v_labels)
        paired_a = []

    # ── Build master grade filter string ─────────────────────────────────
    grade_filters = []

    if color_intensity > 0:
        if filter_type == "vibrant":
            c = round(max(0.0, min(2.0, 1.0 + 0.18 * color_intensity)), 4)
            s = round(max(0.0, min(3.0, 1.0 + 0.55 * color_intensity)), 4)
            grade_filters.append(f"eq=contrast={c}:saturation={s}")
        elif filter_type == "fashion":
            c = round(max(0.0, min(2.0, 1.0 + 0.12 * color_intensity)), 4)
            s = round(max(0.0, min(3.0, 1.0 + 0.35 * color_intensity)), 4)
            b = round(max(-1.0, min(1.0, 0.0 + 0.02 * color_intensity)), 4)
            grade_filters.append(f"eq=contrast={c}:saturation={s}:brightness={b}")
            grade_filters.append("hue=h=3:s=1.05")
        elif filter_type == "cinematic":
            c = round(max(0.0, min(2.0, 1.0 + 0.08 * color_intensity)), 4)
            s = round(max(0.0, min(3.0, 1.0 + 0.30 * color_intensity)), 4)
            grade_filters.append(f"eq=contrast={c}:saturation={s}")

    # Vignette always applied — PI/6 is softer and more cinematic than PI/5
    grade_filters.append("vignette=PI/6")
    MASTER_GRADE = ",".join(grade_filters)
    # ─────────────────────────────────────────────────────────────────────

    parts = []
    safe_text = [f for f in (text_filters or []) if f]

    # ── Single-segment or empty: skip chain, go straight to grade ────────
    if n <= 1:
        if n == 0:
            return []
        # Only one segment — no transition possible
        v_single = paired_v[0]
        a_single = paired_a[0] if has_audio_track else None
        if has_audio_track:
            # Rename audio label so downstream can still use [a_out]
            parts.append(f"{a_single}acopy[a_out]")
        if safe_text:
            parts.append(f"{v_single}{MASTER_GRADE}[v_grade]")
            parts.append(f"[v_grade]{','.join(safe_text)}[v_out]")
        else:
            parts.append(f"{v_single}{MASTER_GRADE}[v_out]")
        return parts

    # ── Multi-segment: xfade video chain + acrossfade audio chain ────────
    #
    # xfade offset formula:
    #   offset_k = (sum of durations d0..dk) - (k+1) * trans_dur_k
    #   (each overlap eats `trans_dur` from the running total)
    #
    # We clamp trans_dur to leave at least 0.1 s of non-overlapping content
    # in each segment so very short clips never produce a negative offset.

    xfade_parts   = []   # filter_complex fragments for video chain
    afade_parts   = []   # filter_complex fragments for audio chain
    cumulative_dur = 0.0
    total_overlap  = 0.0  # sum of all transition windows consumed so far

    prev_v = paired_v[0]
    prev_a = paired_a[0] if has_audio_track else None
    seg_dur_0 = durations[0] if durations else 3.0
    cumulative_dur += seg_dur_0

    for i in range(1, n):
        cur_v = paired_v[i]
        cur_a = paired_a[i] if has_audio_track else None
        seg_dur = durations[i] if i < len(durations) else 3.0

        # ── Resolve transition for this boundary ─────────────────────────
        # Boundary i-1→i: use segment i-1's "transition" field
        seg_meta  = valid_segs[i - 1] if (i - 1) < len(valid_segs) else {}
        raw_style = seg_meta.get(
            "transition",
            seg_meta.get("style", seg_meta.get("transition_after", "crossfade"))
        )
        # Hard-cut styles → very short crossfade (visually imperceptible, technically safe)
        HARD_CUT_STYLES = {"clean", "cut", "match_cut", "hard_cut", "", None}
        if raw_style in HARD_CUT_STYLES:
            xfade_name = "crossfade"
            trans_dur  = 0.06   # 2 frames @ 30fps — visually a hard cut
        else:
            xfade_name = raw_style
            # Honour explicit transition_duration if set by STIE
            stie_dur = seg_meta.get("transition_duration")
            if stie_dur and float(stie_dur) > 0:
                trans_dur = round(max(0.06, min(0.60, float(stie_dur))), 3)
            else:
                beat_iv = seg_meta.get("beat_interval")
                if beat_iv and float(beat_iv) > 0:
                    trans_dur = round(min(0.40, max(0.12, float(beat_iv) * 0.25)), 3)
                else:
                    trans_dur = 0.30   # default CapCut-style

        # Safety: trans_dur must be < the shorter of the two neighbouring segments
        max_allowed = min(cumulative_dur - total_overlap, seg_dur) * 0.45
        trans_dur = round(min(trans_dur, max(0.06, max_allowed)), 3)

        # xfade offset = start time of incoming clip in the assembled video timeline
        xfade_offset = round(cumulative_dur - total_overlap - trans_dur, 3)
        xfade_offset = max(0.01, xfade_offset)  # never negative

        # ── Build xfade fragment ─────────────────────────────────────────
        frag = _resolve_xfade(xfade_name)
        is_last = (i == n - 1)
        out_v_label = "[v_chained]" if is_last else f"[xv{i}]"

        xfade_parts.append(
            f"{prev_v}{cur_v}xfade={frag}:duration={trans_dur}:offset={xfade_offset}{out_v_label}"
        )
        logger.debug(
            f"[XFADE] boundary {i-1}→{i}: '{xfade_name}' d={trans_dur}s offset={xfade_offset}s"
        )

        # ── Build acrossfade fragment ────────────────────────────────────
        if has_audio_track:
            # acrossfade: d=duration, c1=curve, c2=curve
            a_out_label = "[a_chained]" if is_last else f"[xa{i}]"
            afade_parts.append(
                f"{prev_a}{cur_a}acrossfade=d={trans_dur}:c1=tri:c2=tri{a_out_label}"
            )

        # Advance tracking state
        cumulative_dur  += seg_dur
        total_overlap   += trans_dur
        prev_v = out_v_label
        prev_a = a_out_label if has_audio_track else None

    # ── Emit all chain fragments ─────────────────────────────────────────
    parts.extend(xfade_parts)
    if has_audio_track:
        parts.extend(afade_parts)
        # Rename chained audio to canonical [a_out] label
        parts.append("[a_chained]acopy[a_out]")

    # ── Apply master grade + text overlays over assembled output ─────────
    chained_v = "[v_chained]"
    if safe_text:
        parts.append(f"{chained_v}{MASTER_GRADE}[v_grade]")
        parts.append(f"[v_grade]{','.join(safe_text)}[v_out]")
    else:
        parts.append(f"{chained_v}{MASTER_GRADE}[v_out]")

    return parts


def render_scene_reconstruction(
    input_path: Any,
    output_path: str,
    segments: list,
    filters: list = None,
    speed_factor: float = 1.0,
    color_intensity: float = 0.7,   # [FIX] was 1.2 — caused saturation flash between segments
    filter_type: str = "fashion",
    mirror_mode: bool = False,
    price_tag_images: list = None,
    price_tag_time: float = 2.0,
    timeline_instructions: dict = None,
    **kwargs
) -> bool:
    """
    Orchestrator: physically trims, applies per-scene effects, concatenates,
    and renders a reshaped highlight video — all in a single FFmpeg process.

    Pipeline:
      build_trim_segments   → trim + scale + color per segment
      build_transition_graph → zoom / speed ramp / transitions per segment
      build_concat_pipeline  → concat + post-concat text overlays
      FFmpeg render           → single filter_complex command

    Returns True on success, False on failure (caller falls back to render_pipeline).
    """
    input_paths = [input_path] if isinstance(input_path, str) else input_path
    if not input_paths:
        logger.error("render_scene_reconstruction: No input paths provided")
        return False

    for p in input_paths:
        if not os.path.exists(p):
            logger.error(f"render_scene_reconstruction: Input not found: {p}")
            return False

    if not segments:
        logger.warning("render_scene_reconstruction: no segments — cannot reconstruct")
        return False

    try:
        import time as _time

        # ── Step 0.5: Strict Segment Validation ──────────────────────────
        # Prioritize timeline_instructions if they exist
        _tl_scenes = (
            timeline_instructions.get("scenes", []) if timeline_instructions else []
        )
        if _tl_scenes:
            segments = _tl_scenes

        # [MULTI_CLIP FIX] Build a per-clip duration map so that each segment is
        # clamped against its OWN clip's length — not always clip 0's length.
        # The old code did `get_video_info(input_paths[0])` for every segment,
        # which silently truncated or dropped all segments from clips 1 and 2.
        _clip_dur_map = {}
        for _ci, _cp in enumerate(input_paths):
            try:
                _clip_dur_map[_ci] = get_video_info(_cp).get("duration", 0)
            except Exception:
                _clip_dur_map[_ci] = 0

        # Keep total_duration (clip 0) only for the logger line — validation
        # now uses _clip_dur_map keyed on each segment's clip_id.
        total_duration = _clip_dur_map.get(0, 0)
        logger.info(
            f"🔍 Validating {len(segments)} segments across {len(input_paths)} clip(s) "
            f"(clip-0 duration: {total_duration:.2f}s)..."
        )

        # If scenes come from an AI editor (timeline_instructions["scenes"]),
        # we must preserve their order to keep the creative reshaping. We still
        # clamp to duration and drop obviously invalid/too-short segments, but
        # we DO NOT sort or merge them.
        if _tl_scenes:
            clean_segs = []
            for idx, seg in enumerate(segments):
                try:
                    st = float(seg.get("start", 0))
                    en = float(seg.get("end", 0))
                    c_id = int(seg.get("clip_id", 0))
                    reason = seg.get("reason", "ai_cut")
                except (ValueError, TypeError):
                    continue

                # [MULTI_CLIP] Clamp against this segment's specific clip duration.
                _seg_clip_dur = _clip_dur_map.get(c_id, total_duration)
                original_en = en
                en = min(en, _seg_clip_dur - 0.05) if _seg_clip_dur > 0 else en
                st = max(0.0, st)

                if original_en != en:
                    logger.warning(
                        f"⚠️ [SEGMENT_CLAMPED_AI] Segment {idx} end clamped: {original_en:.2f} -> {en:.2f}"
                    )

                if en <= st:
                    logger.warning(
                        f"🚫 [SEGMENT_DROPPED_AI] Segment {idx} invalid: End ({en}) <= Start ({st})"
                    )
                    continue

                seg_dur = en - st
                if seg_dur < 0.4:
                    logger.warning(
                        f"🚫 [SEGMENT_DROPPED_AI] Segment {idx} too short: {seg_dur:.2f}s"
                    )
                    continue

                sanitized_seg = dict(seg)
                sanitized_seg.update(
                    {
                        "clip_id": c_id,
                        "start": round(st, 3),
                        "end": round(en, 3),
                        "reason": reason,
                    }
                )
                clean_segs.append(sanitized_seg)
        else:
            clean_segs = sanitize_segments(segments, total_duration)

        # ── SINGLE-SEGMENT FAST PATH ─────────────────────────────────────
        # The concat filter (used below) requires n≥2 inputs and crashes with n=1.
        # When exporting individual clips (OUTPUT_SEPARATE_SHORTS mode), each call
        # passes exactly 1 segment. We handle that here with a direct trim+re-encode.
        if len(clean_segs) == 1:
            seg = clean_segs[0]
            c_id = int(seg.get("clip_id", 0))
            t_start = float(seg.get("start", 0))
            t_end = float(seg.get("end", 0))
            seg_dur = round(t_end - t_start, 4)
            src = input_paths[c_id] if c_id < len(input_paths) else input_paths[0]
            logger.info(
                f"✂️ [SINGLE_CLIP] Rendering 1-segment clip: "
                f"{t_start:.2f}s→{t_end:.2f}s ({seg_dur:.2f}s) from '{src}'"
            )
            # Build filter: trim → scale/pad → color grade → fps/format
            _vf_parts = [
                f"trim=start={t_start}:end={t_end},setpts=PTS-STARTPTS",
                "scale=1080:1920:force_original_aspect_ratio=decrease",
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
                # Fashion color grade (matches build_concat_pipeline master grade)
                f"eq=contrast=1.084:saturation=1.245:brightness=0.014",
                "hue=h=3:s=1.05",
                "vignette=PI/6",
                "fps=30,format=yuv420p",
            ]
            _af_parts = [
                f"atrim=start={t_start}:end={t_end},asetpts=PTS-STARTPTS",
                "aresample=44100",
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
            ]
            _single_cmd = [
                FFMPEG_BIN, "-y",
                "-hwaccel", "auto",
                "-i", src,
                "-vf", ",".join(_vf_parts),
                "-af", ",".join(_af_parts),
                "-c:v", "libx264",
                "-preset", REENCODE_PRESET,
                "-crf", REENCODE_CRF,
                "-pix_fmt", "yuv420p",
                "-colorspace", "bt709",
                "-color_primaries", "bt709",
                "-color_trc", "bt709",
                "-c:a", "aac",
                "-b:a", "192k",
                output_path,
            ]
            try:
                _res = subprocess.run(
                    _single_cmd, check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300
                )
                logger.info(f"✅ [SINGLE_CLIP] Done → {output_path}")
                return True
            except subprocess.CalledProcessError as _e:
                _err = _e.stderr.decode(errors="ignore")[-600:]
                logger.error(f"❌ [SINGLE_CLIP] FFmpeg failed:\n{_err}")
                return False
        # ─────────────────────────────────────────────────────────────────

        if len(clean_segs) < 2:
            logger.warning(
                "🚫 [FFMPEG_SAFETY_GUARD] Fewer than 2 valid segments remain after sanitization. Falling back."
            )
            return False

        segments = clean_segs

        # ── Step 1: Trim Segments ────────────────────────────────────────
        trim_data = build_trim_segments(
            segments,
            input_paths=input_paths,
            speed_factor=speed_factor,
            color_intensity=color_intensity,
            filter_type=filter_type,
            mirror_mode=mirror_mode,
        )
        if not trim_data["valid_segments"]:
            logger.warning("render_scene_reconstruction: all segments too short")
            return False

        n = len(trim_data["valid_segments"])
        output_duration = sum(trim_data["durations"])
        logger.info(f"✂ Trim Segments: {n} shots → {output_duration:.1f}s highlight")

        # ── Step 2: Transition + Effects Per-Scene ───────────────────────
        effect_nodes = build_transition_graph(trim_data, timeline_instructions)

        # ── Step 3: Concat + Master Grade + Text Overlays ───────────────
        concat_nodes = build_concat_pipeline(
            trim_data,
            text_filters=filters,
            filter_type=filter_type,
            color_intensity=color_intensity,
        )

        final_vid_label = "[v_out]"

        # ── Step 0: Inputs Construction ──────────────────────────────────
        overlay_inputs = []
        overlay_tag_paths = []
        clip_inputs = []
        for p in input_paths:
            clip_inputs.extend(["-hwaccel", "auto", "-i", p])

        if price_tag_images:
            if isinstance(price_tag_images, str):
                price_tag_images = [price_tag_images]
            for i, tag in enumerate(price_tag_images):
                if tag and os.path.exists(tag):
                    overlay_inputs.extend(["-i", tag])
                    overlay_tag_paths.append(tag)

                    is_tracking_video = tag.lower().endswith((".mov", ".webm", ".mp4"))
                    t_start = max(0.0, price_tag_time)
                    t_end = min(5.0, t_start + 1.75) # Short snappy pop-up before she walks away

                    # Apply overlay after concat [v_out]
                    # Dynamic index: total input clips + this tag's index
                    tag_idx = len(input_paths) + i
                    overlay_label = f"[v_with_tag_{i}]"
                    
                    if is_tracking_video:
                        # Tracking videos handle their own timing/transparency
                        concat_nodes.append(
                            f"{final_vid_label}[{tag_idx}:v]overlay=0:0:format=auto{overlay_label}"
                        )
                    else:
                        # Static images need the timing guard
                        concat_nodes.append(
                            f"{final_vid_label}[{tag_idx}:v]overlay=0:0:enable='between(t,{t_start:.2f},{t_end:.2f})'{overlay_label}"
                        )
                    final_vid_label = overlay_label

        # ── Build Final Filter Complex ───────────────────────────────────
        merged_nodes = trim_data["graph_parts"] + effect_nodes + concat_nodes
        fc_graph = ";".join(merged_nodes)

        # [Render Verification]
        logger.info(
            f"📊 [RENDER_VERIFICATION] total_segments={n} | timeline_duration={output_duration:.2f}s"
        )

        # [WIN_FIX] Use -filter_complex inline instead of -filter_complex_script.
        # The script file approach causes 'Invalid argument' on Windows because
        # FFmpeg's internal fopen() fails to resolve temp paths reliably.
        fc_script_path = None  # No longer used; kept for cleanup logic compatibility

        # DEBUG EXPORTS
        import json

        try:
            with open("segments_debug.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "raw_segments": segments,
                        "clean_segs": clean_segs,
                        "durations": trim_data.get("durations", []),
                    },
                    f,
                    indent=2,
                )
            with open("graph_debug.txt", "w", encoding="utf-8") as f:
                f.write(fc_graph)
        except Exception as e:
            logger.warning(f"Failed to export debug files: {e}")

        # [MULTI_CLIP FIX] Audio map: use concat [a_out] when audio was trimmed.
        # If NO clips had an audio stream, fall back to -an (no audio output)
        # instead of [0:a] which crashes when the source has no audio stream.
        aud_labels = trim_data.get("aud_labels", [])
        aud_out_map = "[a_out]" if aud_labels else None  # None → -an added below

        # [MULTI_CLIP FIX] Only apply -hwaccel auto to the first (primary) input.
        # Applying it to every clip causes VP9/AV1 decode failures on clips 1+ when
        # they use codecs the hardware decoder doesn't support, producing corrupt frames.
        clip_inputs_final = []
        for _ci, _cp in enumerate(input_paths):
            if _ci == 0:
                clip_inputs_final.extend(["-hwaccel", "auto", "-i", _cp])
            else:
                clip_inputs_final.extend(["-i", _cp])  # software decode for extra clips

        # Build audio mapping args
        if aud_out_map is not None:
            _audio_map_args = ["-map", aud_out_map, "-c:a", "aac", "-b:a", "192k"]
        else:
            # No audio in any input — output video-only
            logger.info("🔇 [MULTI_CLIP] No audio streams found across all clips — outputting video-only.")
            _audio_map_args = ["-an"]

        cmd = [
            FFMPEG_BIN,
            "-y",
            *clip_inputs_final,
            *overlay_inputs,
            "-filter_complex",
            fc_graph,
            "-map",
            final_vid_label,
            *_audio_map_args,
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:v",
            "libx264",
            "-preset",
            REENCODE_PRESET,
            "-crf",
            REENCODE_CRF,
            output_path,
        ]

        logger.info(f"🏎️ Scene Reconstruction Render: {n} segment(s) → {output_path}")

        # --- FFmpeg Retry Logic ---
        max_retries = 1
        success = False
        last_error = ""

        for attempt in range(max_retries + 1):
            t0 = _time.time()
            try:
                res = subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=300,
                )
                success = True
                break  # Success, exit retry loop
            except subprocess.CalledProcessError as e:
                last_error = e.stderr.decode(errors="ignore")[-800:]
                logger.warning(f"⚠️ FFmpeg Error on attempt {attempt + 1}: {last_error}")
                if attempt < max_retries:
                    logger.info("🔄 Zoom/Crop math likely failed. Stripping effects and retrying...")
                    # Strip effect_nodes (zooms/speed ramps) but we MUST bridge the labels
                    # from [v_segX] to [v_fxX] so concat_nodes don't fail.
                    dummy_fx = []
                    for _idx in range(n):
                        # graph_parts always generates exactly [v_segX]
                        dummy_fx.append(f"[v_seg{_idx}]null[v_fx{_idx}]")
                        
                    fallback_nodes = trim_data["graph_parts"] + dummy_fx + concat_nodes
                    fallback_graph = ";".join(fallback_nodes)
                    # [RETRY-FIX] fc_script_path is None (we use inline -filter_complex).
                    # Patch the command list in-place to use the stripped fallback graph.
                    try:
                        _fc_idx = cmd.index("-filter_complex")
                        cmd[_fc_idx + 1] = fallback_graph
                        logger.info("🔄 Fallback graph injected inline into cmd.")
                    except (ValueError, IndexError) as _e:
                        logger.error(f"❌ Could not patch fallback graph into cmd: {_e}")
                        break
                    _time.sleep(2)  # Brief pause before retry
            except subprocess.TimeoutExpired:
                logger.error("❌ Scene Reconstruction timed out")
                break  # Don't retry timeouts

        # Cleanup script
        try:
            os.remove(fc_script_path)
        except:
            pass

        if not success:
            logger.error(
                f"❌ Scene Reconstruction FFmpeg failed after {max_retries + 1} attempts:\n{last_error}"
            )
            with open("last_failed_reconstruction.txt", "w", encoding="utf-8") as f:
                f.write(" ".join(cmd))
            return False

        elapsed = round(_time.time() - t0, 2)
        # We need to re-run the subprocess if we want the actual stderr of the final success run,
        # but since we suppressed stdout and it succeeded, we'll estimate frames from duration.
        frames = int(output_duration * 30)  # Estimation based on 30fps default
        fps_r = round(frames / elapsed, 1) if elapsed > 0 else 0
        logger.info(
            f"✅ Scene Reconstruction done in {elapsed}s "
            f"(~{frames} frames @ {fps_r} fps, {output_duration:.1f}s output)"
        )
        return True

    except Exception as e:
        logger.error(f"❌ Scene Reconstruction unexpected error: {e}")
        return False


def render_music_driven_video(
    video_path: str,
    music_path: str,
    output_path: str,
    timeline: list,
    text_filters: list = None,
):
    """
    Renders a music-driven video using the provided timeline and music.
    """
    try:
        from .music_driven_editor import MusicDrivenEditor

        editor = MusicDrivenEditor()
        graph_parts, vout_label = editor.generate_ffmpeg_commands(timeline)

        # Add text filters if present
        if text_filters:
            graph_parts.append(f"{vout_label}{','.join(text_filters)}[vtext]")
            vout_label = "[vtext]"

        # Handle Audio
        total_dur = sum(t["dur"] for t in timeline)
        graph_parts.append(
            f"[1:a]atrim=0:{total_dur:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0, total_dur - 1):.2f}:d=1[aout]"
        )

        filter_complex = ";".join(graph_parts)

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i",
            video_path,
            "-i",
            music_path,
            "-filter_complex",
            filter_complex,
            "-map",
            vout_label,
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            REENCODE_PRESET,
            "-crf",
            REENCODE_CRF,
            "-pix_fmt",
            "yuv420p",  # Force stable pixel format (prevents green screen)
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            output_path,
        ]

        logger.info("[VIDEO_PIPELINE] pixel_format_forced=yuv420p")

        logger.info(f"🎞️ Rendering Music-Driven Video: {output_path}")
        subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        return True

    except Exception as e:
        logger.error(f"❌ Music-Driven Render failed: {e}")
        return False


def render_juxtaposition(
    input_a: str,
    input_b: str,
    output_path: str,
    anchor_path: str = None,
    layout: str = "vertical",  # or "horizontal"
) -> bool:
    """
    Renders two videos in a side-by-side or top-bottom juxtaposition.
    The 'Law-Bending' core of the Synthetic Newsroom.
    """
    if not os.path.exists(input_a) or not os.path.exists(input_b):
        logger.error(f"Juxtaposition failed: Inputs not found.")
        return False

    # Filter logic:
    # 1. Scale both to 1080x960 (if vertical)
    # 2. Stack them
    # 3. Overlay anchor if provided

    if layout == "vertical":
        # Split 1080x1920 into two 1080x960 sections
        fc_graph = (
            "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[v0];"
            "[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[v1];"
            "[v0][v1]vstack=inputs=2[base]"
        )
    else:
        # Side-by-side (Square-ish for each)
        fc_graph = (
            "[0:v]scale=540:1920:force_original_aspect_ratio=increase,crop=540:1920[v0];"
            "[1:v]scale=540:1920:force_original_aspect_ratio=increase,crop=540:1920[v1];"
            "[v0][v1]hstack=inputs=2[base]"
        )

    inputs = ["-i", input_a, "-i", input_b]

    if anchor_path and os.path.exists(anchor_path):
        from .anchors import engine as anchor_engine

        inputs.extend(["-i", anchor_path])
        # Add anchor overlay to the chain
        # [base] is the stacked videos, [2:v] is the anchor
        host_filter = anchor_engine.get_overlay_filter()
        # Transform host filter to use common 'base' input
        host_filter = host_filter.replace("[0:v]", "[base]")
        fc_graph += f";{host_filter}"
    else:
        fc_graph += ";[base]null[out]"
        fc_graph = fc_graph.replace("[out]", "")  # Clean up if no anchor

    cmd = [
        FFMPEG_BIN,
        "-y",
        *inputs,
        "-filter_complex",
        fc_graph,
        "-c:v",
        "libx264",
        "-preset",
        REENCODE_PRESET,
        "-crf",
        REENCODE_CRF,
        "-pix_fmt",
        "yuv420p",  # Force stable pixel format (prevents green screen)
        "-c:a",
        "copy",
        output_path,
    ]

    logger.info("[VIDEO_PIPELINE] pixel_format_forced=yuv420p")

    logger.info(f"🚀 Rendering Juxtaposition ({layout})...")
    try:
        subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Juxtaposition Render Failed: {e.stderr.decode()}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
#  Audio Extraction Utility
# ══════════════════════════════════════════════════════════════════════════════

def extract_audio_from_video(
    video_path: str,
    output_dir: str = "Original_audio",
    overwrite: bool = False,
) -> str:
    """
    Extract the audio track from a video file into Original_audio/ as MP3.

    Called once per downloaded clip so the full continuous source audio is
    available for BeatEngine, CreativeEditorBridge and RhythmTimelineBuilder.
    The source video audio is never touched — this only creates a separate file.
    If Original_audio/ is ever wiped the pipeline falls back to source audio.

    Args:
        video_path:  Path to the source video.
        output_dir:  Destination folder  (default: Original_audio).
        overwrite:   Re-extract even if the mp3 already exists.

    Returns:
        Absolute path to the extracted MP3, or empty string on failure.
    """
    if not os.path.exists(video_path):
        logger.warning(f"[AUDIO_EXTRACT] Source not found: {video_path}")
        return ""

    # ══════════════════════════════════════════════════════════════════════
    # HARD FAIL-SAFE: Block all pipeline-generated output clips from the pool.
    # ONLY raw downloaded source clips are allowed into Original_audio/.
    #
    # Blocked patterns:
    #   first_shots / first_shot / general_intro → intro clips (ElevenLabs voice)
    #   _reaction / _textreaction                → Reaction Engine output clips
    #   final_compilation_with_intro             → final concat output
    #   watermark_clean                          → temp working copy (not original)
    # ══════════════════════════════════════════════════════════════════════
    if os.environ.get("SKIP_AUDIO_EXTRACT", "").lower() == "true":
        logger.info(f"🔇 [AUDIO_EXTRACT] Skipped due to SKIP_AUDIO_EXTRACT=true: {video_path}")
        return ""

    _vp_lower = video_path.lower()
    _vp_base  = os.path.basename(_vp_lower)

    _blocked_keywords = [
        "first_shots", "first_shot", "general_intro",
        "_reaction", "_textreaction",
        "final_compilation_with_intro",
        "intro_mixed_temp",
        "apify",
    ]
    for _kw in _blocked_keywords:
        if _kw in _vp_lower:
            logger.info(
                f"🚫 [AUDIO_EXTRACT] FAIL-SAFE TRIGGERED: "
                f"Blocked pipeline-generated clip '{_vp_base}' (matched: '{_kw}')"
            )
            return ""

    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(video_path))[0]
    out_path  = os.path.join(output_dir, f"{base_name}.mp3")

    if os.path.exists(out_path) and not overwrite:
        logger.info(
            f"[AUDIO_EXTRACT] Already exists — skipping: {os.path.basename(out_path)}"
        )
        return os.path.abspath(out_path)

    # 0. Check if audio stream exists (Prevents FFmpeg "Output file does not contain any stream" crash)
    try:
        # We use a lightweight ffprobe call to check for audio streams
        _aprobe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "json", video_path],
            capture_output=True, text=True, timeout=10
        )
        import json as _json
        _adata = _json.loads(_aprobe.stdout)
        if not _adata.get("streams"):
            logger.info(f"[AUDIO_EXTRACT] Skipping silent video (no audio stream): {os.path.basename(video_path)}")
            return ""
    except Exception as _ae:
        logger.warning(f"[AUDIO_EXTRACT] Audio probe failed for {video_path}: {_ae}")
        # If probe fails, we assume audio might exist to avoid missing tracks on flaky systems,
        # but the main ffmpeg call is already wrapped in a try/except.

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", video_path,
        "-vn",                   # strip video stream
        "-acodec", "libmp3lame",
        "-ab",     "192k",
        "-ar",     "44100",
        "-ac",     "2",
        out_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if result.returncode == 0 and os.path.exists(out_path):
            size_kb = os.path.getsize(out_path) / 1024
            logger.info(
                f"[AUDIO_EXTRACT] ✅ Extracted: {os.path.basename(out_path)} "
                f"({size_kb:.0f} KB)"
            )
            return os.path.abspath(out_path)

        err = result.stderr.decode(errors="ignore")[-300:]
        logger.warning(f"[AUDIO_EXTRACT] FFmpeg failed: {err}")
        return ""

    except subprocess.TimeoutExpired:
        logger.warning(
            f"[AUDIO_EXTRACT] Timed out for {os.path.basename(video_path)}"
        )
        return ""
    except Exception as e:
        logger.warning(f"[AUDIO_EXTRACT] Error: {e}")
        return ""