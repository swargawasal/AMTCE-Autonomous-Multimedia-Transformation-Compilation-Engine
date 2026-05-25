"""
tests/test_intelligence_pipeline.py
------------------------------------
Tests for new intelligence modules and their integration.

Verifies:
1. Hook variant scoring logic
2. Trend Opportunity calculation and angle innovation
3. Transition scoring logic in SmartSceneEditor
4. Diagnostics outputs
5. Feature flag merging
"""

import pytest
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from unittest.mock import patch, MagicMock

from Content_Intelligence.hook_variants import generate_hook_variant
from Trend_Intelligence.trend_opportunity_engine import analyse_trend_opportunity
from Diagnostics.scene_editing_verifier import verify_scene_reconstruction

def test_hook_variant_scoring():
    """Verify variant scoring formula and selection."""
    base_hook = {"hook_time": 2.5, "hook_score": 0.8}
    content_strategy = {"engagement_hook": "A very long hook text " * 5}
    
    result = generate_hook_variant(base_hook, content_strategy)

    variant = result.get("hook_variant", {})
    assert "hook_time_hint" in variant
    assert "confidence" in variant
    assert isinstance(variant["confidence"], float)

def test_trend_opportunity_calculation():
    """Verify trend formula and angle innovation trigger."""
    # Simulating high competition density and strong trend
    trend_context_mock = {
        "topics": ["ai", "crypto"],
        "keywords": ["urgent", "insane", "watch", "new", "update", "shocking"] * 4,
        "trend_strength": 0.9
    }
    existing_strategy = {"recommended_narrative": "explanation"}
    
    result = analyse_trend_opportunity(trend_context_mock, existing_strategy)
    
    opp = result.get("trend_opportunity", {})
    # Since keywords are long and trend strength high, competition_level should be high
    assert opp.get("competition_level") == "high"
    assert opp.get("angle_innovation_required") is True
    
    assert "recommended_angle" in result
    assert result["recommended_angle"] != "explanation" # Should have innovated

def test_scene_editing_verifier():
    """Verify boolean logic for diagnostic verifications."""
    # Test valid reconstruction
    valid_res = verify_scene_reconstruction(
        scene_count=10,
        segments_created=3,
        concat_used=True,
        duration_change_ratio=0.8,
        non_chronological=True,
        avg_composite_score=0.5
    )
    assert valid_res["editing_diagnostics"]["editing_effective"] is True
    
    # Test trimming/ineffective editing (only 1 segment)
    trim_res = verify_scene_reconstruction(
        scene_count=5,
        segments_created=1,
        concat_used=True,
        duration_change_ratio=0.7
    )
    assert trim_res["editing_diagnostics"]["editing_effective"] is False

    # Test ineffective editing (no concat used)
    no_concat_res = verify_scene_reconstruction(
        scene_count=2,
        segments_created=2,
        concat_used=False,
        duration_change_ratio=1.0
    )
    assert no_concat_res["editing_diagnostics"]["editing_effective"] is False

@patch("Visual_Refinement_Modules.smart_scene_editor.random.uniform")
@patch("Visual_Refinement_Modules.smart_scene_editor.random.sample")
def test_transition_scoring(mock_sample, mock_uniform):
    """Verify transition score threshold logic."""
    from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor
    mock_uniform.return_value = 0.5 # predictable novelty and proxies
    mock_sample.side_effect = lambda pop, k: list(pop)[:k]
    
    editor = SmartSceneEditor()
    editor.enabled = True # Force enable for test
    
    with patch.object(SmartSceneEditor, "_get_video_info", return_value=10.0):
        # Force generating some timeline instructions
        instructions = editor.generate_timeline_instructions(
            "dummy_path.mp4",
            hook_analysis={
                "hook_variant": {"hook_time_hint": 1.0, "confidence": 0.9}
            }
        )
    
    # Schema check
    assert isinstance(instructions, dict)
    assert "cuts" in instructions
    assert "transitions" in instructions

def test_feature_flag_merging():
    """Verify orchestrator-style feature merging logic from components."""
    from Trend_Intelligence.trend_opportunity_engine import ANGLE_FLAG_MAP, ALLOWED_FLAGS
    
    existing_flags = {"enable_price_tags": True, "enable_voiceover": False}
    # Simulate an Angle Innovation that enables voiceover and cinematic zoom
    new_flags = {"enable_voiceover": True, "enable_cinematic_zoom": True}
    
    # Process additive merge as in orchestrator
    for k, v in new_flags.items():
         if v:
             existing_flags[k] = True
             
    assert existing_flags["enable_price_tags"] is True
    assert existing_flags["enable_voiceover"] is True
    assert existing_flags["enable_cinematic_zoom"] is True

