
import os

def refactor_monetization_brain():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\monetization_brain.py'
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 1. Update __init__
        if 'def __init__(self):' in line:
            new_lines.append(line)
            new_lines.append('        self.router = gemini_router\n')
            new_lines.append('        self.los_pollos_file = "Monetization_Metrics/los_pollos_links.json"\n')
            new_lines.append('        self.link_optimizer = LinkOptimizer() if LinkOptimizer else None\n')
            # Skip until next method or empty line after block
            i += 1
            while i < len(lines) and lines[i].strip() != '' and 'def ' not in lines[i]:
                i += 1
            continue
            
        # 2. Update analyze_content entrance check
        if 'if self.provider != "gemini" or not self.model:' in line:
            new_lines.append('        if not self.router:\n')
            i += 1
            continue

        # 3. Replace Retry Loop
        if '# --- RETRY LOOP WITH FALLBACK' in line:
            new_lines.append('            # 3. Execute Call via Router\n')
            new_lines.append('            try:\n')
            new_lines.append('                res_txt = self.router.generate(\n')
            new_lines.append('                    task_type="master",\n')
            new_lines.append('                    prompt=payload,\n')
            new_lines.append('                    module_name="monetization_brain",\n')
            new_lines.append('                    gen_config=genai.types.GenerationConfig(\n')
            new_lines.append('                        temperature=0.85, \n')
            new_lines.append('                        response_mime_type="application/json"\n')
            new_lines.append('                    ),\n')
            new_lines.append('                    safety_settings={\n')
            new_lines.append('                        genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai.types.HarmBlockThreshold.BLOCK_NONE,\n')
            new_lines.append('                        genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai.types.HarmBlockThreshold.BLOCK_NONE,\n')
            new_lines.append('                        genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: genai.types.HarmBlockThreshold.BLOCK_NONE,\n')
            new_lines.append('                        genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: genai.types.HarmBlockThreshold.BLOCK_NONE,\n')
            new_lines.append('                    }\n')
            new_lines.append('                )\n')
            new_lines.append('                \n')
            new_lines.append('                if not res_txt:\n')
            new_lines.append('                    return self._fallback_response(title, error="Router returned empty response", transformations=transformations)\n')
            new_lines.append('\n')
            new_lines.append('                logger.info("Gemini Raw Response:")\n')
            new_lines.append('                logger.info(res_txt)\n')
            new_lines.append('                main_data = self._parse_json_response(res_txt, clean_title, duration=duration, visual_context=visual_context)\n')
            new_lines.append('                \n')
            new_lines.append('                # 3. Fashion Scout Integration\n')
            new_lines.append('                if image_paths and main_data.get("approved"):\n')
            new_lines.append('                    try:\n')
            new_lines.append('                        from .fashion_scout import scout\n')
            new_lines.append('                        fashion_data = scout.scout_outfit(image_paths) if scout else None\n')
            new_lines.append('                        if fashion_data:\n')
            new_lines.append('                            main_data["fashion_scout"] = fashion_data\n')
            new_lines.append('                    except Exception as fe:\n')
            new_lines.append('                        logger.warning(f"⚠️ Fashion Scout failed: {fe}")\n')
            new_lines.append('                        \n')
            new_lines.append('                return main_data\n')
            new_lines.append('            except Exception as e:\n')
            new_lines.append('                logger.error(f"🧠 Brain Analysis Error: {e}")\n')
            new_lines.append('                return self._fallback_response(title, error=e, transformations=transformations)\n')
            
            # Skip the old retry loop block
            while i < len(lines) and 'if last_error:' not in lines[i]:
                i += 1
            if i < len(lines) and 'if last_error:' in lines[i]:
                i += 1
            continue

        # 4. Replace Title Gen block
        if '# --- RETRY LOOP WITH FALLBACK (GEMINI 2.5 FLASH ONLY) ---' in line and i > 700:
             new_lines.append('             # Execute Request via Router\n')
             new_lines.append('             try:\n')
             new_lines.append('                 res_txt = self.router.generate(\n')
             new_lines.append('                     task_type="master",\n')
             new_lines.append('                     prompt=prompt,\n')
             new_lines.append('                     module_name="monetization_brain_title",\n')
             new_lines.append('                     gen_config=genai.types.GenerationConfig(temperature=0.85)\n')
             new_lines.append('                 )\n')
             new_lines.append('                 if not res_txt: return fallback_title, fallback_desc\n')
             new_lines.append('\n')
             new_lines.append('                 match = re.search(r\'({.*})\', res_txt, re.DOTALL)\n')
             new_lines.append('                 if match:\n')
             new_lines.append('                     data = json.loads(match.group(1))\n')
             new_lines.append('                     title = data.get("title", fallback_title).replace(\'\"\', \'\').replace(\'*\', \'\')\n')
             new_lines.append('                     desc = data.get(\"description\", fallback_desc)\n')
             new_lines.append('                     if context.lower() not in title.lower()[:len(context)+5]:\n')
             new_lines.append('                         title = f\"{context}: {title}\"\n')
             new_lines.append('                     return title, desc\n')
             new_lines.append('                 return res_txt.replace(\'\"\', \'\').replace(\'*\', \'\'), fallback_desc\n')
             new_lines.append('             except Exception as e:\n')
             new_lines.append('                 logger.error(f\"Brain Title Gen Error: {e}\")\n')
             new_lines.append('                 return fallback_title, fallback_desc\n')
             
             # Skip until end of method
             while i < len(lines) and 'def ' not in lines[i] and 'if __name__' not in lines[i]:
                 i += 1
             continue

        new_lines.append(line)
        i += 1

    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.writelines(new_lines)
    print("Successfully refactored monetization_brain.py")

