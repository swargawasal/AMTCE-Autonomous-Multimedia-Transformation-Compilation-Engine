"""
tests/test_attention_editor.py
===============================
Validation tests for Attention-Driven Editing. 
(Re-written for Phase 5 Deep Intelligence where attention is extracted via SubjectTracker and MomentMiner)

Run with:
    cd "d:\\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    python -m pytest tests/test_attention_editor.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Visual_Refinement_Modules.moment_miner import MomentMiner, run_moment_miner

# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — No Subject Tracking Data
# ══════════════════════════════════════════════════════════════════════════════
def test_no_faces_produces_no_appearance_moments():
    miner = MomentMiner({"subject_tracking": []})
    moments = miner._detect_face_moments()
    assert len(moments) == 0, "Expected empty appearance moments when no faces are tracked"

# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — Synthetic Face Appearance
# ══════════════════════════════════════════════════════════════════════════════
def test_synthetic_face_appearance():
    """
    If the tracker finds a large face, it should record an 'appearance' moment.
    """
    profile = {
        "subject_tracking": [
            {"time": 2.5, "bbox": [100, 100, 500, 500]} # Large face = 160k px area
        ]
    }
    miner = MomentMiner(profile)
    moments = miner._detect_face_moments()
    
    assert len(moments) == 1
    assert moments[0]["time"] == 2.5
    assert moments[0]["type"] == "appearance"
    assert moments[0]["face_present"] is True

# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — Cooldown and Limit Enforcement Testing
# ══════════════════════════════════════════════════════════════════════════════
def test_attention_cooldown_and_limit():
    """
    Ensure the attention events are rate-limited by the specified cooldown in deduplication.
    """
    miner = MomentMiner({})
    raw_moments = [
        {"time": 1.0, "score": 0.9, "type": "appearance"},
        {"time": 1.5, "score": 0.8, "type": "appearance"}, # Within 1.5s (min_gap), should be suppressed
        {"time": 5.0, "score": 0.7, "type": "reaction"},
    ]
    
    filtered = miner._deduplicate_moments(raw_moments, min_gap=1.5)
    
    assert len(filtered) == 2
    assert filtered[0]["time"] == 1.0
    assert filtered[1]["time"] == 5.0

# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — Pipeline Compatibility Filter Check
# ══════════════════════════════════════════════════════════════════════════════
def test_pipeline_includes_attention_schema():
    """
    Ensure the moment miner outputs the correct schema for attention/face events.
    """
    profile = {
        "subject_tracking": [
            {"time": 3.0, "bbox": [100, 100, 500, 500]} 
        ]
    }
    candidate_moments = run_moment_miner(profile)
    
    assert isinstance(candidate_moments, list)
    assert len(candidate_moments) > 0
    # The output schema finalizes the moments
    has_appearance = any(m.get("type") == "appearance" for m in candidate_moments)
    assert has_appearance, "MomentMiner did not output an appearance moment schema"
