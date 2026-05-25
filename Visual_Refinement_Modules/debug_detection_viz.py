import sys
import os

# Ensure we can find local modules
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)

import logging
import cv2
import json
from dotenv import load_dotenv

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_viz")

# Load credentials from .env
load_dotenv("Credentials/.env")

# Force Env
os.environ["WATERMARK_INPAINT_QUALITY"] = "hybrid"

try:
    try:
        from . import hybrid_watermark
        from . import gemini_enhance_for_watermark as gemini_enhance
    except ImportError:
        from Visual_Refinement_Modules import hybrid_watermark
        import gemini_enhance_for_watermark as gemini_enhance
except ImportError as e:
    logger.error(f"Import Failed: {e}")
    sys.exit(1)

def run_debug_viz():
    video_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJECT_ROOT, "downloads", "sample.mp4")
    if not os.path.exists(video_path):
        logger.error("Video not found")
        return

    logger.info(f"🔎 Scanning (Direct Gemini): {video_path}")
    
    # Init Gemini
    # gemini_enhance.init_gemini(os.environ["GEMINI_API_KEY"]) # Deprecated - handled automatically via env

    # 1. Get Watermarks from Hybrid Detector
    # We call the wrapper which handles the JSON parsing
    watermarks = hybrid_watermark.hybrid_detector.detect_watermarks(video_path)
    
    if watermarks is None:
         logger.error("❌ Quota Error or Failure returned None.")
         return

    if not watermarks:
        logger.error("❌ No watermarks detected at all (Clean).")
        # Try to manually call process_video to see raw json if possible, 
        # but detect_watermarks calls it.
        return

    logger.info(f"✅ Detected {len(watermarks)} watermarks.")
    
    # 2. Draw on Frame 0
    cap = cv2.VideoCapture(video_path)
    # Grab a few frames to find one that isn't black
    frame = None
    for _ in range(30):
        ret, f = cap.read()
        if ret and f.mean() > 10:
            frame = f
            break
            
    cap.release()
    
    if frame is None:
        logger.error("Could not read valid frame.")
        return

    h_img, w_img = frame.shape[:2]
    logger.info(f"Frame Size: {w_img}x{h_img}")

    for i, wm in enumerate(watermarks):
        box_dict = wm.get('coordinates')
        label = wm.get('semantic_class', 'unknown')
        
        if not box_dict:
            logger.warning(f"Watermark {i} has no coordinates!")
            continue

        # Use Pixel Coordinates directly
        x = int(box_dict.get('x', 0))
        y = int(box_dict.get('y', 0))
        w = int(box_dict.get('w', 0))
        h = int(box_dict.get('h', 0))
        
        logger.info(f"🔹 Box {i+1}: Label='{label}' | PIXEL=[x={x}, y={y}, w={w}, h={h}]")
        
        # Draw Red Box (Thick)
        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 255), 4)
        cv2.putText(frame, f"#{i+1} {label}", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)

    # Save
    out_path = os.path.join(PROJECT_ROOT, "temp", "debug_detection_viz_v2.jpg")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, frame)
    logger.info(f"💾 Saved debug visualization to: {out_path}")

if __name__ == "__main__":
    run_debug_viz()
