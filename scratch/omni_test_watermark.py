import os
import sys
import logging
import json

# Root Dir
root_dir = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
if root_dir not in sys.path:
    sys.path.append(root_dir)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("omni_test")

def main():
    try:
        from Visual_Refinement_Modules.hybrid_watermark import hybrid_detector
        from Visual_Refinement_Modules.static_patch_engine import StaticPatchReuseEngine
        
        downloads_dir = os.path.join(root_dir, "downloads")
        videos = [f for f in os.listdir(downloads_dir) if f.endswith(".mp4")]
        
        logger.info(f"🔍 Starting Omni-Test on {len(videos)} videos...")
        
        results = []
        for vid in videos:
            vpath = os.path.join(downloads_dir, vid)
            logger.info(f"\n🎬 Testing: {vid}")
            
            # Detect 
            json_res = hybrid_detector.process_video(vpath)
            data = json.loads(json_res)
            watermarks = data.get("watermarks", [])
            
            if not watermarks:
                logger.info(f"✅ CLEAN: No watermarks found in {vid}")
                continue
                
            # Check Stability for the first watermark
            wm = watermarks[0]
            box = wm.get("coordinates")
            hint = wm.get("motion_hint", "static")
            
            # Call analyze_stability (Mode Determination)
            mode = StaticPatchReuseEngine.analyze_stability(vpath, ["dummy_mask.png"], motion_hint=hint)
            
            logger.info(f"🎯 Verdict: {mode.upper()} (Hint: {hint})")
            results.append({"video": vid, "mode": mode, "hint": hint})

        # Summary
        logger.info("\n" + "="*40)
        logger.info("FINAL OMNI-TEST SUMMARY")
        logger.info("="*40)
        for r in results:
            logger.info(f"{r['video']:30} | Mode: {r['mode']:10} | Hint: {r['hint']}")
            
    except Exception as e:
        logger.error(f"Omni-Test crash: {e}")

if __name__ == "__main__":
    main()
