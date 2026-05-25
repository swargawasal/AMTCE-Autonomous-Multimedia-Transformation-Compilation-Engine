
import os
import re

def refactor_file(path, replacements):
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return
    
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    for target, replacement in replacements:
        # Use regex for multi-line replacement to handle line ending variations
        # Convert target into a regex pattern
        pattern = re.escape(target).replace(r'\ ', r'\s+').replace(r'\n', r'\s*[\r\n]+\s*')
        content = re.sub(pattern, replacement, content)
    
    if content != original_content:
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
        print(f"Successfully refactored: {path}")
    else:
        print(f"No changes made to: {path} (Matches not found)")

# 1. Monetization Brain
monetization_brain_replacements = [
    # Refactor __init__
    (
        """    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.provider = "none"
        self.model = None
        self.los_pollos_file = "Monetization_Metrics/los_pollos_links.json"
        self.link_optimizer = LinkOptimizer() if LinkOptimizer else None
        
        if self.gemini_key:
            try:
                genai.configure(api_key=self.gemini_key)
                model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
                is_banned = quota_manager.is_banned(model_name) if quota_manager else False
                
                if is_banned:
                    logger.warning(f"🧠 YPP Editor Brain: Model {model_name} is BANNED (Quota Exceeded). Rotating to fallbacks...")
                else:
                    logger.info(f"🧠 YPP Editor Brain: ACTIVE (Model: {model_name})")
                
                self.model = genai.GenerativeModel(model_name)
                self.provider = "gemini"
            except Exception as e:
                logger.error(f"❌ Gemini Brain Init Failed: {e}")
        else:
            logger.warning("🧠 YPP Editor Brain: INACTIVE (No Gemini Key)")""",
        """    def __init__(self):
        self.router = gemini_router
        self.los_pollos_file = "Monetization_Metrics/los_pollos_links.json"
        self.link_optimizer = LinkOptimizer() if LinkOptimizer else None"""
    ),
    # Refactor analyze_content check
    (
        """        if self.provider != "gemini" or not self.model:
            return self._fallback_response(title, visual_context=visual_context)""",
        "        # Call via router"
    ),
    # Refactor retry loop
    (
        """            # --- RETRY LOOP WITH FALLBACK (GEMINI 2.5 FLASH ONLY) ---
            models_to_try = [
                os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
                "gemini-2.5-flash"
            ]
            # Remove duplicates while preserving order
            models_to_try = list(dict.fromkeys(models_to_try))
            
            if quota_manager:
                models_to_try = quota_manager.filter_models(models_to_try)
                if not models_to_try:
                    logger.error("❌ All models in Quota Manager are BANNED. Brain falling back to generic.")
                    return self._fallback_response(title, error="Global Quota Exceeded", transformations=transformations)
            
            # 3. Model Configuration (Max Genuineness)
            safety_settings = {
                genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai.types.HarmBlockThreshold.BLOCK_NONE,
                genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
                genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: genai.types.HarmBlockThreshold.BLOCK_NONE,
                genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
            }

            last_error = None
            for model_name in models_to_try:
                try:
                    logger.info(f"🧠 Attempting multimodal analysis with: {model_name} (Temp: 0.85)")
                    current_model = genai.GenerativeModel(model_name)
                    
                    # Call Gemini — 90s hard timeout prevents pipeline hangs on slow multimodal calls
                    response = current_model.generate_content(
                        payload,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.85, 
                            response_mime_type="application/json"
                        ),
                        safety_settings=safety_settings,
                        request_options={"timeout": 90}
                    )
                    
                    response_text = response.text.strip()
                    logger.info("Gemini Raw Response:")
                    logger.info(response_text)
                    main_data = self._parse_json_response(response_text, clean_title, duration=duration, visual_context=visual_context)
                    
                    # 3. Fashion Scout Integration
                    if image_paths and main_data.get("approved"):
                        try:
                            try:
                                from Monetization_Metrics.fashion_scout import scout
                            except ImportError:
                                try:
                                    from .fashion_scout import scout
                                except (ImportError, ValueError):
                                    try:
                                        from fashion_scout import scout
                                    except ImportError:
                                        scout = None
                            fashion_data = scout.scout_outfit(image_paths) if scout else None
                            if fashion_data:
                                main_data["fashion_scout"] = fashion_data
                        except Exception as fe:
                            logger.warning(f"⚠️ Fashion Scout failed: {fe}")
                            
                    return main_data
                    
                except Exception as e:
                    last_error = e
                    err_msg = str(e).lower()
                    logger.warning(f"⚠️ Brain Analysis Attempt failed with {model_name}: {e}")
                    logger.info(f"🔄 Rotating model... (Finished attempt {models_to_try.index(model_name) + 1}/{len(models_to_try)})")
                    if any(x in err_msg for x in ["429", "quota", "500", "503", "timeout", "404", "not found"]):
                         logger.warning(f"⚠️ Brain Issue with {model_name} ({err_msg}). Rotating immediately...")
                         if any(x in err_msg for x in ["429", "quota"]):
                             if quota_manager: quota_manager.mark_banned(model_name)
                         continue
                    else:
                         raise e
            
            if last_error: raise last_error""",
        """            # 3. Execute Call via Router
            try:
                res_txt = self.router.generate(
                    task_type="master",
                    prompt=payload,
                    module_name="monetization_brain",
                    gen_config=genai.types.GenerationConfig(
                        temperature=0.85, 
                        response_mime_type="application/json"
                    ),
                    safety_settings={
                        genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai.types.HarmBlockThreshold.BLOCK_NONE,
                        genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
                        genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: genai.types.HarmBlockThreshold.BLOCK_NONE,
                        genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: genai.types.HarmBlockThreshold.BLOCK_NONE,
                    }
                )
                
                if not res_txt:
                    return self._fallback_response(title, error="Router returned empty response", transformations=transformations)

                logger.info("Gemini Raw Response:")
                logger.info(res_txt)
                main_data = self._parse_json_response(res_txt, clean_title, duration=duration, visual_context=visual_context)
                
                # 3. Fashion Scout Integration
                if image_paths and main_data.get("approved"):
                    try:
                        from .fashion_scout import scout
                        fashion_data = scout.scout_outfit(image_paths) if scout else None
                        if fashion_data:
                            main_data["fashion_scout"] = fashion_data
                    except Exception as fe:
                        logger.warning(f"⚠️ Fashion Scout failed: {fe}")
                        
                return main_data
            except Exception as e:
                logger.error(f"🧠 Brain Analysis Error: {e}")
                return self._fallback_response(title, error=e, transformations=transformations)"""
    )
]

