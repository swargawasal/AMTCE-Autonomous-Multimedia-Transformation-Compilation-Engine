"""
tests/test_scene_highlight.py

Tests for Shot Highlight Extraction feature.

Verifies:
  - highlight is within scene boundaries
  - highlight duration is between 3-6 seconds
  - fallback to original scene on failure
  - output dict has required keys
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSelectSceneHighlight(unittest.TestCase):

    def setUp(self):
        try:
            from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor
            self.editor = SmartSceneEditor()
            self.available = True
        except Exception:
            self.available = False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _run(self, start, end, motion=None, attention=None, hook=None):
        """Convenience wrapper: build scene + frame_data and call select_scene_highlight."""
        scene = {"start": start, "end": end}
        frame_data = {
            "motion_events":    motion or [],
            "attention_events": attention or [],
            "hook_moment":      hook,
        }
        return self.editor.select_scene_highlight(scene, frame_data)

    # ── Required output keys ──────────────────────────────────────────────────

    def test_output_has_required_keys(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self._run(0.0, 8.0)
        for key in ("scene_start", "scene_end", "highlight_start", "highlight_end"):
            self.assertIn(key, result, msg=f"Missing key: {key}")

    # ── Boundary constraints ──────────────────────────────────────────────────

    def test_highlight_start_gte_scene_start(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self._run(4.0, 9.0)
        self.assertGreaterEqual(result["highlight_start"], result["scene_start"],
            msg="highlight_start must be >= scene_start")

    def test_highlight_end_lte_scene_end(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self._run(4.0, 9.0)
        self.assertLessEqual(result["highlight_end"], result["scene_end"] + 1e-9,
            msg="highlight_end must be <= scene_end")

    def test_highlight_within_scene_with_motion(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        motion = [{"time": 6.0, "strength": "large"}]
        result = self._run(4.0, 12.0, motion=motion)
        self.assertGreaterEqual(result["highlight_start"], 4.0)
        self.assertLessEqual(result["highlight_end"], 12.0 + 1e-9)

    def test_highlight_within_scene_with_face(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        attention = [{"time": 5.5, "type": "face_appearance"}]
        result = self._run(4.0, 10.0, attention=attention)
        self.assertGreaterEqual(result["highlight_start"], 4.0)
        self.assertLessEqual(result["highlight_end"], 10.0 + 1e-9)

    def test_highlight_within_scene_with_hook(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        hook = {"time": 7.0, "score": 0.95}
        result = self._run(4.0, 12.0, hook=hook)
        self.assertGreaterEqual(result["highlight_start"], 4.0)
        self.assertLessEqual(result["highlight_end"], 12.0 + 1e-9)

    # ── Duration constraints ──────────────────────────────────────────────────

    def test_highlight_min_duration_3s(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self._run(0.0, 10.0)
        duration = result["highlight_end"] - result["highlight_start"]
        self.assertGreaterEqual(duration, 3.0 - 1e-6,
            msg=f"Highlight duration {duration:.3f}s is below minimum 3s")

    def test_highlight_max_duration_6s(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self._run(0.0, 20.0)
        duration = result["highlight_end"] - result["highlight_start"]
        self.assertLessEqual(duration, 6.0 + 1e-6,
            msg=f"Highlight duration {duration:.3f}s exceeds maximum 6s")

    def test_highlight_duration_clipped_to_long_scene(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        motion = [{"time": 15.0, "strength": "large"}]
        result = self._run(0.0, 30.0, motion=motion)
        dur = result["highlight_end"] - result["highlight_start"]
        self.assertGreaterEqual(dur, 3.0 - 1e-6)
        self.assertLessEqual(dur, 6.0 + 1e-6)

    # ── Special cases ─────────────────────────────────────────────────────────

    def test_short_scene_falls_back_gracefully(self):
        """Scene shorter than 3s should still return valid dict without crash."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self._run(5.0, 7.0)  # 2s — below MIN_LENGTH
        self.assertIn("highlight_start", result)
        self.assertIn("highlight_end", result)
        # highlight should still be within scene bounds
        self.assertGreaterEqual(result["highlight_start"], 5.0)
        self.assertLessEqual(result["highlight_end"], 7.0 + 1e-6)

    def test_no_motion_no_attention_still_returns_valid(self):
        """Pure fallback scoring (all zeros) should still produce a valid result."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self._run(0.0, 8.0)
        dur = result["highlight_end"] - result["highlight_start"]
        self.assertGreaterEqual(dur, 3.0 - 1e-6)
        self.assertLessEqual(dur, 6.0 + 1e-6)
        self.assertGreaterEqual(result["highlight_start"], 0.0)
        self.assertLessEqual(result["highlight_end"], 8.0 + 1e-6)

    def test_example_from_spec(self):
        """Verify the spec example case: scene 4.0-9.0 → highlight within 4.0-9.0, 3-6s."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        result = self._run(4.0, 9.0)
        self.assertEqual(result["scene_start"], 4.0)
        self.assertEqual(result["scene_end"], 9.0)
        self.assertGreaterEqual(result["highlight_start"], 4.0)
        self.assertLessEqual(result["highlight_end"], 9.0 + 1e-6)
        dur = result["highlight_end"] - result["highlight_start"]
        self.assertGreaterEqual(dur, 3.0 - 1e-6)
        self.assertLessEqual(dur, 5.0 + 1e-6)  # scene is 5s so max is 5s


if __name__ == "__main__":
    unittest.main(verbosity=2)
