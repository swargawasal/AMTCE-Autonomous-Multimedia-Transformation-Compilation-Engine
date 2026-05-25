import logging
from typing import List, Dict, Optional

logger = logging.getLogger("editing_plan")

def validate_editing_plan(plan: Dict) -> bool:
    """
    Validates the editing_plan against the required schema.
    """
    required_keys = ["mode", "segments"]
    for key in required_keys:
        if key not in plan:
            logger.warning(f"❌ Editing Plan missing key: {key}")
            return False
            
    if not isinstance(plan["segments"], list):
        logger.warning("❌ Editing Plan 'segments' must be a list")
        return False
        
    for i, seg in enumerate(plan["segments"]):
        seg_keys = ["clip_id", "start", "end"]
        for sk in seg_keys:
            if sk not in seg:
                logger.warning(f"❌ Segment {i} missing key: {sk}")
                return False
                
        if seg["start"] >= seg["end"]:
            logger.warning(f"❌ Segment {i} has invalid range: {seg['start']} -> {seg['end']}")
            # We don't return False here, just log; SmartSceneEditor will handle removal
            
    return True

def create_ai_editing_plan(segments: List[Dict], mode: str = "AI_CONTROLLED", transitions: str = "fade", effects: List = None, duration_target: float = 15.0) -> Dict:
    """
    Factory to create a structured editing plan.
    """
    return {
        "mode": mode,
        "segments": segments,
        "transitions": transitions,
        "effects": effects or [],
        "duration_target": duration_target
    }
