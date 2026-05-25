import os
import sys
import json
import logging
from pathlib import Path

# Setup minimal logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_niche")

# Mock the _get_session_niche function from main.py
def _get_session_niche(video_path: str) -> str:
    try:
        if not video_path:
            return "General_Fallback"
            
        video_path_obj = Path(video_path)
        base_name = video_path_obj.stem
        
        # 1. Check direct sidecar
        candidate_paths = [
            video_path_obj.with_suffix(".niche.json"),
            Path("downloads") / f"{base_name}.niche.json",
            Path("Processed Shorts") / f"{base_name}.niche.json",
            Path("downloads") / f"{base_name.replace('_processed', '')}.niche.json"
        ]
        
        for sidecar in candidate_paths:
            if sidecar.exists():
                try:
                    with open(sidecar, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    niche = data.get("detected_niche", "General_Fallback")
                    logger.info(f"📂 [NICHE ROUTER] Sidecar niche read: '{niche}'")
                    return niche
                except: pass

        # 2. Inference Fallback: Read main .json metadata
        main_json = video_path_obj.with_suffix(".json")
        if not main_json.exists():
            # Try in Processed Shorts
            main_json = Path("Processed Shorts") / f"{base_name}.json"
            
        if main_json.exists():
            logger.info(f"🔍 Found main metadata: {main_json}")
            try:
                with open(main_json, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                
                # Check various fields for niche hints
                # In Simi_01.json, item_name is in pipeline_metrics -> monetization -> item_name
                mon_data = meta.get("pipeline_metrics", {}).get("monetization", {})
                if not mon_data:
                    mon_data = meta.get("monetization", {})
                
                item_name = str(mon_data.get("item_name", "")).lower()
                caption = str(meta.get("caption_data", {}).get("caption", "")).lower()
                
                logger.info(f"Targeting Item: '{item_name}'")
                
                # Keyword Mapping
                mappings = {
                    "Fashion & Style": ["bra", "outfit", "dress", "style", "wear", "clothing", "fashion", "look"],
                    "AI Tech & Futuristic Content": ["ai", "tech", "robot", "future", "gadget"],
                    "Comedy & Relatable Meme": ["joke", "funny", "meme", "comedy", "laugh"],
                    "Food & Cooking": ["food", "cooking", "recipe", "chef", "eat"],
                    "Fitness & Body Transformation": ["fitness", "gym", "workout", "body", "muscle"]
                }
                
                for niche_name, keywords in mappings.items():
                    if any(kw in item_name or kw in caption for kw in keywords):
                        logger.info(f"🧠 [NICHE ROUTER] Inferred niche from metadata: '{niche_name}'")
                        return niche_name
            except Exception as _inf_e:
                logger.error(f"Niche inference failed: {_inf_e}")

    except Exception as e:
        logger.warning(f"⚠️ [NICHE ROUTER] Error: {e}")
        
    return "General_Fallback"

if __name__ == "__main__":
    test_path = r"Processed Shorts\Simi_01.mp4"
    print(f"\n--- Testing Niche Inference for: {test_path} ---")
    inferred = _get_session_niche(test_path)
    print(f"Result: {inferred}")
    
    if inferred == "Fashion & Style":
        print("\n✅ SUCCESS: Successfully inferred 'Fashion & Style' for Simi_01.mp4")
    else:
        print(f"\n❌ FAILURE: Expected 'Fashion & Style', got '{inferred}'")