def refactor_unified_intelligence():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\unified_intelligence.py'
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 1. Add import
        if 'import jsonschema' in line and 'from gemini_governor import gemini_router' not in "".join(lines[0:30]):
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1
            continue

        # 2. Update __init__
        if 'def __init__(self):' in line:
            new_lines.append(line)
            new_lines.append('        self.router = gemini_router\n')
            new_lines.append('        self.cache_dir = "intelligence_cache"\n')
            new_lines.append('        os.makedirs(self.cache_dir, exist_ok=True)\n')
            i += 1
            while i < len(lines) and lines[i].strip() != '' and 'def ' not in lines[i]:
                i += 1
            continue

        # 3. Update _call_gemini_with_retry
        if 'def _call_gemini_with_retry(' in line:
            new_lines.append(line)
            i += 1
            # Append method signature lines
            while i < len(lines) and ')' not in lines[i-1]:
                new_lines.append(lines[i])
                i += 1
            
            new_lines.append('        if not self.router:\n')
            new_lines.append('            return None\n')
            new_lines.append('        try:\n')
            new_lines.append('            cache.api_calls_made += 1\n')
            new_lines.append('            logger.info("🧠 [GEMINI_MASTER_REQUEST] Sent via Router")\n')
            new_lines.append('\n')
            new_lines.append('            res_txt = self.router.generate(\n')
            new_lines.append('                task_type="master",\n')
            new_lines.append('                prompt=[prompt] + frames,\n')
            new_lines.append('                module_name="unified_intelligence",\n')
            new_lines.append('                gen_config={"response_mime_type": "application/json"}\n')
            new_lines.append('            )\n')
            new_lines.append('\n')
            new_lines.append('            if not res_txt:\n')
            new_lines.append('                return None\n')
            new_lines.append('\n')
            new_lines.append('            raw_text = res_txt.strip()\n')
            
            # Skip until old JSON extraction logic starts
            while i < len(lines) and 'raw_text = response.text.strip()' not in lines[i]:
                i += 1
            if i < len(lines): i += 1 
            continue

        new_lines.append(line)
        i += 1

    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.writelines(new_lines)
    print("Successfully refactored unified_intelligence.py")

def refactor_hybrid_analyzer():
    path = r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\analyzer\hybrid_analyzer.py'
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 1. Add import
        if 'import google.generativeai as genai' in line:
            new_lines.append(line)
            new_lines.append('from gemini_governor import gemini_router\n')
            i += 1
            continue

        # 2. Update __init__
        if 'def __init__(self):' in line:
            new_lines.append(line)
            new_lines.append('        self.router = gemini_router\n')
            i += 1
            while i < len(lines) and lines[i].strip() != '' and 'def ' not in lines[i]:
                i += 1
            continue

        # 3. Update _infer_with_gemini
        if 'def _infer_with_gemini(self, local_profile: Dict[str, str]) -> Dict[str, str]:' in line:
            new_lines.append(line)
            i += 1
            # Skip to prompt definition
            while i < len(lines) and 'prompt =' not in lines[i]:
                i += 1
            
            # Add prompt and router call
            while i < len(lines) and '"""' not in lines[i]:
                 new_lines.append(lines[i])
                 i += 1
            new_lines.append(lines[i]) # First """
            i += 1
            while i < len(lines) and '"""' not in lines[i]:
                 new_lines.append(lines[i])
                 i += 1
            new_lines.append(lines[i]) # Second """
            i += 1
            
            new_lines.append('\n        try:\n')
            new_lines.append('            res_txt = self.router.generate(\n')
            new_lines.append('                task_type="analyzer",\n')
            new_lines.append('                prompt=prompt,\n')
            new_lines.append('                module_name="hybrid_analyzer"\n')
            new_lines.append('            )\n')
            new_lines.append('            if not res_txt: return {}\n')
            new_lines.append('\n')
            new_lines.append('            lines = res_txt.strip().split("\\n")\n')
            new_lines.append('            result = {}\n')
            new_lines.append('            for line in lines:\n')
            new_lines.append('                if ":" in line:\n')
            new_lines.append('                    k, v = line.split(":", 1)\n')
            new_lines.append('                    result[k.strip().lower()] = v.strip().lower()\n')
            new_lines.append('            if result.get("category"): return result\n')
            new_lines.append('        except Exception as e:\n')
            new_lines.append('            print(f"[DEBUG] Gemini inference failed: {e}")\n')
            new_lines.append('        return {}\n')
            
            # Skip old loop
            while i < len(lines) and 'def analyze(' not in lines[i]:
                 i += 1
            continue

        # 4. Update analyze check
        if 'if not self._api_configured:' in line:
             new_lines.append('        if not self.router:\n')
             i += 1
             continue

        new_lines.append(line)
        i += 1

    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.writelines(new_lines)
    print("Successfully refactored hybrid_analyzer.py")

if __name__ == "__main__":
    refactor_monetization_brain()
    refactor_unified_intelligence()
    refactor_hybrid_analyzer()
