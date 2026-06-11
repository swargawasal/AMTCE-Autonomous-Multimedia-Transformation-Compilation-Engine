import os
import json
import pytest
from unittest.mock import patch
from memory.brain_io import load_solved_problems, save_solved_problems, append_to_safety_log

def test_load_solved_problems(tmp_path):
    mock_data = {"version": "1.0", "entries": []}
    mock_file = tmp_path / "solved_problems.json"
    mock_file.write_text(json.dumps(mock_data))
    
    with patch('memory.brain_io.os.path.join', return_value=str(mock_file)):
        with patch('memory.brain_io.os.path.exists', return_value=True):
            data = load_solved_problems()
            assert data["version"] == "1.0"

def test_append_to_safety_log(tmp_path):
    mock_file = tmp_path / "safety_log.json"
    
    with patch('memory.brain_io.os.path.join', return_value=str(mock_file)):
        append_to_safety_log({"issue": "test_flag"})
        
        assert mock_file.exists()
        data = json.loads(mock_file.read_text())
        assert "flags" in data
        assert data["flags"][0]["issue"] == "test_flag"
