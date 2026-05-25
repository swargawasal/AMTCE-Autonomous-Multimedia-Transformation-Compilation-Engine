import os
import json
import logging
import re
import time
import jsonschema
from gemini_governor import gemini_router
from Intelligence_Modules.gemini_governor import gemini_router
from jsonschema import validate
from typing import List, Dict, Any, Optional

logger = logging.getLogger("vision_intelligence")

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "watermark": {
            "type": "object",
            "properties": {
                "present": {"type": "boolean"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "box_2d": {"type": "array", "minItems": 4, "maxItems": 4},
                            "type": {"type": "string"}
                        },
                        "required": ["box_2d", "type"]
                    }
                }
            },
            "required": ["present", "items"]
        },
        "quality": {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "upscale_recommended": {"type": "boolean"},
                "ffmpeg_recipe": {"type": "object"}
            },
            "required": ["score", "upscale_recommended"]
        },
        "forensic": {
            "type": "object",
            "properties": {
                "intent": {"type": "string"},
                "scene_type": {"type": "string"},
                "feature_flags": {"type": "object"},
                "safety": {"type": "string"}
            },
            "required": ["intent", "scene_type", "feature_flags", "safety"]
        }
    },
    "required": ["watermark", "quality", "forensic"]
}

VISION_PROMPT = """
You are an AI Vision Engineer. Analyze the provided video frames and return a strict JSON response.
Structure your analysis into a single JSON object with these exact root keys:

"watermark": {
  "present": true if any branding, logos, social media handles, channel IDs, or promotional text overlays are visible,
  "items": list of { "box_2d": [ymin, xmin, ymax, xmax], "type": "logo|text|handle|id" }
}

"quality": {
  "score": quality rating 0.0-1.0,
  "upscale_recommended": true if quality is low or blurry,
  "ffmpeg_recipe": dict of { "sharpness", "denoise", "contrast", "brightness", "upscale" }
}

"forensic": {
  "intent": "fashion|gaming|vlog|etc.",
  "scene_type": "studio|street|bedroom|runway|etc.",
  "feature_flags": { "enable_price_tags": bool, "enable_fast_pacing": bool, "dynamic_tracking": bool, "high_motion_grading": bool },
  "safety": "safe|risky|blocked"
}

AGGRESSION UPDATE: Be extremely thorough. Scan all four corners for small usernames or IDs.
Output MUST be valid JSON only. Do not include markdown code blocks or explanations.
"""

def get_fallback_payload() -> Dict[str, Any]:
    return {
        "watermark": {
            "present": False,
            "items": []
        },
        "quality": {
            "score": 0.5,
            "upscale_recommended": False,
            "ffmpeg_recipe": {}
        },
        "forensic": {
            "intent": "unknown",
            "scene_type": "unknown",
            "feature_flags": {},
            "safety": "safe"
        }
    }

def analyze(video_path: str, frames: List[str]) -> Dict[str, Any]:
    if not gemini_router: 
        logger.warning("Gemini Router unavailable. Using fallback.")
        return get_fallback_payload()
    
    logger.info("Initiating Vision Intelligence via Router...")
    
    payload = [VISION_PROMPT]
    try:
        from PIL import Image
        for p in frames:
            if os.path.exists(p):
                try:
                    img = Image.open(p)
                    payload.append(img)
                except Exception as e:
                    logger.debug(f"Vision Intelligence could not open frame {p}: {e}")
    except ImportError:
        logger.warning("PIL not available. Vision Intelligence cannot process images.")

    return _call_gemini_with_retry(payload, VISION_SCHEMA)

def _call_gemini_with_retry(payload: List[Any], schema: Dict) -> Dict[str, Any]:
    try:
        res_txt = gemini_router.generate(
            task_type="vision", 
            prompt=payload, 
            module_name="vision_intelligence", 
            gen_config={"temperature": 0.2}
        )
        if not res_txt: return get_fallback_payload()
        
        json_match = re.search(r"\{[\s\S]*\}", res_txt)
        if not json_match:
             logger.error("No JSON found in Vision response")
             return get_fallback_payload()
             
        data = json.loads(json_match.group(0))
        validate(instance=data, schema=schema)
        return data
    except Exception as e:
        logger.error(f"Vision error: {e}")
        return get_fallback_payload()
