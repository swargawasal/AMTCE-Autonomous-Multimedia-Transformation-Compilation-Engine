import unittest
import os
import sys

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Compiler_Modules.video_pipeline import sanitize_segments

class TestSceneSanitization(unittest.TestCase):
    def test_sanitize_segments(self):
        # 1. Test normal case
        segs = [{"clip_id": 0, "start": 0.0, "end": 2.0}]
        res = sanitize_segments(segs, 10.0)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["start"], 0.0)
        self.assertEqual(res[0]["end"], 2.0)

        # 2. Test clamping
        segs = [{"clip_id": 0, "start": 8.0, "end": 12.0}]
        res = sanitize_segments(segs, 10.0)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["end"], 9.95)

        # 3. Test removing short segments
        segs = [{"clip_id": 0, "start": 1.0, "end": 1.2}]
        res = sanitize_segments(segs, 10.0)
        self.assertEqual(len(res), 0)

        # 4. Test merging overlapping segments
        segs = [
            {"clip_id": 0, "start": 1.0, "end": 3.0},
            {"clip_id": 0, "start": 2.5, "end": 4.0}
        ]
        res = sanitize_segments(segs, 10.0)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["end"], 4.0)
        
        # 5. Test invalid segments (end < start)
        segs = [{"clip_id": 0, "start": 5.0, "end": 2.0}]
        res = sanitize_segments(segs, 10.0)
        self.assertEqual(len(res), 0)

if __name__ == '__main__':
    unittest.main()
