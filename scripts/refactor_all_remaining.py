
import os

def refactor_vision_intelligence():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\vision_intelligence.py'
    if not os.path.exists(path): return
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'import jsonschema' in line and 'from gemini_governor import gemini_router' not in "".join(lines[0:30]):
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1
            continue
        if 'def analyze(' in line:
            new_lines.append(line)
            new_lines.append('    if not gemini_router: return get_fallback_payload()\n')
            i += 1
            while i < len(lines) and 'return' not in lines[i] and '_call_gemini_with_retry' not in lines[i]:
                if 'import google.generativeai' in lines[i] or 'genai.configure' in lines[i] or 'model =' in lines[i]:
                    i += 1
                else:
                    new_lines.append(lines[i])
                    i += 1
            if i < len(lines) and '_call_gemini_with_retry' in lines[i]:
                new_lines.append('    return _call_gemini_with_retry(None, payload, VISION_SCHEMA)\n')
                i += 1
            continue
        if 'def _call_gemini_with_retry(' in line:
            new_lines.append(line)
            i += 1
            while i < len(lines) and ')' not in lines[i-1]:
                new_lines.append(lines[i])
                i += 1
            new_lines.append('    try:\n')
            new_lines.append('        res_txt = gemini_router.generate(task_type="vision", prompt=payload, module_name="vision_intelligence", gen_config={"temperature": 0.2})\n')
            new_lines.append('        if not res_txt: return get_fallback_payload()\n')
            new_lines.append('        import re\n')
            new_lines.append('        json_match = re.search(r"\\{[\\s\\S]*\\}", res_txt)\n')
            new_lines.append('        data = json.loads(json_match.group(0))\n')
            new_lines.append('        validate(instance=data, schema=schema)\n')
            new_lines.append('        return data\n')
            new_lines.append('    except Exception as e:\n')
            new_lines.append('        logger.error(f"Vision error: {e}")\n')
            new_lines.append('        return get_fallback_payload()\n')
            while i < len(lines) and (i + 1 < len(lines) and 'def ' not in lines[i+1]):
                i += 1
            i += 1
            continue
        new_lines.append(line)
        i += 1
    with open(path, 'w', encoding='utf-8', newline='') as f: f.writelines(new_lines)
    print("Refactored vision_intelligence.py")

def refactor_generator():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\generator.py'
    if not os.path.exists(path): return
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'import google.generativeai as genai' in line:
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1; continue
        if 'def __init__(self):' in line:
            new_lines.append(line)
            new_lines.append('        self.router = gemini_router\n')
            i += 1
            while i < len(lines) and lines[i].strip() != '' and 'def ' not in lines[i]: i += 1
            continue
        if 'response = self.model.generate_content(' in line:
            new_lines.append('            res_txt = self.router.generate(task_type="creative", prompt=IMAGE_SYNTHESIS_PROMPT.format(context=context), module_name="generator")\n')
            new_lines.append('            if not res_txt: return None\n')
            new_lines.append('            description = res_txt.strip()\n')
            i += 2; continue
        new_lines.append(line)
        i += 1
    with open(path, 'w', encoding='utf-8', newline='') as f: f.writelines(new_lines)
    print("Refactored generator.py")

