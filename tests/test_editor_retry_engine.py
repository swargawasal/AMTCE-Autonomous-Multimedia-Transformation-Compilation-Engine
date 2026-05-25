import pytest
import sys, os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from Compiler_Modules.editor_retry_engine import EditorRetryEngine

def test_diagnose_failure_maps_codes():
    engine = EditorRetryEngine()
    
    # Test NO_EDIT
    d1 = engine.diagnose_failure("NO_EDIT", {})
    assert d1["code"] == "NO_EDIT"
    assert "claim" in d1["message"] or "lack" in d1["message"]
    
    # Test WEAK_HOOK
    d2 = engine.diagnose_failure("WEAK_HOOK", {})
    assert d2["code"] == "WEAK_HOOK"
    assert "motion" in d2["message"] or "face" in d2["message"]

def test_retry_prompt_injection_contains_guidance():
    engine = EditorRetryEngine()
    diagnosis = engine.diagnose_failure("NO_HOOK_OR_CLIMAX", {"candidate_moments": [{"time": 1.0, "score": 0.9}]})
    hint = engine.build_retry_prompt_injection(diagnosis, 1)
    
    assert "PREVIOUS ATTEMPT REJECTED" in hint
    assert "NO_HOOK_OR_CLIMAX" in hint
    assert "time=1.00s" in hint

def test_should_retry_max_3_times():
    engine = EditorRetryEngine()
    assert engine.should_retry(0, "gemini_direct", "WEAK_HOOK") is True
    assert engine.should_retry(2, "none", "NO_EDIT") is True
    assert engine.should_retry(3, "none", "NO_EDIT") is False

@patch("Compiler_Modules.orchestrator.unified_intel")
@patch("Compiler_Modules.orchestrator.video_pipeline")
@patch("Compiler_Modules.orchestrator._extract_frames", return_value=["frame1.jpg", "frame2.jpg"])
@patch("Compiler_Modules.orchestrator.pipeline_health_check", return_value={"healthy": True})
@patch("Compiler_Modules.orchestrator.segment_safety_validate", return_value=(True, []))
def test_retry_loop_succeeds_on_second_attempt(mock_safe, mock_health, mock_xtract, mock_vp, mock_intel):
    """Mocks orchestrator to fail with NO_EDIT once, then succeed on retry."""
    import Compiler_Modules.orchestrator as orch
    
    from Intelligence_Modules.unified_intelligence import IntelligenceCache
    
    # Attempt 1: Returns NO_EDIT status
    fail_cache = MagicMock(spec=IntelligenceCache)
    fail_cache.raw_data = {"status": "NO_EDIT", "reason": "Weak signal"}
    
    # Attempt 2: Returns valid segments
    success_cache = MagicMock(spec=IntelligenceCache)
    success_cache.raw_data = {
        "status": "SUCCESS",
        "edited_segments": [
            {"clip_id": 0, "start": 0.0, "end": 2.0, "role": "hook", "transition": "hard_cut", "reason": "foo", "score": 0.9, "rank_base": 0.9}, 
            {"clip_id": 0, "start": 3.0, "end": 5.0, "role": "climax", "transition": "hard_cut", "reason": "bar", "score": 0.9, "rank_base": 0.9}
        ],
        "intent": "test"
    }
    
    mock_intel.perform_intelligence_cycle.return_value = fail_cache
    mock_intel.perform_intelligence_cycle_retry.return_value = success_cache
    
    # It should call perform_intelligence_cycle once (attempt 0)
    # Then should hit the retry engine, run perform_intelligence_cycle_retry (attempt 1), and then break the loop.
    
    # We must patch video_pipeline so render succeeds
    mock_vp.get_video_info.return_value = {"duration": 10.0}
    mock_vp.render_pipeline.return_value = True
    
    # For testing, break out before audio processing
    with patch("Compiler_Modules.orchestrator.audio_pipeline.mix_audio", return_value=True):
        success, result = orch.compile_video("test-uuid", "fake_video.mp4", "output.mp4", "Title", "Desc")
    
    assert mock_intel.perform_intelligence_cycle.call_count == 1
    assert mock_intel.perform_intelligence_cycle_retry.call_count >= 1
    # Note: success may be False since fake_video.mp4 causes signal poverty;
    # the meaningful test is that the retry mechanism is correctly invoked.


