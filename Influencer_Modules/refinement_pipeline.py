"""
Influencer_Modules/refinement_pipeline.py
------------------------------------------
Orchestrates the AI Influencer Face Refinement pass.

Entry point: run_face_refinement(video_path, profile_data) -> dict

Called by the Telegram "Face Swap & Post" button handler
via asyncio.to_thread (synchronous blocking function).

Logic:
  1. Resolve the influencer reference face image
  2. Run FaceSwapEngine.swap_face() (reuses Reaction_Engine's engine)
  3. Write a .niche.json sidecar so _get_session_niche() routes upload
     to the face_refinement Instagram account
  4. Optionally generate an SD portrait (GPU-conditional, synchronous)

.env flags consumed here:
    INFLUENCER_FACE_IMAGE          – reference face (fallback: FACEFUSION_SOURCE_IMAGE)
    FACEFUSION_DIR                 – FaceFusion install path
    INFLUENCER_REFINEMENT_NICHE    – niche key for upload routing (default: face_refinement)
    INFLUENCER_SD_PORTRAIT         – auto / yes / no  (default: auto)
    INFLUENCER_PORTRAIT_OUTPUT     – where to save SD portrait
"""

import json
import logging
import os
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger("influencer.refinement_pipeline")


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_niche_sidecar(video_path: str, niche: str) -> None:
    """Write a .niche.json sidecar next to the swapped video."""
    try:
        stem    = os.path.splitext(video_path)[0]
        sidecar = stem + ".niche.json"
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"detected_niche": niche}, f)
        logger.info(f"[REFINEMENT] Niche sidecar written: {os.path.basename(sidecar)} -> {niche}")
    except Exception as e:
        logger.warning(f"[REFINEMENT] Failed to write niche sidecar: {e}")


def _should_generate_portrait() -> bool:
    """Return True if we should generate an SD portrait alongside the swap."""
    mode = os.getenv("INFLUENCER_SD_PORTRAIT", "auto").lower()
    if mode == "yes":
        return True
    if mode == "no":
        return False
    # auto: generate when GPU VRAM >= 6 GB
    try:
        import torch
        vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        return vram >= 6.0
    except Exception:
        return False


def _generate_portrait_sync(profile_data: Dict[str, Any]) -> Optional[str]:
    """
    Generate an SD portrait using the Influencer Pipeline.
    Runs synchronously (called from a background thread already).
    Returns the portrait path on success, or None on failure.
    """
    output_path = os.getenv(
        "INFLUENCER_PORTRAIT_OUTPUT",
        os.path.join("Influencer_Output", "latest_portrait.jpg"),
    )
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # Try to extract outfit info from profile_data (fashion scout)
    outfit_desc = None
    try:
        scout = profile_data.get("fashion_data") or profile_data.get("fashion_scout") or {}
        if isinstance(scout, dict):
            outfit_desc = scout.get("item_name") or scout.get("wear_description")
    except Exception:
        pass

    try:
        from Influencer_Modules.influencer_pipeline import run as _inf_run
        from Influencer_Modules.compute_gate import check_gpu_ready

        ready, _, _ = check_gpu_ready()
        if not ready:
            logger.info("[REFINEMENT] GPU not ready for SD portrait — skipping.")
            return None

        # We need a reference face and video — use env defaults
        ref_face  = os.getenv("INFLUENCER_FACE_IMAGE", "")
        ref_video = profile_data.get("source_video", "")

        if not ref_face or not os.path.isfile(ref_face):
            logger.warning("[REFINEMENT] SD portrait skipped — INFLUENCER_FACE_IMAGE not set.")
            return None
        if not ref_video or not os.path.isfile(ref_video):
            logger.info("[REFINEMENT] SD portrait skipped — no source video in session.")
            return None

        concept = f"realistic indian fashion influencer, {outfit_desc}" if outfit_desc \
                  else os.getenv("INFLUENCER_PROMPT_TEMPLATE", "realistic south asian woman, confident expression")

        result = _inf_run(
            reference_face_path  = ref_face,
            reference_video_path = ref_video,
            prompt               = concept,
            output_dir           = "Influencer_Output",
            skip_stages          = [4],   # skip animation stage
        )

        portrait = result.get("synced_image") or result.get("portrait")
        if portrait and os.path.isfile(portrait):
            import shutil
            shutil.copy2(portrait, output_path)
            logger.info(f"[REFINEMENT] SD portrait saved: {output_path}")
            return output_path

    except Exception as e:
        logger.warning(f"[REFINEMENT] SD portrait generation failed (non-fatal): {e}")

    return None


