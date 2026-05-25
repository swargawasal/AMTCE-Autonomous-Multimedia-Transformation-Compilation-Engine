import unittest
from Intelligence_Modules.gemini_governor import GeminiGovernor

class TestTaskSimplification(unittest.TestCase):
    def setUp(self):
        self.governor = GeminiGovernor()

    def test_aggressive_simplification(self):
        """Verify that the elite simplification layer truncates and focuses prompts."""
        long_prompt = "Act as a director and provide a detailed editing plan. " * 50
        simplified = self.governor._simplify_prompt(long_prompt)
        
        # Should be significantly shorter
        self.assertLess(len(simplified), len(long_prompt), "Simplification failed to reduce prompt size.")
        # Should start with the simplification trigger
        self.assertIn("Simplify decision", simplified)

    def test_context_retention(self):
        """Verify that core instructions are retained even after simplification."""
        prompt = "IMPORTANT: Fix the codec for the fashion video. " + "Extra fluff. " * 20
        simplified = self.governor._simplify_prompt(prompt)
        
        # The key instruction should be preserved (usually the first part)
        self.assertIn("Fix the codec", simplified, "Simplification lost critical context.")

if __name__ == "__main__":
    unittest.main()
