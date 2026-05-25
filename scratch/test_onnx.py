try:
    import onnxruntime
    print(f"✅ onnxruntime imported, version: {onnxruntime.__version__}")
    print(f"✅ available providers: {onnxruntime.get_available_providers()}")
except ImportError as e:
    print(f"❌ onnxruntime import failed: {e}")
except Exception as e:
    print(f"❌ onnxruntime test failed: {e}")
