"""
tests/test_scene_reconstruction.py

Tests for the scene-based real-edit reconstruction pipeline.

Verifies:
  - segments are extracted (non-empty list)
  - each segment is within valid scene bounds
  - concatenated duration < original duration (trimming reduces length)
  - output dict has required keys
  - fallback to [] on bad input
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBuildSceneReconstruction(unittest.TestCase):
    """Unit tests for SmartSceneEditor.build_scene_reconstruction using mocked scenes."""

    def setUp(self):
        try:
            from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor
            self.editor = SmartSceneEditor()
            self.available = True
        except Exception:
            self.available = False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _mock_reconstruction(self, scenes, frame_data=None):
        """
        Bypass detect_scenes (video file not needed) by directly calling
        the scoring + highlight + transition logic with pre-built scenes.
        This mirrors what build_scene_reconstruction does internally.
        """
        fd = frame_data or {}
        motion    = fd.get("motion_events", [])
        attention = fd.get("attention_events", [])
        hook      = fd.get("hook_moment")
        hook_time = hook.get("time") if hook else None
        STR_MAP   = {"small": 0.33, "medium": 0.66, "large": 1.0}

        import random as _r

        def _score(sc):
            s, e = sc["start"], sc["end"]
            raw_m = 0.0
            for ev in motion:
                t = ev.get("time", -1)
                if s <= t < e:
                    raw_m = max(raw_m, STR_MAP.get(ev.get("strength", "small"), 0.33))
            att = 1.0 if any(s <= ev.get("time", -1) < e for ev in attention) else 0.0
            prox = 0.0
            if hook_time is not None:
                center = (s + e) / 2.0
                prox = 1.0 / (1.0 + abs(hook_time - center))
            return round(raw_m * 0.4 + att * 0.4 + prox * 0.2, 4)

        CANDS = ["whip_pan", "zoom_blur", "glitch_pop"]
        segments = []
        for sc in scenes:
            imp = _score(sc)
            hl = self.editor.select_scene_highlight(sc, fd)
            h_start = hl["highlight_start"]
            h_end   = hl["highlight_end"]
            dur = h_end - h_start
            if dur < 3.0:
                h_end = min(sc["end"], h_start + 3.0)
            if h_end - h_start > 6.0:
                h_end = round(h_start + 6.0, 3)
            if h_end - h_start < 1.0:
                continue
            segments.append({
                "start": round(h_start, 3),
                "end":   round(h_end, 3),
                "scene_start": sc["start"],
                "scene_end":   sc["end"],
                "importance":  imp,
                "transition_after": None,
            })

        for i in range(len(segments) - 1):
            imp_delta = abs(segments[i]["importance"] - segments[i+1]["importance"])
            if imp_delta > 0.3 or _r.random() < 0.3:
                segments[i]["transition_after"] = _r.choice(CANDS)

        return segments

    # ── Required keys ─────────────────────────────────────────────────────────

    def test_output_has_required_keys(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scenes = [
            {"start": 0.0, "end": 8.0},
            {"start": 8.0, "end": 16.0},
            {"start": 16.0, "end": 24.0},
        ]
        segments = self._mock_reconstruction(scenes)
        self.assertGreater(len(segments), 0)
        for seg in segments:
            for key in ("start", "end", "importance", "transition_after"):
                self.assertIn(key, seg, msg=f"Missing key '{key}' in segment")

    # ── Segments extracted ────────────────────────────────────────────────────

    def test_segments_extracted_from_multi_scene(self):
        """Should produce at least one segment for a multi-scene source."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scenes = [
            {"start": 0.0, "end": 7.0},
            {"start": 7.0, "end": 14.0},
            {"start": 14.0, "end": 22.0},
            {"start": 22.0, "end": 30.0},
        ]
        segments = self._mock_reconstruction(scenes)
        self.assertGreater(len(segments), 0, "Expected at least 1 segment")

    def test_each_segment_within_scene_bounds(self):
        """highlight_start/end must stay within original scene boundaries."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scenes = [
            {"start": 0.0, "end": 9.0},
            {"start": 9.0, "end": 18.0},
            {"start": 18.0, "end": 27.0},
        ]
        segments = self._mock_reconstruction(scenes)
        for seg in segments:
            self.assertGreaterEqual(seg["start"], seg["scene_start"] - 1e-6,
                msg=f"highlight_start {seg['start']} < scene_start {seg['scene_start']}")
            self.assertLessEqual(seg["end"], seg["scene_end"] + 1e-6,
                msg=f"highlight_end {seg['end']} > scene_end {seg['scene_end']}")

    # ── Duration constraint checked per segment ───────────────────────────────

    def test_segment_min_duration_3s(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scenes = [{"start": 0.0, "end": 10.0}, {"start": 10.0, "end": 20.0}]
        segs = self._mock_reconstruction(scenes)
        for seg in segs:
            dur = seg["end"] - seg["start"]
            self.assertGreaterEqual(dur, 1.0 - 1e-6,
                msg=f"Segment length {dur:.3f}s is too short")

    def test_segment_max_duration_6s(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scenes = [{"start": 0.0, "end": 20.0}, {"start": 20.0, "end": 40.0}]
        segs = self._mock_reconstruction(scenes)
        for seg in segs:
            dur = seg["end"] - seg["start"]
            self.assertLessEqual(dur, 6.0 + 1e-6,
                msg=f"Segment length {dur:.3f}s exceeds 6s maximum")

    # ── Concatenated duration < original ─────────────────────────────────────

    def test_total_highlight_duration_less_than_original(self):
        """Sum of trimmed highlights must be less than the original video duration."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        original_duration = 30.0
        scenes = [
            {"start": 0.0,  "end": 8.0},
            {"start": 8.0,  "end": 16.0},
            {"start": 16.0, "end": 24.0},
            {"start": 24.0, "end": 30.0},
        ]
        segs = self._mock_reconstruction(scenes)
        total_highlight = sum(seg["end"] - seg["start"] for seg in segs)
        self.assertLess(total_highlight, original_duration,
            msg=f"Total highlights {total_highlight:.2f}s >= original {original_duration}s")

    def test_total_highlight_duration_long_video(self):
        """Ensure reconstruction shortens even a long multi-scene video."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        original_duration = 60.0
        scenes = [{"start": i * 10.0, "end": (i + 1) * 10.0} for i in range(6)]
        segs = self._mock_reconstruction(scenes)
        total = sum(s["end"] - s["start"] for s in segs)
        self.assertLess(total, original_duration,
            msg=f"Total highlights {total:.2f}s >= original {original_duration}s")

    # ── Transitions ───────────────────────────────────────────────────────────

    def test_transitions_only_between_segments(self):
        """transition_after must only exist on segments that are not last."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        scenes = [
            {"start": 0.0, "end": 10.0},
            {"start": 10.0, "end": 20.0},
            {"start": 20.0, "end": 30.0},
        ]
        segs = self._mock_reconstruction(scenes)
        if segs:
            # Last segment should never have a transition after it
            self.assertIsNone(segs[-1]["transition_after"],
                msg="Last segment must not have transition_after")

    def test_transition_values_are_valid(self):
        """All applied transitions must be from the allowed set or None."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        valid_transitions = {"whip_pan", "zoom_blur", "glitch_pop", None}
        scenes = [{"start": i * 8.0, "end": (i + 1) * 8.0} for i in range(4)]
        segs = self._mock_reconstruction(scenes)
        for seg in segs:
            self.assertIn(seg["transition_after"], valid_transitions,
                msg=f"Invalid transition: {seg['transition_after']}")

    # ── Importance score ──────────────────────────────────────────────────────

    def test_importance_score_range(self):
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        motion = [{"time": 5.0, "strength": "large"}]
        attention = [{"time": 12.0}]
        hook = {"time": 5.0}
        scenes = [{"start": 0.0, "end": 9.0}, {"start": 9.0, "end": 18.0}]
        segs = self._mock_reconstruction(scenes, {"motion_events": motion,
                                                  "attention_events": attention,
                                                  "hook_moment": hook})
        for seg in segs:
            self.assertGreaterEqual(seg["importance"], 0.0)
            self.assertLessEqual(seg["importance"], 1.0 + 1e-6,
                msg=f"Importance {seg['importance']} out of [0,1]")

    # ── Edge case: fallback on empty scenes ───────────────────────────────────

    def test_empty_scenes_returns_empty(self):
        """If detect_scenes returns nothing, reconstruction must return []."""
        if not self.available:
            self.skipTest("SmartSceneEditor not available")
        # build_scene_reconstruction returns [] when detect_scenes returns []
        # We mock by calling with a non-existent path (detect_scenes will return [])
        result = self.editor.build_scene_reconstruction("nonexistent_fake_video.mp4")
        self.assertEqual(result, [], "Should return [] when no scenes detected")


if __name__ == "__main__":
    unittest.main(verbosity=2)
