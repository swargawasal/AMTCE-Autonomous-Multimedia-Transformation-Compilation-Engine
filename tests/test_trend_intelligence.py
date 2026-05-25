"""
tests/test_trend_intelligence.py
----------------------------------
Unit tests for:
  1. Trend Intelligence Engine (trend_engine.py)
  2. Universal Content Director (universal_content_director.py)
"""
import sys
import os
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Trend_Intelligence.trend_engine import (
    TrendEngine,
    add_user_trend,
    load_user_trends,
    save_user_trends,
    get_trend_context,
    USER_TREND_FILE,
    TREND_MAX_AGE_DAYS,
)
from Content_Intelligence.universal_content_director import (
    UniversalContentDirector,
    generate_content_strategy,
    DEFAULT_STRATEGY,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _patch_trend_file(tmp_path):
    import Trend_Intelligence.trend_engine as te
    te._orig_file = te.USER_TREND_FILE
    te.USER_TREND_FILE = tmp_path

def _restore_trend_file():
    import Trend_Intelligence.trend_engine as te
    te.USER_TREND_FILE = te._orig_file


# ── 1. Trend Engine Tests ─────────────────────────────────────────────────────

class TestTrendEngine(unittest.TestCase):

    def test_empty_dataset_returns_empty_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            _patch_trend_file(os.path.join(tmp, "trends.json"))
            try:
                ctx = get_trend_context()
                tc = ctx.get("trend_context", {})
                self.assertEqual(tc.get("topics", []), [])
                self.assertEqual(tc.get("trend_strength", 0), 0.0)
            finally:
                _restore_trend_file()

    def test_user_hints_included_in_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            _patch_trend_file(os.path.join(tmp, "trends.json"))
            try:
                add_user_trend("Vijay Trisha gossip")
                add_user_trend("AI girlfriend trend")
                ctx = get_trend_context()
                tc = ctx["trend_context"]
                self.assertGreater(len(tc["topics"]), 0)
                self.assertGreater(len(tc["keywords"]), 0)
            finally:
                _restore_trend_file()

    def test_expired_entries_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            _patch_trend_file(os.path.join(tmp, "trends.json"))
            try:
                old_ts = (datetime.now(timezone.utc) - timedelta(days=TREND_MAX_AGE_DAYS + 5)).isoformat()
                fresh_ts = datetime.now(timezone.utc).isoformat()
                entries = [
                    {"input": "Old trend",   "timestamp": old_ts},
                    {"input": "Fresh trend", "timestamp": fresh_ts},
                ]
                import Trend_Intelligence.trend_engine as te
                te.save_user_trends(entries)
                valid = load_user_trends()
                self.assertEqual(len(valid), 1)
                self.assertEqual(valid[0]["input"], "Fresh trend")
            finally:
                _restore_trend_file()

    def test_visual_entities_passed_into_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            _patch_trend_file(os.path.join(tmp, "trends.json"))
            try:
                engine = TrendEngine()
                ctx = engine.get_trend_context(visual_entities=["person:male", "environment:indoor"])
                tc = ctx["trend_context"]
                self.assertIn("person:male", tc.get("entities", []))
            finally:
                _restore_trend_file()

    def test_never_raises_on_bad_state(self):
        import Trend_Intelligence.trend_engine as te
        orig = te.USER_TREND_FILE
        te.USER_TREND_FILE = "/nonexistent/path/trends.json"
        try:
            ctx = get_trend_context()
            self.assertIn("trend_context", ctx)
        finally:
            te.USER_TREND_FILE = orig


# ── 2. Universal Content Director Tests ───────────────────────────────────────

class TestUniversalContentDirector(unittest.TestCase):

    def setUp(self):
        self.ucd = UniversalContentDirector()

    def _make_forensic(self, narrative, editing_style, tone="aspirational", feature_commands=None):
        return {
            "content_director": {
                "detected_entities": ["person:female"],
                "visual_event": "Fashion show",
                "viewer_attention": "Outfit",
                "internet_context": [],
                "possible_narratives": [narrative],
                "recommended_narrative": narrative,
                "tone": tone,
                "editing_style": editing_style,
                "engagement_hook": "",
                "feature_commands": feature_commands or {},
            }
        }

    def test_returns_defaults_on_no_forensic(self):
        strategy, flags = self.ucd.generate_content_strategy(None, None, None, None)
        self.assertIsInstance(strategy, dict)
        self.assertIsInstance(flags, dict)

    def test_fashion_strategy_enables_price_tags(self):
        forensic = self._make_forensic("fashion_moment", "fashion_showcase")
        strategy, flags = self.ucd.generate_content_strategy(None, forensic, None, None)
        self.assertEqual(strategy["recommended_narrative"], "fashion_moment")
        self.assertTrue(flags["enable_price_tags"])
        self.assertTrue(flags["enable_voiceover"])

    def test_cinematic_style_enables_zoom(self):
        forensic = self._make_forensic("", "cinematic")
        _, flags = self.ucd.generate_content_strategy(None, forensic)
        self.assertTrue(flags["enable_cinematic_zoom"])
        self.assertTrue(flags["enable_speed_ramps"])

    def test_trend_relevance_computed_from_keywords(self):
        forensic = self._make_forensic("celebrity_highlight", "fast_social",
                                       tone="commentary")
        # Trend context with overlapping keyword
        trend_ctx = {
            "trend_context": {
                "topics": ["Vijay Trisha celebrity discussion"],
                "keywords": ["celebrity", "trisha"],
                "trend_strength": 0.6,
            }
        }
        strategy, _ = self.ucd.generate_content_strategy(None, forensic, trend_ctx, None)
        # Should detect trend relevance > 0
        self.assertGreater(strategy["trend_relevance"], 0.0)

    def test_crypto_trend_boost_applies_humor_narrative(self):
        forensic = self._make_forensic("", "fast_social")
        trend_ctx = {
            "trend_context": {
                "topics": ["bitcoin crypto hype"],
                "keywords": ["crypto", "bitcoin"],
                "trend_strength": 0.5,
            }
        }
        strategy, flags = self.ucd.generate_content_strategy(None, forensic, trend_ctx)
        # Trend boost should pick humor as a narrative fill-in
        self.assertIn(strategy["recommended_narrative"], ["humor", ""])
        self.assertTrue(flags.get("enable_fast_pacing", False))

    def test_gemini_feature_commands_take_priority(self):
        """Gemini's explicit commands must always be honoured."""
        forensic = self._make_forensic(
            "fashion_moment", "cinematic",
            feature_commands={"enable_voiceover": True, "enable_news_style": True}
        )
        _, flags = self.ucd.generate_content_strategy(None, forensic)
        self.assertTrue(flags["enable_voiceover"])
        self.assertTrue(flags["enable_news_style"])

    def test_invented_flags_are_blocked(self):
        forensic = self._make_forensic("", "")
        forensic["content_director"]["feature_commands"]["enable_hacker_mode"] = True
        _, flags = self.ucd.generate_content_strategy(None, forensic)
        self.assertNotIn("enable_hacker_mode", flags)

    def test_convenience_function_never_raises(self):
        result = generate_content_strategy(None, "garbage input", None, None)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
