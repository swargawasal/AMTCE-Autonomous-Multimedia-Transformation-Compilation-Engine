"""
tests/test_signal_repair_layer.py
----------------------------------
Unit tests for Content_Intelligence.signal_repair_layer.

Tests:
  - emotion repair from motion + expression proxies
  - retention repair from scene boundaries
  - face repair from expression data
  - dead moment hard removal + safety resurrection
  - signal confidence scoring formula
  - semantic scoring formula + flags
  - signal health formula
  - signal flags (fallback_active)
  - repair() entry point: never raises, returns valid dict
  - no-overwrite guarantee when signals already present
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Content_Intelligence.signal_repair_layer import (
    repair,
    _repair_emotion_from_proxies,
    _repair_retention_from_scene_cuts,
    _repair_face_from_expression_data,
    _filter_dead_moments,
    _compute_signal_confidence,
    _compute_semantic_score,
    _run_semantic_scoring,
    _score_signal_health,
    _build_signal_flags,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _profile(extra=None):
    """Minimal valid profile_data with no real signals."""
    base = {
        "emotional_spikes":  [],
        "retention_peaks":   [],
        "subject_tracking":  [],
        "motion_scores":     [],
        "expression_moments": [],
        "scene_boundaries":  [],
        "beat_data":         {"beats": []},
        "candidate_moments": [],
        "video_duration":    30.0,
    }
    if extra:
        base.update(extra)
    return base


# ═══════════════════════════════════════════════════════════════════════════════
# 1. EMOTION REPAIR
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmotionRepair:

    def test_emotion_repair_from_motion_scores(self):
        p = _profile({"motion_scores": [
            {"time": 2.0, "score": 0.8},
            {"time": 5.0, "score": 0.7},
            {"time": 8.0, "score": 0.9},
        ]})
        _repair_emotion_from_proxies(p)
        assert len(p["emotional_spikes"]) >= 1
        assert all(s.get("source") in ("motion_proxy", "expression_proxy")
                   for s in p["emotional_spikes"])

    def test_emotion_repair_from_expression_moments(self):
        p = _profile({"expression_moments": [
            {"time": 1.0, "change_intensity": 0.7},
            {"time": 4.0, "change_intensity": 0.5},
        ]})
        _repair_emotion_from_proxies(p)
        assert len(p["emotional_spikes"]) >= 1
        assert any(s.get("source") == "expression_proxy"
                   for s in p["emotional_spikes"])

    def test_emotion_scores_bounded(self):
        p = _profile({"expression_moments": [{"time": 1.0, "change_intensity": 5.0}]})
        _repair_emotion_from_proxies(p)
        for s in p["emotional_spikes"]:
            assert 0.0 <= s["emotion_score"] <= 1.0

    def test_no_overwrite_when_spikes_present(self):
        real_spikes = [
            {"time": 1.0, "emotion_score": 0.9},
            {"time": 3.0, "emotion_score": 0.8},
        ]
        p = _profile({
            "emotional_spikes":   list(real_spikes),
            "expression_moments": [{"time": 5.0, "change_intensity": 0.6}],
        })
        _repair_emotion_from_proxies(p)
        # Must still contain original spikes unchanged
        times = [s["time"] for s in p["emotional_spikes"]]
        assert 1.0 in times and 3.0 in times

    def test_deduplication_within_gap(self):
        p = _profile({"expression_moments": [
            {"time": 1.0, "change_intensity": 0.5},
            {"time": 1.3, "change_intensity": 0.8},  # within 1.5s gap
        ]})
        _repair_emotion_from_proxies(p)
        # Should deduplicate — only 1 spike in the 1.0–1.5s window
        times = [s["time"] for s in p["emotional_spikes"]]
        close = [t for t in times if 0.9 <= t <= 1.4]
        assert len(close) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. RETENTION REPAIR
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetentionRepair:

    def test_repair_from_scene_boundaries(self):
        p = _profile({"scene_boundaries": [
            {"time": 2.0}, {"time": 6.0}, {"time": 12.0},
        ]})
        _repair_retention_from_scene_cuts(p)
        assert len(p["retention_peaks"]) >= 3

    def test_repair_from_beat_data(self):
        p = _profile({"beat_data": {"beats": [1.0, 3.0, 5.0, 7.0]}})
        _repair_retention_from_scene_cuts(p)
        assert len(p["retention_peaks"]) >= 3

    def test_fallback_even_distribution(self):
        """With no scene cuts and no beats, should still produce 5 evenly-spaced peaks."""
        p = _profile()
        _repair_retention_from_scene_cuts(p)
        assert len(p["retention_peaks"]) == 5

    def test_no_overwrite_when_peaks_present(self):
        real_peaks = [{"time": 1.0, "score": 0.9},
                      {"time": 5.0, "score": 0.8},
                      {"time": 10.0, "score": 0.7}]
        p = _profile({
            "retention_peaks":  list(real_peaks),
            "scene_boundaries": [{"time": 15.0}],
        })
        _repair_retention_from_scene_cuts(p)
        # Original peaks must still be present
        times = [pk["time"] for pk in p["retention_peaks"]]
        assert 1.0 in times and 5.0 in times and 10.0 in times


# ═══════════════════════════════════════════════════════════════════════════════
# 3. FACE REPAIR
# ═══════════════════════════════════════════════════════════════════════════════

class TestFaceRepair:

    def test_face_repair_from_expression_moments(self):
        p = _profile({
            "subject_tracking":   [{"face_present": False}],
            "expression_moments": [{"time": 3.0, "change_intensity": 0.6}],
        })
        _repair_face_from_expression_data(p)
        assert any(s.get("face_present") for s in p["subject_tracking"])

    def test_no_repair_when_face_present(self):
        p = _profile({
            "subject_tracking":   [{"face_present": True}],
            "expression_moments": [{"time": 1.0, "change_intensity": 0.5}],
        })
        original_len = len(p["subject_tracking"])
        _repair_face_from_expression_data(p)
        assert len(p["subject_tracking"]) == original_len


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DEAD MOMENT ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeadMomentFilter:

    def _dead_moment(self, t=0.0):
        return {"time": t, "motion_score": 0.1, "emotion_score": 0.05, "face_score": 0.0}

    def _alive_moment(self, t=0.0):
        return {"time": t, "motion_score": 0.5, "emotion_score": 0.6, "face_score": 0.0}

    def test_dead_moments_removed(self):
        p = _profile({"candidate_moments": [
            self._dead_moment(1.0),
            self._alive_moment(2.0),
            self._alive_moment(3.0),
        ]})
        _filter_dead_moments(p)
        assert len(p["candidate_moments"]) == 2
        times = [m["time"] for m in p["candidate_moments"]]
        assert 1.0 not in times

    def test_all_dead_triggers_resurrection(self):
        """With only dead moments, safety guard must keep at least 2."""
        p = _profile({"candidate_moments": [
            self._dead_moment(1.0),
            self._dead_moment(2.0),
            self._dead_moment(3.0),
        ]})
        _filter_dead_moments(p)
        # Safety guard: at least _DEAD_POOL_MIN (2) moments kept
        assert len(p["candidate_moments"]) >= 2

    def test_dead_tag_preserved(self):
        p = _profile({"candidate_moments": [
            self._dead_moment(1.0),
            self._dead_moment(2.0),
        ]})
        _filter_dead_moments(p)
        # Resurrected moments should still have dead=True tag for debug
        for m in p["candidate_moments"]:
            assert m.get("dead") is True

    def test_alive_moments_not_tagged(self):
        p = _profile({"candidate_moments": [self._alive_moment(1.0)]})
        _filter_dead_moments(p)
        assert not p["candidate_moments"][0].get("dead")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SIGNAL CONFIDENCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalConfidence:

    def test_confidence_field_written(self):
        p = _profile({"candidate_moments": [
            {"time": 1.0, "motion_score": 0.4, "face_present": True, "expression_change": 0.5}
        ]})
        _compute_signal_confidence(p)
        m = p["candidate_moments"][0]
        assert "signal_confidence" in m
        assert 0.0 <= m["signal_confidence"] <= 1.0

    def test_high_confidence_face_and_expression(self):
        p = _profile({"candidate_moments": [
            {"time": 1.0, "face_present": True, "face_score": 0.9,
             "subject_presence": 0.8, "expression_change": 0.8,
             "motion_score": 0.4, "beat_aligned": True}
        ]})
        _compute_signal_confidence(p)
        conf = p["candidate_moments"][0]["signal_confidence"]
        assert conf > 0.55, f"Expected high confidence, got {conf}"

    def test_low_confidence_erratic_motion(self):
        p = _profile({"candidate_moments": [
            {"time": 1.0, "face_present": False, "face_score": 0.0,
             "subject_presence": 0.0, "expression_change": 0.0,
             "motion_score": 0.9, "motion_variance": 0.8}
        ]})
        _compute_signal_confidence(p)
        conf = p["candidate_moments"][0]["signal_confidence"]
        assert conf < 0.5, f"Expected low confidence for erratic no-face motion, got {conf}"

    def test_missing_fields_use_safe_defaults(self):
        """repair() on empty moment dict must not raise."""
        p = _profile({"candidate_moments": [{}]})
        _compute_signal_confidence(p)
        conf = p["candidate_moments"][0].get("signal_confidence", None)
        assert conf is not None
        assert 0.0 <= conf <= 1.0

    def test_confidence_formula_range(self):
        """All confidence outputs must be in [0, 1]."""
        moments = [
            {"motion_score": v, "face_present": fp, "expression_change": ec}
            for v in [0.0, 0.3, 0.7, 1.0]
            for fp in [True, False]
            for ec in [0.0, 0.5]
        ]
        p = _profile({"candidate_moments": moments})
        _compute_signal_confidence(p)
        for m in p["candidate_moments"]:
            c = m.get("signal_confidence", -1)
            assert 0.0 <= c <= 1.0, f"Confidence out of range: {c}"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SEMANTIC SCORING
# ═══════════════════════════════════════════════════════════════════════════════

class TestSemanticScoring:

    def test_high_semantic_face_and_expression(self):
        m = {
            "face_present": True, "face_score": 0.9,
            "subject_presence": 0.8,
            "expression_change": 0.7,
            "motion_score": 0.3,
            "continuity": 0.8, "beat_aligned": True,
            "edge_score": 0.8, "blur_penalty": 0.05,
        }
        score = _compute_semantic_score(m)
        assert score > 0.55, f"Expected high semantic score, got {score}"

    def test_low_semantic_random_motion(self):
        m = {
            "face_present": False, "face_score": 0.0,
            "subject_presence": 0.0,
            "expression_change": 0.0,
            "motion_score": 0.95,   # high motion, no meaning
            "continuity": 0.1,
        }
        score = _compute_semantic_score(m)
        assert score < 0.45, f"Expected low semantic score for random motion, got {score}"

    def test_semantic_score_bounded(self):
        for _ in range(20):
            m = {
                "face_present": True, "face_score": 2.0,
                "expression_change": 5.0, "motion_score": 10.0,
            }
            score = _compute_semantic_score(m)
            assert 0.0 <= score <= 1.0

    def test_semantic_dead_flag_set(self):
        p = _profile({"candidate_moments": [
            {"time": 1.0, "face_present": False, "face_score": 0.0,
             "subject_presence": 0.0, "expression_change": 0.0,
             "motion_score": 0.0, "continuity": 0.0,
             "edge_score": 0.0, "blur_penalty": 0.5}
        ]})
        _run_semantic_scoring(p)
        m = p["candidate_moments"][0]
        # Very low semantic should be flagged
        assert m["semantic_score"] < 0.3
        assert m.get("semantic_dead") is True or m.get("semantic_weak") is True

    def test_semantic_strength_high(self):
        moments = [
            {"face_present": True, "face_score": 0.9, "subject_presence": 0.8,
             "expression_change": 0.7, "motion_score": 0.3,
             "beat_aligned": True, "edge_score": 0.8, "blur_penalty": 0.05}
            for _ in range(5)
        ]
        p = _profile({"candidate_moments": moments})
        _run_semantic_scoring(p)
        assert p["semantic_strength"] in ("HIGH", "MEDIUM")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SIGNAL HEALTH + FLAGS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalHealth:

    def test_health_formula_all_present(self):
        p = _profile({
            "emotional_spikes":  [{"time": t} for t in [1, 2, 3]],
            "subject_tracking":  [{"face_present": True}] * 5,
            "retention_peaks":   [{"time": t} for t in [2, 4, 6, 8, 10]],
            "motion_scores":     [{"time": t, "score": 0.5} for t in range(10)],
        })
        h = _score_signal_health(p)
        assert h > 0.7, f"Expected high health, got {h}"

    def test_health_zero_signals(self):
        p = _profile()
        h = _score_signal_health(p)
        assert h == 0.0

    def test_health_bounded(self):
        p = _profile({
            "emotional_spikes": [{}] * 100,
            "subject_tracking": [{"face_present": True}] * 100,
            "retention_peaks":  [{}] * 100,
            "motion_scores":    [{"score": 1.0}] * 100,
        })
        h = _score_signal_health(p)
        assert 0.0 <= h <= 1.0

    def test_fallback_flag_when_health_low(self):
        h = 0.25
        flags = _build_signal_flags(h, _profile())
        assert flags["fallback_active"] is True
        assert flags["signal_mode"] == "fallback"

    def test_normal_mode_when_health_ok(self):
        p = _profile({
            "emotional_spikes": [{}] * 3,
            "subject_tracking": [{"face_present": True}] * 3,
            "retention_peaks":  [{}] * 5,
            "motion_scores":    [{"score": 0.5}] * 5,
        })
        h = _score_signal_health(p)
        flags = _build_signal_flags(h, p)
        if h >= 0.4:
            assert flags["fallback_active"] is False
            assert flags["signal_mode"] == "normal"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. FULL repair() ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

class TestRepairEntryPoint:

    def test_repair_never_raises_empty(self):
        result = repair({})
        assert isinstance(result, dict)
        assert "signal_health" in result
        assert "signal_flags" in result

    def test_repair_never_raises_none(self):
        result = repair(None)
        assert isinstance(result, dict)

    def test_repair_returns_valid_health_range(self):
        result = repair(_profile())
        assert 0.0 <= result["signal_health"] <= 1.0

    def test_repair_writes_to_profile_data(self):
        p = _profile()
        repair(p)
        assert "signal_health" in p
        assert "signal_flags" in p
        assert "semantic_strength" in p

    def test_repair_full_pipeline_order(self):
        """Verify all four phases executed: repair, filter, confidence, semantic."""
        p = _profile({
            "expression_moments": [{"time": 2.0, "change_intensity": 0.7}],
            "scene_boundaries":   [{"time": 5.0}],
            "candidate_moments":  [
                {"time": 1.0, "motion_score": 0.5, "face_present": True,
                 "expression_change": 0.6}
            ],
        })
        repair(p)
        m = p["candidate_moments"][0]
        assert "signal_confidence" in m, "Confidence phase did not run"
        assert "semantic_score"    in m, "Semantic phase did not run"
        assert "signal_health"     in p, "Health phase did not run"
        assert len(p["emotional_spikes"]) >= 1, "Emotion repair did not run"

    def test_repair_does_not_overwrite_real_spikes(self):
        real_spikes = [
            {"time": 1.0, "emotion_score": 0.9},
            {"time": 3.0, "emotion_score": 0.8},
        ]
        p = _profile({"emotional_spikes": list(real_spikes)})
        repair(p)
        times = [s["time"] for s in p["emotional_spikes"]]
        assert 1.0 in times and 3.0 in times


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