def refactor_forensic_analyzer():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\forensic_analyzer.py'
    if not os.path.exists(path): return
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'from typing import List, Optional' in line:
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1; continue
        if 'def __init__(self):' in line:
            new_lines.append(line)
            new_lines.append('        self.router = gemini_router\n')
            new_lines.append('        self._available = True if gemini_router else False\n')
            i += 1
            while i < len(lines) and lines[i].strip() != '' and 'def ' not in lines[i]: i += 1
            continue
        if 'def _call_gemini(self, frame_paths: List[str]) -> dict:' in line:
            new_lines.append(line)
            i += 1
            while i < len(lines) and 'models_to_try =' not in lines[i]:
                 if 'genai = self._genai' in lines[i] or 'safety_settings =' in lines[i] or 'HarmCategory' in lines[i]:
                     i += 1
                 else:
                     new_lines.append(lines[i])
                     i += 1
            new_lines.append('        try:\n')
            new_lines.append('            res_txt = self.router.generate(\n')
            new_lines.append('                task_type="vision",\n')
            new_lines.append('                prompt=payload,\n')
            new_lines.append('                module_name="forensic_analyzer",\n')
            new_lines.append('                gen_config={"temperature": 0.2, "response_mime_type": "application/json"}\n')
            new_lines.append('            )\n')
            new_lines.append('            if not res_txt: return DEFAULT_RESULT.copy()\n')
            new_lines.append('            return self._parse_response(res_txt)\n')
            new_lines.append('        except Exception as e:\n')
            new_lines.append('            logger.error(f"Forensic error: {e}")\n')
            new_lines.append('            return DEFAULT_RESULT.copy()\n')
            while i < len(lines) and 'def _parse_response(' not in lines[i]: i += 1
            continue
        new_lines.append(line)
        i += 1
    with open(path, 'w', encoding='utf-8', newline='') as f: f.writelines(new_lines)
    print("Refactored forensic_analyzer.py")

def refactor_content_brain():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\content_brain.py'
    if not os.path.exists(path): return
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'import jsonschema' in line and 'from gemini_governor import gemini_router' not in "".join(lines[0:30]):
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1; continue
        if 'def generate(' in line:
            new_lines.append(line)
            new_lines.append('    if not gemini_router: return get_fallback_payload()\n')
            i += 1
            while i < len(lines) and 'return' not in lines[i] and '_call_gemini_with_retry' not in lines[i]:
                if 'api_key =' in lines[i] or 'model_name =' in lines[i] or 'genai.configure' in lines[i] or 'model =' in lines[i] or 'import google.generativeai' in lines[i]:
                    i += 1
                else:
                    new_lines.append(lines[i])
                    i += 1
            continue
        if 'def _call_gemini_with_retry(' in line:
            new_lines.append(line)
            i += 1
            while i < len(lines) and ')' not in lines[i-1]:
                new_lines.append(lines[i])
                i += 1
            new_lines.append('    try:\n')
            new_lines.append('        res_txt = gemini_router.generate(task_type="vision", prompt=payload, module_name="content_brain", gen_config={"temperature": 0.4})\n')
            new_lines.append('        if not res_txt: return get_fallback_payload()\n')
            new_lines.append('        json_match = re.search(r"\\{[\\s\\S]*\\}", res_txt)\n')
            new_lines.append('        data = json.loads(json_match.group(0))\n')
            new_lines.append('        validate(instance=data, schema=schema)\n')
            new_lines.append('        return data\n')
            new_lines.append('    except Exception as e:\n')
            new_lines.append('        logger.error(f"Content Brain error: {e}")\n')
            new_lines.append('        return get_fallback_payload()\n')
            while i < len(lines) and 'def ' not in lines[i] and 'if __name__' not in lines[i]: i += 1
            continue
        new_lines.append(line)
        i += 1
    with open(path, 'w', encoding='utf-8', newline='') as f: f.writelines(new_lines)
    print("Refactored content_brain.py")

def refactor_caption_memory():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\caption_memory.py'
    if not os.path.exists(path): return
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'import hashlib' in line:
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1; continue
        if 'def _get_embedding(self, text: str) -> List[float]:' in line:
            new_lines.append(line)
            new_lines.append('        return gemini_router.embed(text, module_name="caption_memory")\n')
            i += 1
            while i < len(lines) and (i + 1 < len(lines) and 'def ' not in lines[i+1]): i += 1
            i += 1
            continue
        new_lines.append(line)
        i += 1
    with open(path, 'w', encoding='utf-8', newline='') as f: f.writelines(new_lines)
    print("Refactored caption_memory.py")

