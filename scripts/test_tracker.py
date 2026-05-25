import sys
import os

# Add root folder to sys_path
sys.path.append(os.getcwd())

try:
    from Visual_Refinement_Modules.dynamic_price_tag import tracker_engine
    
    # Generate 6 seconds of blue video as a placeholder if no real video is found
    test_vid = r"temp/test_tracking_input.mp4"
    os.makedirs("temp", exist_ok=True)
    if not os.path.exists(test_vid):
         print("Generatig dummy video for testing...")
         os.system(f'ffmpeg -y -f lavfi -i color=c=blue:s=1080x1920:d=6 -c:v libx264 "{test_vid}"')

    out = r"temp/test_tracking_output.mov"
    if os.path.exists(out):
        os.remove(out)
        
    human_box = [400, 400, 200, 200]
    print("Starting generator...")
    success = tracker_engine.generate_tracking_video(
        test_vid, out, human_box, "TEST PREP", "$45", "torso"
    )
    
    print("Tracking Success returned:", success)
    if os.path.exists(out):
         print("Output size:", os.path.getsize(out) / 1024, "KB")
         os.system(f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{out}"')

except Exception as e:
    import traceback
    traceback.print_exc()
