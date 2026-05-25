import sys, os, inspect
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

checks = [
    ("Compiler_Modules.rhythm_timeline_builder", "RhythmTimelineBuilder"),
    ("Compiler_Modules.transition_intelligence_engine", "TransitionIntelligenceEngine"),
    ("Compiler_Modules.audio_pipeline", "AudioPipeline"),
    ("Compiler_Modules.video_pipeline", "VideoPipeline"),
]
import importlib
for mod_path, cls_name in checks:
    try:
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        print(f"\n{'='*55}")
        print(f"CLASS: {cls_name}")
        print(f"  __init__{inspect.signature(cls.__init__)}")
        methods = [(n, str(inspect.signature(m)))
                   for n, m in inspect.getmembers(cls, predicate=inspect.isfunction)
                   if not n.startswith("_")]
        for name, sig in methods[:8]:
            print(f"    .{name}{sig}")
    except Exception as e:
        print(f"\n{cls_name} FAILED: {e}")
