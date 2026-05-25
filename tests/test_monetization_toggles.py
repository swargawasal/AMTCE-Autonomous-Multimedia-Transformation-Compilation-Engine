import os
from unittest.mock import patch

def mock_logic(env_val):
    with patch.dict(os.environ, {"LOS_POLLOS_TELEGRAM": env_val}):
        # Mimic main.py logic
        enable_lp_tele = os.getenv("LOS_POLLOS_TELEGRAM", "yes").lower() in [
            "yes",
            "true",
            "on",
        ]
        
        lp_link = None
        if enable_lp_tele:
            lp_link = "https://example.com/cpa" # Mocked link
            
        raw_caption = "Original Caption\n\n"
        if enable_lp_tele and lp_link:
            raw_caption += f"💋🔞 The Secret → {lp_link}"
            
        return enable_lp_tele, raw_caption

def test_toggles():
    print("Testing LOS_POLLOS_TELEGRAM=no")
    enabled, caption = mock_logic("no")
    assert enabled is False
    assert "The Secret" not in caption
    print("✅ Passed: LOS_POLLOS_TELEGRAM=no correctly hides link")

    print("Testing LOS_POLLOS_TELEGRAM=yes")
    enabled, caption = mock_logic("yes")
    assert enabled is True
    assert "The Secret" in caption
    print("✅ Passed: LOS_POLLOS_TELEGRAM=yes correctly shows link")

if __name__ == "__main__":
    test_toggles()
