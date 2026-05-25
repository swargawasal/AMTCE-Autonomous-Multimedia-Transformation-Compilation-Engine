import os, ast, shutil, re

src = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
dst = r"D:\AMTCE-short-form-editor-only"

all_src_modules = set()
for root, dirs, files in os.walk(src):
    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', '__pycache__', 'node_modules']]
    for f in files:
        if f.endswith('.py'):
            rel = os.path.relpath(os.path.join(root, f), src)
            all_src_modules.add(rel.replace('\\', '/'))

def get_all_imports(file_path):
    results = set()
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names: results.add(n.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module: results.add(node.module)
        except: pass
        # also regex fallback for dynamic imports
        for m in re.findall(r'(?:from|import)\s+([\w\.]+)', content):
            results.add(m)
    except: pass
    return results

top_level_dirs = set(os.listdir(src))

def resolve_import_to_path(imp):
    parts = imp.split('.')
    if parts[0] not in top_level_dirs:
        return []
    candidates = []
    # e.g. Audio_Modules.beat_engine -> Audio_Modules/beat_engine.py
    p1 = os.path.join(src, *parts) + '.py'
    if os.path.exists(p1): candidates.append(p1)
    # e.g. Audio_Modules -> Audio_Modules/__init__.py
    p2 = os.path.join(src, *parts, '__init__.py')
    if os.path.exists(p2): candidates.append(p2)
    return candidates

total_copied = 0
iteration = 0
while True:
    iteration += 1
    newly_copied = []
    for root, dirs, files in os.walk(dst):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__']]
        for f in files:
            if not f.endswith('.py'): continue
            fp = os.path.join(root, f)
            imps = get_all_imports(fp)
            for imp in imps:
                for src_path in resolve_import_to_path(imp):
                    rel = os.path.relpath(src_path, src)
                    dst_path = os.path.join(dst, rel)
                    if not os.path.exists(dst_path):
                        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                        shutil.copy2(src_path, dst_path)
                        newly_copied.append(rel)
                        total_copied += 1
    if not newly_copied:
        print(f"DONE after {iteration} iterations. Total newly copied: {total_copied}")
        break
    print(f"Iteration {iteration}: copied {len(newly_copied)} files:")
    for f in sorted(newly_copied): print(f"  + {f}")
