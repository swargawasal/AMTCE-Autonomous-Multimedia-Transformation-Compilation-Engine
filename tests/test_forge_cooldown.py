import pytest
import time
from Intelligence_Modules.vanguard_forge import vanguard_forge

def test_forge_cooldown_enforcement():
    """Verify that a file cannot be forged twice in 5 minutes."""
    target = "Intelligence_Modules/audio_deduplicator.py"
    
    # 1. First forge (Success)
    allowed, msg = vanguard_forge.can_forge(target)
    assert allowed
    
    # Simulate a forge attempt
    vanguard_forge.FORGE_COOLDOWN[target] = time.time()
    
    # 2. Second forge (Fail due to cooldown)
    allowed, msg = vanguard_forge.can_forge(target)
    assert not allowed
    assert "COOLDOWN" in msg
    
    # 3. Manual Reset (for testing)
    vanguard_forge.FORGE_COOLDOWN[target] = time.time() - 400
    allowed, msg = vanguard_forge.can_forge(target)
    assert allowed
