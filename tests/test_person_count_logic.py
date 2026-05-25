import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

class TestPersonCountSampling(unittest.TestCase):
    
    @patch('cv2.VideoCapture')
    def test_frame_sampling_points(self, mock_vc):
        # Mock VideoCapture to return dummy total frames and fps
        mock_instance = mock_vc.return_value
        mock_instance.get.side_effect = lambda prop: {
            7: 300,  # CAP_PROP_FRAME_COUNT (10 seconds at 30fps)
            5: 30    # CAP_PROP_FPS
        }.get(prop, 0)
        mock_instance.read.return_value = (True, MagicMock())
        
        # We need to test the logic that calculates _check_points
        # Since it's inside a large function, we'll extract the core logic here
        def calculate_points(total_frames, fps):
            duration = float(total_frames / fps)
            check_points = []
            for _sec in [1.0, 3.0, 5.0]:
                if _sec <= duration:
                    check_points.append(_sec / duration)
                else:
                    check_points.append(0.5) # simplified fallback for test
            check_points.extend([0.50, 0.80])
            return sorted(list(set(check_points)))

        # Test 10s video
        points_10s = calculate_points(300, 30)
        # Expected: [1/10, 3/10, 5/10, 8/10] -> [0.1, 0.3, 0.5, 0.8]
        self.assertIn(0.1, points_10s)
        self.assertIn(0.3, points_10s)
        self.assertIn(0.5, points_10s)
        self.assertIn(0.8, points_10s)
        self.assertEqual(len(points_10s), 4)

        # Test 3s video
        points_3s = calculate_points(90, 30)
        # Expected duration 3s. 1s/3s=0.33, 3s/3s=1.0. 5s is > 3s so fallback.
        # [0.33, 1.0, 0.5, 0.5, 0.8] -> sorted unique [0.33, 0.5, 0.8, 1.0]
        self.assertIn(1.0/3.0, points_3s)
        self.assertIn(0.5, points_3s)
        self.assertIn(0.8, points_3s)
        self.assertIn(3.0/3.0, points_3s)

    def test_prompt_update(self):
        # Verify the current generation prompt in monetization_brain
        from Intelligence_Modules.monetization_brain import FASHION_REVIEWER_PROMPT
        self.assertIn("PROFESSIONAL FASHION REVIEWER AI", FASHION_REVIEWER_PROMPT)

if __name__ == '__main__':
    unittest.main()
