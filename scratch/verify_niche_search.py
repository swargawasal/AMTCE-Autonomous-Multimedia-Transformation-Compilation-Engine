import os
import json
import logging
import sys
from pathlib import Path

# Mock main.py's _get_session_niche
def _get_session_niche(video_path: str) -> str:
    try:
        if not video_path:
            return "General_Fallback"
            
        video_path_obj = Path(video_path)
        base_name = video_path_obj.stem
        
        # Candidate locations for the .niche.json file
        candidate_paths = [
            video_path_obj.with_suffix(".niche.json"),
            Path("downloads") / f"{base_name}.niche.json",
            Path("Processed Shorts") / f"{base_name}.niche.json",
            # Handle cases where the original filename was different
            Path("downloads") / f"{base_name.replace('_processed', '')}.niche.json"
        ]
        
        print(f"DEBUG: Searching for sidecar for {video_path}")
        for sidecar in candidate_paths:
            print(f"DEBUG: Checking {sidecar}")
            if sidecar.exists():
                try:
                    with open(sidecar, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    niche = data.get("detected_niche", "General_Fallback")
                    print(f"DEBUG: Found '{niche}' in {sidecar}")
                    return niche
                except Exception as _read_err:
                    print(f"DEBUG: Error reading {sidecar}: {_read_err}")
                    
    except Exception as e:
        print(f"DEBUG: Error: {e}")
        
    return "General_Fallback"

# Test 1: File in downloads
test_video = "Processed Shorts/Ayesha_Khan_03.mp4"
test_sidecar = "downloads/Ayesha_Khan_03.niche.json"

os.makedirs("downloads", exist_ok=True)
with open(test_sidecar, "w") as f:
    json.dump({"detected_niche": "Fashion & Style"}, f)

niche = _get_session_niche(test_video)
print(f"RESULT: {niche}")

# Cleanup
if os.path.exists(test_sidecar):
    os.remove(test_sidecar)

if niche == "Fashion & Style":
    print("SUCCESS: Niche discovered in downloads/ folder!")
else:
    print("FAILURE: Niche not found!")
