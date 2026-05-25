import sys
import os
import logging

# Configure logging to stdout
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_integration")

print("--- 🧪 INTEGRATION VERIFICATION (FULL PIPELINE) ---")

try:
    print("1. Importing compiler shim...")
    from Compiler_Modules import compiler
    print("   ✅ Compiler imported successfully.")
except Exception as e:
    print(f"   ❌ Import Failed: {e}")
    sys.exit(1)

print("2. Testing compile_with_transitions (Smart Render w/ Brain & Watermark)...")

input_path = (
    sys.argv[1] if len(sys.argv) > 1
    else os.getenv("VERIFY_INPUT_VIDEO", "")
    or next(iter([f for f in os.listdir("downloads") if f.endswith(".mp4")]), "dummy.mp4")
    if os.path.isdir("downloads") else "dummy.mp4"
)
if not os.path.exists(input_path):
     print(f"⚠️ Warning: {input_path} not found. Using dummy path (Verification might fail on render).")
     input_path = "dummy.mp4"

output_path = "verify_output_restored.mp4"
print(f"   🚀 Invoking Smart Render on {input_path}...")

# This should trigger orchestrator.compile_video -> Watermark -> AI Enhance -> Brain -> Render
result, meta = compiler.compile_with_transitions(
    [input_path], 
    output_path, 
    title="Test Verification [Enhanced]", 
    enhance=True
)

if result and os.path.exists(result):
    print(f"   ✅ Smart Render Success! Output: {result}")
    print(f"   ℹ️ Job ID: {meta.get('job_id')}")
else:
    print(f"   ❌ Smart Render Failed. Result: {result}")

print("\n--- ✅ VERIFICATION COMPLETE ---")
