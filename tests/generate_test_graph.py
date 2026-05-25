import sys
import os
import logging
import json

# Add project root to path
sys.path.append(r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)")

from Compiler_Modules.video_pipeline import render_scene_reconstruction

logging.basicConfig(level=logging.INFO)

input_video = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Download_Modules\downloads\Avneet_kaur.mp4"
output_video = r"test_output.mp4"

# Dummy segments
segments = [
  {"start": 0.2, "end": 3.4, "score": 0.82},
  {"start": 5.1, "end": 7.0, "score": 0.76},
  {"start": 12.0, "end": 16.2, "score": 0.71}
]

print("Generating debug files...")

# Will generate graph_debug.txt and segments_debug.json
try:
    render_scene_reconstruction(
        input_path=input_video,
        output_path=output_video,
        segments=segments,
        filters=[],
        speed_factor=1.0,
        timeline_instructions={"scenes": segments}  # bypass sort
    )
except Exception as e:
    print(f"Failed: {e}")
