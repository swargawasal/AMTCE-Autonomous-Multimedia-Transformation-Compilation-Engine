import os
import sys
import logging

# Add the project root to sys.path
sys.path.append(r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)")

from First_Shot_Engine.first_shot_engine import run_first_shot_engine

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO)

profile_data = {}
job_dir = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\jobs\test_first_shot"
os.makedirs(job_dir, exist_ok=True)

title = "Fashion Review"
description = "A great outfit"

# Set environment variables if needed
os.environ["ENABLE_FIRST_SHOT"] = "yes"

path = run_first_shot_engine(
    profile_data=profile_data,
    job_dir=job_dir,
    title=title,
    description=description
)

print(f"Intro Path: {path}")
print(f"Profile Data: {profile_data}")
