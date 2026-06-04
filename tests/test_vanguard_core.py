import pytest
import os
import json
from unittest.mock import MagicMock, patch
from Intelligence_Modules.gemini_governor import GeminiGovernor
from claw_vanguard.vanguard_director import VanguardDirector
from claw_vanguard.tool_system import ErrorClassifier

def test_error_classification():
    assert ErrorClassifier.classify("unknown encoder 'libx264'") == "codec"
    assert ErrorClassifier.classify("Invalid duration found") == "timing"
    assert ErrorClassifier.classify("No such file or directory: 'input.mp4'") == "file"
    assert ErrorClassifier.classify("something weird happened") == "unknown"

@patch("requests.post")
def test_ollama_fallback(mock_post):
    # Mock Gemini Failure
    gov = GeminiGovernor()
    
    # Mock Ollama Success
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"response": "Local AI Response"}
    
    # Trigger fallback (by making gemini call fail and bypassing orchestra)
    from Intelligence_Modules.router_orchestra import orchestra
    with patch.object(orchestra, 'route', return_value=None):
        with patch.object(gov, 'get_available_model', return_value=None):
            result = gov.generate("reasoning", "test prompt")
            assert result == "Local AI Response"
            assert mock_post.called

@patch("Intelligence_Modules.gemini_governor.GeminiGovernor.generate")
@patch("claw_vanguard.tool_system.vanguard_tools.execute")
def test_director_turn_limit(mock_execute, mock_generate):
    director = VanguardDirector()
    
    # Mock Successful Steps
    mock_generate.side_effect = [
        "Plan",        # Turn 1
        '{"ok": true, "reason": "Looks good"}' # Turn 3
    ]
    mock_execute.return_value = MagicMock(success=True)
    
    result = director.execute_mission("Fashion", "Test Request", input_paths=["clip.mp4"])
    
    assert result.success == True
    # Ensure mission_dashboard exists
    assert os.path.exists("logs/mission_dashboard.json")

def test_vanguard_md_structure():
    assert os.path.exists("claw_vanguard/VANGUARD.md")
    with open("claw_vanguard/VANGUARD.md", "r", encoding="utf-8") as f:
        content = f.read()
        assert "Winning Styles" in content
        assert "Failed Patterns" in content
        assert "Rules" in content
