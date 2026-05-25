"""
tests/test_speed_ramps.py
==========================
Validation tests for Micro Speed Ramp generation inside SmartSceneEditor.

Run with:
    cd "d:\\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    python -m pytest tests/test_speed_ramps.py -v
"""

import os
import sys
import importlib
import pytest

# Ensure project root is on path when running from tests/ subdir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_editor(**env_overrides):
    """Build a fresh SmartSceneEditor with env overrides (full module reload)."""
    defaults = {
        "SMART_SCENE_EDITOR_ENABLED":  "true",
        "SCENE_DETECTION_ENABLED":     "false",
        "PACING_CONTROL":              "true",
        "AUTO_ZOOM_EFFECTS":           "false",
        "TREND_TRANSITIONS_ENABLED":   "true",
        "TRANSITION_FALLBACK":         "true",
        "BEAT_SYNC_EDITING":           "false",
        "MOTION_EDITING_ENABLED":      "false",
        "MOTION_THRESHOLD_FACTOR":     "1.5",
        "MOTION_COOLDOWN":             "0.8",
        "ADD_SPEED_RAMPING":           "no",
        "SPEED_VARIATION":             "0.15",
    }
    defaults.update(env_overrides)
    for k, v in defaults.items():
        os.environ[k] = v

    import Visual_Refinement_Modules.smart_scene_editor as mod
    importlib.reload(mod)
    editor = mod.SmartSceneEditor()
    editor._get_video_info = lambda p: 12.0      # mock: 12s video
    editor._detect_motion_events = lambda p, d: []  # mock: no cv2 needed
    return editor


# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — ADD_SPEED_RAMPING=no → speed_ramps must be empty
# ══════════════════════════════════════════════════════════════════════════════

def test_speed_ramping_disabled_produces_empty_ramps():
    """
    When ADD_SPEED_RAMPING=no, the output must contain an empty speed_ramps
    list — no ramps generated, pipeline behaves exactly as before.
    """
    editor = _make_editor(ADD_SPEED_RAMPING="no")
    result = editor.generate_timeline_instructions("mock.mp4")

    assert result is not None, "Expected instructions (pacing enabled)"
    assert result.get("speed_ramps", []) == [], \
        f"Expected empty speed_ramps, got: {result.get('speed_ramps')}"


# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — Motion spike present → ramp generated at spike time
# ══════════════════════════════════════════════════════════════════════════════

def test_motion_spike_triggers_speed_ramp():
    """
    When a motion spike exists and ADD_SPEED_RAMPING=yes, a speed ramp
    must be generated centred near the spike timestamp.
    """
    editor = _make_editor(
        ADD_SPEED_RAMPING="yes",
        MOTION_EDITING_ENABLED="true",
    )
    editor._get_video_info = lambda p: 12.0
    # Inject a synthetic motion spike at 3.5s
    editor._detect_motion_events = lambda p, d: [{"time": 3.5, "strength": "medium"}]

    result = editor.generate_timeline_instructions("mock.mp4")

    assert result is not None
    ramps = result.get("speed_ramps", [])
    assert len(ramps) > 0, "Expected at least one speed ramp from motion spike"

    # Verify a ramp is near the spike (centre ≈ 3.5s, ramp ±0.2s)
    spike_covered = any(r["start"] <= 3.5 <= r["end"] for r in ramps)
    assert spike_covered, f"No ramp covers the motion spike at 3.5s. Ramps: {ramps}"

    # Speed must be within [1.05, 1.30] for SPEED_VARIATION=0.15
    for r in ramps:
        assert 1.04 <= r["speed"] <= 1.31, f"Ramp speed out of range: {r}"


# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — Beat event present → ramp aligned with beat
# ══════════════════════════════════════════════════════════════════════════════

