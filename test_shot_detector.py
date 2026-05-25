import sys, os, inspect, importlib
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

mod_path = "Intelligence_Modules.shot_detector"
try:
    mod = importlib.import_module(mod_path)
    classes = [(n, c) for n, c in inspect.getmembers(mod, inspect.isclass) if c.__module__ == mod_path]
    funcs   = [n for n, _ in inspect.getmembers(mod, inspect.isfunction) if not n.startswith("_")]
    print(f"{mod_path}:")
    print(f"  CLASSES: {[n for n,_ in classes]}")
    print(f"  PUBLIC FUNCTIONS: {funcs[:8]}")
except Exception as e:
    print(f"FAILED - {e}")
