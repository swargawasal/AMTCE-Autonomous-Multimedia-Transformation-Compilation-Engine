"""
Influencer Pipeline — Orchestrator
=====================================
End-to-end runner for the AI Influencer Engine.

Stages
------
  Stage 0  GPU Gate        — verify VRAM ≥ 6 GB (reuses AMTCE ComputeCaps)
  Stage 1  Identity Gen    — SDXL + IP-Adapter Plus Face (CLIP, Apache 2.0)
  Stage 2  Frame Extract   — ffmpeg scene detection (already in AMTCE)
  Stage 3  Pose Sync       — ControlNet OpenPose + IP-Adapter (Apache 2.0)
  Stage 4  Motion Animate  — Wan2.1-1.3B I2V (Apache 2.0)

Usage (from Colab or main.py):
  from Influencer_Modules.influencer_pipeline import run

  result = run(
      reference_face_path  = "downloads/face_ref.jpg",
      reference_video_path = "downloads/pinterest_reel.mp4",
      prompt               = "realistic indian instagram influencer, ethnic silk saree",
      output_dir           = "Influencer_Output",
  )
  print(result["output_video"])   # path to final MP4

All components are Apache 2.0 / CreativeML RAIL+M — 100% commercial-safe.
NO InsightFace | NO ArcFace | NO FaceID | NO FLUX dev | NO InstantID weights.
"""

import os
import logging
import time
from pathlib import Path
from datetime import datetime

from Influencer_Modules.compute_gate      import check_gpu_ready, get_model_tier
from Influencer_Modules.frame_extractor   import extract_best_frame
from Influencer_Modules.identity_generator import generate_portrait
from Influencer_Modules.pose_syncer       import sync_pose
from Influencer_Modules.motion_animator   import animate
from Influencer_Modules.prompt_engineer   import build_prompts, auto_build_prompts

logger = logging.getLogger("influencer.pipeline")


# ────────────────────────────────────────────────────────────────────────────
# Default prompt templates  (user can override)
# ────────────────────────────────────────────────────────────────────────────

_DEFAULT_PORTRAIT_SUFFIX = (
    ", realistic photo, natural skin texture, soft studio lighting, "
    "ultra-detailed, sharp focus, photorealistic, 4k"
)

# Content Policy: Use INSPIRATION language only — never replication.
# DO NOT use: "same outfit", "matching clothing", "same scene", "exact pose copy".
# These push the model toward derivative reproduction.
# Use:  "inspired by", "similar style", "original setting", "unique background".
#
# This is the legal/platform safety line between transformative AI content
# and near-copy reproduction.
_DEFAULT_SYNC_SUFFIX = (
    ", inspired by the pose style, similar fashion aesthetic, "
    "unique original background setting, photorealistic, ultra-detailed, "
    "fresh scene environment different from reference"
)


# ────────────────────────────────────────────────────────────────────────────
# Pipeline
# ────────────────────────────────────────────────────────────────────────────

