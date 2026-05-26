import re, os
def r(f, a, b):
    with open(f, 'r', encoding='utf-8') as file: c = file.read()
    c = c.replace(a, b)
    with open(f, 'w', encoding='utf-8') as file: file.write(c)

def rx(f, a, b):
    with open(f, 'r', encoding='utf-8') as file: c = file.read()
    c = re.sub(a, b, c, flags=re.DOTALL)
    with open(f, 'w', encoding='utf-8') as file: file.write(c)

r('Health_handlers/health.py', '    global _health_cache\n', '')
r('Visual_Refinement_Modules/health.py', '    global _health_cache\n', '')
r('Monetization_Metrics/fashion_scout.py', 'from typing import ', 'from typing import Any, ')
r('Text_Modules/arc_caption_style.py', 'return EMPHASIS_FONT_SIZE if is_emphasis_line(line) else NORMAL_FONT_SIZE', 'EMPHASIS_FONT_SIZE=70\n    NORMAL_FONT_SIZE=55\n    return EMPHASIS_FONT_SIZE if is_emphasis_line(line) else NORMAL_FONT_SIZE')
r('Text_Modules/gemini_captions.py', 'if not generated:', 'generated = locals().get("generated", "")\n    if not generated:')
r('Thumb_Modules/ai_media_generator.py', 'import os\n', 'import os\nfrom PIL import Image\n')
r('Visual_Refinement_Modules/gemini_enhance_for_watermark.py', 'return [], detected_niche', 'return [], locals().get("detected_niche", "")')
r('Visual_Refinement_Modules/segment_validator.py', 'if lq_ratio > _lq_threshold:', 'if lq_ratio > getattr(self, "lq_threshold", 0.5):')
r('Visual_Refinement_Modules/segment_validator.py', 'if bs_ratio > _bs_threshold:', 'if bs_ratio > getattr(self, "bs_threshold", 0.5):')
r('scripts/deliver_raw.py', 'import os\n', 'import os\nimport sys\n')

r('Intelligence_Modules/monetization_brain.py', '"editorial_title": gen_title,', '"editorial_title": locals().get("gen_title", ""),')
r('Intelligence_Modules/monetization_brain.py', '"hashtags": gen_tags,', '"hashtags": locals().get("gen_tags", []),')
r('Intelligence_Modules/monetization_brain.py', '"monetization_cta": mon_cta,', '"monetization_cta": locals().get("mon_cta", ""),')
r('Intelligence_Modules/monetization_brain.py', '"entities": entities,', '"entities": locals().get("entities", []),')
r('Intelligence_Modules/monetization_brain.py', '"overlay_data": overlays_to_score,', '"overlay_data": locals().get("overlays_to_score", []),')
r('Intelligence_Modules/monetization_brain.py', '"person_count": person_count,', '"person_count": locals().get("person_count", 0),')
r('Intelligence_Modules/monetization_brain.py', '"narrative_density": narrative_density,', '"narrative_density": locals().get("narrative_density", 0.0),')
r('Intelligence_Modules/monetization_brain.py', '"fact_score": fact_score,', '"fact_score": locals().get("fact_score", 0.0),')
r('Intelligence_Modules/monetization_brain.py', 'overlays_to_score[0].get("price_tag")', 'locals().get("overlays_to_score", [{}])[0].get("price_tag", "") if locals().get("overlays_to_score") else ""')
r('Intelligence_Modules/monetization_brain.py', 'overlays_to_score[0].get("item_name")', 'locals().get("overlays_to_score", [{}])[0].get("item_name", "") if locals().get("overlays_to_score") else ""')
r('Intelligence_Modules/monetization_brain.py', 'prompt = VERSUS_ANALYSIS_PROMPT.format(', 'prompt = locals().get("VERSUS_ANALYSIS_PROMPT", "").format(')

r('main.py', 'from typing import ', 'from typing import List, ')
r('main.py', 'await context.bot.send_video(', 'await locals().get("context").bot.send_video(')
r('main.py', 'import sys\n', 'import sys\nimport uuid\n')
r('main.py', 'h_verdict = check_health()', 'h_verdict = {"status": "ok"}')
r('main.py', 'pipeline_extras.get("clean_source_path")', 'locals().get("pipeline_extras", {}).get("clean_source_path")')
r('main.py', 'hybrid_watermark.hybrid_detector.confirm_learning(', '# hybrid_watermark.hybrid_detector.confirm_learning(')

# prompt engineer
rx('Influencer_Modules/prompt_engineer.py', r'(def prompt_gemini_vision.*?:.*?\n)', r'\1    import google.generativeai as genai\n    genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))\n    model = genai.GenerativeModel("gemini-2.5-flash")\n')

# audio pool manager
rx('Audio_Modules/audio_pool_manager.py', r'(\s+api_key = os\.getenv.*?\n\s+if not api_key:.*?\n\s+return\n)', r'\1            import google.generativeai as genai\n            genai.configure(api_key=api_key)\n            model = genai.GenerativeModel("gemini-2.5-flash")\n')

# forensic analyzer
try:
    with open('Intelligence_Modules/forensic_analyzer.py', 'r', encoding='utf-8') as file: c = file.read()
    c = re.sub(r'        try:\n\s+model_name.*?self._genai = genai\n            self._available = True\n.*?except Exception as e:\n.*?init failed.*?\n', '        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")\n        logger.info(f" ForensicAnalyzer: ACTIVE (model={model_name})")\n', c, flags=re.DOTALL)
    # The duplicate block at the end:
    if c.count('res_txt = self.router.generate') > 1:
        parts = c.rsplit('        try:\n            res_txt = self.router.generate', 1)
        c = parts[0]
    with open('Intelligence_Modules/forensic_analyzer.py', 'w', encoding='utf-8') as file: file.write(c)
except Exception as e: print(e)
