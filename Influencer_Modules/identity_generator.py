"""
Identity Generator — Influencer Modules
=========================================
Generates a photorealistic influencer portrait from a single reference face image.

Stack — 100% Commercial-Safe (Apache 2.0 / CreativeML RAIL+M):
  • SDXL Base 1.0      — stabilityai/stable-diffusion-xl-base-1.0
                         (CreativeML Open RAIL+M — commercial OK for normal content)
  • IP-Adapter Plus Face — h94/IP-Adapter
                         sdxl_models/ip-adapter-plus-face_sdxl_vit-h.safetensors
                         Uses CLIP ViT-H image encoder — NO InsightFace, NO ArcFace.
                         Apache 2.0
  • MediaPipe            — Face detection / crop  (Apache 2.0)

Output: 896 × 1152 px  (≈ 3 : 4 — Instagram portrait ratio, ~1 K quality)

NOT USED (non-commercial):
  ✗ InsightFace / ArcFace
  ✗ ip-adapter-faceid* weights
  ✗ FLUX.1 [dev] weights
  ✗ InstantID
"""

import os
import logging
from pathlib import Path
from PIL import Image

logger = logging.getLogger("influencer.identity_generator")

# ────────────────────────────────────────────────────────────────────────────
# Identity Consistency System
# ────────────────────────────────────────────────────────────────────────────
# The key to a consistent AI influencer persona across all videos is:
#   1. Fixed seed (same random seed → same face details each generation)
#   2. Fixed prompt template (same wording → same style lock)
#   3. Fixed IP-Adapter scale (same face weighting)
#
# Store these in your .env:
#   INFLUENCER_SEED=42
#   INFLUENCER_PROMPT_TEMPLATE=realistic south asian woman, natural skin, confident
#   INFLUENCER_IP_SCALE=0.65
#
# This replaces the need for LoRA training (expensive, complex) for batch use.
# When you want a NEW persona, simply change INFLUENCER_SEED.

INFLUENCER_SEED_ENV        = int(os.getenv("INFLUENCER_SEED", "42"))
INFLUENCER_IP_SCALE_ENV    = float(os.getenv("INFLUENCER_IP_SCALE", "0.65"))
INFLUENCER_PROMPT_TEMPLATE = os.getenv(
    "INFLUENCER_PROMPT_TEMPLATE",
    "realistic south asian woman, natural skin, confident expression"
)

_SDXL_MODEL_ID     = "stabilityai/stable-diffusion-xl-base-1.0"
_IP_ADAPTER_REPO   = "h94/IP-Adapter"
_IP_ADAPTER_WEIGHT = "ip-adapter-plus-face_sdxl_vit-h.safetensors"
_IP_SUBFOLDER      = "sdxl_models"

_DEFAULT_NEG_PROMPT = (
    "deformed, bad anatomy, disfigured, poorly drawn face, mutation, mutated, "
    "extra limb, ugly, poorly drawn hands, missing limb, floating limbs, "
    "disconnected limbs, blurry, watermark, tiktok watermark, text, logo, "
    "cartoon, unrealistic skin, plastic skin"
)

# 3:4 Instagram portrait — nearest SDXL-friendly bucket
_DEFAULT_WIDTH  = 896
_DEFAULT_HEIGHT = 1152


# ────────────────────────────────────────────────────────────────────────────
# Face detection helper  (MediaPipe — Apache 2.0)
# ────────────────────────────────────────────────────────────────────────────

def _crop_face_opencv(image_path: str, margin: float = 0.15) -> Image.Image:
    """
    Detect and crop the primary face from *image_path* using OpenCV Haar Cascades.
    Completely bypasses MediaPipe Colab bugs.
    """
    import cv2
    import numpy as np

    img_bgr = cv2.imread(image_path)
    if img_bgr is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")

    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
    
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    if len(faces) > 0:
        x, y, fw, fh = faces[0]
        x1 = max(0, int(x - margin * fw))
        y1 = max(0, int(y - margin * fh))
        x2 = min(w, int(x + fw + margin * fw))
        y2 = min(h, int(y + fh + margin * fh))
        logger.info(f"🔍  Face crop via OpenCV ({x2-x1}x{y2-y1}px)")
        return Image.fromarray(img_rgb[y1:y2, x1:x2])

    logger.warning("⚠️  No face detected by OpenCV — falling back to centre-square crop.")
    side = min(w, h)
    cx, cy = w // 2, h // 2
    sx, sy = cx - side//2, cy - side//2
    return Image.fromarray(img_rgb[sy:sy+side, sx:sx+side])


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────

