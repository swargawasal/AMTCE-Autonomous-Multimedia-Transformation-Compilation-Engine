import os
import ast

def get_internal_imports(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read(), filename=file_path)
        except SyntaxError:
            return []
            
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    
    return [i for i in imports if i.split('.')[0] in ['Audio_Modules', 'Compiler_Modules', 'Content_Intelligence', 'Core_Modules', 'Diagnostics_Modules', 'Intelligence_Modules', 'Reaction_Engine', 'Text_Modules', 'Uploader_Modules', 'Visual_Refinement_Modules']]

base_dir = r'D:\AMTCE-short-form-editor-only'
all_imports = set()

for root, dirs, files in os.walk(base_dir):
    for file in files:
        if file.endswith('.py'):
            file_path = os.path.join(root, file)
            imports = get_internal_imports(file_path)
            all_imports.update(imports)

missing = []
for imp in all_imports:
    parts = imp.split('.')
    # We copied everything flat into subfolders except we need to know what original name they had.
    # Actually, we don't need to know the folder if the file name exists.
    # The file name is usually the last part of the import.
    module_name = parts[-1] + '.py'
    
    found = False
    for root, dirs, files in os.walk(base_dir):
        if module_name in files or (parts[-1] == parts[0] and parts[0] in [d for d in dirs]): 
            found = True
            break
    
    if not found:
        missing.append(imp)

if missing:
    print('MISSING DEPENDENCIES:')
    for m in missing:
        print(m)
else:
    print('ALL DEPENDENCIES PRESENT!')
