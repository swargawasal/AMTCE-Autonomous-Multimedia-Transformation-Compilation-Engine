"""Golden tests for EditorBrain editing decisions."""

import pytest

from Intelligence_Modules.editor_brain import EditorBrain
from Content_Intelligence.persona_engine import load_personas
from Visual_Refinement_Modules.style_validator import StyleValidator


def _sample_moments_high_energy():
    return [
        {"start": 0.0, "end": 1.0, "energy_level": 0.9, "motion_intensity": 0.8, "emotion_score": 0.6, "importance": 0.9},
        {"start": 1.0, "end": 2.2, "energy_level": 0.7, "motion_intensity": 0.7, "emotion_score": 0.5, "importance": 0.7},
    ]


def _sample_moments_low_energy():
    return [
        {"start": 0.0, "end": 2.5, "energy_level": 0.2, "motion_intensity": 0.2, "emotion_score": 0.4, "importance": 0.4},
        {"start": 2.5, "end": 5.0, "energy_level": 0.3, "motion_intensity": 0.25, "emotion_score": 0.3, "importance": 0.3},
    ]


def test_hype_persona_selection():
    eb = EditorBrain()
    res = eb.process_moments(_sample_moments_high_energy())
    assert res["persona"] == load_personas()["HYPE"].name
    assert res["edl"]["segments"], "EDL should contain segments"


def test_aesthetic_persona_selection():
    eb = EditorBrain()
    res = eb.process_moments(_sample_moments_low_energy())
    assert res["persona"] == load_personas()["AESTHETIC"].name


def test_edl_generation_segment_count_and_fields():
    eb = EditorBrain()
    res = eb.process_moments(_sample_moments_high_energy())
    segments = res["edl"]["segments"]
    assert len(segments) == 2
    for seg in segments:
        assert "duration" in seg
        assert "transition" in seg
        assert "caption_style" in seg


def test_style_validation_passes_for_generated_edl():
    eb = EditorBrain()
    moments = _sample_moments_high_energy()
    res = eb.process_moments(moments)
    persona = load_personas()[res["persona"]]
    validator = StyleValidator()
    validation = validator.validate_all(res["edl"], persona)
    assert validation["valid"]
    assert validation["score"] <= 1.0


def test_style_validation_flags_caption_overload():
    eb = EditorBrain()
    moments = _sample_moments_high_energy()
    res = eb.process_moments(moments)
    persona = load_personas()[res["persona"]]
    validator = StyleValidator()

    # Force captions on every segment to exceed bold threshold.
    for seg in res["edl"]["segments"]:
        seg["captions"] = "forced caption"

    validation = validator.validate_all(res["edl"], persona)
    assert not validation["valid"]
    assert any("Caption density" in issue for issue in validation["issues"])

