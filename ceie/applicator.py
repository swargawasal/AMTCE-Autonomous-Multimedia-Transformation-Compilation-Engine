"""
ceie/applicator.py
------------------
Master edit applicator for CEIE.
Executes the aggregated timeline by orchestrating the sub-tools:
slicing, speed ramping, transitions stitching, overlays, voiceovers, and subtitles.
"""

import os
import shutil
import logging
from typing import Dict, List, Any

from ceie.tools.cutter import trims_to_keep_ranges, extract_keep_segments
from ceie.tools.speed_applier import apply_speed_ramps
from ceie.tools.transition_applier import apply_transitions_sequential
from ceie.tools.overlay_applier import apply_overlays
from ceie.tools.voiceover_applier import apply_voiceovers
from ceie.tools.karaoke_applier import apply_karaoke

logger = logging.getLogger("ceie.applicator")

def get_video_duration(path: str) -> float:
    """Gets total duration via ffprobe."""
    import subprocess
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to get duration for {path}: {e}")
        return 0.0

def match_transitions_to_gaps(keep_ranges: List[Dict[str, float]], transitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Matches planned transitions to the gaps between segment cuts.
    For N segments, returns N-1 transition dicts.
    """
    gap_transitions = []
    for i in range(len(keep_ranges) - 1):
        gap_end = keep_ranges[i]["end"]
        # Find closest transition in plan
        closest = None
        min_dist = 2.0  # 2 seconds threshold
        for t in transitions:
            dist = abs(float(t["at_sec"]) - gap_end)
            if dist < min_dist:
                min_dist = dist
                closest = t
                
        if closest:
            gap_transitions.append(closest)
        else:
            gap_transitions.append({
                "type": "clean",
                "duration_ms": 400,
                "engine": "smart",
                "reason": "default hard cut"
            })
            
    return gap_transitions

def apply_edit_timeline(
    video_path: str,
    timeline: Dict[str, List[Any]],
    output_path: str,
    temp_dir: str
) -> bool:
    """
    Executes the full edit pipeline on video_path according to timeline.
    Saves final output to output_path.
    """
    os.makedirs(temp_dir, exist_ok=True)
    
    total_duration = get_video_duration(video_path)
    if total_duration <= 0:
        logger.error("Invalid input video duration.")
        return False
        
    # Step 1: Invert trims to keep ranges
    keep_ranges = trims_to_keep_ranges(total_duration, timeline["trims"])
    logger.info(f"Step 1: Computed {len(keep_ranges)} keep ranges.")
    
    # Step 2: Extract keep ranges into separate temporary clips
    logger.info("Step 2: Extracting segment clips...")
    segment_clips = extract_keep_segments(video_path, keep_ranges, temp_dir)
    
    # Step 3: Apply speed ramps locally to each clip
    logger.info("Step 3: Applying speed ramps locally...")
    ramped_clips = []
    for idx, clip in enumerate(segment_clips):
        start = keep_ranges[idx]["start"]
        end = keep_ranges[idx]["end"]
        duration = end - start
        
        # Find speed ramps overlapping this segment
        local_ramps = []
        for r in timeline["speed_ramps"]:
            r_start = float(r["start_sec"])
            r_end = float(r["end_sec"])
            factor = float(r.get("factor", 1.0))
            
            # Check overlap
            if r_start < end and r_end > start:
                local_start = max(0.0, r_start - start)
                local_end = min(duration, r_end - start)
                local_ramps.append({
                    "start_sec": local_start,
                    "end_sec": local_end,
                    "factor": factor
                })
                
        if local_ramps:
            ramped_path = os.path.join(temp_dir, f"ramped_seg_{idx:03d}.mp4")
            logger.info(f"Applying {len(local_ramps)} speed ramps to clip #{idx}...")
            if apply_speed_ramps(clip, ramped_path, local_ramps):
                ramped_clips.append(ramped_path)
            else:
                logger.warning(f"Speed ramp failed for clip #{idx}, using unramped clip.")
                ramped_clips.append(clip)
        else:
            ramped_clips.append(clip)
            
    # Step 4: Stitch segments together with transitions
    logger.info("Step 4: Stitching clips together with transitions...")
    stitched_path = os.path.join(temp_dir, "timeline_stitched.mp4")
    gap_transitions = match_transitions_to_gaps(keep_ranges, timeline["transitions"])
    
    if not apply_transitions_sequential(ramped_clips, gap_transitions, stitched_path):
        logger.error("Failed to stitch clips with transitions.")
        return False
        
    # Step 5: Apply text overlays
    logger.info("Step 5: Applying text overlays...")
    overlaid_path = os.path.join(temp_dir, "timeline_overlaid.mp4")
    if not apply_overlays(stitched_path, overlaid_path, timeline["text_overlays"]):
        logger.error("Failed to apply text overlays.")
        return False
        
    # Step 6: Generate and mix voiceovers
    logger.info("Step 6: Generating and mixing voiceover narration...")
    voiceover_path = os.path.join(temp_dir, "timeline_voiceover.mp4")
    success, vo_clips = apply_voiceovers(
        overlaid_path,
        voiceover_path,
        timeline["voiceover_segments"],
        temp_dir
    )
    if not success:
        logger.error("Failed to generate/mix voiceovers.")
        return False
        
    # Step 7: Burn subtitles
    logger.info("Step 7: Transcribing and burning karaoke subtitles...")
    if not apply_karaoke(voiceover_path, output_path, vo_clips, temp_dir):
        logger.error("Failed to burn subtitles.")
        return False
        
    logger.info(f"🎉 CEIE Edit Pipeline complete! Output saved: {output_path}")
    return True
