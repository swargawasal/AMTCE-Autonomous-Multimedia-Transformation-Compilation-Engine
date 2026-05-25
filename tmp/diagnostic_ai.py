import os
import logging
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Intelligence_Modules.router_orchestra import orchestra
from Intelligence_Modules.gemini_governor import GeminiGovernor

logging.basicConfig(level=logging.INFO)

def test_orchestra():
    print("\n--- Testing Router Orchestra ---")
    prompt = "Say 'Hello from AMTCE diagnostic script'"
    task_type = "creative"
    
    # Reset health for fresh test
    for h in orchestra.health.values():
        h.banned_until = 0
        h.fail_count = 0
        h.success_count = 0
    
    # Try multiple times to see rotation
    for i in range(3):
        print(f"\nAttempt {i+1}:")
        result = orchestra.route(prompt, task_type)
        if result:
            print(f"✅ Success: {result[:50]}...")
        else:
            print("❌ Failure (Check logs for reason)")
            
    orchestra.health_report()

if __name__ == "__main__":
    test_orchestra()
