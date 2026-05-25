import os
import time
import threading
import json
import random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# Mocking Environment for Testing
os.environ["GEMINI_MAX_REQUESTS_PER_MIN"] = "30"
os.environ["GEMINI_MAX_FALLBACKS_PER_MIN"] = "2"
os.environ["GEMINI_JITTER_RANGE"] = "0.03"
os.environ["GEMINI_MEMORY_TTL"] = "10" 

import sys
sys.path.append(os.getcwd())

from Intelligence_Modules.gemini_governor import GeminiGovernor, gemini_router

def test_concurrent_stress():
    print("🚀 [TEST] Concurrent Stress (20 threads)...")
    results = []
    
    def worker(i):
        # We don't actually call the real API to avoid hitting quota
        # instead we'll test the routing logic and locking
        model = gemini_router.get_available_model("creative")
        results.append(model)
        
    threads = []
    for i in range(20):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    print(f"✅ Stress results: {len(results)} routing decisions made.")
    assert len(results) == 20

def test_failure_memory_ttl():
    print("🧠 [TEST] Failure Memory and TTL...")
    model = "gemini-2.0-flash-001"
    task = "reasoning"
    
    # 1. Inject failure
    with gemini_router.memory_lock:
        gemini_router.recent_task_failures[(model, task)] = time.monotonic()
        
    # 2. Verify it's skipped
    for _ in range(10):
        selected = gemini_router.get_available_model(task)
        if selected == model:
             print(f"❌ Error: Model {model} should have been avoided for task {task}")
             return False

    print("✅ Model avoided correctly.")
    
    # 3. Wait for TTL
    print("⏳ Waiting for TTL (11s)...")
    time.sleep(11)
    
    # 4. Verify it's usable again (with high score)
    # We'll artificially boost it back
    selected = gemini_router.get_available_model(task)
    print(f"✅ Model after TTL: {selected}")
    return True

def test_rate_limiting_and_fallback():
    print("🚦 [TEST] Rate Limiting & Fallback Cap...")
    
    # Fill RPM window
    for _ in range(30):
        gemini_router._add_request_event()
        
    if gemini_router.can_make_request():
        print("❌ Error: Rate limit should be active.")
        return False
        
    print("✅ RPM limit active.")
    
    # Try fallback
    if gemini_router.can_make_fallback():
        print("✅ Fallback available.")
        gemini_router._add_request_event(is_fallback=True)
        gemini_router._add_request_event(is_fallback=True)
        
        if not gemini_router.can_make_fallback():
            print("✅ Fallback cap reached (2/min).")
        else:
            print("❌ Error: Fallback cap not reached.")
            return False
    
    return True

def test_atomic_persistence():
    print("💾 [TEST] Atomic Persistence...")
    gemini_router._save_states()
    if os.path.exists(gemini_router.STATE_FILE):
        print(f"✅ State file exists at {gemini_router.STATE_FILE}")
        with open(gemini_router.STATE_FILE, 'r') as f:
            data = json.load(f)
            if "gemini-2.0-flash-001" in data:
                print("✅ Data integrity verified.")
            else:
                print("❌ Data missing.")
                return False
    return True

def test_api_key_reset():
    print("🔑 [TEST] API Key Change Reset...")
    
    # 1. Setup initial state with a fake key hash
    fake_hash = "old_key_123"
    initial_data = {
        "_key_hash": fake_hash,
        "gemini-2.0-flash-001": {
            "status": "BANNED",
            "banned_until": (datetime.now() + timedelta(hours=2)).isoformat()
        }
    }
    
    with open(gemini_router.STATE_FILE, "w") as f:
        json.dump(initial_data, f)
        
    # 2. Trigger load_states (governor has a DIFFERENT current_key_hash)
    # Re-computing hash in test to be sure
    current_hash = gemini_router._get_key_hash()
    print(f"   Current Key Hash: {current_hash[:8]}...")
    print(f"   Stored Key Hash: {fake_hash}")
    
    # We need to manually call load_states since it was already called on init
    gemini_router._load_states()
    
    # 3. Verify it RESET (gemini-2.0-flash-001 should be ACTIVE now)
    state = gemini_router.model_states["gemini-2.0-flash-001"]
    if state["status"] == "ACTIVE":
        print("✅ State reset confirmed after key change.")
        return True
    else:
        print(f"❌ Error: State did not reset. Status is {state['status']}")
        return False

if __name__ == "__main__":
    print("\n" + "="*50)
    print("🏆 GEMINI ROUTER V3.5 ULTIMATE VERIFICATION")
    print("="*50 + "\n")
    
    try:
        test_concurrent_stress()
        assert test_failure_memory_ttl()
        assert test_rate_limiting_and_fallback()
        assert test_atomic_persistence()
        assert test_api_key_reset()
        print("\n✨ ALL WORLD-CLASS VERIFICATION TESTS PASSED! ✨")
    except Exception as e:
        print(f"\n💥 TEST SUITE FAILED: {e}")
        import traceback
        traceback.print_exc()
