import sys, os, inspect, importlib
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

modules = [
    "Compiler_Modules.transition_intelligence_engine",
    "Compiler_Modules.audio_pipeline",
    "Compiler_Modules.video_pipeline",
    "Compiler_Modules.rhythm_timeline_builder",
]
for mod_path in modules:
    try:
        mod = importlib.import_module(mod_path)
        classes = [(n, c) for n, c in inspect.getmembers(mod, inspect.isclass) if c.__module__ == mod_path]
        funcs   = [n for n, _ in inspect.getmembers(mod, inspect.isfunction) if not n.startswith("_")]
        print(f"\n{mod_path}:")
        print(f"  CLASSES: {[n for n,_ in classes]}")
        print(f"  PUBLIC FUNCTIONS: {funcs[:8]}")
        for cls_name, cls in classes[:3]:
            methods = [n for n,_ in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")]
            sig = str(inspect.signature(cls.__init__))
            print(f"  {cls_name}.__init__{sig}")
            print(f"    methods: {methods[:8]}")
    except Exception as e:
        print(f"\n{mod_path}: FAILED - {e}")
