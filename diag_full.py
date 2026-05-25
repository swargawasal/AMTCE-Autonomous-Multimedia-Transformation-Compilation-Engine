import sys, os, traceback
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

# Test 1: RhythmTimelineBuilder
try:
    from Compiler_Modules.rhythm_timeline_builder import RhythmTimelineBuilder
    rtb = RhythmTimelineBuilder()
    beats = [0.1*i for i in range(91)]
    result = rtb.build_timeline(
        clips=[r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\downloads\Lisa_001\Lisa_001_001.mp4"],
        beat_maps=[beats],
        beat_grid=beats,
        scenes=[],
        motion_events=[],
        subject_data=[],
        target_duration_hint=30,
        vibe=None,
    )
    print("RTB OK:", len(result), "segments")
except Exception as e:
    print("RTB FAIL:", e)
    traceback.print_exc()

# Test 2: build_concat_pipeline
print("\n--- concat test ---")
try:
    from Compiler_Modules.video_pipeline import build_concat_pipeline, build_trim_segments
    print("build_concat_pipeline: OK import")
    print("build_trim_segments: OK import")
    import inspect
    print("concat sig:", inspect.signature(build_concat_pipeline))
except Exception as e:
    print("concat FAIL:", e)
    traceback.print_exc()
