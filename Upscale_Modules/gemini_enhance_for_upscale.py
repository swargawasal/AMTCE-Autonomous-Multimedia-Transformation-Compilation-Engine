
"""
Gemini Video Enhancement & Upscale Module
------------------------------------------
Analyzes video quality and builds FFmpeg instruction chains.
Used by the main compiler for high-quality production.
"""

import os
import cv2
import base64
import logging
import json
import re
import numpy as np
import subprocess
import time
from typing import Optional, Dict, Any, List
from Intelligence_Modules.decision_engine import DecisionEngine
from Intelligence_Modules.quality_evaluator import QualityEvaluator

logger = logging.getLogger("gemini_upscale")

from Intelligence_Modules.gemini_governor import gemini_router

HAS_GEMINI = True
try:
    from PIL import Image
except ImportError:
    pass

# Configuration
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

# Legacy Quota Manager Removed. Using gemini_governor.
gemini_client = None

def init_gemini(api_key: str, model_name: str = None) -> bool:
    """Compatibility shim."""
    return True

# _safe_gemini_call removed. Using gemini_router.

def frame_to_base64(frame: np.ndarray) -> Optional[str]:
    try:
        h, w = frame.shape[:2]
        if w > 1024:
            scale = 1024 / w
            frame = cv2.resize(frame, (1024, int(h * scale)))
        success, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return base64.b64encode(buffer).decode('utf-8') if success else None
    except: return None

def get_hybrid_prompt(n_frames: int = 1) -> str:
    return f"""
Analyze these {n_frames} video frames and generate a JSON recipe for FFmpeg enhancement.
{{
  "results": [
      {{
          "enhance": true,
          "sharpness": 0.0 to 1.0,
          "denoise": 0.0 to 1.0,
          "contrast": 0.5 to 2.0,
          "brightness": -0.2 to 0.2,
          "saturation": 0.5 to 2.0,
          "upscale": "1x" or "2x"
      }},
      ...
  ]
}}
"""

def analyze_frames_batch(frames: List[np.ndarray]) -> List[Dict[str, Any]]:
    try:
        request_contents = []
        for f in frames:
            b64 = frame_to_base64(f)
            if b64: request_contents.append({'mime_type': 'image/jpeg', 'data': b64})
        if not request_contents: return []
        request_contents.append(get_hybrid_prompt(len(request_contents)))
        
        res_txt = gemini_router.generate(
            task_type="analyzer",
            prompt=request_contents,
            module_name="gemini_upscale"
        )
        
        if not res_txt: return []
        data = json.loads(re.sub(r"```(json)?", "", res_txt).strip())
        return data.get("results", [])
    except: return []

def run(input_video: str, output_video: str, intelligence_cache=None) -> str:
    if not gemini_client: init_gemini(os.getenv("GEMINI_API_KEY"))
    try:
        cap = cv2.VideoCapture(input_video)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0: return "GEMINI_FAIL"
        frames = []
        for idx in [int(total*0.1), int(total*0.5), int(total*0.9)]:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, f = cap.read()
            if ret: frames.append(f)
        cap.release()
        
        # --- [CONSOLIDATION FIX] Check Intelligence Cache First ---
        results = []
        if intelligence_cache and hasattr(intelligence_cache, 'ffmpeg_recipe') and intelligence_cache.ffmpeg_recipe:
            logger.info("🔬 [Consolidation] Reusing FFmpeg Recipe from Intelligence Cache.")
            # Wrap recipe in a list to match expected return type of analyze_frames_batch
            results = [intelligence_cache.ffmpeg_recipe]
        else:
            results = analyze_frames_batch(frames)
            
        if not results: return "GEMINI_FAIL"
        
        sharp = np.median([float(r.get("sharpness", 0)) for r in results])
        denoise = max([float(r.get("denoise", 0)) for r in results])
        filters = []
        if sharp > 0: filters.append(f"unsharp=5:5:{sharp*1.5:.2f}:5:5:0.0")
        if denoise > 0: filters.append(f"hqdn3d={denoise*10:.1f}:{denoise*10:.1f}:6:6")
        filters.append("scale=1080:1920:flags=lanczos:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2")
        
        cmd = ["ffmpeg", "-y", "-i", input_video, "-vf", ",".join(filters), "-c:v", "libx264", "-crf", "23", output_video]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "SUCCESS"
    except: return "GEMINI_FAIL"
