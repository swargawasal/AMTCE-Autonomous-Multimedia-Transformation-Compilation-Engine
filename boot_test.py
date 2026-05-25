import sys, os
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

import importlib, ast, traceback

# Step 1: collect all top-level imports from main.py
with open("main.py", "r", encoding="utf-8") as f:
    src = f.read()

try:
    tree = ast.parse(src)
except Exception as e:
    print(f"Parse error: {e}")
    sys.exit(1)

imports = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for n in node.names:
            imports.append(("import", n.name))
    elif isinstance(node, ast.ImportFrom):
        imports.append(("from", node.module or ""))

print(f"Total top-level import statements in main.py: {len(imports)}")
print()

failed = []
succeeded = []
for kind, mod in imports:
    top = mod.split(".")[0]
    try:
        importlib.import_module(mod)
        succeeded.append(mod)
    except Exception as e:
        failed.append((mod, str(e)))

print(f"IMPORTS OK: {len(succeeded)}")
print(f"IMPORTS FAILED: {len(failed)}")
print()
if failed:
    print("FAILED IMPORTS (these are what crashes the boot):")
    for mod, err in failed:
        print(f"  !! {mod}  ->  {err}")
