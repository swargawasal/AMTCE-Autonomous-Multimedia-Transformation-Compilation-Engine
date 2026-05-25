import sys, os, inspect, importlib
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

# Get full sigs of the functions we need
checks = {
    "tie.enrich_segment_with_tie": ("Compiler_Modules.transition_intelligence_engine", "enrich_segment_with_tie"),
    "tie.decide_transition":       ("Compiler_Modules.transition_intelligence_engine", "decide_transition"),
    "vp.render_pipeline":          ("Compiler_Modules.video_pipeline", "render_pipeline"),
    "vp.render_music_driven":      ("Compiler_Modules.video_pipeline", "render_music_driven_video"),
    "ap.mix_audio":                ("Compiler_Modules.audio_pipeline", "mix_audio"),
    "rtb.build_timeline":          ("Compiler_Modules.rhythm_timeline_builder", "RhythmTimelineBuilder"),
}
for label, (mod_path, name) in checks.items():
    try:
        mod = importlib.import_module(mod_path)
        obj = getattr(mod, name)
        if inspect.isclass(obj):
            # get all public methods + their sigs
            for mname, meth in inspect.getmembers(obj, inspect.isfunction):
                if not mname.startswith("_"):
                    print(f"{label}.{mname}{inspect.signature(meth)}")
        else:
            print(f"{label}{inspect.signature(obj)}")
    except Exception as e:
        print(f"{label}: {e}")
