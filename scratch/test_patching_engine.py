import os
import sys
import logging
import json

# Root Dir
root_dir = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
if root_dir not in sys.path:
    sys.path.append(root_dir)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_patching")

try:
    from Visual_Refinement_Modules.hybrid_watermark import hybrid_detector
    from Visual_Refinement_Modules.watermark_auto import run_adaptive_watermark_orchestration
    
    video_path = r"downloads\Akanksha_puri_5.mp4"
    if not os.path.exists(video_path):
        logger.error(f"Video not found: {video_path}")
        sys.exit(1)
        
    # 1. Detect
    json_res = hybrid_detector.process_video(video_path)
    data = json.loads(json_res)
    watermarks = data.get("watermarks", [])
    
    if not watermarks:
        logger.error("No watermarks detected for test.")
        sys.exit(1)
        
    logger.info(f"Detected {len(watermarks)} watermarks. Motion Hint for first: {watermarks[0].get('motion_hint')}")
    
    # 2. Orchestrate (Dry Run / Temp Out)
    output_temp = r"temp_watermark\test_patch_out.mp4"
    job_dir = r"temp_watermark\test_job"
    os.makedirs(job_dir, exist_ok=True)
    
    # We call run_adaptive_watermark_orchestration
    success, status = run_adaptive_watermark_orchestration(
        video_path, watermarks, output_temp, job_dir
    )
    
    logger.info(f"Orchestration Result: {success} ({status})")
    
except Exception as e:
    logger.error(f"Test failed: {e}")
    import traceback
    traceback.print_exc()
