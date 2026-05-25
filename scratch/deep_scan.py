import os
import re
import json

def scan_directory(root_dir):
    findings = []
    
    # Regex patterns
    absolute_path_pattern = re.compile(r'([c-zC-Z]:\\[^\s"\']+|/[Uu]sers/[^\s"\']+|/[Hh]ome/[^\s"\']+)')
    credential_pattern = re.compile(r'(?i)(password|secret|token|api_key|access_token)[\s]*=[\s]*[\'"][^\'"]+[\'"]')
    
    ignore_dirs = {'.git', 'venv', '__pycache__', 'node_modules', '.gradio', '.pytest_cache'}
    ignore_exts = {'.jpg', '.png', '.mp4', '.json', '.txt', '.log', '.env', '.pyc'}

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in ignore_exts:
                continue
                
            filepath = os.path.join(dirpath, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    for i, line in enumerate(f):
                        # Skip import lines or comments for paths
                        if not line.strip().startswith('#'):
                            # Find hardcoded paths
                            for match in absolute_path_pattern.finditer(line):
                                val = match.group(0)
                                if "whatsupneyork" in val.lower() or "c:\\" in val.lower() or "d:\\" in val.lower():
                                    findings.append({
                                        "file": os.path.relpath(filepath, root_dir),
                                        "line": i + 1,
                                        "type": "absolute_path",
                                        "match": val
                                    })
                            
                            # Find hardcoded credentials
                            for match in credential_pattern.finditer(line):
                                findings.append({
                                    "file": os.path.relpath(filepath, root_dir),
                                    "line": i + 1,
                                    "type": "credential",
                                    "match": match.group(0)
                                })
            except Exception:
                pass

    return findings

if __name__ == "__main__":
    proj_root = r"D:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)"
    results = scan_directory(proj_root)
    with open(os.path.join(proj_root, "scratch", "deep_scan_results.json"), "w") as f:
        json.dump(results, f, indent=4)
    print(f"Deep scan complete. Found {len(results)} potential issues.")
