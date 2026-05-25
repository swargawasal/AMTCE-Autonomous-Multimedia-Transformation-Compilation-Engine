import pytest

def test_track_latency_exists():
    """Health check for the latency tracker."""
    try:
        from Audio_Modules.audio_deduplicator import track_latency
        assert track_latency is not None
    except ImportError:
        pytest.fail("Could not import track_latency")

def test_track_latency_runs():
    """Ensure the tracker returns a numerical value."""
    from Audio_Modules.audio_deduplicator import track_latency
    res = track_latency()
    assert isinstance(res, (int, float))
