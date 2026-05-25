"""
Compute Gate — Influencer Modules
==================================
Reuses the existing AMTCE ComputeCaps from Upscale_Modules.
Requires >=6 GB VRAM — same gate as the heavy upscaler.

No new hardware detection logic — single source of truth stays in
Upscale_Modules/compute_caps.py as designed.
"""

import sys
import os
import logging

logger = logging.getLogger("influencer.compute_gate")

# ---------------------------------------------------------------------------
# Tier thresholds (GB)
# ---------------------------------------------------------------------------
_TIER_LOW_MAX    = 14.0   # T4 16 GB free Colab — uses small models
_TIER_MEDIUM_MAX = 22.0   # L4 24 GB Colab Pro  — uses medium models
# >= TIER_MEDIUM_MAX        A100 40 GB           — full quality


def check_gpu_ready():
    """
    Query AMTCE's ComputeCaps and decide if the influencer engine can run.

    Returns
    -------
    tuple(is_ready: bool, vram_gb: float, error_msg: str | None)
    """
    try:
        # Insert project root so the import works whether called from
        # within Influencer_Modules/ or from the project root.
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _root not in sys.path:
            sys.path.insert(0, _root)

        from Upscale_Modules.compute_caps import ComputeCaps  # reuse existing

        caps   = ComputeCaps.get()
        vram   = caps.get("vram_gb", 0.0)
        ready  = caps.get("allow_ai_enhance", False)

        if ready:
            logger.info(
                f"✅ Influencer GPU Gate: PASSED — {vram:.1f} GB VRAM | "
                f"tier={get_model_tier(vram)}"
            )
            return True, vram, None
        else:
            msg = (
                f"GPU gate failed: VRAM={vram:.1f} GB (need >=6 GB) or no CUDA. "
                "Run on Colab T4 / L4 / A100 using colab/Influencer_Colab.ipynb"
            )
            logger.warning(f"⚠️  {msg}")
            return False, vram, msg

    except Exception as exc:
        logger.error(f"ComputeCaps import failed: {exc}")
        return False, 0.0, str(exc)


def get_model_tier(vram_gb: float) -> str:
    """
    Return model-size tier based on available VRAM.

    low    -> T4  16 GB  (Colab free)   — Wan2.1-1.3B, SDXL FP16 offload
    medium -> L4  24 GB  (Colab Pro)    — Wan2.1-14B quant, SDXL full
    high   -> A100 40 GB (Colab Pro+)   — Wan2.1-14B full, best quality
    """
    if vram_gb < _TIER_LOW_MAX:
        return "low"
    elif vram_gb < _TIER_MEDIUM_MAX:
        return "medium"
    else:
        return "high"
