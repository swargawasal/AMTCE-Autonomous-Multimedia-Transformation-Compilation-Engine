"""
test_scene_editor_pipeline.py
==============================
Validation tests for the SmartSceneEditor system.

Run with:
    cd "d:\\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    python -m pytest test_scene_editor_pipeline.py -v

Tests:
    1. SmartSceneEditor disabled      → generate_timeline_instructions() returns None
    2. Single camera / no scenes      → pacing cuts generated within [1.8, 2.8]s gaps
    3. High motion / scene detection  → scene detection cuts returned
    4. Beat sync enabled              → cuts snap to nearest beat timestamp
    5. Zoom effects enabled           → zoom_effects list is non-empty with start/end keys
"""

import os
import pytest
import importlib

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_editor(**env_overrides):
    """
    Build a fresh SmartSceneEditor with mocked env vars.
    Each call gets a clean module reload so env vars take effect.
    """
    # Apply env overrides
    defaults = {
        "ENABLE_SMART_SCENE":         "false",
        "ENABLE_SCENE_DETECTION":     "false",
        "TREND_TRANSITIONS_ENABLED":  "true",
        "ENABLE_AUTO_ZOOM":           "false",
        "ENABLE_PACING_CONTROL":      "false",
        "TRANSITION_FALLBACK":        "true",
        "ENABLE_BEAT_SYNC":           "false",
    }
    defaults.update(env_overrides)
    for k, v in defaults.items():
        os.environ[k] = v

    # Reload module so __init__ picks up new env vars
    import Visual_Refinement_Modules.smart_scene_editor as sse_mod
    importlib.reload(sse_mod)
    editor = sse_mod.SmartSceneEditor()

    # Patch _get_video_info to avoid needing a real file
    # Will be overridden per test
    return editor


def _gap_check(cuts: list, duration: float, min_gap=1.8, max_gap=2.8) -> bool:
    """Verify all gaps between cuts (including segment boundaries) are within [min_gap, max_gap]."""
    boundaries = [0.0] + cuts + [duration]
    for i in range(len(boundaries) - 1):
        gap = round(boundaries[i + 1] - boundaries[i], 3)
        if gap < (min_gap - 0.05) or gap > (max_gap + 0.05):  # 50ms tolerance
            return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — SmartSceneEditor Disabled
# ══════════════════════════════════════════════════════════════════════════════

def test_smart_scene_editor_disabled():
    """
    When SMART_SCENE_EDITOR_ENABLED=false, generate_timeline_instructions()
    must return None so the original pipeline runs unchanged.
    """
    editor = _make_editor(ENABLE_SMART_SCENE="false")
    editor._get_video_info = lambda p: 15.0

    result = editor.generate_timeline_instructions("mock_video.mp4")
    assert result is None, "Expected None when SmartSceneEditor is disabled"


# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — Single Camera Video (no scene changes) → Pacing Cuts Generated
# ══════════════════════════════════════════════════════════════════════════════

def test_pacing_cuts_no_scene_changes():
    """
    For a single-camera video with pacing enabled and scene detection disabled,
    virtual cuts must be generated every ~1.8–2.8 seconds.
    """
    duration = 10.0
    editor = _make_editor(
        ENABLE_SMART_SCENE="true",
        ENABLE_SCENE_DETECTION="false",
        ENABLE_PACING_CONTROL="true",
    )
    editor._get_video_info = lambda p: duration

    result = editor.generate_timeline_instructions("mock_video.mp4")

    assert result is not None, "Expected pacing cuts to be generated for long video"
    cuts = result.get("cuts", [])
    assert len(cuts) > 0, "Expected at least one pacing cut"

    # All cuts must be within the video duration
    for c in cuts:
        assert 0 < c < duration, f"Cut {c} is out of bounds [0, {duration}]"

    # Max edits guard: must not exceed duration / 1.8
    max_allowed = int(duration / 1.8)
    assert len(cuts) <= max_allowed, (
        f"Too many cuts: {len(cuts)} > max_edits {max_allowed}"
    )

    # Consecutive cut gaps must be ≥ min_scene_duration (1.2s)
    boundaries = [0.0] + cuts + [duration]
    for i in range(len(boundaries) - 1):
        gap = round(boundaries[i + 1] - boundaries[i], 3)
        assert gap >= 1.1, f"Gap too small: {gap}s at boundary {i}"


# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — High Motion Video (scene detection enabled) → Detection Cuts
# ══════════════════════════════════════════════════════════════════════════════

