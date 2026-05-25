
import os
import subprocess
import json
import logging
import sys
import uuid

# Add current directory to path
sys.path.append(os.getcwd())

from Compiler_Modules import video_pipeline, orchestrator

logging.basicConfig(level=logging.INFO)

def get_stream_durations(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,duration",
        "-of", "json", path
    ]
    ffprobe_bin = os.getenv("FFMPEG_BIN", "ffmpeg").replace("ffmpeg", "ffprobe")
    if "ffmpeg" not in ffprobe_bin.lower(): ffprobe_bin = "ffprobe"
    cmd[0] = ffprobe_bin
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    
    durations = {}
    for stream in data.get("streams", []):
        durations[stream["codec_type"]] = float(stream.get("duration", 0))
    return durations

def create_test_video(path):
    # Create a 20s test video with audio
    cmd = [
        os.getenv("FFMPEG_BIN", "ffmpeg"), "-y",
        "-f", "lavfi", "-i", "testsrc=duration=20:size=1080x1920:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=20",
        "-c:v", "libx264", "-c:a", "aac",
        path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def test_sync():
    test_input = "test_input_sync.mp4"
    test_output = "test_output_sync.mp4"
    music_dir = "music"
    os.makedirs(music_dir, exist_ok=True)
    test_music = os.path.join(music_dir, "test_track.mp3")
    
    # Create test assets
    create_test_video(test_input)
    # Create a 10s music track
    cmd_music = [
        os.getenv("FFMPEG_BIN", "ffmpeg"), "-y", "-f", "lavfi", "-i", "sine=frequency=500:duration=10",
        "-c:a", "libmp3lame", test_music
    ]
    subprocess.run(cmd_music, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Test with full orchestration
    print("\n--- Testing with Full Orchestration (Trim, Speed, and BGM) ---")
    
    # Run orchestrator directly to test the whole flow
    job_id = f"test_{uuid.uuid4().hex[:6]}"
    # Mocking metadata for compiler
    orchestrator.compile_video(
        uuid_str=job_id,
        input_path=test_input,
        output_path=test_output,
        title="Test Video",
        description="Verification Run"
    )
    
    durs = get_stream_durations(test_output)
    print(f"Output Durations: {durs}")
    
    if "video" in durs and "audio" in durs:
        diff = abs(durs["video"] - durs["audio"])
        print(f"Duration Difference: {diff:.4f}s")
        # Check music_usage.json
        usage_path = "The_json/music_usage.json"
        if os.path.exists(usage_path):
            with open(usage_path, 'r', encoding='utf-8') as f:
                usage = json.load(f)
                print(f"Music Usage State: {usage}")
        
        if diff < 0.6: # 0.6s is safe for container/aac priming at 1.04x speedup
            print("SUCCESS: Durations are synchronized!")
        else:
            print(f"FAILURE: Durations are NOT synchronized (Diff: {diff:.4f}s)!")
    else:
        print("FAILURE: Missing video or audio stream in output.")
        
    # Cleanup
    if os.path.exists(test_input): os.remove(test_input)
    # if os.path.exists(test_output): os.remove(test_output) # Keep for manual check if needed
    if os.path.exists(test_music): os.remove(test_music)

if __name__ == "__main__":
    test_sync()
