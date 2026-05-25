import os
import sys
import logging

# Add project root to path
sys.path.append(os.getcwd())

from Intelligence_Modules.gemini_governor import GeminiGovernor

logging.basicConfig(level=logging.INFO)

def test_langfuse():
    print("Starting Langfuse verification test...")
    governor = GeminiGovernor()
    
    if not governor.langfuse:
        print("X Langfuse client not found in GeminiGovernor. Check if library is installed and keys are in .env")
        return

    print("OK Langfuse client initialized.")
    
    # Try a fake generation to trigger tracing
    try:
        print("Sending test trace via governor.generate()...")
        governor.generate(
            task_type="test_trace",
            prompt="Verification test for Langfuse integration. Please ignore.",
            module_name="VerificationScript"
        )
        print("OK Test trace signal sent. Check your Langfuse dashboard at cloud.langfuse.com")
    except Exception as e:
        print(f"X Test trace failed: {e}")

if __name__ == "__main__":
    test_langfuse()
