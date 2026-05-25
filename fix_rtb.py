import sys, os, importlib, traceback, shutil
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

src_base = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
dst_base = r"D:\AMTCE-short-form-editor-only"

missing_log = []

for iteration in range(40):
    try:
        from Compiler_Modules.rhythm_timeline_builder import RhythmTimelineBuilder
        print(f"\nSUCCESS after {iteration} fixes!")
        print("Fixed modules:", missing_log)
        break
    except ModuleNotFoundError as e:
        mod = str(e).replace("No module named ", "").strip("'")
        # Get just the top-level package name for folder lookup
        top = mod.split(".")[0]
        print(f"[{iteration}] Missing: {mod}  (top={top})")
        missing_log.append(mod)

        # Try copy entire top-level package folder
        src_pkg = os.path.join(src_base, top)
        dst_pkg = os.path.join(dst_base, top)
        if os.path.isdir(src_pkg) and not os.path.isdir(dst_pkg):
            shutil.copytree(src_pkg, dst_pkg, dirs_exist_ok=True)
            print(f"  -> Copied package: {top}/")
            # Reload to clear cached failed import
            for k in list(sys.modules.keys()):
                if k == top or k.startswith(top + "."):
                    del sys.modules[k]
            continue

        # Try copy just the specific submodule .py
        sub_path = mod.replace(".", os.sep) + ".py"
        src_file = os.path.join(src_base, sub_path)
        dst_file = os.path.join(dst_base, sub_path)
        if os.path.exists(src_file):
            os.makedirs(os.path.dirname(dst_file), exist_ok=True)
            shutil.copy2(src_file, dst_file)
            print(f"  -> Copied: {sub_path}")
            for k in list(sys.modules.keys()):
                if k == mod or k.startswith(mod + "."):
                    del sys.modules[k]
            continue

        print(f"  -> NOT FOUND: {mod}")
        break
    except ImportError as e:
        print(f"[{iteration}] ImportError: {e}")
        traceback.print_exc()
        break
