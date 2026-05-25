import sys, os, inspect
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

modules_to_check = {
    "BeatEngine": ("Audio_Modules.beat_engine", "BeatEngine"),
    "MotionDetector": ("Visual_Refinement_Modules.motion_detector", "MotionDetector"),
    "EmotionalSpikeDetector": ("Content_Intelligence.emotional_spike_detector", "EmotionalSpikeDetector"),
    "RetentionCurveEngine": ("Content_Intelligence.retention_curve_engine", "RetentionCurveEngine"),
    "SignalFusionEngine": ("Content_Intelligence.signal_fusion_engine", "SignalFusionEngine"),
    "MomentMiner": ("Visual_Refinement_Modules.moment_miner", "MomentMiner"),
    "UnifiedIntelligence": ("Intelligence_Modules.unified_intelligence", "UnifiedIntelligence"),
    "RhythmTimelineBuilder": ("Compiler_Modules.rhythm_timeline_builder", "RhythmTimelineBuilder"),
    "TransitionIntelligenceEngine": ("Compiler_Modules.transition_intelligence_engine", "TransitionIntelligenceEngine"),
    "TimelineReconstructor": ("Visual_Refinement_Modules.timeline_reconstructor", "TimelineReconstructor"),
}

for label, (mod_path, cls_name) in modules_to_check.items():
    try:
        import importlib
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        
        # Get __init__ signature
        init_sig = str(inspect.signature(cls.__init__))
        
        # Get all public methods
        methods = [(name, str(inspect.signature(m))) 
                   for name, m in inspect.getmembers(cls, predicate=inspect.isfunction)
                   if not name.startswith('_')]
        
        print(f"\n{'='*60}")
        print(f"CLASS: {label}")
        print(f"  __init__{init_sig}")
        print(f"  PUBLIC METHODS:")
        for mname, msig in methods[:8]:
            print(f"    .{mname}{msig}")
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"CLASS: {label} -- IMPORT FAILED: {e}")
