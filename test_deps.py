import sys, os, importlib, traceback
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

modules_to_test = [
    ("Visual_Refinement_Modules.subject_tracker", "SubjectTracker"),
    ("Visual_Refinement_Modules.shot_detector", "ShotDetector")
]

for mod_path, cls_name in modules_to_test:
    try:
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        print(f"OK: {cls_name}")
    except Exception as e:
        print(f"FAIL {cls_name}: {e}")
