
import os
import sys

# Force CPU Mode
os.environ["COMPUTE_MODE"] = "cpu"
os.environ["CPU_MODE"] = "on"

# Fix Python Path to include root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock missing GPU tools if needed (not needed since CPU mode forces skip)

print("🧪 STARTING VERIFICATION: CPU Mode Import Isolation")

# 1. Import Infrastructure
try:
    from Upscale_Modules import compute_caps
    print("✅ compute_caps imported")
except ImportError:
    print("❌ compute_caps missing")
    sys.exit(1)

# 2. Check Caps
caps = compute_caps.ComputeCaps.get()
print(f"ℹ️ Caps: {caps}")

if caps["has_cuda"]:
    print("❌ FAIL: CPU Mode requested but has_cuda is True")
    sys.exit(1)

# 3. Import Heavy Modules
print("🔄 Importing ai_engine...")
import Upscale_Modules.ai_engine as ai_engine
print("🔄 Importing compiler...")
from Compiler_Modules import compiler
print("🔄 Importing gpu_utils...")
import Upscale_Modules.gpu_utils as gpu_utils

# 4. Check sys.modules for 'torch'
if "torch" in sys.modules:
    # It might be imported but None? No, sys.modules has it if imported.
    # Wait, some minor utility might import it?
    # Let's check if it's actually loaded as a module
    torch_mod = sys.modules["torch"]
    if torch_mod is not None:
        print(f"❌ FAIL: 'torch' is present in sys.modules! ({torch_mod})")
        
        # traceback imports? Hard.
        # But we can check if it really matters. 
        # If it's the valid huge torch library, that's bad.
        # But maybe health.py imported it? I removed it.
        # gpu_utils? I gated it.
        sys.exit(1)
    else:
        print("✅ 'torch' is in sys.modules but is None (Lazy placeholder?)")
else:
    print("✅ 'torch' NOT found in sys.modules")

if "realesrgan" in sys.modules:
    print("❌ FAIL: 'realesrgan' is present in sys.modules!")
    sys.exit(1)

print("✅ VERIFICATION PASSED: No heavy imports detected in CPU Mode.")
