import pytest
from Visual_Refinement_Modules.rhythm_quality_guard import (
    VariableSlotSelector,
    EnergyProgressionGuard,
    DynamicTrimCalculator
)

class TestVariableSlotSelector:
    def test_minimal_template(self):
        # Few candidates or very short
        slots = VariableSlotSelector.choose(candidate_count=3, duration=4.0)
        assert len(slots) == 3
        assert slots == [("hook", 1), ("build", 1), ("climax", 1)]

    def test_standard_template(self):
        # 5 candidates, moderate length
        slots = VariableSlotSelector.choose(candidate_count=5, duration=15.0)
        assert len(slots) == 5
        assert slots == [("hook", 1), ("reaction", 1), ("build", 1), ("climax", 1), ("resolution", 1)]
        
    def test_rich_template(self):
        # Many candidates, long video
        slots = VariableSlotSelector.choose(candidate_count=10, duration=30.0)
        # Should max out build at 5 -> 1 hook, 1 reaction, 5 build, 1 climax, 1 resolution
        # Count = 9 slots total (wait, the returned list has ("build", 5))
        assert dict(slots)["build"] == 5

class TestEnergyProgressionGuard:
    def test_valid_progression(self):
        story_map = [
            {"role": "build", "composite_score": 0.5},
            {"role": "build", "composite_score": 0.6},
            {"role": "climax", "composite_score": 0.8}
        ]
        result = EnergyProgressionGuard.validate_and_fix(story_map)
        # Should remain unchanged
        assert result[2]["role"] == "climax"
        assert result[2]["composite_score"] == 0.8

    def test_invalid_progression_swaps(self):
        # Climax is weaker than builds
        story_map = [
            {"role": "build", "composite_score": 0.5},
            {"role": "build", "composite_score": 0.9}, # Strongest build
            {"role": "climax", "composite_score": 0.6}
        ]
        result = EnergyProgressionGuard.validate_and_fix(list(story_map))
        
        # After sorting and swap, climax should now have 0.9 score
        climax = next((s for s in result if s["role"] == "climax"), None)
        assert climax is not None
        assert climax["composite_score"] == 0.9
        
        # Original climax should now be a build
        builds = [s for s in result if s["role"] == "build"]
        assert len(builds) == 2
        build_scores = [b["composite_score"] for b in builds]
        assert 0.6 in build_scores

class TestDynamicTrimCalculator:
    def test_hook_trim(self):
        moment = {"role": "hook"}
        pre, post = DynamicTrimCalculator.compute(moment)
        # Default hook: 0.3, 1.2
        assert pre == 0.3
        assert post == 1.2

    def test_beat_aligned_trim(self):
        moment = {"role": "build", "beat_aligned": True}
        pre, post = DynamicTrimCalculator.compute(moment)
        # Default build 1.5, cut by beat -> max(0.1, 1.5 - 0.3) = 1.2
        assert pre == 1.2
        assert moment["trim_tightness"] == "tight"

    def test_face_and_emotion_trim(self):
        moment = {"role": "build", "face_present": True}
        pre, post = DynamicTrimCalculator.compute(moment)
        # Diffuse padding for face
        assert post == 2.9  # base 2.5 + 0.4
        assert moment["trim_tightness"] == "diffuse"
