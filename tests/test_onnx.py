import sys
import os

print("Python version:", sys.version)
print("VENV path:", os.environ.get("VIRTUAL_ENV"))

try:
    import onnxruntime
    print("onnxruntime version:", onnxruntime.__version__)
    print("onnxruntime path:", onnxruntime.__file__)
    
    # Try to create a session to see if DLLs are missing
    import numpy as np
    # Very simple dummy model test if possible, or just check session creation
    print("Attempting to import onnxruntime.InferenceSession...")
    from onnxruntime import InferenceSession
    print("Successfully imported InferenceSession")
except Exception as e:
    print("FAILED to verify onnxruntime:")
    import traceback
    traceback.print_exc()