def generate_portrait(
    reference_face_path: str,
    prompt: str,
    output_path: str,
    width:  int   = _DEFAULT_WIDTH,
    height: int   = _DEFAULT_HEIGHT,
    num_steps:          int   = 35,
    guidance_scale:     float = 5.5,
    ip_adapter_scale:   float = 0.65,
    seed:               int   = 42,
) -> str:
    """
    Generate a photorealistic influencer portrait from a reference face image.

    Parameters
    ----------
    reference_face_path : Path to the reference face (e.g., downloaded from Pinterest).
    prompt              : Style description, e.g.
                          "realistic indian instagram influencer, ethnic silk saree,
                           natural outdoor lighting, ultra-detailed, photorealistic".
    output_path         : Where to save the result (.jpg or .png).
    width / height      : Output resolution. Default 896×1152 (≈ 3:4 Instagram).
    num_steps           : Diffusion steps (35 = good quality/speed balance).
    guidance_scale      : CFG scale (5–7 works well for SDXL realism).
    ip_adapter_scale    : How strongly the reference face drives the output
                          (0.5 = subtle, 0.7 = strong identity lock).
    seed                : Random seed for reproducibility.

    Returns
    -------
    str : Absolute path to the generated image.
    """
    import torch
    from diffusers import StableDiffusionXLPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype  = torch.float16 if device == "cuda" else torch.float32

    logger.info(f"🎨  Loading SDXL + IP-Adapter Plus Face on {device.upper()} ...")

    # ── Load base pipeline ────────────────────────────────────────────────
    pipe = StableDiffusionXLPipeline.from_pretrained(
        _SDXL_MODEL_ID,
        torch_dtype=dtype,
        use_safetensors=True,
        variant="fp16" if device == "cuda" else None,
        add_watermarker=False,  # suppress SDXL's built-in invisible watermark
    )

    # Memory-efficient offloading — each sub-model moves to GPU only when needed.
    # This keeps us inside 16 GB on Colab T4.
    pipe.enable_model_cpu_offload()

    # ── Load IP-Adapter Plus Face  (CLIP-based, Apache 2.0, NO InsightFace) ─
    logger.info("📎  Loading IP-Adapter Plus Face (CLIP ViT-H) ...")
    pipe.load_ip_adapter(
        _IP_ADAPTER_REPO,
        subfolder=_IP_SUBFOLDER,
        weight_name=_IP_ADAPTER_WEIGHT,
    )
    pipe.set_ip_adapter_scale(ip_adapter_scale)

    # ── Crop face from reference image (OpenCV format) ────────────────────
    logger.info("🔍  Detecting face in reference image ...")
    face_crop = _crop_face_opencv(reference_face_path)
    # CLIP expects 224 × 224 — resize while keeping square
    face_input = face_crop.resize((224, 224), Image.LANCZOS)

    # ── Generate ──────────────────────────────────────────────────────────
    # Use env-defined seed for identity consistency across all generations
    # (same seed + same prompt = same face details every time)
    effective_seed = seed if seed != 42 else INFLUENCER_SEED_ENV
    generator = torch.Generator(device).manual_seed(effective_seed)
    logger.info(
        f"✨  Generating {width}×{height} portrait "
        f"(steps={num_steps}, cfg={guidance_scale}, ip={ip_adapter_scale}) ..."
    )

    result = pipe(
        prompt=prompt,
        negative_prompt=_DEFAULT_NEG_PROMPT,
        ip_adapter_image=face_input,
        width=width,
        height=height,
        num_inference_steps=num_steps,
        guidance_scale=guidance_scale,
        generator=generator,
    )

    image = result.images[0]

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    image.save(output_path, quality=95)
    logger.info(f"✅  Portrait saved → {output_path}")

    # ── Free VRAM before next stage ───────────────────────────────────────
    del pipe
    if device == "cuda":
        torch.cuda.empty_cache()

    return output_path
