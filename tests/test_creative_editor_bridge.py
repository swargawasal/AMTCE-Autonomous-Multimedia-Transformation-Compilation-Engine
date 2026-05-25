"""
tests/test_creative_editor_bridge.py
=====================================
Unit tests for CreativeEditorBridge.

Run with:
    cd "d:\\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    python -m pytest tests/test_creative_editor_bridge.py -v
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Compiler_Modules.creative_editor_bridge import CreativeEditorBridge, MIN_CUT_SPACING, VO_WORDS_PER_SEC


class TestBeatThinning(unittest.TestCase):
    def setUp(self):
        self.bridge = CreativeEditorBridge()

    def test_removes_beats_closer_than_min_spacing(self):
        """Beats closer than MIN_CUT_SPACING (0.7s) should be removed."""
        # 140 BPM style: every 0.43s
        dense = [
            {"time": 0.0, "energy": 0.5}, {"time": 0.43, "energy": 0.5},
            {"time": 0.86, "energy": 0.5}, {"time": 1.29, "energy": 0.5},
            {"time": 1.72, "energy": 0.5}, {"time": 2.15, "energy": 0.5},
            {"time": 2.58, "energy": 0.5}
        ]
        thinned = self.bridge._thin_beats(dense, min_spacing=MIN_CUT_SPACING)
        # Verify no two consecutive beats are closer than 0.7s
        for i in range(len(thinned) - 1):
            gap = thinned[i + 1]["time"] - thinned[i]["time"]
            self.assertGreaterEqual(gap, MIN_CUT_SPACING - 1e-9,
                msg=f"Gap {gap:.3f}s < {MIN_CUT_SPACING}s")

    def test_retains_well_spaced_beats(self):
        """Beats already spaced >= MIN_CUT_SPACING should be kept unchanged."""
        spaced = [
            {"time": 0.0, "energy": 0.5}, {"time": 0.8, "energy": 0.5},
            {"time": 1.6, "energy": 0.5}, {"time": 2.4, "energy": 0.5},
            {"time": 3.2, "energy": 0.5}
        ]
        thinned = self.bridge._thin_beats(spaced)
        self.assertEqual(thinned, spaced)

    def test_empty_input(self):
        self.assertEqual(self.bridge._thin_beats([]), [])

    def test_single_beat(self):
        self.assertEqual(self.bridge._thin_beats([{"time": 1.5, "energy": 0.5}]), [{"time": 1.5, "energy": 0.5}])


class TestBeatClassification(unittest.TestCase):
    def setUp(self):
        self.bridge = CreativeEditorBridge()

    def test_classify_by_energy_thresholds(self):
        """Beats should be classified strictly by energy: >0.85 = drop, >0.60 = strong, else weak"""
        beats = [
            {"time": 0.0, "energy": 0.20},  # weak
            {"time": 1.0, "energy": 0.65},  # strong
            {"time": 2.0, "energy": 0.90},  # drop
            {"time": 3.0, "energy": 0.50},  # weak
        ]
        classified = self.bridge._classify_beats(beats)
        self.assertEqual(classified[0]["strength"], "weak")
        self.assertEqual(classified[1]["strength"], "strong")
        self.assertEqual(classified[2]["strength"], "drop")
        self.assertEqual(classified[3]["strength"], "weak")

    def test_missing_energy_fallback(self):
        """If energy key is missing, defaults to weak."""
        beats = [{"time": 1.0}]  # no 'energy' key
        classified = self.bridge._classify_beats(beats)
        self.assertEqual(classified[0]["strength"], "weak")


class TestStrengthToTransition(unittest.TestCase):
    def test_drop_gives_flash(self):
        self.assertEqual(CreativeEditorBridge._strength_to_transition("drop"), "flash")

    def test_strong_gives_fade(self):
        self.assertEqual(CreativeEditorBridge._strength_to_transition("strong"), "fade")

    def test_weak_gives_cut(self):
        self.assertEqual(CreativeEditorBridge._strength_to_transition("weak"), "cut")

    def test_unknown_falls_back_to_cut(self):
        self.assertEqual(CreativeEditorBridge._strength_to_transition("medium"), "cut")





class TestVOPacing(unittest.TestCase):
    def setUp(self):
        self.bridge = CreativeEditorBridge()

    def test_uses_words_div_2_7(self):
        """VO pacing should use words / 2.7 (result must exceed the 8s clamp)."""
        # 30 words / 2.7 ≈ 11.1s — safely above the 8s min so clamping won't hide the formula
        script_30_words = (
            "trending fashion look bold outfit sizzling appearance viral sensation "
            "style stunning luxury feel modern aesthetic premium quality design "
            "wardrobe collection runway season inspiration creative look now trending"
        )
        profile = {"editorial_script": script_30_words}
        word_count = len(script_30_words.split())
        expected = round(word_count / VO_WORDS_PER_SEC, 2)
        result = self.bridge._compute_vo_target_duration(profile)
        self.assertAlmostEqual(result, expected, places=1)

    def test_clamp_min_8s(self):
        """Very short scripts (but valid) should produce >= 8s target."""
        # 3 words / 2.7 = 1.11s → clamped to 8.0s
        profile = {"editorial_script": "Look at this"}
        result = self.bridge._compute_vo_target_duration(profile)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result, 8.0)

    def test_clamp_max_60s(self):
        """Very long scripts should be clamped to 60s."""
        profile = {"editorial_script": " ".join(["word"] * 300)}  # 300/2.7 ≈ 111s
        result = self.bridge._compute_vo_target_duration(profile)
        self.assertLessEqual(result, 60.0)

    def test_no_script_returns_none(self):
        self.assertIsNone(self.bridge._compute_vo_target_duration({}))
        self.assertIsNone(self.bridge._compute_vo_target_duration({"editorial_script": ""}))


class TestEnergyToColorMode(unittest.TestCase):
    def test_strong_is_vibrant(self):
        self.assertEqual(CreativeEditorBridge._energy_to_color_mode("strong"), "vibrant")

    def test_medium_is_fashion(self):
        self.assertEqual(CreativeEditorBridge._energy_to_color_mode("medium"), "fashion")

    def test_weak_is_cinematic(self):
        self.assertEqual(CreativeEditorBridge._energy_to_color_mode("weak"), "cinematic")

    def test_unknown_falls_back(self):
        self.assertEqual(CreativeEditorBridge._energy_to_color_mode("unknown"), "fashion")


class TestSegmentConversion(unittest.TestCase):
    def setUp(self):
        self.bridge = CreativeEditorBridge()

    def test_converts_beat_timeline_to_standard_segments(self):
        beat_timeline = [
            {"scene_start": 0.0, "scene_end": 1.5, "dur": 1.5,
             "scene_ref": {"importance": 0.8}, "_beat_strength": "strong"},
            {"scene_start": 5.0, "scene_end": 7.0, "dur": 2.0,
             "scene_ref": {"importance": 0.5}, "_beat_strength": "weak"},
        ]
        segments = self.bridge._convert_to_segments(beat_timeline)
        self.assertEqual(len(segments), 2)
        
        self.assertEqual(segments[0]["start"], 0.0)
        self.assertEqual(segments[0]["end"], 1.5)
        self.assertEqual(segments[0]["color_mode"], "vibrant")  # strong beat
        self.assertEqual(segments[0]["style"], "fade")
        self.assertEqual(segments[0]["transition"], "fade")

        self.assertEqual(segments[1]["color_mode"], "cinematic")   # weak beat
        self.assertEqual(segments[1]["transition"], "cut")
        
        self.assertIn("clip_id", segments[0])
        self.assertEqual(segments[0]["reason"], "bgm_beat_driven")

    def test_skips_degenerate_segments(self):
        """Segments shorter than 0.3s should be skipped."""
        beat_timeline = [
            {"scene_start": 0.0, "scene_end": 0.1, "dur": 0.1,
             "transition_after": None, "scene_ref": {"importance": 0.5},
             "_beat_strength": "weak"},
        ]
        segments = self.bridge._convert_to_segments(beat_timeline)
        self.assertEqual(len(segments), 0)


class TestRunNoBGM(unittest.TestCase):
    """Bridge run() should gracefully complete when no music_path provided."""

    def test_run_without_music_returns_vo_hint(self):
        bridge = CreativeEditorBridge()
        profile = {"editorial_script": "Short script with some words here today"}
        result = bridge.run(profile, music_path=None)
        self.assertIn("vo_pacing_hints", result)
        self.assertIsNotNone(result["vo_pacing_hints"]["target_duration"])
        # No beat segments should be generated without music
        self.assertNotIn("beat_timeline_segments", result)

    def test_run_with_nonexistent_music_skips_beats(self):
        bridge = CreativeEditorBridge()
        profile = {}
        result = bridge.run(profile, music_path="/nonexistent/track.mp3")
        self.assertNotIn("beat_timeline_segments", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
