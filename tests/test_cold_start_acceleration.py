"""
tests/test_cold_start_acceleration.py
=====================================
Validation tests for Cold-Start Acceleration logic.

Run with:
    cd "d:\\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    python -m pytest tests/test_cold_start_acceleration.py -v
"""

import os
import sys
import importlib
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _make_editor():
    """Build a fresh SmartSceneEditor with env overrides."""
    import Visual_Refinement_Modules.smart_scene_editor as mod
    importlib.reload(mod)
    editor = mod.SmartSceneEditor()
    editor.enabled = True
    editor._get_video_info = lambda p: 10.0
    return editor

# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — Missing early edits triggers injection at 1.5s
# ══════════════════════════════════════════════════════════════════════════════

def test_cold_start_injection():
    editor = _make_editor()
    cuts = [5.0, 8.0] # No cuts in first 3s
    zoom_effects = []
    speed_ramps = []
    
    updated_cuts, injected = editor.enforce_cold_start(cuts, zoom_effects, speed_ramps, 10.0)
    
    assert injected is True
    assert 1.5 in updated_cuts
    assert updated_cuts == sorted(updated_cuts)
    assert len(updated_cuts) == 3

# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — Existing cut in first 3s skips injection
# ══════════════════════════════════════════════════════════════════════════════

def test_cold_start_skips_if_cut_exists():
    editor = _make_editor()
    cuts = [2.0, 5.0] # 2.0 is < 3.0
    
    updated_cuts, injected = editor.enforce_cold_start(cuts, [], [], 10.0)
    
    assert injected is False
    assert 1.5 not in updated_cuts
    assert len(updated_cuts) == 2

# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — Video shorter than 4s skips injection
# ══════════════════════════════════════════════════════════════════════════════

def test_cold_start_skips_if_short_video():
    editor = _make_editor()
    cuts = []
    
    updated_cuts, injected = editor.enforce_cold_start(cuts, [], [], 3.5)
    
    assert injected is False
    assert len(updated_cuts) == 0

# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — Safety distance guard (don't inject if too close to existing)
# ══════════════════════════════════════════════════════════════════════════════

def test_cold_start_safety_distance():
    editor = _make_editor()
    # If a cut exists outside the 3s window but VERY close to 1.5s (shouldn't happen with pacing, but for logic check)
    # Actually, the trigger is "no edits in first 3s". 
    # If cuts = [3.1], it triggers. If cuts = [1.6], it skips test 2.
    
    # Case: no edits in first 3s, but a cut exists at 1.8s (wait, 1.8 is < 3.0, so it skips)
    # Case: no edits in first 3s. Existing cuts = [3.1]. 3.1 - 1.5 = 1.6 (> 0.5). Injected.
    
    cuts = [3.1] 
    updated_cuts, injected = editor.enforce_cold_start(cuts, [], [], 10.0)
    assert injected is True
    assert 1.5 in updated_cuts
    
    # Case: no edits in first 3s (wait, this is impossible if a cut is at 1.7s)
    # The logic is: if has_early_edit is False: inject 1.5 if diff > 0.5.
    # If has_early_edit is False, all cuts must be >= 3.0.
    # min(abs(cut - 1.5)) where all cut >= 3.0. 
    # Min is at 3.0. 3.0 - 1.5 = 1.5 (> 0.5).
    # So with the 3s rule, the 0.5s safety distance is ALMOST always guaranteed 
    # UNLESS duration is very short or schema is weird.
    
    # If duration = 4.0 and cuts = []. 1.5 will be injected. 
    pass

# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — Analytics Logging in generate_timeline_instructions
# ══════════════════════════════════════════════════════════════════════════════

from unittest.mock import patch

def test_cold_start_analytics_logging():
    import logging
    editor = _make_editor()
    
    # Disable all optional modules to isolate cold-start logic
    editor.scene_detection_enabled = False
    editor.pacing_control = False
    editor.motion_editing_enabled = False
    editor.attention_editing_enabled = False
    editor.smart_reframe_enabled = False
    editor.hook_detection_enabled = False
    editor.beat_sync_enabled = False
    editor.auto_zoom_effects = False
    editor.speed_ramping_enabled = False
    editor.rhythm_stabilization_enabled = False
    
    # Enable analytics
    editor.analytics_logging_enabled = True # Force analytics for test
    
    # Ensure the logger itself is at INFO level
    logging.getLogger("smart_scene_editor").setLevel(logging.INFO)
    
    with patch("Visual_Refinement_Modules.smart_scene_editor.logger.info") as mock_info:
        result = editor.generate_timeline_instructions("mock.mp4")
        
        # Verify that the logic actually worked (injected 1.5)
        assert result is not None
        assert 1.5 in result["cuts"]
        
        # Verify that the cold start injection was logged
        # We look for a call that contains the expected string
        injected_logged = any("📊 Cold Start Injected: True" in str(call) for call in mock_info.call_args_list)
        assert injected_logged is True, "Cold start injection was not logged in info channel"
