"""
tests/test_env_feature_toggles.py
=================================
Validation tests for .env feature toggles and schema stability.

Run with:
    cd "d:\\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    python -m pytest tests/test_env_feature_toggles.py -v
"""

import os
import sys
import importlib
import pytest
from unittest.mock import patch

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _make_editor():
    """Build a fresh SmartSceneEditor with env overrides."""
    import Visual_Refinement_Modules.smart_scene_editor as mod
    importlib.reload(mod)
    editor = mod.SmartSceneEditor()
    # Force basic enablement
    editor.enabled = True
    editor._get_video_info = lambda p: 10.0
    return editor

# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — Disabling Motion removes motion_events
# ══════════════════════════════════════════════════════════════════════════════

@patch.dict(os.environ, {"ENABLE_MOTION_EDITING": "false"})
def test_disable_motion():
    editor = _make_editor()
    assert editor.motion_editing_enabled is False
    
    # Even if we explicitly call the detector (mocked), 
    # the pipeline should skip it or return empty.
    # We rely on the internal guard in generate_timeline_instructions.
    res = editor.generate_timeline_instructions("mock.mp4")
    
    assert res is not None
    assert res["motion_events"] == []

# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — Disabling Attention removes attention_events
# ══════════════════════════════════════════════════════════════════════════════

@patch.dict(os.environ, {"ENABLE_ATTENTION_EDITING": "false"})
def test_disable_attention():
    editor = _make_editor()
    assert editor.attention_editing_enabled is False
    
    res = editor.generate_timeline_instructions("mock.mp4")
    
    assert res is not None
    assert res["attention_events"] == []

# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — Disabling Hook removes hook_moment
# ══════════════════════════════════════════════════════════════════════════════

@patch.dict(os.environ, {"ENABLE_HOOK_DETECTION": "false"})
def test_disable_hook():
    editor = _make_editor()
    assert editor.hook_detection_enabled is False
    
    res = editor.generate_timeline_instructions("mock.mp4")
    
    assert res is not None
    assert res["hook_moment"] is None

# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — Schema Stability (All keys present even if all disabled)
# ══════════════════════════════════════════════════════════════════════════════

@patch.dict(os.environ, {
    "ENABLE_MOTION_EDITING": "false",
    "ENABLE_ATTENTION_EDITING": "false",
    "ENABLE_SMART_REFRAME": "false",
    "ENABLE_HOOK_DETECTION": "false",
    "ENABLE_RHYTHM_STABILIZATION": "false",
    "ENABLE_ANALYTICS_LOGGING": "false"
})
def test_schema_stability():
    editor = _make_editor()
    res = editor.generate_timeline_instructions("mock.mp4")
    
    expected_keys = {
        "cuts", "zoom_effects", "transitions", "motion_events", 
        "attention_events", "smart_reframe", "hook_moment", "speed_ramps",
        "scenes", "moment_driven"
    }
    assert set(res.keys()) == expected_keys
    assert res["motion_events"] == []
    assert res["attention_events"] == []
    assert res["smart_reframe"] == []
    assert res["hook_moment"] is None

# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — Analytics Logging Toggle
# ══════════════════════════════════════════════════════════════════════════════

def test_analytics_logging_toggle(caplog):
    import logging
    # Enable analytics
    with patch.dict(os.environ, {"ENABLE_ANALYTICS_LOGGING": "true"}):
        editor = _make_editor()
        editor.pacing_control = True
        with caplog.at_level(logging.INFO):
            editor.generate_timeline_instructions("mock.mp4")
        assert "📊 Edit Density:" in caplog.text
        
    caplog.clear()
    
    # Disable analytics
    with patch.dict(os.environ, {"ENABLE_ANALYTICS_LOGGING": "false"}):
        editor = _make_editor()
        editor.pacing_control = True
        with caplog.at_level(logging.INFO):
            editor.generate_timeline_instructions("mock.mp4")
        assert "📊 Edit Density:" not in caplog.text
