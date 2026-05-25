import sys, os, shutil, traceback
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")
src_base = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
dst_base = r"D:\AMTCE-short-form-editor-only"

targets = [
    ("Compiler_Modules.transition_intelligence_engine", "TransitionIntelligenceEngine"),
    ("Compiler_Modules.audio_pipeline", "AudioPipeline"),
    ("Compiler_Modules.video_pipeline", "VideoPipeline"),
]

for mod_path, cls_name in targets:
    for iteration in range(20):
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name, None)
            print(f"OK: {mod_path}.{cls_name}")
            break
        except ModuleNotFoundError as e:
            missing = str(e).replace("No module named ", "").strip("'")
            top = missing.split(".")[0]
            print(f"  Missing for {cls_name}: {missing}")
            
            src_pkg = os.path.join(src_base, top)
            dst_pkg = os.path.join(dst_base, top)
            if os.path.isdir(src_pkg):
                shutil.copytree(src_pkg, dst_pkg, dirs_exist_ok=True)
                print(f"    -> Copied package: {top}/")
            else:
                sub_path = missing.replace(".", os.sep) + ".py"
                src_file = os.path.join(src_base, sub_path)
                if os.path.exists(src_file):
                    os.makedirs(os.path.join(dst_base, os.path.dirname(sub_path)), exist_ok=True)
                    shutil.copy2(src_file, os.path.join(dst_base, sub_path))
                    print(f"    -> Copied: {sub_path}")
                else:
                    print(f"    -> NOT FOUND: {missing}")
                    break

            for k in list(sys.modules.keys()):
                if k == top or k.startswith(top + ".") or k == mod_path or k.startswith(mod_path + "."):
                    del sys.modules[k]
        except ImportError as e:
            print(f"  ImportError for {cls_name}: {e}")
            break