# 2. Unified Intelligence
unified_intelligence_replacements = [
    # Import
    (
        """import google.generativeai as genai
import jsonschema
from jsonschema import validate""",
        """import google.generativeai as genai
import jsonschema
from jsonschema import validate
from Intelligence_Modules.gemini_governor import gemini_router"""
    ),
    # __init__
    (
        """    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.cache_dir = "intelligence_cache"
        os.makedirs(self.cache_dir, exist_ok=True)

        if self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                # [Phase A] Enforce JSON Mode
                generation_config = {"response_mime_type": "application/json"}
                self.model = genai.GenerativeModel(
                    self.model_name, generation_config=generation_config
                )
            except Exception as e:
                logger.error(f"❌ Failed to init Gemini model: {e}")
                self.model = None
        else:
            self.model = None""",
        """    def __init__(self):
        self.router = gemini_router
        self.cache_dir = "intelligence_cache"
        os.makedirs(self.cache_dir, exist_ok=True)"""
    ),
    # _call_gemini_with_retry header and loop
    (
        """    def _call_gemini_with_retry(
        self,
        prompt: str,
        frames: List[Any],
        schema: Dict,
        cache: IntelligenceCache,
        retries: int = 2,
    ) -> Optional[Dict]:
        if not self.model:
            return None
        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    time.sleep(2**attempt)
                cache.api_calls_made += 1
                logger.info(f"🧠 [GEMINI_MASTER_REQUEST] Sent (Attempt {attempt + 1})")

                response = self.model.generate_content([prompt] + frames)

                if not response.text:
                    continue

                # ── Robust JSON Extraction (Phase 1) ──
                raw_text = response.text.strip()""",
        """    def _call_gemini_with_retry(
        self,
        prompt: str,
        frames: List[Any],
        schema: Dict,
        cache: IntelligenceCache,
        retries: int = 2,
    ) -> Optional[Dict]:
        if not self.router:
            return None
        
        try:
            cache.api_calls_made += 1
            logger.info("🧠 [GEMINI_MASTER_REQUEST] Sent via Router")

            res_txt = self.router.generate(
                task_type="master",
                prompt=[prompt] + frames,
                module_name="unified_intelligence",
                gen_config={"response_mime_type": "application/json"}
            )

            if not res_txt:
                return None

            # ── Robust JSON Extraction (Phase 1) ──
            raw_text = res_txt.strip()"""
    )
]

# Run refactors
refactor_file(r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\monetization_brain.py', monetization_brain_replacements)
refactor_file(r'd:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Intelligence_Modules\unified_intelligence.py', unified_intelligence_replacements)