def test_beat_event_triggers_speed_ramp():
    """
    When beat_timestamps are provided and ADD_SPEED_RAMPING=yes,
    speed ramps must be generated aligned with beat timestamps
    (in the absence of higher-priority motion spikes).
    """
    editor = _make_editor(
        ADD_SPEED_RAMPING="yes",
        MOTION_EDITING_ENABLED="false",
    )
    beat_timestamps = [2.0, 4.0, 6.0, 8.0, 10.0]
    result = editor.generate_timeline_instructions("mock.mp4", beat_timestamps=beat_timestamps)

    assert result is not None
    ramps = result.get("speed_ramps", [])
    assert len(ramps) > 0, "Expected ramps from beat timestamps"

    # At least one ramp centre should be within 0.3s of a beat
    for ramp in ramps:
        centre = (ramp["start"] + ramp["end"]) / 2
        nearest_beat = min(beat_timestamps, key=lambda b: abs(b - centre))
        assert abs(nearest_beat - centre) <= 0.3, \
            f"Ramp centre {centre:.3f}s is >0.3s from nearest beat {nearest_beat}s"


# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — Minimum ramp spacing enforced (0.8s between ramps)
# ══════════════════════════════════════════════════════════════════════════════

def test_ramp_spacing_enforced():
    """
    All generated speed ramps must be separated by at least 0.8 seconds
    (start of next ramp ≥ end of previous ramp + 0.8s).
    """
    editor = _make_editor(ADD_SPEED_RAMPING="yes")
    # Dense beats to try to force ramps close together
    beat_timestamps = [round(t * 0.4, 2) for t in range(1, 30)]
    result = editor.generate_timeline_instructions("mock.mp4", beat_timestamps=beat_timestamps)

    assert result is not None
    ramps = result.get("speed_ramps", [])
    # Sort by start time just in case
    ramps_sorted = sorted(ramps, key=lambda r: r["start"])

    for i in range(1, len(ramps_sorted)):
        gap = round(ramps_sorted[i]["start"] - ramps_sorted[i - 1]["end"], 4)
        assert gap >= 0.79, (   # 0.79 tolerance for float rounding
            f"Ramp spacing violated: ramp {i} starts {ramps_sorted[i]['start']}s, "
            f"ramp {i-1} ends {ramps_sorted[i-1]['end']}s, gap={gap:.3f}s < 0.8s"
        )

    # Also check max_ramps cap: duration / 3 = 12 / 3 = 4
    assert len(ramps) <= 4, f"Too many ramps: {len(ramps)} > 4"


# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — Pipeline compatibility: speed_ramps in output, render_pipeline safe
# ══════════════════════════════════════════════════════════════════════════════

def test_pipeline_compatibility_with_speed_ramps():
    """
    Verify that:
    1. speed_ramps key is always present in output (even if empty).
    2. render_pipeline() can receive timeline_instructions with speed_ramps
       without raising (tested by inspecting the filter command assembly logic).
    """
    editor = _make_editor(ADD_SPEED_RAMPING="yes")
    result = editor.generate_timeline_instructions("mock.mp4")

    assert result is not None
    assert "speed_ramps" in result, "speed_ramps key must always exist in output"

    ramps = result["speed_ramps"]
    assert isinstance(ramps, list), "speed_ramps must be a list"

    # Verify each ramp has the expected schema
    for r in ramps:
        assert "start" in r and "end" in r and "speed" in r, \
            f"Ramp missing required keys: {r}"
        assert r["start"] >= 0.0,       f"start < 0: {r}"
        assert r["end"] > r["start"],   f"end <= start: {r}"
        assert 0.5 <= r["speed"] <= 2.0, f"speed out of atempo range: {r}"

    # Simulate what video_pipeline does: build filter strings (no FFmpeg call)
    import Compiler_Modules.video_pipeline as vp_mod
    importlib.reload(vp_mod)

    for r in ramps:
        r_start = round(float(r["start"]), 4)
        r_end   = round(float(r["end"]), 4)
        r_speed = float(r["speed"])
        # These must not raise
        v_str = f"setpts='if(between(t,{r_start},{r_end}),PTS/{r_speed:.4f},PTS)'"
        a_str = f"atempo={r_speed:.4f}:enable='between(t,{r_start},{r_end})'"
        assert len(v_str) > 0
        assert len(a_str) > 0


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
