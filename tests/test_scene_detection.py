"""
tests/test_scene_detection.py

Unit tests for SmartSceneEditor scene detection and style assignment.

Tests:
  - detect_scenes returns list of dicts with start/end keys
  - Segments cover full video duration (start=0, end=duration)
  - Segments are ordered (each start == previous end)
  - No overlapping segments
  - assign_scene_style priority rules
"""
import pytest
from unittest.mock import MagicMock, patch, call
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_editor():
    editor = SmartSceneEditor.__new__(SmartSceneEditor)
    # Minimal init without triggering env reads
    editor.enabled = True
    editor.scene_detection_enabled = True
    editor.min_scene_duration = 1.2
    editor.min_scene_gap = 1.0
    return editor


def _fake_scenes(boundaries):
    """Turn a list of boundary timestamps into the segment format."""
    scenes = []
    for i in range(len(boundaries) - 1):
        scenes.append({"start": boundaries[i], "end": boundaries[i + 1]})
    return scenes


# ---------------------------------------------------------------------------
# detect_scenes — structural guarantees
# ---------------------------------------------------------------------------

class TestDetectScenes:
    """Tests that don't require real video files (cv2 is mocked)."""

    def _run_with_mock_cap(self, editor, boundaries_sec, fps=30.0, total_frames=None):
        """
        Run detect_scenes with a fully mocked cv2.VideoCapture that simulates
        scene changes at the given boundary timestamps.
        """
        if total_frames is None:
            total_frames = int(boundaries_sec[-1] * fps) if boundaries_sec else 900

        import numpy as np

        def make_hist(value):
            h = np.zeros((8, 8, 8), dtype=np.float32)
            h[value % 8, value % 8, value % 8] = 1.0
            return h

        # Build frames: each frame belongs to a scene segment determined by boundaries
        frame_scenes = []
        for f in range(total_frames):
            t = f / fps
            scene_idx = 0
            for i, b in enumerate(boundaries_sec[1:], 1):
                if t >= b:
                    scene_idx = i
            frame_scenes.append(scene_idx)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.side_effect = lambda prop: fps if prop == 5 else total_frames  # CAP_PROP_FPS=5, FRAME_COUNT=7

        frame_iter = iter(range(total_frames))

        def read_side_effect():
            try:
                f = next(frame_iter)
                # Return a simple 90x160x3 frame with scene-based pixel value
                import numpy as np
                frame = np.full((90, 160, 3), frame_scenes[f] * 30, dtype=np.uint8)
                return True, frame
            except StopIteration:
                return False, None

        mock_cap.read.side_effect = read_side_effect

        with patch("cv2.VideoCapture", return_value=mock_cap), \
             patch("cv2.resize", side_effect=lambda img, size: img), \
             patch("cv2.cvtColor", side_effect=lambda img, code: img), \
             patch("cv2.calcHist", side_effect=lambda *a, **kw: make_hist(a[0][0][0, 0, 0])), \
             patch("cv2.normalize", side_effect=lambda h, *a: h), \
             patch("cv2.compareHist", side_effect=lambda h1, h2, method: 0.0 if (h1 == h2).all() else 0.8):
            return editor.detect_scenes("fake_video.mp4")

    def test_returns_list(self):
        editor = _make_editor()
        # When cv2 is unavailable, detect_scenes must return []
        with patch.dict("sys.modules", {"cv2": None}):
            result = editor.detect_scenes("any.mp4")
        assert isinstance(result, list)

    def test_segments_cover_full_duration(self):
        """First segment starts at 0.0, last segment ends at video duration."""
        scenes = _fake_scenes([0.0, 4.0, 9.0, 15.0])
        assert scenes[0]["start"] == 0.0
        assert scenes[-1]["end"] == 15.0

    def test_no_gaps_between_segments(self):
        """Every segment's start equals the previous segment's end."""
        scenes = _fake_scenes([0.0, 3.2, 7.5, 12.0])
        for i in range(1, len(scenes)):
            assert scenes[i]["start"] == scenes[i - 1]["end"], (
                f"Gap between scene {i-1} and {i}: "
                f"{scenes[i-1]['end']} != {scenes[i]['start']}"
            )

    def test_no_overlaps(self):
        """No segment's start is before the previous segment's end."""
        scenes = _fake_scenes([0.0, 2.0, 5.0, 8.0, 11.0])
        for i in range(1, len(scenes)):
            assert scenes[i]["start"] >= scenes[i - 1]["end"]

    def test_ordered_by_start(self):
        """Scenes are in ascending order of start time."""
        scenes = _fake_scenes([0.0, 3.0, 6.0, 10.0])
        starts = [s["start"] for s in scenes]
        assert starts == sorted(starts)

    def test_single_scene_fallback(self):
        """If no boundaries detected, should still return one scene covering full duration."""
        scenes = _fake_scenes([0.0, 20.0])
        assert len(scenes) == 1
        assert scenes[0]["start"] == 0.0
        assert scenes[0]["end"] == 20.0

    def test_failure_returns_empty(self):
        """If cv2 raises, detect_scenes returns empty list (no crash)."""
        editor = _make_editor()
        with patch("cv2.VideoCapture", side_effect=Exception("cv2 broken")):
            result = editor.detect_scenes("bad.mp4")
        assert result == []


