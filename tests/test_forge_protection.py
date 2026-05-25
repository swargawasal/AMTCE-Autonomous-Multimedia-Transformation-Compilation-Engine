import pytest
import os
import time
from Intelligence_Modules.vanguard_forge import vanguard_forge

def test_protected_files_rejection():
    """Verify that core brain files are strictly off-limits."""
    target = "Intelligence_Modules/vanguard_director.py"
    allowed, msg = vanguard_forge.can_forge(target)
    assert not allowed
    assert "PROTECTED" in msg

def test_diff_size_limit():
    """Verify that massive rewrites are rejected."""
    original = ["line1\n", "line2\n"]
    # Create 55 lines of change
    forge = [f"new_line_{i}\n" for i in range(60)]
    
    risk, warnings = vanguard_forge.analyze_diff(original, forge)
    assert risk == "HIGH_RISK"
    assert "large" in warnings[0]

def test_risky_diff_import_deletion():
    """Verify that accidental import deletions are flagged as CRITICAL."""
    original = ["import os\n", "def test():\n", "  pass\n"]
    # Missing import
    forge = ["def test():\n", "  pass\n"]
    
    risk, warnings = vanguard_forge.analyze_diff(original, forge)
    assert risk == "CRITICAL"
    assert "Import deletion" in warnings[0]

def test_semantic_intent_validation():
    """Verify that intent mismatch is caught."""
    task = "Add high-frequency latency tracking"
    code = "def process():\n  print('Hello World')\n"
    
    # Task mentioned latency, but code has none
    assert not vanguard_forge.semantic_validator(task, code)
    
    code_with_latency = "def process():\n  latency = 0.5\n  print(f'Done in {latency}ms')\n"
    assert vanguard_forge.semantic_validator(task, code_with_latency)
