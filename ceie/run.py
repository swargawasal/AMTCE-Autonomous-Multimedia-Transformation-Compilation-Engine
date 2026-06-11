"""
ceie/run.py
-----------
Command-line interface and entry point for CEIE.
Orchestrates the entire Raw -> Beautiful edit pipeline.
"""

import argparse
import os
import shutil
import json
import logging
import time

from ceie.chunker import chunk_video
from ceie.analyzer import VideoAnalyzer
from ceie.aggregator import globalize_plan, flatten_timeline
from ceie.applicator import apply_edit_timeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ceie.main")

def main():
    parser = argparse.ArgumentParser(description="Cinematic Edit Intelligence Engine (CEIE) — v3")
    parser.add_argument("--input", required=True, help="Path to raw input video file")
    parser.add_argument("--output", help="Path for final edited video output")
    parser.add_argument("--chunk-duration", type=float, default=60.0, help="Target duration of analysis chunks (seconds)")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary files after rendering")
    
    args = parser.parse_args()
    
    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        logger.error(f"Input video file not found: {input_path}")
        return
        
    session_id = f"ceie_{int(time.time())}"
    
    # Establish default output path if not specified
    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(input_path)), "ceie", "output")
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(output_dir, f"{base_name}_ceie_edit.mp4")
        
    temp_dir = os.path.join(os.path.dirname(output_path), "temp", session_id)
    os.makedirs(temp_dir, exist_ok=True)
    
    logger.info("======================================================================")
    logger.info("🎬 Starting Cinematic Edit Intelligence Engine (CEIE) Session")
    logger.info(f"Input Video:  {input_path}")
    logger.info(f"Output Video: {output_path}")
    logger.info(f"Session ID:   {session_id}")
    logger.info("======================================================================")
    
    start_time = time.time()
    
    try:
        # Phase 1: Chunker (FFmpeg -> shot snapping)
        logger.info("⚡ [PHASE 1] Slicing video into analysis chunks...")
        chunk_clips_dir = os.path.join(temp_dir, "analysis_chunks")
        chunks = chunk_video(input_path, chunk_clips_dir, args.chunk_duration)
        logger.info(f"✅ Generated {len(chunks)} chunks for sequential analysis.")
        
        # Phase 2: Sequential Analysis (Gemini File API + Governor Model Rotation)
        logger.info("⚡ [PHASE 2] Starting sequential Gemini analysis (model rotation)...")
        analyzer = VideoAnalyzer(session_id=session_id)
        master_plan = analyzer.analyze_chunks(chunks, input_path)
        
        # Save master blueprint for audit/debugging
        blueprint_json_path = f"{output_path}.blueprint.json"
        with open(blueprint_json_path, "w", encoding="utf-8") as f:
            json.dump(master_plan.model_dump(), f, indent=2)
        logger.info(f"✅ Saved generated Master Edit Blueprint to: {blueprint_json_path}")
        
        # Phase 3 & 4: Aggregation and Timeline Globalization
        logger.info("⚡ [PHASE 3] Globalizing timeline timestamps and flattening events...")
        global_plan = globalize_plan(master_plan)
        timeline = flatten_timeline(global_plan)
        
        # Save flattened timeline for inspection
        timeline_json_path = f"{output_path}.timeline.json"
        with open(timeline_json_path, "w", encoding="utf-8") as f:
            json.dump(timeline, f, indent=2)
        logger.info(f"✅ Saved flattened timeline events to: {timeline_json_path}")
        
        # Phase 5: Applicator (FFmpeg rendering)
        logger.info("⚡ [PHASE 5] Initiating master timeline editing applicator...")
        render_temp_dir = os.path.join(temp_dir, "rendering")
        success = apply_edit_timeline(input_path, timeline, output_path, render_temp_dir)
        
        if success:
            logger.info("======================================================================")
            logger.info(f"🏆 CEIE Edit Session Completed Successfully in {time.time() - start_time:.1f}s!")
            logger.info(f"Final Video File: {output_path}")
            logger.info(f"Global Narrative Summary:\n{master_plan.global_narrative}")
            logger.info(f"YouTube Transformative Eligibility: {master_plan.yt_eligibility.upper()}")
            logger.info(f"YouTube Notes: {master_plan.yt_eligibility_notes}")
            logger.info("======================================================================")
        else:
            logger.error("❌ Edit applicator pipeline failed.")
            
    except Exception as e:
        logger.exception(f"❌ Session crashed with exception: {e}")
        success = False
        
    finally:
        # Clean up temporary directory
        if not args.keep_temp and os.path.exists(temp_dir):
            logger.info("🧹 Cleaning up temporary session files...")
            try:
                shutil.rmtree(temp_dir)
                logger.info("🧹 Cleanup complete.")
            except Exception as cleanup_err:
                logger.warning(f"⚠️ Failed to remove temporary directory {temp_dir}: {cleanup_err}")
                
if __name__ == "__main__":
    main()
