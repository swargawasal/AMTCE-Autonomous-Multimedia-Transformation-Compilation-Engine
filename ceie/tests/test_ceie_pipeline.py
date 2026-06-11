"""
ceie/tests/test_ceie_pipeline.py
---------------------------------
Unit tests for the CEIE pipeline covering:
- Chunk boundary determination
- Timeline globalization & flattening
- Applicator keep-range calculation
- Schema validation
"""

import pytest
import sys
import os

# Ensure the AMTCE root is on PYTHONPATH for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# --- Isolated imports that don't need the governor or GPU ---

from ceie.chunker import determine_chunk_boundaries
from ceie.aggregator import globalize_plan, flatten_timeline
from ceie.models.edit_schema import (
    ChunkEditBlueprint, MasterEditPlan, ContextHandoff,
    Cut, Trim, SpeedRamp, Transition, TextOverlay,
    VoiceoverSegment, KaraokeSegment, ZoomFocus
)


# =============================================================================
# Helper: Build a minimal valid ChunkEditBlueprint
# =============================================================================
def _make_blueprint(chunk_index: int, start: float, end: float) -> ChunkEditBlueprint:
    return ChunkEditBlueprint(
        chunk_index=chunk_index,
        chunk_start_sec=start,
        chunk_end_sec=end,
        chapter_role="rising_action",
        emotional_arc="building",
        hook_strength="medium",
        energy_score=0.6,
        cuts=[Cut(at_sec=10.0, type="hard_cut", reason="pace cut")],
        trims=[Trim(start_sec=15.0, end_sec=18.0, action="remove", reason="shaky cam")],
        speed_ramps=[SpeedRamp(start_sec=5.0, end_sec=8.0, factor=2.0, reason="speed up intro")],
        transitions=[Transition(at_sec=25.0, type="slide_left", duration_ms=400, engine="xfade", reason="energy shift")],
        text_overlays=[TextOverlay(at_sec=2.0, duration_sec=3.0, text="Hook!", lane="title", style="hype")],
        karaoke_segments=[KaraokeSegment(start_sec=30.0, end_sec=35.0, reason="highlight")],
        voiceover_segments=[VoiceoverSegment(insert_at_sec=0.0, script="Watch this.", tone="hype")],
        zoom_focus=[ZoomFocus(at_sec=20.0, duration_sec=2.0, target="face", zoom_level=1.4)],
        pacing_notes="Fast pacing with rhythm-driven edits.",
        yt_transformative_value="high",
        yt_transformative_notes="Commentary + transformative edit.",
        context_handoff=ContextHandoff(
            story_arc_so_far="Rising action begins.",
            pacing_momentum="building",
            narrative_thread="Subject intensifies",
            chapter=chunk_index + 1,
            chapters_total=3
        )
    )


# =============================================================================
# Test: Chunk boundary determination
# =============================================================================
class TestChunkBoundaries:

    def test_empty_shots_uniform_split(self):
        boundaries = determine_chunk_boundaries([], 180.0, 60.0)
        assert len(boundaries) == 3
        assert boundaries[0] == (0.0, 60.0)
        assert boundaries[1] == (60.0, 120.0)
        assert boundaries[2] == (120.0, 180.0)

    def test_short_video_single_chunk(self):
        boundaries = determine_chunk_boundaries([], 45.0, 60.0)
        assert len(boundaries) == 1
        assert boundaries[0][0] == 0.0
        assert boundaries[0][1] == 45.0

    def test_shots_snap_to_boundary(self):
        shots = [
            {"start": 0.0, "end": 55.0},
            {"start": 55.0, "end": 65.0},
            {"start": 65.0, "end": 120.0},
        ]
        boundaries = determine_chunk_boundaries(shots, 150.0, 60.0)
        # Should generate at least 2 chunks
        assert len(boundaries) >= 2
        # All boundaries should be contiguous
        for i in range(len(boundaries) - 1):
            assert boundaries[i][1] == boundaries[i+1][0]
        # Coverage: first starts at 0, last ends at total_duration
        assert boundaries[0][0] == 0.0
        assert boundaries[-1][1] == 150.0


# =============================================================================
# Test: Timeline globalization
# =============================================================================
class TestGlobalization:

    def _make_master_plan(self) -> MasterEditPlan:
        bp0 = _make_blueprint(0, 0.0, 60.0)
        bp1 = _make_blueprint(1, 60.0, 120.0)
        return MasterEditPlan(
            video_path="test.mp4",
            total_duration_sec=120.0,
            total_chunks=2,
            chunks=[bp0, bp1],
            global_narrative="Test narrative",
            yt_eligibility="eligible",
            yt_eligibility_notes="Test eligible"
        )

    def test_globalize_chunk0_unchanged(self):
        plan = self._make_master_plan()
        global_plan = globalize_plan(plan)
        chunk0 = global_plan.chunks[0]
        # Chunk 0 has offset 0, so timestamps should stay the same
        assert chunk0.cuts[0].at_sec == 10.0
        assert chunk0.trims[0].start_sec == 15.0
        assert chunk0.transitions[0].at_sec == 25.0
        assert chunk0.voiceover_segments[0].insert_at_sec == 0.0

    def test_globalize_chunk1_shifted_by_60(self):
        plan = self._make_master_plan()
        global_plan = globalize_plan(plan)
        chunk1 = global_plan.chunks[1]
        # Chunk 1 has offset 60.0
        assert chunk1.cuts[0].at_sec == pytest.approx(10.0 + 60.0)
        assert chunk1.trims[0].start_sec == pytest.approx(15.0 + 60.0)
        assert chunk1.transitions[0].at_sec == pytest.approx(25.0 + 60.0)
        assert chunk1.voiceover_segments[0].insert_at_sec == pytest.approx(0.0 + 60.0)


