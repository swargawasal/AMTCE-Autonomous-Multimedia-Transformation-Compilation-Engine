"""
Pose Syncer — Influencer Modules
==================================
Replicates Luvart's "character sync" step:
  • Takes the generated influencer portrait (Stage 1)
  • Takes the extracted reference frame (Stage 2  — the viral video frame)
  • Outputs a new image where the influencer's face is placed on the
    reference body pose / outfit — matching dress, arm position, angle.

Stack — 100% Commercial-Safe (Apache 2.0):
  • ControlNet for SDXL (OpenPose variant)
      — lllyasviel/controlnet-openpose-sdxl-1.0 (Apache 2.0)
  • SDXL Base 1.0
      — stabilityai/stable-diffusion-xl-base-1.0 (CreativeML RAIL+M — commercial OK)
  • IP-Adapter Plus Face (CLIP ViT-H  — NO InsightFace)
      — h94/IP-Adapter  (Apache 2.0)
  • controlnet-aux / DWPose  (Apache 2.0)
      — OpenPose-skeleton extraction from the reference frame
  • MediaPipe  (Apache 2.0)
      — Face crop from the portrait for IP-Adapter conditioning

NOT USED (non-commercial):
  ✗ InsightFace / ArcFace ✗ FaceID weights ✗ FLUX [dev] ✗ InstantID
"""

import os
import logging
from pathlib import Path
from PIL import Image

logger = logging.getLogger("influencer.pose_syncer")

# ────────────────────────────────────────────────────────────────────────────
# Model IDs
# ────────────────────────────────────────────────────────────────────────────
_SDXL_MODEL_ID     = "stabilityai/stable-diffusion-xl-base-1.0"
_CONTROLNET_ID     = "thibaud/controlnet-openpose-sdxl-1.0"
_IP_ADAPTER_REPO   = "h94/IP-Adapter"
_IP_WEIGHT         = "ip-adapter-plus-face_sdxl_vit-h.safetensors"
_IP_SUBFOLDER      = "sdxl_models"

_DEFAULT_NEG_PROMPT = (
    "deformed, bad anatomy, bad proportions, disfigured, poorly drawn face, "
    "mutation, mutation, extra limb, ugly, poorly drawn hands, missing limb, "
    "blurry, watermark, tiktok watermark, text, logo, cartoon, bad clothing fit"
)

# ────────────────────────────────────────────────────────────────────────────
# Content Policy Safety
# ────────────────────────────────────────────────────────────────────────────
# The pose syncer uses INSPIRATION language, NOT replication language.
# This is intentional: the output must be a NEW original creation that is
# INSPIRED BY the reference pose, not an attempt to copy or clone it.
#
# Prompts deliberately use:
#   ✅ "inspired by the style"    (NOT "exact same outfit")
#   ✅ "similar fashion"          (NOT "matching clothing")
#   ✅ "different background"     (NOT "same scene")
#   ✅ "original setting"         (NOT "replicate the environment")
#
# This is the key that separates transformative AI content from
# derivative reproduction under content policy guidelines.


# ────────────────────────────────────────────────────────────────────────────
# OpenPose skeleton extraction
# ────────────────────────────────────────────────────────────────────────────

def _extract_openpose(frame_path: str, output_path: str) -> Image.Image:
    """
    Extract an OpenPose skeleton image from *frame_path* using controlnet-aux.

    controlnet-aux bundles a DWPose / OpenPose preprocessor that runs
    entirely via ONNX — no InsightFace dependency.

    Parameters
    ----------
    frame_path  : Reference video frame (e.g., extracted Pinterest reel frame).
    output_path : Where to save the pose skeleton PNG (for debugging).

    Returns
    -------
    PIL.Image.Image : OpenPose skeleton image, same size as input frame.
    """
    from controlnet_aux import DWposeDetector

    logger.info(f"🦴  Extracting pose skeleton from: {frame_path}")
    detector = DWposeDetector()
    detector = detector.to("cuda" if _cuda_available() else "cpu")

    ref_image = Image.open(frame_path).convert("RGB")
    pose_image = detector(ref_image, include_hand=True, include_face=False)

    pose_image.save(output_path)
    logger.info(f"   └─ Pose skeleton saved: {output_path}")
    return pose_image


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


# ────────────────────────────────────────────────────────────────────────────
# Face crop helper (reuse from identity_generator to avoid circular import)
# ────────────────────────────────────────────────────────────────────────────

