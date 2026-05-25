import os
import sys
import logging

# Ensure the root directory is in the path
root_dir = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
if root_dir not in sys.path:
    sys.path.append(root_dir)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_watermark")

try:
    from Visual_Refinement_Modules.hybrid_watermark import hybrid_detector
    
    video_path = r"downloads\Mrunal_thakkur_1.mp4"
    if not os.path.exists(video_path):
        logger.error(f"Video not found: {video_path}")
        sys.exit(1)
        
    logger.info(f"Running forensic scan on: {video_path}")
    result_json = hybrid_detector.process_video(video_path)
    
    print("\n--- DETECTION RESULT ---")
    print(result_json)
    
except Exception as e:
    logger.error(f"Verification script failed: {e}")
    import traceback
    traceback.print_exc()
