import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Compiler_Modules.music_driven_editor import MusicDrivenEditor

class TestMusicDrivenEditor(unittest.TestCase):
    def setUp(self):
        self.editor = MusicDrivenEditor()

    def test_score_scenes(self):
        scenes = [{"start": 0, "end": 5}, {"start": 5, "end": 10}]
        motion_events = [{"time": 1, "strength": "large"}, {"time": 6, "strength": "small"}]
        scored = self.editor.score_scenes(scenes, motion_events)
        self.assertEqual(len(scored), 2)
        # First scene should have higher score due to large motion
        self.assertGreater(scored[0]["importance"], scored[1]["importance"])

    def test_map_scenes_to_beats(self):
        scored_scenes = [
            {"start": 0, "end": 5, "importance": 0.9},
            {"start": 5, "end": 10, "importance": 0.5},
            {"start": 10, "end": 15, "importance": 0.3}
        ]
        classified_beats = [
            {"time": 0.0, "strength": "strong"},
            {"time": 1.2, "strength": "medium"},
            {"time": 2.4, "strength": "weak"},
            {"time": 3.6, "strength": "strong"},
            {"time": 4.8, "strength": "medium"},
            {"time": 6.0, "strength": "weak"},
            {"time": 10.0, "strength": "strong"}
        ]
        
        timeline = self.editor.map_scenes_to_beats(scored_scenes, classified_beats)
        self.assertTrue(len(timeline) >= 1)
        
        # Check Rule 1: Hook first
        self.assertEqual(timeline[0]["scene_ref"]["importance"], 0.9)
        self.assertGreaterEqual(timeline[0]["dur"], 0.8)
        self.assertLessEqual(timeline[0]["dur"], 1.5)

    def test_generate_ffmpeg_commands(self):
        timeline = [
            {
                "scene_start": 1.0, "scene_end": 2.5, "dur": 1.5,
                "transition_after": "blur_cut", "scene_ref": {"importance": 0.8}
            },
            {
                "scene_start": 3.0, "scene_end": 4.5, "dur": 1.5,
                "scene_ref": {"importance": 0.5}
            }
        ]
        filter_parts, vout = self.editor.generate_ffmpeg_commands(timeline)
        self.assertTrue(any("trim=start=1.0:end=2.5" in f for f in filter_parts))
        self.assertTrue(any("boxblur" in f for f in filter_parts))
        self.assertEqual(vout, "[vout]")

if __name__ == "__main__":
    unittest.main()
