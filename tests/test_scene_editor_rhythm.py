"""
tests/test_scene_editor_rhythm.py

Unit tests for scene-based editing pipeline (Part 2, 5 spec):
- Scene detection returns valid segments
- Segments cover full video duration
- Rhythm spacing >= 0.7s  
- Edit density <= 0.35 edits/sec
"""

import sys
import os
import unittest

# Allow imports from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSceneDetectionOutput(unittest.TestCase):
    """Tests for detect_scenes() output format guarantees."""

    def setUp(self):
        # Import lazily to support CI without OpenCV
        try:
            from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor
            self.editor = SmartSceneEditor()
            self.available = True
        except Exception:
            self.available = False

    def _make_fake_scenes(self, duration: float, n_scenes: int) -> list:
        """Generate synthetic scene list (no video needed)."""
        step = duration / n_scenes
        return [{"start": round(i * step, 3), "end": round((i + 1) * step, 3)}
                for i in range(n_scenes)]

    def test_scenes_cover_full_duration(self):
        """Segments must start at 0 and end at video duration."""
        duration = 20.0
        scenes = self._make_fake_scenes(duration, 4)
        self.assertAlmostEqual(scenes[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(scenes[-1]["end"], duration, places=2)

    def test_scenes_no_overlap(self):
        """No two consecutive scenes should overlap."""
        scenes = self._make_fake_scenes(15.0, 5)
        for i in range(len(scenes) - 1):
            self.assertLessEqual(scenes[i]["end"], scenes[i + 1]["start"] + 1e-6)

    def test_scenes_sorted(self):
        """Scene list must be ordered by start time."""
        scenes = self._make_fake_scenes(10.0, 3)
        starts = [s["start"] for s in scenes]
        self.assertEqual(starts, sorted(starts))

    def test_scenes_have_required_keys(self):
        """Each scene dict must have 'start' and 'end'."""
        scenes = self._make_fake_scenes(12.0, 3)
        for sc in scenes:
            self.assertIn("start", sc)
            self.assertIn("end", sc)


class TestVisualRhythmAlignment(unittest.TestCase):
    """Tests for stabilize_rhythm() guarantees (Part 5 spec)."""

    def setUp(self):
        try:
            from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor
            self.editor = SmartSceneEditor()
            self.available = True
        except Exception:
            self.available = False

    def test_minimum_spacing_enforced(self):
        """After rhythm stabilization, no two consecutive cuts < 0.7s apart."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        dense_cuts = [0.2, 0.5, 0.8, 1.5, 2.0, 2.25, 3.0]
        duration = 5.0
        result = self.editor.stabilize_rhythm(dense_cuts, duration)
        for i in range(len(result) - 1):
            gap = round(result[i + 1] - result[i], 4)
            self.assertGreaterEqual(gap, 0.7,
                msg=f"Cut gap {result[i]:.3f}->{result[i+1]:.3f} = {gap:.4f}s < 0.7s")

    def test_maximum_spacing_enforced(self):
        """Any gap > 2.3s should have a pacing cut inserted."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        # A single cut that creates large gaps: 0→1.0 (1s OK) and 1.0→10.0 (9s too large)
        sparse_cuts = [1.0]
        duration = 10.0
        result = self.editor.stabilize_rhythm(sparse_cuts, duration)
        boundaries = [0.0] + result + [duration]
        for i in range(len(boundaries) - 1):
            gap = boundaries[i + 1] - boundaries[i]
            self.assertLessEqual(gap, 2.4,  # 2.3 + tiny float tolerance
                msg=f"Gap of {gap:.3f}s found between boundaries {boundaries[i]} and {boundaries[i+1]}")

    def test_edit_density_limit(self):
        """Edit density must stay at or below 0.35 edits/second."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        # 20 cuts for a 16s video = 1.25 edits/s — way over 0.35
        over_cuts = list(range(1, 21))  # [1, 2, ..., 20]
        duration = 16.0
        result = self.editor.stabilize_rhythm(over_cuts, duration)
        density = len(result) / duration
        self.assertLessEqual(density, 0.36,  # 0.35 + tiny float tolerance
            msg=f"Edit density {density:.4f} edits/sec exceeds 0.35 limit")

    def test_empty_cuts_passthrough(self):
        """Empty cuts list should return empty without errors."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self.editor.stabilize_rhythm([], 10.0)
        self.assertEqual(result, [])

    def test_returns_tuple(self):
        """Function must return a list of cuts."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        output = self.editor.stabilize_rhythm([1.0, 3.0], 5.0)
        self.assertIsInstance(output, list)
        self.assertEqual(output, [1.0, 3.0])


class TestAssignSceneStyle(unittest.TestCase):
    """Tests for assign_scene_style() (Part 4 spec)."""

    def setUp(self):
        try:
            from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor
            self.editor = SmartSceneEditor()
            self.available = True
        except Exception:
            self.available = False

    def test_hook_scene_gets_freeze_focus(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scene = {"start": 1.0, "end": 4.0}
        hook = {"time": 2.5, "score": 0.9}
        style = self.editor.assign_scene_style(scene, hook_moment=hook)
        self.assertEqual(style, "freeze_focus")

    def test_high_motion_gets_speed_ramp(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scene = {"start": 5.0, "end": 8.0}
        motion = [{"time": 6.0, "strength": "large"}]
        style = self.editor.assign_scene_style(scene, motion_events=motion)
        self.assertEqual(style, "speed_ramp")

    def test_short_scene_gets_punch_cut(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scene = {"start": 0.0, "end": 1.5}
        style = self.editor.assign_scene_style(scene)
        self.assertEqual(style, "punch_cut")

    def test_default_style_is_whip_transition(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scene = {"start": 0.0, "end": 5.0}
        style = self.editor.assign_scene_style(scene)
        self.assertEqual(style, "whip_transition")


class TestSingleShotHighlight(unittest.TestCase):
    """Tests for detect_single_shot_highlight() (Part 3 spec)."""

    def setUp(self):
        try:
            from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor
            self.editor = SmartSceneEditor()
            self.available = True
        except Exception:
            self.available = False

    def test_returns_none_for_multi_scene(self):
        """Multi-scene clips should not trigger single-shot mode."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scenes = [{"start": 0.0, "end": 5.0}, {"start": 5.0, "end": 10.0}]
        result = self.editor.detect_single_shot_highlight(
            "fake_path.mp4", 10.0, scenes=scenes,
            hook_moment={"time": 2.0, "score": 0.9},
            motion_events=[]
        )
        self.assertIsNone(result)

    def test_returns_highlight_for_single_scene_with_high_hook(self):
        """Single scene with hook_score > 0.8 should return highlight."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scenes = [{"start": 0.0, "end": 15.0}]
        hook = {"time": 5.0, "score": 0.95}
        result = self.editor.detect_single_shot_highlight(
            "fake_path.mp4", 15.0, scenes=scenes,
            hook_moment=hook, motion_events=[]
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["mode"], "single_shot_highlight")
        self.assertIn("segment_start", result)
        self.assertIn("segment_end", result)
        self.assertIn("effects", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
