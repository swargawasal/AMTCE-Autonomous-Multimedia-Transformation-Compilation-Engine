
import sys
import os
# Add current dir to path
sys.path.append(os.getcwd())

from Text_Modules.caption_sanitizer import sanitize_caption_text
from Text_Modules.gemini_captions import GeminiCaptionGenerator

def test_sanitizer_rotation():
    print("--- Testing Sanitizer Rotation ---")
    fallbacks = []
    for _ in range(10):
        # Pass an empty string to trigger fallback
        fallbacks.append(sanitize_caption_text(""))
    
    print(f"Generated fallbacks: {fallbacks}")
    unique_fallbacks = set(fallbacks)
    print(f"Unique fallbacks: {len(unique_fallbacks)}")
    assert len(unique_fallbacks) > 1, "Sanitizer fallback should be diverse"

def test_generator_fallback():
    print("\n--- Testing Generator Fallback ---")
    gen = GeminiCaptionGenerator()
    # Mock cache to be empty for a moment to test preset rotation
    original_cache = gen.caption_cache
    gen.caption_cache = []
    
    fallbacks = []
    for _ in range(5):
        fallbacks.append(gen.get_diversified_fallback())
    
    print(f"Generator fallbacks: {fallbacks}")
    unique_fallbacks = set(fallbacks)
    print(f"Unique fallbacks: {len(unique_fallbacks)}")
    assert len(unique_fallbacks) > 1, "Generator fallback should be diverse"
    
    gen.caption_cache = original_cache

if __name__ == "__main__":
    try:
        test_sanitizer_rotation()
        # test_generator_fallback() # Requires Gemini API Key to initialize gen completely, skip or mock
    except Exception as e:
        print(f"Test failed: {e}")
