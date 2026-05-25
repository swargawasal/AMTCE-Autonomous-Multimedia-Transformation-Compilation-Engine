import sys, os, inspect, importlib
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

try:
    from Intelligence_Modules.shot_detector import detect_shots
    print("detect_shots:", inspect.signature(detect_shots))
except Exception as e:
    print(e)

try:
    from Visual_Refinement_Modules.subject_tracker import track_subject
    print("track_subject:", inspect.signature(track_subject))
except Exception as e:
    print(e)