def run(
    reference_face_path:  str,
    reference_video_path: str,
    prompt:               str,
    output_dir:           str   = "Influencer_Output",
    portrait_width:       int   = 896,
    portrait_height:      int   = 1152,
    video_frames:         int   = 81,
    video_fps:            int   = 24,
    ip_adapter_scale:     float = 0.65,
    controlnet_scale:     float = 0.85,
    seed:                 int   = 42,
    skip_stages:          list  = None,
    portrait_override:    str   = None,
    # Prompt engineer params — auto-detected via Gemini if not set
    niche:        str = None,   # e.g. "saree", "streetwear", "lehenga"
    outfit_desc:  str = None,   # e.g. "emerald silk saree with gold zari border"
    setting:      str = "rooftop",
    lighting:     str = "golden_hour",
    hook_type:    str = "fabric_macro",
    walk_type:    str = "slow_sway",
    attitude:     str = "grace",
    expression:   str = "subtle_smile",
    fabric:       str = None,   # auto-inferred from outfit_desc if not set
    motion_energy:str = "moderate",
    use_auto_prompt: bool = True,  # use Gemini to auto-fill params from prompt
) -> dict:
    """
    Run the full AI Influencer pipeline.

    Parameters
    ----------
    reference_face_path   : PNG/JPG — the face to replicate (e.g., Pinterest photo).
    reference_video_path  : MP4    — the trending reel to extract pose + motion from.
    prompt                : Base prompt describing the influencer style / niche.
    output_dir            : Directory to save all stage outputs.
    portrait_width/height : Resolution of the generated portrait (default 3:4).
    video_frames          : Number of frames in the animated output (81 ≈ 4 s).
    video_fps             : FPS for the final MP4.
    ip_adapter_scale      : How strongly the face reference drives generation.
    controlnet_scale      : How strictly the pose is followed.
    seed                  : Random seed for all stages.
    skip_stages           : List of stage numbers to skip (1, 2, 3, or 4).
    portrait_override     : Provide an existing portrait to skip Stage 1.

    Returns
    -------
    dict with keys:
        output_video   (str)  : path to the final animated MP4
        portrait       (str)  : path to Stage 1 generated portrait
        reference_frame(str)  : path to Stage 2 extracted frame
        synced_image   (str)  : path to Stage 3 pose-synced image
        elapsed_sec    (float): total wall-clock seconds
        vram_gb        (float): detected VRAM
        tier           (str)  : "low" | "medium" | "high"
    """
    skip_stages = skip_stages or []
    t_start     = time.time()

    # ── Prepare output directories ────────────────────────────────────────
    run_id     = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir    = os.path.join(output_dir, run_id)
    stages_dir = os.path.join(run_dir, "stages")
    os.makedirs(stages_dir, exist_ok=True)

    logger.info("=" * 65)
    logger.info(f"🎬  AMTCE Influencer Pipeline — Run ID: {run_id}")
    logger.info("=" * 65)

    # ─────────────────────────────────────────────────────────────────────
    # Stage 0: GPU Gate
    # ─────────────────────────────────────────────────────────────────────
    logger.info("⚙️   Stage 0 / GPU Gate ...")
    ready, vram_gb, err = check_gpu_ready()
    if not ready:
        raise EnvironmentError(
            f"❌  Influencer Engine requires GPU with >=6 GB VRAM.\n"
            f"    Details: {err}\n"
            f"    👉  Run using colab/Influencer_Colab.ipynb on a T4/L4/A100."
        )
    tier = get_model_tier(vram_gb)

    # ─────────────────────────────────────────────────────────────────────
    # Prompt Engineering  — Build stage-specific prompts from concept
    # ─────────────────────────────────────────────────────────────────────
    logger.info("📝  Building stage-specific prompts ...")
    if use_auto_prompt and not niche:
        # Let Gemini analyse the concept and fill all parameters
        eng = auto_build_prompts(
            concept        = prompt,
            gemini_api_key = os.getenv("GEMINI_API_KEY"),
        )
        if "auto_params" in eng:
            logger.info(f"   🤖 Gemini params: {eng['auto_params']}")
    else:
        # Manual parameter control
        eng = build_prompts(
            niche         = niche or "fashion",
            outfit_desc   = outfit_desc or prompt,
            setting       = setting,
            lighting      = lighting,
            hook_type     = hook_type,
            walk_type     = walk_type,
            attitude      = attitude,
            expression    = expression,
            fabric        = fabric or "default",
            motion_energy = motion_energy,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Stage 1: Identity / Portrait Generation
    # ─────────────────────────────────────────────────────────────────────
    # ── Stage 1: Identity / Portrait Generation ───────────────────────────
    # Identity consistency: read seed + ip_scale from .env to keep the SAME
    # face across all videos (fixed seed = fixed face details).
    from Influencer_Modules.identity_generator import (
        INFLUENCER_SEED_ENV, INFLUENCER_IP_SCALE_ENV, INFLUENCER_PROMPT_TEMPLATE
    )
    effective_seed     = seed if seed != 42 else INFLUENCER_SEED_ENV
    effective_ip_scale = ip_adapter_scale if ip_adapter_scale != 0.65 else INFLUENCER_IP_SCALE_ENV

    portrait_path = portrait_override or os.path.join(
        stages_dir, "stage1_portrait.jpg"
    )

    if 1 not in skip_stages and portrait_override is None:
        logger.info("🎨  Stage 1 / Portrait Generation ...")
        generate_portrait(
            reference_face_path = reference_face_path,
            prompt              = eng["stage1_portrait"],
            output_path         = portrait_path,
            width               = portrait_width,
            height              = portrait_height,
            ip_adapter_scale    = effective_ip_scale,
            seed                = effective_seed,
        )
        logger.info(f"   ✅  Stage 1 complete → {portrait_path}")
    else:
        logger.info(f"⏭️   Stage 1 skipped (using: {portrait_path})")

    # ─────────────────────────────────────────────────────────────────────
    # Stage 2: Frame Extraction
    # ─────────────────────────────────────────────────────────────────────
    reference_frame_path = os.path.join(stages_dir, "stage2_ref_frame.jpg")

    if 2 not in skip_stages:
        logger.info("🎬  Stage 2 / Frame Extraction ...")
        reference_frame_path = extract_best_frame(
            video_path  = reference_video_path,
            output_dir  = stages_dir,
        )
        logger.info(f"   ✅  Stage 2 complete → {reference_frame_path}")
    else:
        logger.info("⏭️   Stage 2 skipped.")

    # ─────────────────────────────────────────────────────────────────────
    # Stage 3: Pose / Character Sync
    # ─────────────────────────────────────────────────────────────────────
    synced_image_path = os.path.join(stages_dir, "stage3_synced.jpg")

    if 3 not in skip_stages:
        logger.info("🎭  Stage 3 / Pose & Character Sync ...")
        sync_pose(
            portrait_path        = portrait_path,
            reference_frame_path = reference_frame_path,
            prompt               = eng["stage3_sync"],
            output_path          = synced_image_path,
            ip_adapter_scale     = effective_ip_scale,
            controlnet_scale     = controlnet_scale,
            seed                 = effective_seed,
        )
        logger.info(f"   ✅  Stage 3 complete → {synced_image_path}")
    else:
        logger.info("⏭️   Stage 3 skipped.")

    # ─────────────────────────────────────────────────────────────────────
    # Stage 4: Motion Animation  (Wan2.1 I2V)
    # ─────────────────────────────────────────────────────────────────────
    output_video_path = os.path.join(run_dir, "influencer_video.mp4")

    if 4 not in skip_stages:
        logger.info("🎥  Stage 4 / Motion Animation (Wan2.1 I2V) ...")
        animate(
            synced_image_path    = synced_image_path,
            reference_video_path = reference_video_path,
            prompt               = eng["stage4_motion"],
            output_path          = output_video_path,
            vram_gb              = vram_gb,
            num_frames           = video_frames,
            fps                  = video_fps,
            seed                 = effective_seed,
        )
        logger.info(f"   ✅  Stage 4 complete → {output_video_path}")
    else:
        logger.info("⏭️   Stage 4 skipped.")

    elapsed = time.time() - t_start
    logger.info("=" * 65)
    logger.info(f"🏁  Pipeline complete in {elapsed:.1f}s  |  Output: {output_video_path}")
    logger.info("=" * 65)

    return {
        "output_video":    output_video_path,
        "portrait":        portrait_path,
        "reference_frame": reference_frame_path,
        "synced_image":    synced_image_path,
        "elapsed_sec":     elapsed,
        "vram_gb":         vram_gb,
        "tier":            tier,
        "run_id":          run_id,
        "prompts":         eng,    # exposes all 3 stage prompts for debugging
    }
