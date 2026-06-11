import sys
import os
import json
import logging

# Add project root to sys.path
sys.path.append(os.path.abspath(os.getcwd()))

from Intelligence_Modules.monetization_brain import MonetizationStrategist

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_brain_fix")

def test_fallback_logic():
    brain = MonetizationStrategist()
    
    # Test 1: Fallback trigger with clean title
    title = "Samantha"
    # We force fallback by calling _fallback_response directly
    res = brain._fallback_response(title, error=Exception("Test Error"))
    
    print("\n--- Test 1: Basic Fallback ---")
    print(f"Title: {title}")
    print(f"Editorial Script: {res['editorial_script']}")
    print(f"Final Caption: {res['final_caption']}")
    print(f"Brand Text: {res['overlay_data'][0]['brand_text']}")
    
    assert "Link in description." not in res['editorial_script']
    assert res['final_caption'] in res['editorial_script']
    
    # Test 2: Fallback with junk/spam script (should trigger get_safe_fallback)
    failed_script = "Check the link in description for more info!"
    res2 = brain._fallback_response(title, failed_script=failed_script)
    
    print("\n--- Test 2: Spam Fallback ---")
    print(f"Failed Script Input: {failed_script}")
    print(f"Recovered Script: {res2['final_caption']}")
    print(f"Editorial Script: {res2['editorial_script']}")
    
    assert "link in description" not in res2['final_caption'].lower()
    
    # Test 3: Cache Rotation
    cache_path = os.path.join("The_json", "captions_cache.json")
    state_path = os.path.join("The_json", "caption_state.json")
    with open(state_path, "r") as f:
        state_before = json.load(f)
    with open(cache_path, "r", encoding="utf-8") as f:
        captions = json.load(f)
    
    idx_before = state_before.get("fallback_index", 0)
    print(f"\n--- Test 3: Cache Rotation ---")
    print(f"Index Before: {idx_before}")
    
    # Trigger fallback that uses get_safe_fallback
    brain.get_safe_fallback()
    
    with open(state_path, "r") as f:
        state_after = json.load(f)
    idx_after = state_after.get("fallback_index", 0)
    print(f"Index After: {idx_after}")
    
    if len(captions) > 1:
        assert idx_after != idx_before

if __name__ == "__main__":
    try:
        test_fallback_logic()
        print("\n[SUCCESS] ALL TESTS PASSED!")
    except Exception as e:
        print(f"\n[FAILURE] TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
