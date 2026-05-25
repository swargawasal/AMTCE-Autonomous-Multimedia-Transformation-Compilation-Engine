"""
tests/test_hook_detection.py
===========================
Validation tests for Hook Moment Detection.

Run with:
    cd "d:\\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    python -m pytest tests/test_hook_detection.py -v
"""

import os
import sys
import importlib
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_editor():
    """Build a fresh SmartSceneEditor with env overrides (full module reload)."""
    import Visual_Refinement_Modules.smart_scene_editor as mod
    importlib.reload(mod)
    editor = mod.SmartSceneEditor()
    # Force defaults to ensure pipeline doesn't early-return None
    editor.enabled = True
    editor.scene_detection_enabled = True
    editor.pacing_control = True
    editor._get_video_info = lambda p: 10.0 # 10 second mock duration
    return editor

# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — Hook detection disabled/fails -> no hook moment
# ══════════════════════════════════════════════════════════════════════════════

def test_hook_moment_fails_safe():
    editor = _make_editor()
    # If no frame cache is present and motion/attention didn't populate it,
    # or if we force opencv to be missing:
    
    # Let's mock _detect_hook_moment to throw an exception
    editor._detect_hook_moment = lambda p, d, m: (_ for _ in ()).throw(Exception("Mock cv2 failure"))
    
    result = editor.generate_timeline_instructions("mock.mp4")
    assert result is not None
    
    # Assuming hook moment defaults to None when it fails
    assert result.get("hook_moment") is None

# ══════════════════════════════════════════════════════════════════════════════
# Test 2, 3, 4 — Hook Scoring Simulation
# ══════════════════════════════════════════════════════════════════════════════

def test_hook_scoring_logic():
    editor = _make_editor()
    import numpy as np
    import Visual_Refinement_Modules.smart_scene_editor as mod
    
    # 4 frames in cache, duration = 10.0 (max eval = 6.0)
    # Frame 1: 1.0s - dark, no face
    # Frame 2: 2.0s - bright, no face
    # Frame 3: 3.0s - bright, face off-center
    # Frame 4: 4.0s - bright, face centered, near motion spike
    # Frame 5: 9.0s - perfectly engaging, but past 60% limit
    
    dark_frame = np.zeros((360, 640), dtype=np.uint8)
    bright_frame = np.ones((360, 640), dtype=np.uint8) * 255
    
    editor.frame_cache["mock.mp4"] = [
        (1.0, dark_frame),
        (2.0, bright_frame),
        (3.0, bright_frame),
        (4.0, bright_frame),
        (9.0, bright_frame),
    ]
    
    # We will directly test _detect_hook_moment logic by feeding it data
    from unittest.mock import MagicMock, patch
    
    mock_cv2 = MagicMock()
    # Mock face_cascade
    mock_cascade = MagicMock()
    mock_cascade.empty.return_value = False
    
    def side_effect(img, **kwargs):
        # We track how many times this specific mock cascade was called
        # We'll use a local counter
        side_effect.call_count += 1
        if side_effect.call_count == 3: # Frame 3 (one-based)
            return [[10, 10, 50, 50]]
        if side_effect.call_count == 4: # Frame 4
            # For 640w x 360h frame: center is 320, 180
            # coords: x=300, y=160, w=40, h=40 -> center_x = 320, center_y = 180
            return [[300, 160, 40, 40]]
        return []
    
    side_effect.call_count = 0
    mock_cascade.detectMultiScale.side_effect = side_effect
    mock_cv2.CascadeClassifier.return_value = mock_cascade
    mock_cv2.data.haarcascades = "mock_path/"

    with patch.dict('sys.modules', {'cv2': mock_cv2}):
        motion_events = [{"time": 4.1, "type": "motion_spike"}]
        hook = editor._detect_hook_moment("mock.mp4", 10.0, motion_events)
        
        print(f"DEBUG: hook result: {hook}")
        
        # Expectation:
        # F1 (1.0s): motion=0, face=0, center=0, bright=0 => 0.0
        # F2 (2.0s): motion=0, face=0, center=0, bright=0.1 => 0.1
        # F3 (3.0s): motion=0, face=0.3, center=0.0, bright=0.1 => 0.4
        # F4 (4.0s): motion=1.0*0.4, face=1.0*0.3, center=1.0*0.2, bright=1.0*0.1 => 1.0 (Hook)
        
        assert hook is not None
        assert hook["time"] == 4.0
        assert hook["score"] == 1.0

# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — Schema compatibility 
# ══════════════════════════════════════════════════════════════════════════════

def test_schema_compatibility():
    editor = _make_editor()
    editor._detect_hook_moment = lambda p, d, m: {"time": 3.25, "score": 0.82}
    
    result = editor.generate_timeline_instructions("mock.mp4")
    assert "hook_moment" in result
    assert result["hook_moment"]["time"] == 3.25
    assert result["hook_moment"]["score"] == 0.82

# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