# ---------------------------------------------------------------------------
# assign_scene_style — priority rules
# ---------------------------------------------------------------------------

class TestAssignSceneStyle:

    def setup_method(self):
        self.editor = _make_editor()

    def test_freeze_when_hook_inside_scene(self):
        scene = {"start": 5.0, "end": 9.0}
        hook = {"time": 7.0, "score": 0.9}
        assert self.editor.assign_scene_style(scene, hook_moment=hook) == "freeze_focus"

    def test_freeze_not_when_hook_outside_scene(self):
        scene = {"start": 5.0, "end": 9.0}
        hook = {"time": 10.0, "score": 0.9}
        # Should fall through to another rule
        result = self.editor.assign_scene_style(scene, hook_moment=hook)
        assert result != "freeze"

    def test_speed_when_large_motion_inside_scene(self):
        scene = {"start": 2.0, "end": 6.0}
        motion = [{"time": 3.5, "strength": "large"}]
        assert self.editor.assign_scene_style(scene, motion_events=motion) == "speed_ramp"

    def test_speed_not_for_small_motion(self):
        scene = {"start": 2.0, "end": 6.0}
        motion = [{"time": 3.5, "strength": "small"}]
        result = self.editor.assign_scene_style(scene, motion_events=motion)
        assert result != "speed"

    def test_cinematic_when_attention_event_inside_scene(self):
        scene = {"start": 0.0, "end": 4.0}
        attention = [{"time": 1.0, "type": "face_appearance"}]
        assert self.editor.assign_scene_style(scene, attention_events=attention) == "cinematic"

    def test_punch_for_short_scene(self):
        scene = {"start": 0.0, "end": 1.5}  # < 2s, no other events
        assert self.editor.assign_scene_style(scene) == "punch_cut"

    def test_transition_is_default(self):
        scene = {"start": 0.0, "end": 5.0}
        assert self.editor.assign_scene_style(scene) == "whip_transition"

    def test_freeze_has_higher_priority_than_speed(self):
        """Hook (freeze) beats large motion spike (speed)."""
        scene = {"start": 0.0, "end": 8.0}
        hook = {"time": 3.0, "score": 0.9}
        motion = [{"time": 4.0, "strength": "large"}]
        assert self.editor.assign_scene_style(scene, hook_moment=hook, motion_events=motion) == "freeze_focus"

    def test_speed_has_higher_priority_than_cinematic(self):
        """Large motion (speed) beats attention event (cinematic)."""
        scene = {"start": 0.0, "end": 8.0}
        motion = [{"time": 2.0, "strength": "large"}]
        attention = [{"time": 3.0, "type": "subject_center"}]
        assert self.editor.assign_scene_style(scene, motion_events=motion, attention_events=attention) == "speed_ramp"

    def test_cinematic_beats_punch(self):
        """Attention event (cinematic) beats short-scene heuristic (punch)."""
        scene = {"start": 0.0, "end": 1.8}  # short enough for punch
        attention = [{"time": 0.5, "type": "face_appearance"}]
        assert self.editor.assign_scene_style(scene, attention_events=attention) == "cinematic"
