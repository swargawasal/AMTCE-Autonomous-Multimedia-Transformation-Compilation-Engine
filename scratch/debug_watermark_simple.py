import os
import cv2
import sys

# Ensure current directory is in path
sys.path.append(os.getcwd())

from Visual_Refinement_Modules.gemini_enhance_for_watermark import detect_watermark

def debug_single_frame(video_path):
    print(f"DEBUGGING: {video_path}")
    cap = cv2.VideoCapture(video_path)
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        print("ERROR: Could not read frame 1.")
        return

    print("LOG: Calling detect_watermark with 1 frame...")
    results, niche = detect_watermark([frame])
    
    print("\n--- RESULTS ---")
    print(f"Niche: {niche}")
    print(f"Watermarks found: {len(results) if results else 0}")
    if results:
        for r in results:
            print(f"  - {r}")

if __name__ == "__main__":
    video = "downloads/Jennie.mp4"
    if os.path.exists(video):
        debug_single_frame(video)
    else:
        print(f"File not found: {video}")
