import unittest
import os
import sys

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Diagnostics_Modules.pipeline_feature_auditor import FeatureAuditor

class TestFeatureAuditor(unittest.TestCase):
    def test_state_tracking(self):
        auditor = FeatureAuditor()
        auditor.mark_executed("watermark_detection")
        auditor.mark_failed("scene_detection")
        auditor.mark_disabled("voiceover_generation")
        auditor.mark_skipped("trend_engine")
        
        self.assertEqual(auditor.status("watermark_detection"), "EXECUTED")
        self.assertEqual(auditor.status("scene_detection"), "FAILED")
        self.assertEqual(auditor.status("voiceover_generation"), "DISABLED")
        self.assertEqual(auditor.status("trend_engine"), "SKIPPED")
        self.assertEqual(auditor.status("unknown_feature"), "UNKNOWN")

    def test_dependency_validation(self):
        auditor = FeatureAuditor()
        
        # Test valid path
        auditor.mark_executed("caption_generation")
        auditor.mark_executed("voiceover_generation")
        auditor.validate_dependencies() # Should not raise
        
        # Reset and test invalid path 1: Voiceover without captions FAILED
        auditor = FeatureAuditor()
        auditor.mark_failed("caption_generation")
        auditor.mark_executed("voiceover_generation")
        # In this simplistic check, it just logs a warning. We verify it doesn't crash.
        try:
            auditor.validate_dependencies()
            passed = True
        except:
            passed = False
        self.assertTrue(passed)

    def test_report_generation(self):
        auditor = FeatureAuditor()
        auditor.mark_executed("watermark_inpaint")
        report_str = auditor.generate_report()
        self.assertIn("watermark_inpaint -> EXECUTED", report_str)

if __name__ == '__main__':
    unittest.main()
