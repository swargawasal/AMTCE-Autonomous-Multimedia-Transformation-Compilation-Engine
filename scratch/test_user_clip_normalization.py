import os
import sys
import logging

# Add the project root to sys.path
sys.path.append(r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)")

from First_Shot_Engine.first_shot_engine import run_first_shot_engine, normalize_intro

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO)

profile_data = {}
job_dir = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\jobs\test_first_shot_user_clip"
os.makedirs(job_dir, exist_ok=True)

# User's specific clip path
user_clip = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\First_Shots\general_intro\Welcome back to my channel When you don't know what to wear, get yourself this.mp4"

# We want to force the engine to use this clip, but run_first_shot_engine picks from library.
# So let's test normalize_intro directly on it.

output_path = os.path.join(job_dir, "normalized_user_clip.mp4")
final = normalize_intro(user_clip, output_path)

print(f"Normalized Path: {final}")

if final:
    import subprocess
    import json
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", final]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Normalized Duration: {res.stdout.strip()}")
