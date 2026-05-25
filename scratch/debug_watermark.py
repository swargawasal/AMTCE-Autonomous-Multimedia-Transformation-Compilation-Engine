import os
import cv2
import logging
import sys

# Ensure current directory is in path
sys.path.append(os.getcwd())

from Visual_Refinement_Modules.gemini_enhance_for_watermark import detect_watermark

# Configure logging to see our new debug messages
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("debug_detect")

def debug_video(video_path):
    print(f"DEBUGGING: {video_path}")
    cap = cv2.VideoCapture(video_path)
    frames = []
    
    # Grab 7 frames as per the module's logic
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    indices = [int(i * total_frames / 7) for i in range(7)]
    
    for i, idx in enumerate(indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
            cv2.imwrite(f"scratch/debug_frame_{i}.jpg", frame)
    cap.release()
    
    if not frames:
        print("ERROR: Could not read frames.")
        return

    print(f"LOG: Calling detect_watermark with {len(frames)} frames...")
    results, niche = detect_watermark(frames)
    
    print("\n--- RESULTS ---")
    print(f"Niche: {niche}")
    print(f"Watermarks found: {len(results) if results else 0}")
    if results:
        for r in results:
            print(f"  - {r}")
    else:
        print("  (Empty results)")

if __name__ == "__main__":
    video = "downloads/Jennie.mp4"
    if os.path.exists(video):
        debug_video(video)
    else:
        print(f"File not found: {video}")
