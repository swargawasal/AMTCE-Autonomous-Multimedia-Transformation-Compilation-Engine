
import sys
import cv2
import os

video_path = sys.argv[1] if len(sys.argv) > 1 else ""
if not os.path.exists(video_path):
    print("Video not found at that path.")
else:
    cap = cv2.VideoCapture(video_path)
    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    print(f"Dimensions: {w}x{h}")
    ret, frame = cap.read()
    if ret:
        print(f"Actual Frame shape: {frame.shape}")
    cap.release()
