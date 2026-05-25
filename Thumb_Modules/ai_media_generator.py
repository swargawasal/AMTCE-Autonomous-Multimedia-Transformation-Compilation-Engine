"""
AI Media Generator — Smart Background Replacement Ad Engine
===========================================================
Creates professional product advertisement images by:
  1. Taking the real video frame (actual outfit preserved — NOT hallucinated)
  2. Removing the background using rembg (ONNX, CPU-only, no GPU needed)
  3. Compositing the subject onto a clean professional studio background
  4. Exporting in Pinterest (2:3) and Instagram Feed (4:5) formats

This approach ALWAYS shows the exact same outfit from the video — no AI guessing.

Dependencies:
  pip install rembg[cpu] onnxruntime pillow

Configuration (.env):
  ENABLE_AI_IMAGE_CREATION  — master toggle
  GENERATE_PINTEREST_ADS    — emit 1000x1500 pin
  GENERATE_INSTAGRAM_ADS    — emit 1080x1350 IG feed crop
  AD_VARIANTS_COUNT         — 1-3 background style variants
  AD_BG_STYLE               — default background style: white|gray|cream|gradient
"""

import io
import logging
import os

logger = logging.getLogger("ai_media_generator")

# ── Ratio Presets ──────────────────────────────────────────────────────────────
RATIO_PRESETS = {
    "pinterest": {
        "label":  "Pinterest Ad (2:3)",
        "width":  1000,
        "height": 1500,
        "suffix": "_pin",
    },
    "instagram_feed": {
        "label":  "Instagram Feed (4:5)",
        "width":  1080,
        "height": 1350,
        "suffix": "_ig_feed",
    },
}

# ── Studio Background Styles ───────────────────────────────────────────────────
BG_STYLES = [
    # Style 0: Pure white studio
    {"type": "solid", "color": (255, 255, 255)},
    # Style 1: Soft warm cream (fashion editorial)
    {"type": "gradient", "top": (245, 240, 235), "bottom": (255, 255, 255)},
    # Style 2: Light cool gray (e-commerce)
    {"type": "gradient", "top": (230, 232, 235), "bottom": (248, 249, 250)},
]


def _make_background(width: int, height: int, style: dict):
    """Create a PIL Image with the specified studio background style."""
    from PIL import Image
    import numpy as np

    img = Image.new("RGB", (width, height))

    if style["type"] == "solid":
        img.paste(style["color"], [0, 0, width, height])

    elif style["type"] == "gradient":
        top_c = style["top"]
        bot_c = style["bottom"]
        arr   = np.zeros((height, width, 3), dtype=np.uint8)
        for y in range(height):
            t = y / height
            r = int(top_c[0] * (1 - t) + bot_c[0] * t)
            g = int(top_c[1] * (1 - t) + bot_c[1] * t)
            b = int(top_c[2] * (1 - t) + bot_c[2] * t)
            arr[y, :] = [r, g, b]
        img = Image.fromarray(arr, "RGB")

    return img


def _remove_background(image_bytes: bytes) -> "Image":
    """
    Remove background from image bytes using rembg (ONNX, CPU-only).
    Returns a PIL RGBA image with transparent background.
    Downloads ~170MB ONNX model on first call (cached locally forever after).
    """
    from rembg import remove
    from PIL import Image

    input_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    result    = remove(input_img)  # Returns RGBA with alpha mask
    return result


def _composite_on_background(subject_rgba, bg_width: int, bg_height: int, style: dict):
    """
    Composite the subject (RGBA with transparent bg) onto a studio background.
    Subject is scaled to fill most of the frame, centered.
    """
    from PIL import Image

    bg = _make_background(bg_width, bg_height, style)

    # Scale subject to fit inside the frame with 5% padding on each side
    subject = subject_rgba.copy()
    sub_w, sub_h = subject.size
    max_w = int(bg_width  * 0.90)
    max_h = int(bg_height * 0.92)

    scale  = min(max_w / sub_w, max_h / sub_h)
    new_w  = int(sub_w * scale)
    new_h  = int(sub_h * scale)
    subject = subject.resize((new_w, new_h), Image.LANCZOS)

    # Center-bottom align (feet at bottom, like a product catalog)
    paste_x = (bg_width  - new_w) // 2
    paste_y = (bg_height - new_h) - int(bg_height * 0.02)   # 2% bottom margin
    paste_y = max(0, paste_y)

    # Composite using alpha channel as mask
    bg.paste(subject, (paste_x, paste_y), subject.split()[3])
    return bg


def _smart_crop(img, target_width: int, target_height: int):
    """
    Smart crop: resize maintaining aspect ratio, then center-crop to exact size.
    """
    from PIL import Image

    src_w, src_h   = img.size
    target_ratio   = target_width / target_height
    src_ratio      = src_w / src_h

    if src_ratio > target_ratio:
        new_h = src_h
        new_w = int(src_h * target_ratio)
    else:
        new_w = src_w
        new_h = int(src_w / target_ratio)

    left = (src_w - new_w) // 2
    top  = (src_h - new_h) // 2
    img  = img.crop((left, top, left + new_w, top + new_h))
    return img.resize((target_width, target_height), Image.LANCZOS)