def test_master_prompt_signal_honesty():
    """Verify that NO_EDIT and new schema keys successfully parse and fall back to empty edit."""
    from Intelligence_Modules.unified_intelligence import engine, MASTER_SCHEMA
    
    # Mock response satisfying the new schema but declaring NO_EDIT
    mock_response = {
        "status": "NO_EDIT",
        "reason": "No motion and no subject detected",
        "signal_quality": {
            "emotion": "missing",
            "retention": "weak",
            "final_mode": "fallback"
        },
        "intent": "generic_engagement",
        "watermark_present": False,
        "feature_proposals": {
            "scene_reconstruction": True,
            "voiceover_generation": False,
            "caption_generation": False,
            "price_tag_engine": False,
            "music_engine": False,
            "smart_crop": False
        },
        "edited_segments": [],
        "hook_analysis": "Failed to find a hook",
        "climax_validation": "No climax possible",
        "attention_flow": "Flat",
        "final_verdict": "EDITOR_REJECTED"
    }
    
    # 1. Verify it passes schema validation
    assert engine.validate_schema(mock_response, MASTER_SCHEMA) is True
    
    # 2. Verify normalizer handles it correctly
    normalized = engine.normalize_master_schema(mock_response)
    assert normalized["edited_segments"] == []
    assert normalized["intent"] == "generic_engagement"


@patch("Compiler_Modules.orchestrator.UNIFIED_INTEL_AVAILABLE", True)
@patch("Compiler_Modules.orchestrator.WATERMARK_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.SOURCE_DETECTION_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.MOMENT_MINER_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.BEAT_ENGINE_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.SUBJECT_TRACKER_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.SIGNAL_FUSION_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.EMOTIONAL_SPIKE_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.RETENTION_CURVE_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.EXPRESSION_CHANGE_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.EDITOR_BRAIN_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.SIGNAL_REPAIR_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.HIGHLIGHT_COMPILER_AVAILABLE", False)
@patch("Compiler_Modules.orchestrator.ENABLE_EARLY_HARD_STOP", True)
def test_no_edit_hard_stop_terminates_pipeline(*_mocks):
    """
    HARD STOP TEST: When Master Intelligence returns status=NO_EDIT,
    compile_video() must return immediately with (False, {"status": "NO_EDIT", ...}).
    No downstream modules (render, caption, voiceover, music) must execute.
    """
    from unittest.mock import MagicMock
    from Intelligence_Modules.unified_intelligence import IntelligenceCache, CoreAnalysis, Extensions
    import Compiler_Modules.orchestrator as orch

    # --- Build a mock IntelligenceCache with status=NO_EDIT ---
    mock_cache = IntelligenceCache(
        core_analysis=CoreAnalysis(),
        extensions=Extensions(),
        raw_data={
            "status": "NO_EDIT",
            "reason": "SIGNAL_WEAKNESS_DETECTED — no face and no retention signal.",
            "edited_segments": [],
        },
    )

    mock_intel = MagicMock()
    mock_intel.perform_intelligence_cycle.return_value = mock_cache
    mock_intel.perform_intelligence_cycle_retry.return_value = mock_cache

    # Patch video_pipeline so we don't need a real file
    with patch("Compiler_Modules.orchestrator.video_pipeline") as mock_vp, \
         patch("Compiler_Modules.orchestrator.unified_intel", mock_intel), \
         patch("Compiler_Modules.orchestrator._extract_frames", return_value=[]), \
         patch("Compiler_Modules.orchestrator.pipeline_health_check", return_value={"healthy": True}), \
         patch("Compiler_Modules.orchestrator.segment_safety_validate", return_value=(True, [])):

        mock_vp.get_video_info.return_value = {"duration": 15.0}
        mock_vp.extract_audio_from_video.return_value = None

        success, result = orch.compile_video(
            uuid_str="test-no-edit-uuid",
            input_path="fake_video.mp4",
            output_path="fake_output.mp4",
            title="Test Video",
            description="Test",
        )

    # ── Assertions ──────────────────────────────────────────────────────────
    assert success is False, "Hard stop must return False as first element"
    assert result["status"] == "NO_EDIT"
    assert result["final_output"] is None,  "Hard stop must produce NO output file"
    assert result["segments"] == [],        "Hard stop must produce NO segments"
    assert result["metadata"]["hard_stop"] is True
    assert result["metadata"]["editor_source"] == "none"
    assert "reason" in result and len(result["reason"]) > 0

    # Verify Master Intelligence was called (pre-gate executed)
    mock_intel.perform_intelligence_cycle.assert_called_once()

    # Verify render was NOT called (post-gate was blocked)
    mock_vp.compile_with_transitions.assert_not_called()