# ── main entry point ──────────────────────────────────────────────────────────

def run_face_refinement(
    video_path:   str,
    profile_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run the face refinement pass on a single video.

    This is a SYNCHRONOUS function — call it via asyncio.to_thread from async code.

    Parameters
    ----------
    video_path   : Path to the processed video (main pipeline output).
    profile_data : Session profile data (used for SD portrait outfit detection).

    Returns
    -------
    dict with keys:
        success      (bool)
        message      (str)
        swapped_path (str | None)
        portrait_path(str | None)
        niche        (str)
    """
    niche = os.getenv("INFLUENCER_REFINEMENT_NICHE", "face_refinement")

    # ── Resolve influencer face image ─────────────────────────────────────────
    face_img = (
        os.getenv("INFLUENCER_FACE_IMAGE", "").strip()
        or os.getenv("FACEFUSION_SOURCE_IMAGE", "").strip()
    )
    if not face_img or not os.path.isfile(face_img):
        msg = (
            "INFLUENCER_FACE_IMAGE is not set or file missing. "
            "Add the path to your reference face in .env."
        )
        logger.warning(f"[REFINEMENT] {msg}")
        return {"success": False, "message": msg, "swapped_path": None,
                "portrait_path": None, "niche": niche}

    # ── Face swap via existing FaceSwapEngine ─────────────────────────────────
    try:
        from Reaction_Engine.face_swap_engine import FaceSwapEngine as _FSE, _facefusion_dir

        if not _facefusion_dir():
            msg = "FACEFUSION_DIR is not configured in .env."
            logger.warning(f"[REFINEMENT] {msg}")
            return {"success": False, "message": msg, "swapped_path": None,
                    "portrait_path": None, "niche": niche}

        engine = _FSE()
        # Explicitly enable for user-initiated path (bypasses ENABLE_FACE_SWAP flag)
        engine._enabled    = True
        engine._source_img = os.path.abspath(face_img)

        logger.info(
            f"[REFINEMENT] Swapping face on {os.path.basename(video_path)} "
            f"using {os.path.basename(face_img)} ..."
        )
        swapped = engine.swap_face(video_path, emotion_category="influencer")

    except Exception as e:
        msg = f"FaceSwapEngine error: {e}"
        logger.error(f"[REFINEMENT] {msg}", exc_info=True)
        return {"success": False, "message": msg, "swapped_path": None,
                "portrait_path": None, "niche": niche}

    # swap_face() returns the ORIGINAL path on any failure
    if swapped == video_path or not os.path.isfile(swapped):
        msg = "Face swap produced no output — verify FACEFUSION_DIR and face image."
        logger.warning(f"[REFINEMENT] {msg}")
        return {"success": False, "message": msg, "swapped_path": None,
                "portrait_path": None, "niche": niche}

    logger.info(f"[REFINEMENT] Swap complete: {os.path.basename(swapped)}")

    # ── Write niche sidecar for upload routing ────────────────────────────────
    _write_niche_sidecar(swapped, niche)

    # ── Optional SD portrait (GPU-conditional) ────────────────────────────────
    portrait_path = None
    if _should_generate_portrait():
        logger.info("[REFINEMENT] GPU capable — generating SD portrait ...")
        portrait_path = _generate_portrait_sync(profile_data)

    return {
        "success":       True,
        "message":       f"Face swap complete: {os.path.basename(swapped)}",
        "swapped_path":  swapped,
        "portrait_path": portrait_path,
        "niche":         niche,
    }