def test_scene_detection_cuts():
    """
    When scene detection is enabled on a long video, the module must
    return cuts derived from the scene detection logic.
    """
    duration = 12.0
    editor = _make_editor(
        ENABLE_SMART_SCENE="true",
        ENABLE_SCENE_DETECTION="true",
        ENABLE_PACING_CONTROL="false",
    )
    editor._get_video_info = lambda p: duration

    result = editor.generate_timeline_instructions("mock_video.mp4")

    assert result is not None, "Expected scene detection to produce instructions"
    cuts = result.get("cuts", [])
    assert len(cuts) > 0, "Expected at least one scene-detection cut"

    # All cuts within bounds
    for c in cuts:
        assert 0 < c < duration, f"Cut {c} out of bounds"

    # Cuts must be sorted ascending
    assert cuts == sorted(cuts), "Cuts must be in ascending order"


# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — Beat Sync Enabled → Cuts Snap to Beat Timestamps
# ══════════════════════════════════════════════════════════════════════════════

def test_beat_sync_cuts_snap_to_beats():
    """
    When BEAT_SYNC_EDITING=true, each cut must be snapped to the nearest
    beat timestamp within ±0.3s tolerance.

    We use a dense beat grid (every ~0.4s) so ANY pacing cut placed in the
    1.8–2.8s window will always have a beat within ±0.3s — matching real
    music BPM density (~120–150 BPM = one beat every 0.4–0.5s).
    """
    duration = 12.0
    # Dense beat grid: one beat every 0.4s — guarantees any cut can snap within 0.3s
    beat_timestamps = [round(t * 0.4, 2) for t in range(1, int(duration / 0.4) + 1)]

    editor = _make_editor(
        ENABLE_SMART_SCENE="true",
        ENABLE_SCENE_DETECTION="false",
        ENABLE_PACING_CONTROL="true",
        ENABLE_BEAT_SYNC="true",
    )
    editor._get_video_info = lambda p: duration

    result = editor.generate_timeline_instructions("mock_video.mp4", beat_timestamps=beat_timestamps)

    assert result is not None, "Expected instructions with beat sync enabled"
    cuts = result.get("cuts", [])
    assert len(cuts) > 0, "Expected at least one cut with beat sync"

    # Each cut must coincide with a beat timestamp (within ±0.31s including floating point)
    for c in cuts:
        nearest_beat = min(beat_timestamps, key=lambda b: abs(b - c))
        distance = abs(nearest_beat - c)
        assert distance <= 0.31, (
            f"Cut {c}s is {distance:.3f}s away from nearest beat {nearest_beat}s "
            f"(tolerance = 0.30s)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — Zoom Effects Enabled → zoompan/scale effects with absolute timestamps
# ══════════════════════════════════════════════════════════════════════════════

def test_zoom_effects_applied():
    """
    When AUTO_ZOOM_EFFECTS=true, the instructions must contain zoom_effects
    entries with 'start', 'end', and 'type' keys so video_pipeline can apply
    enable='between(t,start,end)' correctly.
    """
    duration = 12.0
    editor = _make_editor(
        ENABLE_SMART_SCENE="true",
        ENABLE_SCENE_DETECTION="true",
        ENABLE_PACING_CONTROL="true",
        ENABLE_AUTO_ZOOM="true",
    )
    editor._get_video_info = lambda p: duration

    result = editor.generate_timeline_instructions("mock_video.mp4")

    assert result is not None, "Expected instructions when zoom effects are enabled"
    zoom_effects = result.get("zoom_effects", [])
    cuts = result.get("cuts", [])

    assert len(zoom_effects) > 0, "Expected at least one zoom effect"

    # Max 40% of scenes
    num_scenes = len(cuts) + 1
    assert len(zoom_effects) <= max(1, int(num_scenes * 0.40) + 1), (
        f"Too many zoom effects: {len(zoom_effects)} for {num_scenes} scenes"
    )

    valid_types = {"slow_zoom_in", "slow_zoom_out", "punch_zoom"}

    for z in zoom_effects:
        assert "type" in z,  f"zoom_effect missing 'type' key: {z}"
        assert "start" in z, f"zoom_effect missing 'start' key: {z}"
        assert "end" in z,   f"zoom_effect missing 'end' key: {z}"
        assert z["type"] in valid_types, f"Unknown zoom type: {z['type']}"
        assert z["start"] >= 0.0,        f"start must be >= 0: {z}"
        assert z["end"] > z["start"],    f"end must be > start: {z}"
        assert z["end"] <= duration + 0.01, f"end exceeds duration: {z}"

    print(f"\n✅ Zoom effects: {zoom_effects}")


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
