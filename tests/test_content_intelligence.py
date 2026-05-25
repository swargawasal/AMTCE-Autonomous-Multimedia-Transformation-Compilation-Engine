"""
tests/test_content_intelligence.py
-----------------------------------
Unit tests for the Content Intelligence Layer.
Tests all three components:
  1. Content Director (content_intelligence_engine.py)
  2. Engagement Intelligence (engagement_intelligence.py)
  3. Forensic parser content_director extraction
"""
import sys
import os
import json
import unittest
import tempfile

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Content_Intelligence.content_intelligence_engine import (
    ContentIntelligenceEngine,
    interpret_visual_context,
    DEFAULT_CD_BLOCK,
    DEFAULT_CI_FLAGS,
)
from Analytics_Modules.engagement_intelligence import (
    compute_engagement_score,
    record_video_result,
    get_strategy_signals,
    DATASET_PATH,
)


# ── Layer 1: Content Director ─────────────────────────────────────────────────

class TestContentDirectorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ContentIntelligenceEngine()

    def test_returns_defaults_on_none_forensic(self):
        cd, flags = self.engine.interpret_visual_context(None, None)
        self.assertEqual(cd, DEFAULT_CD_BLOCK)
        self.assertEqual(flags, DEFAULT_CI_FLAGS)

    def test_returns_defaults_on_missing_content_director_key(self):
        forensic_no_cd = {"intent": "fashion", "feature_flags": {}}
        cd, flags = self.engine.interpret_visual_context(None, forensic_no_cd)
        self.assertEqual(cd["detected_entities"], [])

    def test_fashion_narrative_enables_price_tags(self):
        forensic = {
            "content_director": {
                "detected_entities": ["person:female"],
                "visual_event": "Fashion walk",
                "viewer_attention": "outfit",
                "internet_context": [],
                "possible_narratives": ["fashion_moment"],
                "recommended_narrative": "fashion_moment",
                "tone": "aspirational",
                "editing_style": "fashion_showcase",
                "engagement_hook": "This outfit is fire",
                "feature_commands": {
                    "enable_price_tags": True,
                    "enable_voiceover": True,
                }
            }
        }
        cd, flags = self.engine.interpret_visual_context(None, forensic)
        self.assertEqual(cd["recommended_narrative"], "fashion_moment")
        self.assertTrue(flags["enable_price_tags"])
        self.assertTrue(flags["enable_voiceover"])

    def test_cinematic_style_enables_zoom(self):
        forensic = {
            "content_director": {
                "editing_style": "cinematic",
                "recommended_narrative": "",
                "feature_commands": {},
            }
        }
        _, flags = self.engine.interpret_visual_context(None, forensic)
        self.assertTrue(flags["enable_cinematic_zoom"])
        self.assertTrue(flags["enable_speed_ramps"])

    def test_humor_fast_pacing(self):
        forensic = {
            "content_director": {
                "editing_style": "fast_social",
                "recommended_narrative": "humor",
                "feature_commands": {"enable_fast_pacing": True},
            }
        }
        _, flags = self.engine.interpret_visual_context(None, forensic)
        self.assertTrue(flags["enable_fast_pacing"])

    def test_unknown_flags_are_filtered(self):
        """Gemini must not invent new flags."""
        forensic = {
            "content_director": {
                "editing_style": "",
                "recommended_narrative": "",
                "feature_commands": {
                    "enable_price_tags": True,
                    "enable_hacked_flag": True,   # invented — must be ignored
                },
            }
        }
        cd, flags = self.engine.interpret_visual_context(None, forensic)
        self.assertNotIn("enable_hacked_flag", flags)
        self.assertTrue(flags["enable_price_tags"])

    def test_convenience_function_never_raises(self):
        # Should not raise even with garbage input
        cd, flags = interpret_visual_context(None, "not a dict")
        self.assertIsInstance(cd, dict)
        self.assertIsInstance(flags, dict)


# ── Layer 2: Engagement Intelligence ──────────────────────────────────────────

class TestEngagementIntelligence(unittest.TestCase):

    def _patch_dataset_path(self, tmp_dir):
        """Redirect DATASET_PATH to a temp file for isolation."""
        import Analytics_Modules.engagement_intelligence as ei_mod
        self._orig_path = ei_mod.DATASET_PATH
        ei_mod.DATASET_PATH = os.path.join(tmp_dir, "test_engagement.json")
        return ei_mod.DATASET_PATH

    def _restore_dataset_path(self):
        import Analytics_Modules.engagement_intelligence as ei_mod
        ei_mod.DATASET_PATH = self._orig_path

    def test_score_formula_max(self):
        perfect = {
            "views": 1000, "completion_rate": 1.0, "likes": 1000,
            "shares": 1000, "watch_time": 30000, "video_duration": 30,
        }
        score = compute_engagement_score(perfect)
        self.assertAlmostEqual(score, 1.0, places=2)

    def test_score_formula_zero(self):
        empty = {"views": 0}
        score = compute_engagement_score(empty)
        self.assertEqual(score, 0.0)

    def test_record_and_retrieve_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._patch_dataset_path(tmp)
            try:
                # Record two fashion videos (high score) and one news (low score)
                for _ in range(3):
                    record_video_result(
                        "vid_fashion",
                        {"narrative": "fashion_moment", "editing_style": "fashion_showcase", "tone": "aspirational", "feature_flags": {}},
                        {"views": 1000, "completion_rate": 0.9, "likes": 200, "shares": 50, "watch_time": 25000, "video_duration": 30},
                    )
                record_video_result(
                    "vid_news",
                    {"narrative": "news_context", "editing_style": "news", "tone": "serious", "feature_flags": {}},
                    {"views": 100, "completion_rate": 0.2, "likes": 5, "shares": 1, "watch_time": 600, "video_duration": 30},
                )
                signals = get_strategy_signals()
                self.assertIn("preferred_style", signals)
                self.assertEqual(signals["preferred_style"], "fashion_showcase")
                self.assertGreater(signals["confidence_boost"], 0.0)
            finally:
                self._restore_dataset_path()

    def test_signals_empty_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._patch_dataset_path(tmp)
            try:
                signals = get_strategy_signals()
                self.assertEqual(signals, {})
            finally:
                self._restore_dataset_path()


if __name__ == "__main__":
    unittest.main()
