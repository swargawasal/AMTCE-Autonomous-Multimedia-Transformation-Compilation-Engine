import unittest
import os
from Content_Intelligence import source_detector, transformation_engine
from Intelligence_Modules.monetization_brain import MonetizationStrategist

class TestReusedContentPipeline(unittest.TestCase):
    def test_source_detection_link(self):
        # Simulation of a URL-based input
        source_info = source_detector.detect_source("https://www.instagram.com/p/reel/", source_type_hint="link")
        self.assertTrue(source_info["content_source"]["reused"])
        self.assertEqual(source_info["content_source"]["source_type"], "link")

    def test_source_detection_upload(self):
        # Simulation of a direct upload
        source_info = source_detector.detect_source("uploads/video.mp4", source_type_hint="raw_upload")
        self.assertFalse(source_info["content_source"]["reused"])
        self.assertEqual(source_info["content_source"]["source_type"], "raw_upload")

    def test_transformation_score_enforcement(self):
        # Reused content with only 1 layer active (should be bumped to >= 2)
        reused = True
        current_features = {"scene_restructure": True}
        strategy = transformation_engine.get_transformation_strategy(reused, current_features)
        
        self.assertEqual(strategy["transformation_level"], "high")
        self.assertGreaterEqual(strategy["transformation_score"], 2)
        self.assertTrue(strategy["enforced_features"]["narration"]) # Should have been enabled

    def test_commentary_prompt_structure(self):
        # Verify that the brain generates a script following the new structure
        # (Checking the current prompt to ensure factual constraints are maintained)
        from Intelligence_Modules.monetization_brain import FASHION_REVIEWER_PROMPT
        self.assertIn("STRICT JSON ONLY", FASHION_REVIEWER_PROMPT)
        self.assertIn("item_name", FASHION_REVIEWER_PROMPT)
        self.assertIn("garment_type", FASHION_REVIEWER_PROMPT)
        self.assertIn("material", FASHION_REVIEWER_PROMPT)
        self.assertIn("color", FASHION_REVIEWER_PROMPT)

if __name__ == '__main__':
    unittest.main()
