import os
import shutil
import time
import json
import pytest
import numpy as np
from pathlib import Path
from Audio_Modules.audio_pool_manager import AudioPoolManager
from Audio_Modules.music_manager import ContinuousMusicManager

@pytest.fixture
def temp_pool(tmp_path):
    """Fixture to create a temporary audio pool directory structure."""
    base = tmp_path / "Original_audio"
    base.mkdir()
    (base / "active").mkdir()
    (base / "cooldown").mkdir()
    (base / "beats").mkdir()
    
    manager = AudioPoolManager(base_dir=str(base))
    return manager, base

def test_process_new_audio_v2(temp_pool):
    manager, base = temp_pool
    
    dummy_audio = base / "test_audio.mp3"
    dummy_audio.write_text("dummy mp3 content")
    
    analysis = {
        "beats": [
            {"time": 0.5, "energy": 0.5},
            {"time": 1.0, "energy": 0.8},
            {"time": 1.5, "energy": 0.9}
        ],
        "drops": [1.5]
    }
    
    manager.process_new_audio(str(dummy_audio), bpm=120.0, energy=0.75, beat_analysis=analysis)
    
    # Metadata checks
    assert manager.metadata["version"] == 2
    meta = manager._get_file_metadata("test_audio.mp3")
    
    assert meta["bpm"] == 120.0
    assert meta["version"] == 2
    assert "audio_hash" in meta
    
    import os
    # The file has been moved to the active pool
    moved_audio = base / "active" / "test_audio.mp3"
    expected_hash = f"{os.path.getsize(moved_audio)}_{int(os.path.getmtime(moved_audio))}"
    assert meta["audio_hash"] == expected_hash
    
    assert len(meta["drop_times"]) == 1
    assert meta["drop_times"][0] == pytest.approx(1.5)
    
    # Binary check
    npz_path = base / meta["beat_data_path"]
    assert npz_path.exists()
    
    with np.load(npz_path) as data:
        assert "times" in data
        assert "energies" in data
        
        assert len(data["times"]) == 3
        assert len(data["times"]) == len(data["energies"])
        
        assert data["times"][1] == pytest.approx(1.0)
        assert data["energies"][2] == pytest.approx(0.9)
        
        # Quantization validation
        assert data["times"][1] == pytest.approx(round(1.0, 3))
        
        # Drop alignment
        assert meta["drop_times"][0] in data["times"]

def test_lazy_loading_and_cache(temp_pool):
    manager, base = temp_pool
    dummy_audio = base / "test_audio.mp3"
    dummy_audio.write_text("content")
    
    analysis = {"beats": [{"time": 1.0, "energy": 0.5}], "drops": []}
    manager.process_new_audio(str(dummy_audio), 120.0, 0.5, analysis)
    
    # Initial load (should hit disk)
    data1 = manager.get_beat_data("test_audio.mp3")
    assert data1 is not None
    assert "test_audio.mp3" in manager._beat_cache
    
    # Second load (should hit memory)
    # We can verify by deleting the npz file and seeing if it still returns data
    os.remove(base / manager._get_file_metadata("test_audio.mp3")["beat_data_path"])
    data2 = manager.get_beat_data("test_audio.mp3")
    assert data2 == data1

def test_hash_integrity_detection(temp_pool):
    manager, base = temp_pool
    dummy_audio = base / "test_audio.mp3"
    dummy_audio.write_text("content version 1")
    
    manager.process_new_audio(str(dummy_audio), 120.0, 0.5)
    old_hash = manager._get_file_metadata("test_audio.mp3")["audio_hash"]
    
    # Modify file
    time.sleep(1.1) # Ensure mtime changes
    final_path = base / "active" / "test_audio.mp3"
    final_path.write_text("content version 2 - modified")
    
    new_hash = manager._calculate_hash(str(final_path))
    assert new_hash != old_hash

def test_maintenance_orphan_cleanup(temp_pool):
    manager, base = temp_pool
    
    # Create orphaned npz
    orphan_path = base / "beats" / "orphan.npz"
    np.savez_compressed(orphan_path, times=[1, 2], energies=[0.5, 0.5])
    
    # Create valid npz
    dummy = base / "active" / "valid.mp3"
    dummy.write_text("...")
    manager.process_new_audio(str(dummy), 120, 0.5, {"beats": [{"time": 1, "energy": 1}], "drops": []})
    
    manager.maintenance()
    
    assert not orphan_path.exists()
    assert (base / "beats" / "valid.npz").exists()

def test_schema_backward_compatibility(temp_pool):
    manager, base = temp_pool
    
    # Manually insert V1 style metadata (flat)
    manager.metadata = {
        "v1_audio.mp3": {"bpm": 120, "energy": 0.5, "usage_count": 0, "last_used": 0}
    }
    manager._save_metadata()
    
    # Loading should auto-migrate or helper should handle it
    meta = manager._get_file_metadata("v1_audio.mp3")
    assert meta is not None
    assert meta["bpm"] == 120

def test_zombie_protection_root_ignored(tmp_path):
    """Verifies that files in the root Original_audio folder are NEVER loaded into the playlist."""
    base = tmp_path / "Original_audio"
    base.mkdir()
    active = base / "active"
    active.mkdir()
    
    # 1. Place file in root
    (base / "root_zombie.mp3").write_text("zombie" * 200) # > 1KB
    # 2. Place file in active
    (active / "active_human.mp3").write_text("human human" * 100) # > 1KB
    
    # Initialize Music Manager pointing to root
    mm = ContinuousMusicManager(music_dir=str(base))
    
    # Check playlist
    human_in = False
    for p in mm.playlist:
        assert isinstance(p, Path), "Playlist entries must be Path objects"
        assert p.name != "root_zombie.mp3", "❌ Root file (zombie) was incorrectly loaded!"
        if p.name == "active_human.mp3":
            human_in = True
            
    assert human_in, "Active file should be in the playlist"
    # Verification of redirection
    assert "active" in str(mm.playlist[0]), "❌ MusicManager is not pointing to the active subfolder!"

def test_empty_active_pool_contract(tmp_path):
    """Verifies the strict active-only contract: empty active folder = empty playlist."""
    base = tmp_path / "Original_audio"
    base.mkdir()
    active = base / "active"
    active.mkdir()
    
    # root has files, active is empty
    (base / "potential_zombie.mp3").write_text("zombie")
    
    mm = ContinuousMusicManager(music_dir=str(base))
    
    assert len(mm.playlist) == 0, "Playlist must be empty if the active pool is empty"
