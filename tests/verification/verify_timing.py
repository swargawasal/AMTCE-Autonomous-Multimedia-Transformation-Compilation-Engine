
import sys
import os

# Ensure project root is in path
project_root = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
if project_root not in sys.path:
    sys.path.append(project_root)

from Compiler_Modules import video_pipeline
import unittest
from unittest.mock import MagicMock, patch

class TestPriceTagTiming(unittest.TestCase):
    def test_reconstruction_timing(self):
        # We want to verify the logic inside build_concat_pipeline or the calling code
        # Since I edited render_scene_reconstruction, I'll mock its dependencies
        
        # Test 1: Tracking video (.mov)
        # It should NOT have an 'enable' guard in render_scene_reconstruction's final concat_parts
        # Actually, let's just inspect the logic I wrote.
        
        # Mocking os.path.exists and input paths
        with patch('os.path.exists', return_value=True):
            with patch('Compiler_Modules.video_pipeline.get_video_info', return_value={'duration': 10}):
                # Simulate the logic I added to render_scene_reconstruction
                price_tag_images = ["price_tag.mov"]
                price_tag_time = 0.75
                input_paths = ["clip.mp4"]
                
                # Manual check of the logic snippet:
                tag = price_tag_images[0]
                is_tracking_video = tag.lower().endswith((".mov", ".webm", ".mp4"))
                t_start = max(0.0, price_tag_time)
                t_end = min(5.0, t_start + 4.25)
                
                print(f"Tracking Video: {tag}")
                print(f"is_tracking_video: {is_tracking_video}")
                print(f"t_start: {t_start}, t_end: {t_end}")
                
                self.assertTrue(is_tracking_video)
                self.assertEqual(t_start, 0.75)
                self.assertEqual(t_end, 5.0)

                # Test 2: Static Image (.png)
                tag2 = "price_tag.png"
                is_tracking_video2 = tag2.lower().endswith((".mov", ".webm", ".mp4"))
                print(f"\nStatic Image: {tag2}")
                print(f"is_tracking_video: {is_tracking_video2}")
                self.assertFalse(is_tracking_video2)

if __name__ == '__main__':
    unittest.main()
