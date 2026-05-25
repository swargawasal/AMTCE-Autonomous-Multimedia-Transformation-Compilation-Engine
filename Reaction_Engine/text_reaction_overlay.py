"""
Reaction_Engine/text_reaction_overlay.py
----------------------------------------
Standalone module for Option D: Text/Emoji Reaction Overlay.
Reads `fused_moments` and overlays emoji bubbles directly on the source video.
Controlled exclusively via .env variables (no dependency on original Reaction Engine).
"""

import os
import logging
import subprocess
import uuid
import tempfile
import random
from typing import Dict, Any, List

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("text_reaction_overlay")

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

# Emotion -> List of (Emoji, Text, BackgroundColorHex)
# Psychology: Curiosity Gap | Scarcity/FOMO | Desire Trigger | Social Proof | Direct CTA
_EMOTION_HOOKS = {
    "shocked": [
        ("😱", "WAIT FOR THIS 🔗", "#CC0000"),
        ("🤯", "HOW IS THIS LEGAL?", "#CC0000"),
        ("😳", "SHE GOT IT WHERE?!", "#CC0000"),
        ("🚨", "LINK IN BIO NOW", "#CC0000"),
        ("😱", "GIRLS ARE OBSESSED", "#CC0000"),
        ("🔥", "SOLD OUT TWICE", "#880000"),
    ],
    "laughing": [
        ("😂", "BESTIE SEND THIS", "#D45C00"),
        ("💀", "TAG HER NOW 👇", "#D45C00"),
        ("😂", "SHE KNOWS 😭", "#D45C00"),
        ("🤣", "COMMENT IF U AGREE", "#D45C00"),
    ],
    "hype": [
        ("🔥", "SAVE THIS. TRUST.", "#5B0DA6"),
        ("⚡", "ONLY 3 LEFT 😭", "#5B0DA6"),
        ("🔥", "GOING VIRAL RN", "#5B0DA6"),
        ("👀", "WATCH TILL END 🔗", "#5B0DA6"),
        ("💥", "EVERYONE'S BUYING", "#5B0DA6"),
        ("🚀", "TAP PROFILE - BUY", "#3D0080"),
        ("🔥", "LIMITED DROP 🔗", "#5B0DA6"),
    ],
    "impressed": [
        ("✨", "HOW TO GET THIS?", "#0D47A1"),
        ("😍", "LINK IN BIO", "#0D47A1"),
        ("💎", "SHE'S WEARING...", "#0D47A1"),
        ("✨", "SAVE FOR LATER 📌", "#0D47A1"),
        ("👀", "SHOP HER LOOK 🔗", "#0D47A1"),
        ("🤩", "UNDER 999 ONLY 😭", "#1565C0"),
    ],
    "confused": [
        ("🤔", "WHERE IS THIS FROM?", "#1B5E20"),
        ("🤨", "COMMENT LINK 👇", "#1B5E20"),
        ("❓", "DROPPING TONIGHT", "#1B5E20"),
    ],
    "cringe": [
        ("😬", "BETTER OPTION 👇", "#4E342E"),
        ("🤦", "DO THIS INSTEAD 🔗", "#4E342E"),
        ("🙅", "GIRLS SWITCHED TO", "#4E342E"),
    ],
    "neutral": [
        ("📌", "SAVE THIS NOW", "#212121"),
        ("👀", "COMMENT YES 👇", "#212121"),
        ("🛒", "IN BIO - SHOP", "#212121"),
        ("🔗", "TAP PROFILE NOW", "#212121"),
    ],
    "default": [
        ("🔗", "LINK IN BIO", "#263238"),
        ("🔥", "GOING FAST", "#263238"),
        ("📌", "SAVE THIS", "#263238"),
    ]
}

# Standard Shorts output dimensions for PIP overlay context
_OUT_W = 1080
_OUT_H = 1920

def _is_enabled() -> bool:
    return os.getenv("ENABLE_TEXT_REACTIONS", "no").lower() in ("yes", "true", "1")

