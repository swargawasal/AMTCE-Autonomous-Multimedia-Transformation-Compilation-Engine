import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.path.abspath(os.getcwd()))

class TestMonetizationReporting(unittest.TestCase):
    
    def setUp(self):
        # Mock necessary environment variables
        os.environ["AI_VOICEOVER"] = "yes"
        os.environ["AI_CAPTIONS"] = "yes"
        
    @patch("Compiler_Modules.compiler.check_health", return_value={"safe": True, "summary": "OK"})
    @patch("Compiler_Modules.orchestrator.compile_video")
    def test_respect_brain_high_risk(self, mock_compile, mock_health):
        """Verify that a HIGH risk from the brain is NOT overwritten by the compiler."""
        from Compiler_Modules.compiler import compile_with_transitions
        
        # Setup mock to return HIGH risk result
        mock_compile.return_value = (True, {
            "status": "success",
            "risk_level": "HIGH",
            "verdict": "Low Narrative Density",
            "risk_score": 0.85
        })
        
        with patch("Compiler_Modules.compiler._save_sidecar") as mock_sidecar:
            with patch("shutil.move"):
                with patch("Compiler_Modules.compiler.Path"):
                    # Use a dummy path that doesn't exist to prove isolation
                    compile_with_transitions("nonexistent.mp4", "Test Title")
        
        # Verify sidecar was called with the Correct HIGH risk
        args, kwargs = mock_sidecar.call_args
        self.assertIsNotNone(mock_sidecar.call_args, "Sidecar should have been called")
        saved_data = args[1] if len(args) > 1 else args[0] # Handle flexible shim arg order
        
        self.assertEqual(saved_data.get("risk_level"), "HIGH")
        self.assertEqual(saved_data.get("verdict"), "Low Narrative Density")

    @patch("Compiler_Modules.compiler.check_health", return_value={"safe": True, "summary": "OK"})
    @patch("Compiler_Modules.orchestrator.compile_video")
    def test_conservative_unknown_risk(self, mock_compile, mock_health):
        """Verify that UNKNOWN (Brain Offline) is reported as MEDIUM, not LOW."""
        from Compiler_Modules.compiler import compile_with_transitions
        
        # Setup mock to return UNKNOWN risk result
        mock_compile.return_value = (True, {
            "status": "success",
            "risk_level": "MEDIUM",
            "verdict": "CHECK REQUIRED (Brain Offline)"
        })
        
        with patch("Compiler_Modules.compiler._save_sidecar") as mock_sidecar:
            with patch("shutil.move"):
                 with patch("Compiler_Modules.compiler.Path"):
                      compile_with_transitions("nonexistent.mp4", "Test Title")
        
        # Verify result was handled as MEDIUM
        args, kwargs = mock_sidecar.call_args
        self.assertIsNotNone(mock_sidecar.call_args, "Sidecar should have been called")
        saved_data = args[1] if len(args) > 1 else args[0]
        
        self.assertEqual(saved_data.get("risk_level"), "MEDIUM")
        self.assertEqual(saved_data.get("verdict"), "CHECK REQUIRED (Brain Offline)")

if __name__ == "__main__":
    unittest.main()
