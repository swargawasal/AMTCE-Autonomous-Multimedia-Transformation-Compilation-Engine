"""
tests/test_hook_and_trend_opportunity.py
-----------------------------------------
Unit tests for Hook Prediction Engine and Trend Opportunity Analyzer.
Tests that OpenCV fallback works and that Angle Innovation Engine triggers
only on high competition density.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Content_Intelligence.hook_engine import get_engine as get_hook_engine, analyse_hook
from Trend_Intelligence.trend_opportunity_engine import get_engine as get_toe_engine, analyse_trend_opportunity


class TestHookEngine(unittest.TestCase):
    def test_missing_video_returns_default(self):
        result = analyse_hook("/path/to/missing.mp4")
        self.assertIn("hook_analysis", result)
        self.assertEqual(result["hook_analysis"]["hook_time"], 0.0)
        self.assertEqual(result["hook_analysis"]["hook_score"], 0.0)

    def test_analyse_never_raises(self):
        engine = get_hook_engine()
        result = engine.analyse(None)
        self.assertIn("hook_analysis", result)


class TestTrendOpportunityEngine(unittest.TestCase):
    def setUp(self):
        self.engine = get_toe_engine()

    def test_empty_context_returns_defaults(self):
        res = self.engine.analyse(None, None)
        self.assertEqual(res["trend_stage"], "unknown")
        self.assertEqual(res["opportunity_score"], 0.0)
        self.assertEqual(res["recommended_angle"], "")

    def test_high_competition_triggers_innovation(self):
        # We need density >= 0.65 to trigger 'high' competition
        trend_context = {
            "topics": ["AI girlfriend", "virtual AI partner", "AI relationship"],
            "keywords": ["ai", "girlfriend", "partner", "virtual", "relationship",
                         "chat", "bot", "lonely", "tech", "future", "app", "simulator",
                         "romance", "dating", "companion"],
            "trend_strength": 0.9
        }
        res = self.engine.analyse(trend_context, {"recommended_narrative": "explainer"})
        
        # High competition expected
        self.assertEqual(res["competition_level"], "high")
        self.assertNotEqual(res["recommended_angle"], "explainer")  # Must switch
        self.assertTrue(res["engagement_hook"]) # Should have a hook string
        self.assertIn(res["recommended_angle"], [
            "humor", "unexpected_twist", "satire", "comparison", "reaction", "story", "explanation"
        ])

    def test_low_competition_retains_angle(self):
        trend_context = {
            "topics": ["new specific topic"],
            "keywords": ["specific"],
            "trend_strength": 0.2
        }
        existing = {"recommended_narrative": "story"}
        res = self.engine.analyse(trend_context, existing)
        
        self.assertIn(res["competition_level"], ["low", "medium"])
        self.assertEqual(res["recommended_angle"], "story")

    def test_feature_flags_merged(self):
        # Triggering a humor or satire angle maps to fast pacing / voiceover
        trend_context = {
            "topics": ["crypto boom"],
            "keywords": ["crypto", "bitcoin", "ethereum", "web3", "nft", "blockchain",
                         "coin", "token", "moon", "hodl", "invest", "money", "finance", "gains", "loss"],
            "trend_strength": 0.9
        }
        res = self.engine.analyse(trend_context, {"recommended_narrative": "educational"})
        
        self.assertEqual(res["competition_level"], "high")
        flags = res["feature_commands"]
        
        # Flags dictionary is returned
        self.assertIn("enable_fast_pacing", flags)
        self.assertTrue(isinstance(flags, dict))


if __name__ == "__main__":
    unittest.main()
