import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

print("Starting verification...")

try:
    from Audio_Modules.audio_pool_manager import AudioPoolManager
    print("SUCCESS: AudioPoolManager imported")
except Exception as e:
    print(f"FAILURE: AudioPoolManager import error: {e}")

try:
    from Audio_Modules.lyric_rhythm_aligner import analyze_music
    print("SUCCESS: lyric_rhythm_aligner imported")
except Exception as e:
    print(f"FAILURE: lyric_rhythm_aligner import error: {e}")
