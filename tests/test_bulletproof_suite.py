import sys
import os
import time
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

# Add workspace to path
sys.path.append(os.getcwd())

from Intelligence_Modules.gemini_governor import GeminiGovernor

@pytest.fixture
def governor():
    # Use a fresh singleton or reset the existing one for testing
    gov = GeminiGovernor()
    # Reset states for testing
    with gov.state_lock:
        for name, state in gov.model_states.items():
            state["status"] = "ACTIVE"
            state["fail_count"] = 0
            state["banned_until"] = None
        gov.global_cooldown_until = 0
        gov.last_successful_model = None
    return gov

def test_success_reset(governor):
    model = "gemini-2.0-flash"
    governor.mark_model_banned(model, error_type="429")
    governor.mark_model_banned(model, error_type="429")
    
    with governor.state_lock:
        assert governor.model_states[model]["fail_count"] == 2
        assert governor.model_states[model]["status"] == "BANNED"
    
    governor.log_success(model)
    
    with governor.state_lock:
        assert governor.model_states[model]["fail_count"] == 0
        assert governor.model_states[model]["status"] == "ACTIVE"
        assert governor.last_successful_model == model

def test_backoff_growth(governor):
    model = "gemini-2.0-flash"
    # 1st fail: 30 * 2^0 = 30
    governor.mark_model_banned(model, error_type="429")
    with governor.state_lock:
        banned_until = governor.model_states[model]["banned_until"]
        duration = (banned_until - datetime.now()).total_seconds()
        assert 30 <= duration <= 33 # 30 + jitter
    
    # 2nd fail: 30 * 2^1 = 60
    governor.mark_model_banned(model, error_type="429")
    with governor.state_lock:
        banned_until = governor.model_states[model]["banned_until"]
        duration = (banned_until - datetime.now()).total_seconds()
        assert 60 <= duration <= 63

def test_global_deadline(governor):
    # Mock call_model to take 10s each
    with patch("google.generativeai.GenerativeModel.generate_content") as mock_gen:
        def slow_call(*args, **kwargs):
            time.sleep(10)
            raise Exception("429 Quota Exceeded")
        
        mock_gen.side_effect = slow_call
        
        # This should try 2 models (20s) and then hit the 25s deadline on the 3rd
        start = time.time()
        result = governor.generate("caption", "test prompt")
        elapsed = time.time() - start
        
        # Deadline is 25s. It should exit before trying the 3rd model fully.
        assert elapsed >= 20 
        assert elapsed < 35 # Should not take 80s (8 attempts * 10s)

def test_circuit_breaker_and_probe(governor):
    # Ban all models
    with governor.state_lock:
        for name, state in governor.model_states.items():
            state["status"] = "BANNED"
            state["banned_until"] = datetime.now() + timedelta(minutes=10)
    
    # generate should fail immediately and return Ollama result
    with patch.object(governor, "_call_ollama", return_value="ollama_fallback") as mock_ollama:
        res = governor.generate("caption", "test")
        assert res == "ollama_fallback"
        assert governor.global_cooldown_until > time.time()

    # Fast forward time to expire global cooldown
    governor.global_cooldown_until = time.time() - 1
    
    # Next call should "probe"
    with patch.object(governor, "get_available_model", wraps=governor.get_available_model) as mock_get:
        governor.generate("caption", "test")
        # Check if probe logic was logged or if we can verify the call
        # We check if global_cooldown_until was reset to 0 inside get_available_model during probe
        assert governor.global_cooldown_until == 0

if __name__ == "__main__":
    pytest.main([__file__])
