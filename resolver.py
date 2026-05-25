import os
import ast
import shutil

amtce_root = r'D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)'
target_root = r'D:\AMTCE-short-form-editor-only'

top_dirs = [d for d in os.listdir(amtce_root) if os.path.isdir(os.path.join(amtce_root, d)) and not d.startswith('.')]

def get_imports(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read(), filename=file_path)
        except Exception:
            return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports

def copy_missing():
    added_any = False
    for root_dir, _, files in os.walk(target_root):
        for file in files:
            if not file.endswith('.py'): continue
            file_path = os.path.join(root_dir, file)
            imports = get_imports(file_path)
            for imp in imports:
                parts = imp.split('.')
                if parts[0] in top_dirs:
                    rel_path = os.path.join(*parts) + '.py'
                    src_file = os.path.join(amtce_root, rel_path)
                    
                    if not os.path.exists(src_file):
                        src_dir = os.path.join(amtce_root, *parts)
                        if os.path.isdir(src_dir):
                            src_file = os.path.join(src_dir, '__init__.py')
                            rel_path = os.path.join(*parts, '__init__.py')
                    
                    if os.path.exists(src_file):
                        target_file = os.path.join(target_root, rel_path)
                        if not os.path.exists(target_file):
                            os.makedirs(os.path.dirname(target_file), exist_ok=True)
                            shutil.copy2(src_file, target_file)
                            print(f"Copied {rel_path}")
                            added_any = True
    return added_any

iteration = 1
while copy_missing():
    print(f"Finished iteration {iteration}")
    iteration += 1
print("Done recursively resolving all local imports!")
