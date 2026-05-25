"""Verification script for Phase 5: Resilient Efficiency Architecture (3-4 Calls Target)."""

import unittest
from unittest.mock import MagicMock, patch
import json

# Import the refactored modules
from analyzer.hybrid_analyzer import HybridAnalyzer
from Intelligence_Modules.unified_intelligence import UnifiedIntelligence
from Text_Modules.gemini_captions import GeminiCaptionGenerator
from decision.decision_engine import generate_with_rag
from Intelligence_Modules.gemini_governor import gemini_router

class TestResilientArchitecture(unittest.TestCase):

    def setUp(self):
        self.router = gemini_router
        self.router.stats = {
            "api_calls": 0,
            "blocked_calls": 0,
            "cache_hits": 0,
            "multi_task_calls": 0,
            "failures": 0,
            "payload_sizes": [],
            "logical_requests": 0,
            "calls_per_module": {}
        }
        self.router.cache = {}

    @patch("gemini_governor.gemini_router.embed")
    @patch("gemini_governor.gemini_router.generate")
    def test_analyzer_confidence_gating(self, mock_gen, mock_embed):
        mock_embed.return_value = [0.1] * 768
        analyzer = HybridAnalyzer()
        
        # Scenario 1: High Confidence (Should SKIP Gemini)
        high_conf_signals = {
            "motion_intensity": 0.9,
            "pace": "fast",
            "cut_density": "high",
            "energy": "high"
        }
        profile = analyzer.analyze(high_conf_signals)
        self.assertFalse(profile["gemini_used"])
        self.assertEqual(profile["category"], "fitness")
        self.assertEqual(mock_gen.call_count, 0)

        # Scenario 2: Low Confidence (Should CALL Gemini)
        low_conf_signals = {
            "motion_intensity": 0.5,
            "pace": "steady",
            "cut_density": "medium",
            "energy": "medium"
        }
        mock_gen.return_value = json.dumps({"category": "test", "style": "test"})
        profile = analyzer.analyze(low_conf_signals)
        self.assertTrue(profile["gemini_used"])
        self.assertEqual(mock_gen.call_count, 1)

    @patch("gemini_governor.gemini_router.embed")
    @patch("gemini_governor.gemini_router.generate")
    def test_master_brain_failsafe(self, mock_gen, mock_embed):
        mock_embed.return_value = [0.1] * 768
        intel = UnifiedIntelligence()
        
        # Scenario: Master call fails (Should use fallbacks)
        mock_gen.return_value = None
        
        # We simulate the normalization logic directly for speed
        raw_data = {} # Empty response
        normalized = intel.normalize_master_schema(raw_data)
        
        self.assertTrue(normalized["fallback_used"])
        self.assertEqual(normalized["intent"], "generic_engagement")
        self.assertIn("Street rhythm motion", normalized["content_director"]["caption_candidates"])

    def test_rag_zero_call_enforcement(self):
        # We don't need to patch here, just check if it logs gemini_used=False
        profile = {"energy": "high", "pace": "fast"}
        patterns = [{"metadata": {"strategy": "test", "hook": "test"}}]
        
        # Capturing stdout to verify logs if needed, but the return value is the key
        plan_json = generate_with_rag(profile, patterns)
        plan = json.loads(plan_json)
        
        self.assertIn("zero_call_enforced", plan["strategy_tags"])

    @patch("gemini_governor.gemini_router.embed")
    @patch("gemini_governor.gemini_router.generate")
    def test_caption_master_first_strategy(self, mock_gen, mock_embed):
        mock_embed.return_value = [0.1] * 768
        generator = GeminiCaptionGenerator()
        
        # Scenario 1: Master has candidates (Should SKIP Gemini)
        profile_with_master = {
            "content_director": {
                "caption_candidates": ["Master Caption 1", "Master Caption 2", "Master Caption 3"]
            }
        }
        from Text_Modules.gemini_captions import generate_caption
        caption = generate_caption(profile_with_master)
        
        self.assertIn(caption, profile_with_master["content_director"]["caption_candidates"])
        self.assertEqual(mock_gen.call_count, 0)

        # Scenario 2: No Master candidates (Should CALL Gemini ONCE for 3)
        mock_gen.return_value = json.dumps(["New 1", "New 2", "New 3"])
        caption = generate_caption(None) # No profile data
        self.assertEqual(mock_gen.call_count, 1)

    def test_governor_blocking_rules(self):
        # Test confidence-based blocking
        self.router.generate("test", "test", existing_confidence=0.8)
        self.assertEqual(self.router.stats["blocked_calls"], 1)
        self.assertEqual(self.router.stats["api_calls"], 0)

if __name__ == "__main__":
    unittest.main()
