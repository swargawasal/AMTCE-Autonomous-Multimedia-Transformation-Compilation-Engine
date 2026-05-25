import pytest
from unittest.mock import patch, MagicMock
from Intelligence_Modules.vanguard_forge import VanguardForge

def test_auditor_disagreement_blocking():
    """Verify that a 'REJECT' from the Auditor blocks promotion even if Pytest passes."""
    forge = VanguardForge()
    target = "Audio_Modules/audio_deduplicator.py"
    
    # Mock Turn 1 (Optimization)
    mock_opt = "def bad_logic():\n  return 1/0 # Passes lint but bad logic\n"
    
    # Mock Signal 1: Pytest PASSES (Computational)
    # We mock verify_with_swap to return success
    
    # Mock Signal 2: Auditor REJECTS (Intellectual)
    # 0.65 score is ABOVE 0.6 threshold, but 'approved': False triggers [LOGIC_RISK]
    mock_audit = {
        "approved": False,
        "score": 0.65,
        "critique": "Code passes tests but introduces a logic error.",
        "risks": [{"type": "division_by_zero", "severity": "critical"}],
        "fix_suggestions": ["Actually don't divide by zero."]
    }
    
    with patch("Intelligence_Modules.gemini_governor.GeminiGovernor.generate", return_value=mock_opt):
        with patch.object(VanguardForge, 'verify_with_swap', return_value=(True, "Tests passed")):
            with patch.object(VanguardForge, 'run_ai_auditor', return_value=mock_audit):
                with patch.object(VanguardForge, 'can_forge', return_value=(True, "")):
                    with patch.object(VanguardForge, 'semantic_validator', return_value=True):
                        # Run pipeline
                        result = forge.run_forge_pipeline(target, "Fix logic")
                        
                        # VERIFY: Result must NOT be success
                        assert not result.success
                        assert "[LOGIC_RISK]" in result.message
                        assert result.disagreement == True
                        assert result.ai_critique["approved"] == False