def _save_jpeg(img, path: str, quality: int = 95):
    """Save PIL image as JPEG."""
    img.convert("RGB").save(path, format="JPEG", quality=quality, optimize=True)


def generate_ad_images(title: str, output_dir: str, base_name: str,
                       source_frame_bytes: bytes = None) -> dict:
    """
    Generate polished studio-background advertisement images.

    Strategy:
      - If source_frame_bytes is provided (a video frame), remove its background
        and composite it onto a clean studio background.
      - Output covers Pinterest (2:3) and Instagram Feed (4:5) formats.
      - Multiple background style variants for A/B testing.

    Args:
        title:              Product/video title (used for logging only in this version).
        output_dir:         Directory to save generated images.
        base_name:          Base filename prefix.
        source_frame_bytes: Optional raw JPEG/PNG bytes of a video frame to process.
                            If None or not provided, the function returns {} so the
                            caller falls back to the original OpenCV extractor.

    Returns:
        Dict {"pinterest": [paths], "instagram_feed": [paths]}
        Empty dict on failure (caller falls back to OpenCV).
    """
    gen_pinterest = os.getenv("GENERATE_PINTEREST_ADS",  "true").lower() in ("true", "yes", "on", "1")
    gen_instagram = os.getenv("GENERATE_INSTAGRAM_ADS",  "true").lower() in ("true", "yes", "on", "1")
    n_variants    = min(int(os.getenv("AD_VARIANTS_COUNT", "1")), len(BG_STYLES))

    if not gen_pinterest and not gen_instagram:
        return {}

    if not source_frame_bytes:
        logger.info("[AD_GEN] No source frame provided — caller should extract a frame first.")
        return {}

    os.makedirs(output_dir, exist_ok=True)
    results = {}

    try:
        logger.info("[AD_GEN] Removing background from source frame (rembg/ONNX, CPU-only)...")
        subject_rgba = _remove_background(source_frame_bytes)
        logger.info("[AD_GEN] Background removed successfully.")
    except Exception as e:
        logger.error(f"[AD_GEN] Background removal failed: {e}")
        return {}

    for variant_idx in range(n_variants):
        style         = BG_STYLES[variant_idx % len(BG_STYLES)]
        variant_suffix = f"_v{variant_idx + 1}" if n_variants > 1 else ""
        logger.info(f"[AD_GEN] Variant {variant_idx + 1}/{n_variants} — style: {style['type']}")

        if gen_pinterest:
            preset  = RATIO_PRESETS["pinterest"]
            w, h    = preset["width"], preset["height"]
            composed = _composite_on_background(subject_rgba, w, h, style)
            out_path = os.path.join(output_dir, f"{base_name}{preset['suffix']}{variant_suffix}.jpg")
            _save_jpeg(composed, out_path)
            results.setdefault("pinterest", []).append(out_path)
            logger.info(f"[AD_GEN] Pinterest ad saved: {out_path}")

        if gen_instagram:
            preset  = RATIO_PRESETS["instagram_feed"]
            w, h    = preset["width"], preset["height"]
            composed = _composite_on_background(subject_rgba, w, h, style)
            out_path = os.path.join(output_dir, f"{base_name}{preset['suffix']}{variant_suffix}.jpg")
            _save_jpeg(composed, out_path)
            results.setdefault("instagram_feed", []).append(out_path)
            logger.info(f"[AD_GEN] Instagram Feed ad saved: {out_path}")

    return results


def generate_ad_images_from_path(title: str, source_image_path: str,
                                  output_dir: str, base_name: str) -> dict:
    """
    Convenience wrapper: load image from file path then call generate_ad_images.
    Used by the Gradio WebUI when user uploads an existing image.
    """
    if not source_image_path or not os.path.exists(source_image_path):
        logger.warning(f"[AD_GEN] Source image not found: {source_image_path}")
        return {}
    with open(source_image_path, "rb") as f:
        image_bytes = f.read()
    return generate_ad_images(title, output_dir, base_name, source_frame_bytes=image_bytes)


def generate_ad_video(title: str, source_image_path: str,
                       output_dir: str, base_name: str) -> str | None:
    """
    Video ad placeholder — no free-tier video API exists.
    Set ENABLE_AI_VIDEO_CREATION=false to suppress this log.
    """
    enable = os.getenv("ENABLE_AI_VIDEO_CREATION", "false").lower() in ("true", "yes", "on", "1")
    if not enable:
        return None
    logger.warning("[VIDEO_GEN] No free-tier video generation available. Set ENABLE_AI_VIDEO_CREATION=false.")
    return None
