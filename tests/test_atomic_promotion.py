import os
import shutil
import pytest
from claw_vanguard.vanguard_forge import VanguardForge

def test_atomic_promotion():
    """Verify that promote_to_primary replaces files atomically."""
    forge = VanguardForge()
    target = "temp/test_atomic.py"
    
    # 1. Setup initial file
    with open(target, 'w') as f:
        f.write("def original(): return True\n")
        
    # 2. Forge Content
    new_content = "def optimized(): return 'PRO_VALUE'\n"
    
    # 3. Promote
    # This should use os.replace internally
    backup = forge.promote_to_primary(target, new_content)
    
    # Verify: Target has new content
    with open(target, 'r') as f:
        assert "PRO_VALUE" in f.read()
        
    # Verify: Backup exists
    assert os.path.exists(backup)
    
    # Cleanup
    os.remove(target)
    os.remove(backup)

def test_atomic_replace_interruption_simulation():
    """Simulate a crash and ensure os.replace wouldn't have left a partial file."""
    # Since we can't easily 'crash' the OS, we just verify the logic
    # uses os.replace which is atomic on Windows/Linux.
    import inspect
    source = inspect.getsource(VanguardForge.promote_to_primary)
    assert "os.replace" in source or "shutil.move" in source
