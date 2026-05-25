import os
import ast

def analyze_complexity(directory):
    files = []
    for root, _, filenames in os.walk(directory):
        if 'venv' in root or '.git' in root or '__pycache__' in root:
            continue
        for filename in filenames:
            if filename.endswith('.py'):
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    lines = len(content.splitlines())
                    tree = ast.parse(content)
                    classes = sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
                    functions = sum(isinstance(node, ast.FunctionDef) for node in ast.walk(tree))
                    files.append({
                        'path': filepath,
                        'lines': lines,
                        'classes': classes,
                        'functions': functions,
                        'score': lines + (classes * 50) + (functions * 20)
                    })
                except Exception as e:
                    pass
    
    files.sort(key=lambda x: x['score'], reverse=True)
    print("Top 15 most complex files:")
    for f in files[:15]:
        print(f"{f['lines']} lines | {f['classes']} classes | {f['functions']} funcs | {f['path']}")

if __name__ == '__main__':
    analyze_complexity('.')
