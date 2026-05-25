import logging

logger = logging.getLogger("transformation_engine")

def calculate_transformation_score(features):
    """
    Computes transformation_score using:
    scene_restructure + commentary + captions + overlays + narration + visual_reframing
    Each feature is expected to be a boolean/active state (1 if active, 0 if not).
    """
    score = sum([
        1 if features.get("scene_restructure") else 0,
        1 if features.get("commentary") else 0,
        1 if features.get("captions") else 0,
        1 if features.get("overlays") else 0,
        1 if features.get("narration") else 0,
        1 if features.get("visual_reframing") else 0
    ])
    return score

def get_transformation_strategy(reused_content, current_features):
    """
    Determines required transformation intensity and enforces score >= 2 for reused content.
    """
    level = "normal"
    if reused_content:
        level = "high"
    
    score = calculate_transformation_score(current_features)
    
    # Enforcement Logic
    enforced_features = current_features.copy()
    if reused_content and score < 2:
        logger.info(f"⚖️ Transformation score {score} < 2 for reused content. Enabling additional layers...")
        
        # Priority for additional layers:
        # 1. Narration (Voiceover)
        # 2. Captions
        # 3. Visual Reframing
        
        if not enforced_features.get("narration"):
            enforced_features["narration"] = True
            score += 1
        
        if score < 2 and not enforced_features.get("captions"):
            enforced_features["captions"] = True
            score += 1
            
        if score < 2 and not enforced_features.get("visual_reframing"):
            enforced_features["visual_reframing"] = True
            score += 1

    return {
        "transformation_level": level,
        "transformation_score": score,
        "enforced_features": enforced_features
    }

def enforce_transformation_rules(feature_proposals, reused_content, transformation_score):
    """
    Apply hard transformation mandates over AI proposals.
    Caption and voiceover are always forced ON — they are non-negotiable creative output.
    """
    proposals = feature_proposals.copy() if feature_proposals else {}

    # [FIX] Caption and voiceover are always ON — Gemini must not disable them.
    # These are the primary creative outputs of the pipeline.
    proposals["caption_generation"] = True
    proposals["voiceover_generation"] = True
    proposals["music_engine"] = True

    if reused_content and transformation_score < 2.0:
        logger.info(f"🛡️ Transformation rules overriding Gemini flags. Score ({transformation_score}) < 2 for Reused Content.")
        proposals["scene_reconstruction"] = True
        proposals["smart_crop"] = True
    return proposals
