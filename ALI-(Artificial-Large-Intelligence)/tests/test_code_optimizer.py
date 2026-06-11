import os
import json
import pytest
from unittest.mock import MagicMock, patch, mock_open
from son_of_anton.code_optimizer import SonOfAntonCodeOptimizer, code_optimizer

def test_protected_files():
    optimizer = SonOfAntonCodeOptimizer()
    res = optimizer.run_optimization_loop(
        target_file="claw_vanguard/vanguard_forge.py",
        optimization_task="Optimize this file"
    )
    assert res["success"] is False
    assert "protected core file" in res["message"]

@patch("son_of_anton.code_optimizer.vanguard_tools.execute")
@patch("son_of_anton.code_optimizer.get_visual_critique")
@patch("son_of_anton.code_optimizer.judge_improvement")
@patch("connectors.gemini.call_gemini")
@patch("claw_vanguard.vanguard_forge.vanguard_forge.promote_to_primary")
@patch("os.path.exists")
def test_successful_optimization_loop(mock_exists, mock_promote, mock_call_gemini, mock_judge, mock_critique, mock_execute):
    optimizer = SonOfAntonCodeOptimizer()
    
    # Setup mocks
    mock_exists.side_effect = lambda path: True
    
    # Mock compile_video success
    mock_comp_res = MagicMock()
    mock_comp_res.success = True
    mock_execute.return_value = mock_comp_res
    
    # Mock visual critiques: baseline critique + 1 iteration critique
    mock_critique.side_effect = [
        {"ok": True, "reason": "Baseline looks fine", "adjustments": "Make it better"}, # Baseline
        {"ok": True, "reason": "Optimized version has crossfades", "adjustments": ""} # Iteration 1
    ]
    
    # Mock judge improvement
    mock_judge.return_value = (True, "Strictly improved")
    
    # Mock code gen answer from Gemini
    mock_call_gemini.return_value = {
        "answer": "```python\ndef new_func():\n    return 'opt'\n```"
    }
    
    # Mock read/write of files to avoid hitting disk
    m_open = mock_open(read_data="def old_func():\n    pass")
    
    # Mock subprocess run to simulate successful pytest
    mock_pytest_res = MagicMock()
    mock_pytest_res.returncode = 0
    mock_pytest_res.stdout = "All tests passed"
    mock_pytest_res.stderr = ""
    
    with patch("builtins.open", m_open), \
         patch("subprocess.run", return_value=mock_pytest_res), \
         patch("shutil.copy"), \
         patch("os.remove"):
         
        res = optimizer.run_optimization_loop(
            target_file="Compiler_Modules/video_pipeline.py",
            optimization_task="Add crossfades",
            test_file="tests/test_vanguard_core.py"
        )
        
    assert res["success"] is True
    assert res["iterations"] == 1
    mock_promote.assert_called_once()

@patch("son_of_anton.code_optimizer.vanguard_tools.execute")
@patch("son_of_anton.code_optimizer.get_visual_critique")
@patch("son_of_anton.code_optimizer.judge_improvement")
@patch("connectors.gemini.call_gemini")
@patch("claw_vanguard.vanguard_forge.vanguard_forge.promote_to_primary")
@patch("os.path.exists")
def test_loop_exhaustion(mock_exists, mock_promote, mock_call_gemini, mock_judge, mock_critique, mock_execute):
    optimizer = SonOfAntonCodeOptimizer()
    
    mock_exists.side_effect = lambda path: True
    mock_comp_res = MagicMock()
    mock_comp_res.success = True
    mock_execute.return_value = mock_comp_res
    
    # Return not ok, or judge says not improved
    mock_critique.return_value = {"ok": False, "reason": "Pacing is off", "adjustments": "Fix pacing"}
    mock_judge.return_value = (False, "Not improved")
    
    mock_call_gemini.return_value = {
        "answer": "```python\ndef opt(): pass\n```"
    }
    
    m_open = mock_open(read_data="def old(): pass")
    mock_pytest_res = MagicMock()
    mock_pytest_res.returncode = 0
    
    with patch("builtins.open", m_open), \
         patch("subprocess.run", return_value=mock_pytest_res), \
         patch("shutil.copy"), \
         patch("os.remove"):
         
        res = optimizer.run_optimization_loop(
            target_file="Compiler_Modules/video_pipeline.py",
            optimization_task="Add crossfades"
        )
        
    assert res["success"] is False
    assert "exhausted" in res["message"]
    mock_promote.assert_not_called()
