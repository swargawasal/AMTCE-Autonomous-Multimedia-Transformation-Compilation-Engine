import os, re, shutil

src = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
dst = r"D:\AMTCE-short-form-editor-only"

patterns = [
    r'["\']([A-Za-z_][A-Za-z0-9_\-/\\]+\.(json|env|txt|ttf|otf|png|jpg|mp3|mp4|wav|db|csv))["\']',
]

missing_files = {}
copied = []

for root, dirs, files in os.walk(dst):
    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__']]
    for f in files:
        if not f.endswith('.py'): continue
        fp = os.path.join(root, f)
        try:
            with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
                content = fh.read()
        except: continue
        
        for pat in patterns:
            for m in re.finditer(pat, content):
                rel = m.group(1).replace('\\\\', '/').replace('\\', '/')
                first = rel.split('/')[0]
                known_dirs = ['The_json','Monetization_Metrics','Credentials','assets','fonts',
                              'data','cache','Actress_Modules','Intelligence_Data','rag',
                              'Datasets_and_text_files','trend_context','First_Shots','config',
                              'music']
                if first not in known_dirs: continue
                
                src_path = os.path.join(src, rel.replace('/', os.sep))
                dst_path = os.path.join(dst, rel.replace('/', os.sep))
                
                if os.path.exists(dst_path): continue
                
                if os.path.isfile(src_path):
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(src_path, dst_path)
                    copied.append(rel)
                elif not os.path.exists(src_path):
                    missing_files[rel] = os.path.basename(fp)

if copied:
    print(f"COPIED {len(copied)} data files:")
    for c in sorted(set(copied)): print(f"  + {c}")
else:
    print("No additional data files needed copying.")

if missing_files:
    print(f"\nTRULY MISSING (will be auto-created at runtime - no action needed):")
    for path, from_file in sorted(missing_files.items()):
        print(f"  ! {path}  <- {from_file}")
