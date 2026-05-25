import os
import sys
import pytest

# ensure project root is on import path (same pattern used by other tests)
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from Intelligence_Modules import smart_reuse_engine as sr
from Download_Modules import downloader


def test_check_smart_reuse_id_only_logs_no_error(tmp_path, caplog, monkeypatch):
    """Calling the reuse checker with no path should not trigger an error log.
    The function should return (None, metadata) when the database is empty.
    """
    engine = sr.get_engine()
    engine.db.clear()

    caplog.set_level("ERROR", logger="smart_reuse")
    existing, metadata = sr.check_smart_reuse("", reel_id="TESTID")

    assert existing is None
    assert metadata["final_decision"] in ("new_video",)
    # ensure no error message was logged
    assert not any("metadata_extraction_failed" in rec.message for rec in caplog.records)


def test_check_smart_reuse_id_match_without_path(tmp_path, monkeypatch):
    """If the database has an entry for the reel id we should get a reuse
    decision even when no video file is available.
    """
    engine = sr.get_engine()
    engine.db.clear()

    dummy_file = tmp_path / "video.mp4"
    dummy_file.write_bytes(b"")

    # add an entry as if the video was previously registered
    engine.db["FOOID"] = {"file": str(dummy_file), "duration": 5.0}

    existing, metadata = sr.check_smart_reuse("", reel_id="FOOID")
    assert existing == str(dummy_file)
    assert metadata["id_match"] is True
    assert metadata["final_decision"].startswith("reuse")


def test_download_video_unpacking(monkeypatch):
    """Ensure download_video unwraps the tuple returned by check_smart_reuse.
    """
    # pretend _extract_url_id returns a known ID
    monkeypatch.setattr(downloader, "_extract_url_id", lambda url: "SOMEID")

    # stub the smart reuse call to return a fake path + metadata
    def fake_reuse(path, reel_id="", **kwargs):
        assert path == ""  # should be called with empty string now
        assert reel_id == "SOMEID"
        return ("/tmp/found.mp4", {"id_match": True})
    monkeypatch.setattr(downloader, "check_smart_reuse", fake_reuse)

    # stub DownloadIndex so it doesn't interfere
    class DummyIndex:
        @staticmethod
        def find_by_id(_):
            return None
    monkeypatch.setattr(downloader, "DownloadIndex", DummyIndex)

    # call the function; other parts of download_video will run but we can stop early
    result_path, is_cached = downloader.download_video("https://example.com/test")
    assert result_path == "/tmp/found.mp4"
    assert is_cached is True