def _get_overlay_position(pip_w: int, pip_h: int) -> str:
    margin = int(os.getenv("TEXT_REACTION_MARGIN", "20"))
    corner = os.getenv("TEXT_REACTION_POSITION", "top_right").lower()

    # Conflict guard: if the price tag is enabled it can drift to the right edge.
    # Automatically push the emoji bubble to top_left to avoid overlap.
    price_tag_enabled = os.getenv("ENABLE_PRICE_TAG", "yes").lower() in ("yes", "true", "1")
    if price_tag_enabled and corner == "top_right":
        logger.debug(
            "[TEXT_REACTION] ENABLE_PRICE_TAG=yes detected — auto-shifting emoji "
            "to top_left to prevent overlap with price tag overlay."
        )
        corner = "top_left"
    
    if corner == "top_left":
        return f"{margin}:{margin}"
    elif corner == "top_center":
        return f"(W-w)/2:{margin}"
    else:  # top_right
        return f"W-w-{margin}:{margin}"

def _create_bubble_image(emoji: str, text: str, bg_color: str, output_path: str, width: int = 270):
    """Generates a reaction bubble PNG using Pillow."""
    import textwrap
    
    height = int(width * 16 / 9)  # matches PIP portrait size
    
    # Create image with transparent background
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    
    # Draw a rounded rectangle bubble
    bubble_margin = 20
    bubble_box = [bubble_margin, height - 120, width - bubble_margin, height - bubble_margin]
    
    # Convert hex to RGBA for Pillow (with 85% opacity)
    bg_rgba = tuple(int(bg_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + (220,)
    
    d.rounded_rectangle(bubble_box, radius=15, fill=bg_rgba)
    
    # Try to load a font, fallback to default (won't look great but safe)
    try:
        font = ImageFont.truetype("arialbd.ttf", 26)  # Bold arial
        emoji_font = ImageFont.truetype("seguiemj.ttf", 40) # Emoji font on Windows
    except IOError:
        font = ImageFont.load_default()
        emoji_font = font
    
    # Draw Emoji (Centered in bubble top)
    try:
        # textbbox returns (left, top, right, bottom)
        left, top, right, bottom = d.textbbox((0, 0), emoji, font=emoji_font)
        emoji_w = right - left
    except AttributeError:
        # Fallback for older Pillow
        emoji_w = emoji_font.getsize(emoji)[0]
    
    emoji_x = (width - emoji_w) / 2
    emoji_y = height - 100
    d.text((emoji_x, emoji_y), emoji, font=emoji_font, fill="white")
    
    # Draw Text (Centered below emoji)
    try:
        left, top, right, bottom = d.textbbox((0, 0), text, font=font)
        text_w = right - left
    except AttributeError:
        text_w = font.getsize(text)[0]
        
    text_x = (width - text_w) / 2
    text_y = height - 50
    d.text((text_x, text_y), text, font=font, fill="white")

    img.save(output_path, "PNG")


def run_text_reaction_overlay(
    source_video: str,
    profile_data: Dict[str, Any],
    job_dir: str,
) -> str:
    """
    Overlays animated text/emoji reaction bubbles onto the source video.
    Returns path to the new video, or None if disabled/failed.
    """
    if not _is_enabled():
        return None

    logger.info("💬 [TEXT_REACTION] Starting Standalone Text Reaction Overlay...")

    if not source_video or not os.path.isfile(source_video):
        logger.warning(f"[TEXT_REACTION] Source video not found: {source_video}")
        return None

    # 1. Collect trigger moments
    trigger_moments = []
    fused = profile_data.get("fused_moments", [])
    for m in fused:
        if not isinstance(m, dict): continue
        score = float(m.get("fusion_score", m.get("score", 0.0)))
        if score >= 0.40:
            emotion_tag = m.get("editor_tag", m.get("source", "default"))
            emotion_mapped = "impressed" # Default fallback
            # Map editor tag to known emotions
            if "laugh" in emotion_tag.lower(): emotion_mapped = "laughing"
            elif "surpris" in emotion_tag.lower(): emotion_mapped = "shocked"
            elif "motion" in emotion_tag.lower() or "hero" in emotion_tag.lower(): emotion_mapped = "hype"
            
            trigger_moments.append({
                "time": float(m.get("time", 0.0)),
                "emotion": emotion_mapped
            })
            
    # Optional: Inject Editorial Context Moment at beginning if requested
    inject_editorial = os.getenv("REACTION_EDITORIAL_INJECT", "yes").lower() in ("yes", "true", "1")
    if inject_editorial:
        script = (profile_data.get("monetization_data", {}).get("editorial_script", "") or profile_data.get("editorial_script", "")).strip()
        if script and trigger_moments:
            first_ts = sorted(trigger_moments, key=lambda m: m["time"])[0]["time"]
            trigger_moments.insert(0, {
                "time": max(0.3, first_ts - 0.5), # Just before first spike
                "emotion": "hype" # Default positive vibe
            })

    if not trigger_moments:
        logger.info("[TEXT_REACTION] No moments found. Skipping overlay.")
        return None

    # Filter overlaps (min 3s gap)
    trigger_moments.sort(key=lambda m: m["time"])
    filtered_moments = []
    last_ts = -999.0
    for m in trigger_moments:
        if m["time"] - last_ts >= 3.0:
            filtered_moments.append(m)
            last_ts = m["time"]

    # Limit to 5 moments to prevent spam
    filtered_moments = filtered_moments[:5]

    logger.info(f"[TEXT_REACTION] Found {len(filtered_moments)} reaction moments to process.")

    # 2. Setup FFmpeg inputs and filters
    os.makedirs(job_dir, exist_ok=True)
    
    suffix = os.getenv("TEXT_REACTION_OUTPUT_SUFFIX", "_textreaction")
    source_dir = os.path.dirname(os.path.abspath(source_video))
    source_stem = os.path.splitext(os.path.basename(source_video))[0]
    output_path = os.path.join(source_dir, f"{source_stem}{suffix}.mp4")

    # Base inputs
    inputs = ["-i", source_video]
    filter_complex = []
    
    # Track the last overlay output name
    last_v_out = "[0:v]"
    
    bubble_width = int(os.getenv("TEXT_REACTION_SIZE", "270"))
    bubble_height = int(bubble_width * 16 / 9)
    position_expr = _get_overlay_position(bubble_width, bubble_height)

    used_texts = set()
    for i, moment in enumerate(filtered_moments):
        emotion = moment["emotion"]
        start_ts = moment["time"] + 0.5  # Slight delay for realism
        end_ts = start_ts + 2.5          # Bubble stays for 2.5s
        
        # Select random hook avoiding immediate repeats
        candidates = _EMOTION_HOOKS.get(emotion, _EMOTION_HOOKS["default"])
        available = [c for c in candidates if c[1] not in used_texts]
        if not available: 
            available = candidates # fallback if all used
        chosen = random.choice(available)
        
        emoji, text, bg_color = chosen
        used_texts.add(text)
        if len(used_texts) > 5: used_texts.clear()
        
        # Create bubble image
        img_path = os.path.join(job_dir, f"bubble_{i}_{uuid.uuid4().hex[:6]}.png")
        _create_bubble_image(emoji, text, bg_color, img_path, width=bubble_width)
        
        # Add to inputs
        input_idx = i + 1
        inputs.extend(["-i", img_path])
        
        # Add overlay filter with enable timeline
        current_v_out = f"[vout{i}]"
        filter_complex.append(
            f"{last_v_out}[{input_idx}:v]overlay={position_expr}:enable='between(t,{start_ts},{end_ts})'{current_v_out}"
        )
        last_v_out = current_v_out

    # Finalize filter complex
    filter_string = ";".join(filter_complex)

    cmd = [
        FFMPEG_BIN, "-y",
    ] + inputs + [
        "-filter_complex", filter_string,
        "-map", last_v_out,
        "-map", "0:a?",  # copy audio if exists
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        output_path
    ]

    logger.info("⏳ [TEXT_REACTION] Rendering text overlay video...")
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=300
        )
        if result.returncode == 0 and os.path.isfile(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"✅ [TEXT_REACTION] Finished: {os.path.basename(output_path)} ({size_mb:.1f}MB)")
            return output_path
        else:
            logger.error(f"[TEXT_REACTION] FFmpeg failed: {result.stderr[-500:]}")
            return None
    except Exception as e:
        logger.error(f"[TEXT_REACTION] Fatal error rendering overlay: {e}")
        return None