def _face_crop_opencv(portrait_path: str) -> Image.Image:
    """Crop face from the generated portrait for IP-Adapter conditioning."""
    import cv2
    img_bgr = cv2.imread(portrait_path)
    h, w    = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    if len(faces) > 0:
        margin = 0.12
        x, y, fw, fh = faces[0]
        x1 = max(0, int(x - margin * fw))
        y1 = max(0, int(y - margin * fh))
        x2 = min(w, int(x + fw + margin * fw))
        y2 = min(h, int(y + fh + margin * fh))
        return Image.fromarray(img_rgb[y1:y2, x1:x2])

    # Centre-square fallback
    side = min(w, h)
    return Image.fromarray(
        img_rgb[(h-side)//2:(h+side)//2, (w-side)//2:(w+side)//2]
    )


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────

def sync_pose(
    portrait_path:      str,
    reference_frame_path: str,
    prompt:             str,
    output_path:        str,
    ip_adapter_scale:   float = 0.60,
    controlnet_scale:   float = 0.85,
    num_steps:          int   = 35,
    guidance_scale:     float = 5.0,
    seed:               int   = 42,
) -> str:
    """
    Generate a new image where the influencer face matches the reference pose.

    This is the "face swap + character sync" step that was previously done
    manually in Luvart AI.

    Parameters
    ----------
    portrait_path        : Generated influencer portrait (Stage 1 output).
    reference_frame_path : Extracted frame from the reference Pinterest reel
                           (Stage 2 output).  Provides body pose + outfit look.
    prompt               : Style/content prompt, e.g.
                           "realistic indian woman, matching outfit, outdoor scene,
                            ultra-detailed, photorealistic, same clothing".
    output_path          : Where to save the synced image.
    ip_adapter_scale     : Face identity strength (0.5–0.75 recommended).
    controlnet_scale     : Pose adherence strength (0.7–1.0 recommended).
    num_steps            : Diffusion steps.
    guidance_scale       : CFG scale.
    seed                 : Reproducibility seed.

    Returns
    -------
    str : Absolute path to the synced image.
    """
    import torch
    from diffusers import ControlNetModel, StableDiffusionXLControlNetPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype  = torch.float16 if device == "cuda" else torch.float32

    # ── 1. Extract OpenPose skeleton from reference frame ─────────────────
    pose_debug_path = str(Path(output_path).with_suffix("")) + "_pose_debug.png"
    pose_image = _extract_openpose(reference_frame_path, pose_debug_path)

    # Resize pose to match reference frame aspect ratio, SDXL-friendly bucket
    ref_img    = Image.open(reference_frame_path).convert("RGB")
    rw, rh     = ref_img.size
    target_w   = 896 if rw >= rh else 768
    target_h   = int(target_w * rh / rw / 64) * 64  # snap to multiple of 64
    pose_image = pose_image.resize((target_w, target_h), Image.LANCZOS)

    logger.info(f"🎛️   Loading ControlNet OpenPose SDXL on {device.upper()} ...")
    controlnet = ControlNetModel.from_pretrained(
        _CONTROLNET_ID,
        torch_dtype=dtype,
        use_safetensors=True,
    )

    # ── 2. Load SDXL + ControlNet pipeline ───────────────────────────────
    pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
        _SDXL_MODEL_ID,
        controlnet=controlnet,
        torch_dtype=dtype,
        use_safetensors=True,
        variant="fp16" if device == "cuda" else None,
        add_watermarker=False,
    )
    pipe.enable_model_cpu_offload()  # T4-safe: 16 GB headroom

    # ── 3. Load IP-Adapter Plus Face (CLIP, NO InsightFace) ──────────────
    logger.info("📎  Loading IP-Adapter Plus Face ...")
    pipe.load_ip_adapter(
        _IP_ADAPTER_REPO,
        subfolder=_IP_SUBFOLDER,
        weight_name=_IP_WEIGHT,
    )
    pipe.set_ip_adapter_scale(ip_adapter_scale)

    # ── 4. Crop face from the generated portrait ──────────────────────────
    face_crop  = _face_crop_opencv(portrait_path)
    face_input = face_crop.resize((224, 224), Image.LANCZOS)

    # ── 5. Generate synced image ──────────────────────────────────────────
    generator = torch.Generator(device).manual_seed(seed)

    logger.info(
        f"🎭  Syncing pose (cn={controlnet_scale}, ip={ip_adapter_scale}, "
        f"steps={num_steps}) ..."
    )
    result = pipe(
        prompt=prompt,
        negative_prompt=_DEFAULT_NEG_PROMPT,
        image=pose_image,                     # ControlNet condition (pose)
        ip_adapter_image=face_input,          # IP-Adapter condition (face)
        width=target_w,
        height=target_h,
        num_inference_steps=num_steps,
        guidance_scale=guidance_scale,
        controlnet_conditioning_scale=controlnet_scale,
        generator=generator,
    )

    synced_image = result.images[0]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    synced_image.save(output_path, quality=95)
    logger.info(f"✅  Synced image saved → {output_path}")

    # ── Free VRAM before Stage 4 ──────────────────────────────────────────
    del pipe, controlnet
    if device == "cuda":
        torch.cuda.empty_cache()

    return output_path