# =============================================================================
# Test: Timeline flattening
# =============================================================================
class TestFlatten:

    def test_flatten_keys(self):
        bp = _make_blueprint(0, 0.0, 60.0)
        plan = MasterEditPlan(
            video_path="test.mp4",
            total_duration_sec=60.0,
            total_chunks=1,
            chunks=[bp]
        )
        global_plan = globalize_plan(plan)
        timeline = flatten_timeline(global_plan)

        assert "cuts" in timeline
        assert "trims" in timeline
        assert "speed_ramps" in timeline
        assert "transitions" in timeline
        assert "text_overlays" in timeline
        assert "karaoke_segments" in timeline
        assert "voiceover_segments" in timeline
        assert "zoom_focus" in timeline

    def test_flatten_sorted_by_timestamp(self):
        bp0 = _make_blueprint(0, 0.0, 60.0)
        bp1 = _make_blueprint(1, 60.0, 120.0)
        plan = MasterEditPlan(
            video_path="test.mp4",
            total_duration_sec=120.0,
            total_chunks=2,
            chunks=[bp0, bp1]
        )
        global_plan = globalize_plan(plan)
        timeline = flatten_timeline(global_plan)

        # Cuts should be chronological
        cut_times = [c["at_sec"] for c in timeline["cuts"]]
        assert cut_times == sorted(cut_times)

        # Trims should be chronological
        trim_times = [t["start_sec"] for t in timeline["trims"]]
        assert trim_times == sorted(trim_times)


# =============================================================================
# Test: Applicator keep-range computation
# =============================================================================
class TestApplicatorKeepRanges:

    def test_no_trims_returns_full_video(self):
        from ceie.tools.cutter import trims_to_keep_ranges
        keep = trims_to_keep_ranges(total_duration=60.0, trims=[])
        assert len(keep) == 1
        assert keep[0]["start"] == 0.0
        assert keep[0]["end"] == 60.0

    def test_single_trim_middle(self):
        from ceie.tools.cutter import trims_to_keep_ranges
        trims = [{"start_sec": 10.0, "end_sec": 20.0, "action": "remove", "reason": "test"}]
        keep = trims_to_keep_ranges(total_duration=60.0, trims=trims)
        assert len(keep) == 2
        assert keep[0] == {"start": 0.0, "end": 10.0}
        assert keep[1] == {"start": 20.0, "end": 60.0}

    def test_multiple_trims(self):
        from ceie.tools.cutter import trims_to_keep_ranges
        trims = [
            {"start_sec": 5.0,  "end_sec": 10.0, "action": "remove", "reason": "filler"},
            {"start_sec": 30.0, "end_sec": 35.0, "action": "remove", "reason": "shaky"},
        ]
        keep = trims_to_keep_ranges(total_duration=60.0, trims=trims)
        assert len(keep) == 3
        assert keep[0] == {"start": 0.0, "end": 5.0}
        assert keep[1] == {"start": 10.0, "end": 30.0}
        assert keep[2] == {"start": 35.0, "end": 60.0}

    def test_trim_at_start(self):
        from ceie.tools.cutter import trims_to_keep_ranges
        trims = [{"start_sec": 0.0, "end_sec": 5.0, "action": "remove", "reason": "intro junk"}]
        keep = trims_to_keep_ranges(total_duration=60.0, trims=trims)
        # Should only be 1 range starting from 5.0
        assert len(keep) == 1
        assert keep[0]["start"] == pytest.approx(5.0)

    def test_trim_at_end(self):
        from ceie.tools.cutter import trims_to_keep_ranges
        trims = [{"start_sec": 55.0, "end_sec": 60.0, "action": "remove", "reason": "outro junk"}]
        keep = trims_to_keep_ranges(total_duration=60.0, trims=trims)
        assert len(keep) == 1
        assert keep[0]["end"] == pytest.approx(55.0)


# =============================================================================
# Test: Schema validation with Pydantic
# =============================================================================
class TestSchemaValidation:

    def test_valid_blueprint_no_error(self):
        bp = _make_blueprint(0, 0.0, 60.0)
        assert bp.chunk_index == 0
        assert bp.energy_score == 0.6

    def test_energy_score_out_of_range_raises(self):
        with pytest.raises(Exception):
            ChunkEditBlueprint(
                chunk_index=0,
                chunk_start_sec=0.0,
                chunk_end_sec=60.0,
                energy_score=1.5,  # Out of range
                context_handoff=ContextHandoff(story_arc_so_far="test")
            )

    def test_zoom_level_out_of_range_raises(self):
        with pytest.raises(Exception):
            ZoomFocus(at_sec=5.0, duration_sec=2.0, zoom_level=5.0)  # Max is 3.0

    def test_speed_ramp_factor_range(self):
        ramp = SpeedRamp(start_sec=0.0, end_sec=5.0, factor=0.5, reason="slow motion")
        assert ramp.factor == 0.5
        with pytest.raises(Exception):
            SpeedRamp(start_sec=0.0, end_sec=5.0, factor=0.1, reason="too slow")  # Min is 0.25


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
