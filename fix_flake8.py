import os
import re
import subprocess

def fix_file(filepath, replacements):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
        else:
            print(f"WARNING: Could not find '{old}' in {filepath}")
            
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

# 1. audio_pool_manager.py
fix_file("Audio_Modules/audio_pool_manager.py", [
    (
        "            if not api_key:\n                logger.debug(\"[GEMINI_POOL] No API key found — skipping enrichment.\")\n                return",
        "            if not api_key:\n                logger.debug(\"[GEMINI_POOL] No API key found — skipping enrichment.\")\n                return\n            import google.generativeai as genai\n            genai.configure(api_key=api_key)\n            model = genai.GenerativeModel(\"gemini-2.5-flash\")"
    )
])

# 2. Health_handlers/health.py
fix_file("Health_handlers/health.py", [
    ("    global _health_cache\n", "")
])

# 4. Intelligence_Modules/forensic_analyzer.py
fix_file("Intelligence_Modules/forensic_analyzer.py", [
    (
        "        if not self.api_key:\n            logger.warning(\"?? ForensicAnalyzer: GEMINI_API_KEY not set — will return defaults\")\n            return\n\n        try:\n                                    model_name = os.getenv(\"GEMINI_MODEL\", \"gemini-2.5-flash\")\n                        self._genai = genai\n            self._available = True\n            logger.info(f\"?? ForensicAnalyzer: ACTIVE (model={model_name})\")\n        except Exception as e:\n            logger.warning(f\"?? ForensicAnalyzer: init failed — {e}\")",
        "        model_name = os.getenv(\"GEMINI_MODEL\", \"gemini-2.5-flash\")\n        logger.info(f\"?? ForensicAnalyzer: ACTIVE (model={model_name})\")"
    )
])

print("Fixed some basic issues, let's run flake8 to see remaining.")
