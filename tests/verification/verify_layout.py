
import sys
import os
import logging
from unittest.mock import MagicMock, patch

# Add the project root to sys.path
sys.path.append(os.getcwd())

from Text_Modules.text_overlay import TextOverlay

def test_caption_and_brand_layout():
    # Setup logging to capture output
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("text_overlay")
    
    overlay = TextOverlay()
    overlay._drawtext_supported = True
    overlay._font_checked = True
    
    # Mock subprocess.run to capture the command
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        
        # Test parameters
        video_path = "dummy_video.mp4"
        output_path = "output_video.mp4"
        caption = "Test Caption: [brackets] (parentheses)"
        brand_text = "@swargawasal"
        
        # Create a dummy file to satisfy os.path.exists
        with open(video_path, "w") as f:
            f.write("dummy")
            
        success = overlay.add_caption_and_brand_overlay(video_path, output_path, caption, brand_text)
        
        # Get the command
        args, kwargs = mock_run.call_args
        cmd = args[0]
        cmd_str = " ".join(cmd)
        
        print("\n--- Generated FFmpeg Command ---")
        print(cmd_str)
        
        # Assertions for PNG Overlay (Caption) and Drawtext (Brand)
        assert "overlay=0:0" in cmd_str, "Overlay at 0:0 for full-frame PNG missing"
        assert "y=h-60" in cmd_str, "Brand Y position (h-60) missing"
        assert "gte(t,0.75)" in cmd_str, "Delay condition (gte(t,0.75)) missing"
        assert "drawtext" in cmd_str, "Brand drawtext missing"
        
        # Cleanup
        os.remove(video_path)
        
    print("\n--- PNG Overlay Layout Test Passed! ---")

if __name__ == "__main__":
    test_caption_and_brand_layout()
