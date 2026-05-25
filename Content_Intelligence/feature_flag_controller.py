import logging
from typing import Dict, Any
from Content_Intelligence import transformation_engine

logger = logging.getLogger("feature_flag_ctrl")

import os

def merge_feature_flags(
    feature_proposals: Dict[str, Any], 
    transformation_score: float, 
    reused_content: bool,
    pipeline_context_flags: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Merge feature flags from Gemini proposals, safety overrides, and transformation rules.
    """
    # 1. Base initialization from pipeline context or safe defaults
    final_flags = pipeline_context_flags.copy() if pipeline_context_flags else {}

    # Handle ENABLE_PRICE_TAG override
    enable_price_tag = os.getenv("ENABLE_PRICE_TAG", "auto").lower()
    
    # Define safe defaults if Gemini output is chaotic/missing
    safe_defaults = {
        "scene_detection": True,
        "smart_crop": True,
        "caption_generation": True,
        "music_engine": True,
        "voiceover_generation": True,   # [FIX] Was False — now always defaults ON
        "beat_detection": False,
        "price_tag_engine": False,
        "subject_tracking": False,
        "scene_reconstruction": True
    }
    
    for k, v in safe_defaults.items():
        if k not in final_flags:
            final_flags[k] = v

    # 2. Merge Gemini requested Feature Proposals (if they exist and are boolean)
    if isinstance(feature_proposals, dict):
        # Override with Transformation rules BEFORE merging
        feature_proposals = transformation_engine.enforce_transformation_rules(
            feature_proposals, reused_content, transformation_score
        )
        for k, v in feature_proposals.items():
            if isinstance(v, bool):
                final_flags[k] = v

    # 4. Absolute Hardcoded Preprocessing Requirements
    final_flags["watermark_detection"] = True
    final_flags["watermark_inpaint"] = True

    # Forced Overrides
    if enable_price_tag == "yes":
        final_flags["price_tag_engine"] = True
    elif enable_price_tag == "no":
        final_flags["price_tag_engine"] = False
    
    return final_flags
