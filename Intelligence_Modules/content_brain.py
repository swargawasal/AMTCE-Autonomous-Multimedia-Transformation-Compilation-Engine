import os
import json
import logging
import re
import time
import jsonschema
from gemini_governor import gemini_router
from Intelligence_Modules.gemini_governor import gemini_router
from jsonschema import validate
from typing import List, Dict, Any

logger = logging.getLogger("content_brain")

CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "brain": {
            "type": "object",
            "properties": {
                "editorial_script": {"type": "string"},
                "generated_title": {"type": "string"},
                "overlay_data": {
                    "type": "object",
                    "properties": {
                        "brand_text": {"type": "string"},
                        "trend_text": {"type": "string"},
                        "context_text": {"type": "string"},
                        "item_name": {"type": "string"},
                        "price_tag": {"type": "string"}
                    },
                    "required": ["brand_text", "trend_text", "context_text", "item_name", "price_tag"]
                }
            },
            "required": ["editorial_script", "generated_title", "overlay_data"]
        },
        "narrative": {
            "type": "object",
            "properties": {
                "script": {"type": "string"},
                "mood": {"type": "string"}
            },
            "required": ["script", "mood"]
        }
    },
    "required": ["brain", "narrative"]
}

CONTENT_PROMPT = """
You are a CINEMATIC STORYTELLER and SHORT-FORM EDITOR. Analyze these frames and write a STORY, not a review.
Your output powers KARAOKE SUBTITLES that are synced to the video's edit cuts — so the story MUST match the rhythm of the visuals.

Vision Context:
{vision_context}

YOUR CORE RULES:
1. STORY FIRST — Write like a documentary narrator or film voiceover. Every sentence should advance a story arc: HOOK → TENSION → REVELATION → PAY-OFF.
2. VISUAL SYNC — Each story beat must correspond to a REAL MOMENT in the video (a cut, a reaction, a reveal). The subtitles will display one beat per scene cut.
3. BANNED WORDS — Do NOT use: "effortless", "stunning", "amazing", "incredible". Replace with: "composed", "precise", "deliberate", "formidable".
4. UNIVERSAL — This must work for ANY content type: fashion clips, movie scenes, fitness, travel. Adapt the story genre to match the visuals.
5. KARAOKE PACING — Aim for 4-6 words per beat. Short beats for fast cuts. Longer beats for slow dramatic shots.

Return a single JSON object:

"brain": {{
  "editorial_script": "The story HOOK — 1 punchy sentence in present tense, first-person perspective of the subject. Max 120 characters. This is the OPENING LINE of the karaoke voiceover.",
  "generated_title": "viral title for the hook (front-loaded keyword)",
  "overlay_data": {{ "brand_text": "...", "trend_text": "...", "context_text": "...", "item_name": "...", "price_tag": "..." }}
}}

"narrative": {{
  "script": "Full story voiceover (4-6 short sentences). Written in PRESENT TENSE, DOCUMENTARY STYLE. Arc: Hook → Build → Tension → Climax → Pay-off. Each sentence = one scene cut. No motivational clichés. No product selling language.",
  "mood": "Cinematic|Dramatic|Triumphant|Mysterious|Aspirational|Raw|Elegant",
  "story_beats": [
    {{"beat": 1, "text": "Opening hook sentence (4-6 words)", "emotion": "curiosity|tension|awe"}},
    {{"beat": 2, "text": "Building tension sentence", "emotion": "build"}},
    {{"beat": 3, "text": "Revelation or turning point", "emotion": "tension|revelation"}},
    {{"beat": 4, "text": "Climax moment", "emotion": "climax|peak"}},
    {{"beat": 5, "text": "Pay-off or loop-back line", "emotion": "resolution|loop"}}
  ]
}}

Output MUST be valid JSON only. Do not include markdown code blocks or explanations.
"""

def get_fallback_payload() -> Dict[str, Any]:
    return {
        "brain": {
            "editorial_script": "This is where the story begins.",
            "generated_title": "Viral Moment",
            "overlay_data": {
                "brand_text": "",
                "trend_text": "",
                "context_text": "",
                "item_name": "",
                "price_tag": ""
            }
        },
        "narrative": {
            "script": "Every frame tells a story. This one starts now.",
            "mood": "Cinematic",
            "story_beats": [
                {"beat": 1, "text": "Every frame tells a story.", "emotion": "curiosity"},
                {"beat": 2, "text": "This one starts now.", "emotion": "build"}
            ]
        }
    }

def generate(video_path: str, frames: List[str], vision_data: Dict[str, Any]) -> Dict[str, Any]:
    if not gemini_router: 
        logger.warning("Gemini Router unavailable. Using fallback.")
        return get_fallback_payload()

    logger.info("Initiating Content Brain via Router...")
    
    vision_context = json.dumps(vision_data.get("forensic", {}), indent=2)
    prompt = CONTENT_PROMPT.format(vision_context=vision_context)
    
    payload = [prompt]
    try:
        from PIL import Image
        for p in frames:
            if os.path.exists(p):
                try:
                    img = Image.open(p)
                    payload.append(img)
                except Exception as e:
                    logger.debug(f"Content Brain could not open frame {p}: {e}")
    except ImportError:
        logger.warning("PIL not available. Content Brain cannot process images.")

    return _call_gemini_with_retry(payload, CONTENT_SCHEMA)

def _call_gemini_with_retry(payload: List[Any], schema: Dict) -> Dict[str, Any]:
    try:
        res_txt = gemini_router.generate(
            task_type="vision", 
            prompt=payload, 
            module_name="content_brain", 
            gen_config={"temperature": 0.4}
        )
        if not res_txt: return get_fallback_payload()
        
        json_match = re.search(r"\{[\s\S]*\}", res_txt)
        if not json_match:
             logger.error("No JSON found in Content Brain response")
             return get_fallback_payload()

        data = json.loads(json_match.group(0))
        validate(instance=data, schema=schema)
        return data
    except Exception as e:
        logger.error(f"Content Brain error: {e}")
        return get_fallback_payload()
