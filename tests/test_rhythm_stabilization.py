"""
tests/test_rhythm_stabilization.py
==================================
Validation tests for Rhythm Stabilization and Metrics Logging.

Run with:
    cd "d:\\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    python -m pytest tests/test_rhythm_stabilization.py -v
"""

import os
import sys
import importlib
import pytest
import logging

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_editor():
    """Build a fresh SmartSceneEditor with env overrides (full module reload)."""
    import Visual_Refinement_Modules.smart_scene_editor as mod
    importlib.reload(mod)
    editor = mod.SmartSceneEditor()
    editor.enabled = True
    editor.scene_detection_enabled = False
    editor.pacing_control = False # We'll provide our own cuts
    editor.motion_editing_enabled = True
    editor.attention_editing_enabled = True
    editor._get_video_info = lambda p: 10.0 # 10 second mock duration
    return editor

# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — Dense Cuts are reduced (min 0.7s)
# ══════════════════════════════════════════════════════════════════════════════

def test_dense_cuts_reduced():
    editor = _make_editor()
    # 0.5s is < 0.7s. 1.1s is > 0.7s.
    cuts = [0.5, 0.9, 2.0] 
    # Logic: 
    # [0.5] -> 0.5-0.0 = 0.5 (Too close! Phase 1 logic removes later one)
    # The logic in stabilize_rhythm for Phase 1 is:
    # if c - last_cut >= MIN_SPACING: stabilized.append(c); last_cut = c
    
    # 0.5 - 0.0 = 0.5 (< 0.7) -> Skipped.
    # 0.9 - 0.0 = 0.9 (>= 0.7) -> Stabilized. Append 0.9. last_cut = 0.9.
    # 2.0 - 0.9 = 1.1 (>= 0.7) -> Stabilized. Append 2.0. last_cut = 2.0.
    
    # Phase 2 (Fill gaps):
    # current=0. 0.9-0.0 = 0.9 < 2.5. OK.
    # current=0.9. 2.0-0.9 = 1.1 < 2.5. OK.
    # Duration = 10.0. 10.0-2.0 = 8.0 > 2.5.
    # 8.0/2 = 4.0. Insert at 2.0 + 4.0 = 6.0.
    # current=6.0. 10.0-6.0 = 4.0 > 2.5.
    # 4.0/2 = 2.0. Insert at 6.0 + 2.0 = 8.0.
    # current=8.0. 10.0-8.0 = 2.0 < 2.5. OK.
    
    stabilized = editor.stabilize_rhythm(cuts, 10.0)
    
    assert 0.5 not in stabilized
    assert 0.9 in stabilized
    assert 2.0 in stabilized
    assert all(stabilized[i] - stabilized[i-1] >= 0.7 for i in range(1, len(stabilized)))
    assert len(stabilized) > len(cuts) # Due to sparse fill

# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — Large spacing inserts pacing cut (max 2.5s)
# ══════════════════════════════════════════════════════════════════════════════

def test_sparse_cuts_filled():
    editor = _make_editor()
    cuts = [5.0] # 0 to 5 is 5s (> 2.5s). 5 to 10 is 5s (> 2.5s).
    
    stabilized = editor.stabilize_rhythm(cuts, 10.0)
    
    # Needs to fill gap between 0 and 5. 0+(5-0)/2 = 2.5.
    # Needs to fill gap between 5 and 10. 5+(10-5)/2 = 7.5.
    
    assert 2.5 in stabilized
    assert 5.0 in stabilized
    assert 7.5 in stabilized
    assert len(stabilized) == 3

# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — Metrics Logging Safety
# ══════════════════════════════════════════════════════════════════════════════

def test_metrics_logging_safety(caplog):
    """Ensure logging doesn't crash and output matches format."""
    editor = _make_editor()
    editor.pacing_control = True # Allow generate_timeline_instructions to run
    
    # Mock return values to trigger logging
    editor._detect_motion_events = lambda p, d: [{"time": 1.0, "strength": "small"}]
    editor._detect_attention_events = lambda p, d: [{"time": 2.0, "type": "face_appearance"}]
    editor._detect_hook_moment = lambda p, d, m: {"time": 3.0, "score": 0.95}
    
    with caplog.at_level(logging.INFO):
        instructions = editor.generate_timeline_instructions("mock.mp4")
    
    assert instructions is not None
    assert "hook_moment" in instructions
    
    # Check log content
    log_text = caplog.text
    assert "📊 Edit Density:" in log_text
    assert "📊 Motion Events: 1" in log_text
    assert "📊 Attention Events: 1" in log_text
    assert "📊 Hook Score: 0.95" in log_text

# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — Schema Invariance
# ══════════════════════════════════════════════════════════════════════════════

def test_schema_unchanged():
    editor = _make_editor()
    editor.pacing_control = True
    result = editor.generate_timeline_instructions("mock.mp4")
    
    expected_keys = {
        "cuts", "zoom_effects", "transitions", "motion_events", 
        "attention_events", "smart_reframe", "hook_moment", "speed_ramps",
        "scenes", "moment_driven"
    }
    assert set(result.keys()) == expected_keys
