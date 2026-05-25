import sys, os, importlib, traceback
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

# Peel the onion — find ALL missing modules in the import chain
missing = []
iteration = 0

while True:
    iteration += 1
    try:
        from Compiler_Modules.rhythm_timeline_builder import RhythmTimelineBuilder
        print(f"SUCCESS after {iteration} iterations: {missing}")
        break
    except ModuleNotFoundError as e:
        mod = str(e).replace("No module named ", "").strip("'")
        if mod in missing:
            print(f"STUCK on same module: {mod}")
            break
        missing.append(mod)
        print(f"Missing: {mod}")
        
        # Try to find it in AMTCE and copy it
        src_base = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
        dst_base = r"D:\AMTCE-short-form-editor-only"
        
        mod_path = mod.replace(".", os.sep)
        
        # Try as a package folder
        src_pkg = os.path.join(src_base, mod_path)
        dst_pkg = os.path.join(dst_base, mod_path)
        if os.path.isdir(src_pkg):
            import shutil
            shutil.copytree(src_pkg, dst_pkg, dirs_exist_ok=True)
            print(f"  COPIED package: {mod_path}/")
            continue
        
        # Try as a .py file (parent dir)
        parts = mod_path.split(os.sep)
        if len(parts) >= 2:
            parent_src = os.path.join(src_base, parts[0])
            parent_dst = os.path.join(dst_base, parts[0])
            py_file = parts[-1] + ".py"
            src_file = os.path.join(parent_src, py_file)
            dst_file = os.path.join(parent_dst, py_file)
            if os.path.exists(src_file):
                os.makedirs(parent_dst, exist_ok=True)
                import shutil
                shutil.copy2(src_file, dst_file)
                print(f"  COPIED: {parts[0]}/{py_file}")
                continue
        
        # Try as top-level package
        src_dir = os.path.join(src_base, mod_path)
        if os.path.exists(src_dir + ".py"):
            import shutil
            shutil.copy2(src_dir + ".py", os.path.join(dst_base, mod_path + ".py"))
            print(f"  COPIED: {mod_path}.py")
            continue
            
        print(f"  NOT FOUND in AMTCE: {mod}")
        break
    
    if iteration > 30:
        print("Too many iterations, stopping")
        break
