import librosa
import os
import subprocess
import logging
import tempfile

logger = logging.getLogger("audio_beat_detector")

# Get FFMPEG_BIN from environment or default to "ffmpeg"
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

def detect_beats(video_path):
    """
    Extracts audio from video and detects beats using Librosa.
    """
    if not os.path.exists(video_path):
        logger.error(f"❌ Video file not found: {video_path}")
        return None

    # 1. Extract audio using ffmpeg
    # Using tempfile to avoid collisions and ensure cleanup
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        temp_audio = tmp.name

    try:
        cmd = [
            FFMPEG_BIN, "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
            temp_audio
        ]
        logger.info(f"Extracting audio for beat detection: {video_path}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"❌ FFmpeg audio extraction failed: {result.stderr}")
            return None

        # 2. Use Librosa to compute beats
        logger.info(f"Computing beats using Librosa...")
        y, sr = librosa.load(temp_audio)
        
        # Beat track returns tempo as an array in librosa 0.10.0+
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        
        # If tempo is an array/float, handle consistently
        if hasattr(tempo, "__len__"):
            tempo_val = float(tempo[0])
        else:
            tempo_val = float(tempo)
            
        beat_times = librosa.frames_to_time(beats, sr=sr)
        
        beat_data = {
            "tempo": tempo_val,
            "beats": beat_times.tolist()
        }
        
        # 3. Log beat information
        logger.info(f"[BEAT_ANALYSIS] tempo={beat_data['tempo']:.2f} BPM | beats_detected={len(beat_data['beats'])}")
        
        return beat_data

    except Exception as e:
        logger.error(f"❌ Beat detection failed: {e}", exc_info=True)
        return None
    finally:
        if os.path.exists(temp_audio):
            try:
                os.remove(temp_audio)
            except Exception as e:
                logger.warning(f"Could not remove temp audio file {temp_audio}: {e}")

if __name__ == "__main__":
    # Simple test
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        res = detect_beats(sys.argv[1])
        print(res)
    else:
        print("Usage: python audio_beat_detector.py <video_path>")