def refactor_analytics_optimizer():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\analytics_optimizer.py'
    if not os.path.exists(path): return
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'import google.generativeai as genai' in line:
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1; continue
        if 'def __init__(self):' in line:
            new_lines.append(line)
            new_lines.append('        self.router = gemini_router\n')
            new_lines.append('        self.gemini_available = True if gemini_router else False\n')
            i += 1
            while i < len(lines) and lines[i].strip() != '' and 'def ' not in lines[i]: i += 1
            continue
        if 'response = self.model.generate_content(prompt)' in line:
            new_lines.append('            res_txt = self.router.generate(task_type="analytics", prompt=prompt, module_name="analytics_optimizer")\n')
            new_lines.append('            if not res_txt: return None\n')
            new_lines.append('            text = res_txt.strip().replace("```json", "").replace("```", "")\n')
            i += 2; continue
        new_lines.append(line)
        i += 1
    with open(path, 'w', encoding='utf-8', newline='') as f: f.writelines(new_lines)
    print("Refactored analytics_optimizer.py")

def refactor_fashion_scout():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\fashion_scout.py'
    if not os.path.exists(path): return
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'import jsonschema' in line and 'from gemini_governor import gemini_router' not in "".join(lines[0:30]):
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1; continue
        if 'def scan(' in line:
            new_lines.append(line)
            new_lines.append('    if not gemini_router: return get_fallback_payload()\n')
            i += 1
            while i < len(lines) and 'return' not in lines[i] and '_call_gemini_with_retry' not in lines[i]:
                if 'api_key =' in lines[i] or 'model_name =' in lines[i] or 'genai.configure' in lines[i] or 'model =' in lines[i] or 'import google.generativeai' in lines[i]:
                    i += 1
                else:
                    new_lines.append(lines[i])
                    i += 1
            continue
        if 'def _call_gemini_with_retry(' in line:
            new_lines.append(line)
            i += 1
            while i < len(lines) and ')' not in lines[i-1]:
                new_lines.append(lines[i])
                i += 1
            new_lines.append('    try:\n')
            new_lines.append('        res_txt = gemini_router.generate(task_type="vision", prompt=payload, module_name="fashion_scout", gen_config={"temperature": 0.3})\n')
            new_lines.append('        if not res_txt: return get_fallback_payload()\n')
            new_lines.append('        json_match = re.search(r"\\{[\\s\\S]*\\}", res_txt)\n')
            new_lines.append('        data = json.loads(json_match.group(0))\n')
            new_lines.append('        validate(instance=data, schema=schema)\n')
            new_lines.append('        return data\n')
            new_lines.append('    except Exception as e:\n')
            new_lines.append('        logger.error(f"Fashion Scout error: {e}")\n')
            new_lines.append('        return get_fallback_payload()\n')
            while i < len(lines) and 'def ' not in lines[i] and 'if __name__' not in lines[i]: i += 1
            continue
        new_lines.append(line)
        i += 1
    with open(path, 'w', encoding='utf-8', newline='') as f: f.writelines(new_lines)
    print("Refactored fashion_scout.py")

def refactor_narrative_brain():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\narrative_brain.py'
    if not os.path.exists(path): return
    with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'import os' in line and 'from gemini_governor import gemini_router' not in "".join(lines[0:30]):
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1; continue
        if 'def __init__(self):' in line:
            new_lines.append(line)
            new_lines.append('        self.router = gemini_router\n')
            i += 1
            while i < len(lines) and lines[i].strip() != '' and 'def ' not in lines[i]: i += 1
            continue
        if 'response = self.model.generate_content(' in line:
            new_lines.append('            try:\n')
            new_lines.append('                res_txt = self.router.generate(task_type="master", prompt=payload, module_name="narrative_brain")\n')
            new_lines.append('                if not res_txt:\n')
            new_lines.append('                    full_script.append("[Narrative gap]")\n')
            new_lines.append('                    continue\n')
            new_lines.append('                resp_text = res_txt.strip()\n')
            i += 4
            continue
        new_lines.append(line)
        i += 1
    with open(path, 'w', encoding='utf-8', newline='') as f: f.writelines(new_lines)
    print("Refactored narrative_brain.py")

if __name__ == "__main__":
    refactor_vision_intelligence()
    refactor_generator()
    refactor_forensic_analyzer()
    refactor_content_brain()
    refactor_caption_memory()
    refactor_analytics_optimizer()
    refactor_fashion_scout()
    refactor_narrative_brain()
