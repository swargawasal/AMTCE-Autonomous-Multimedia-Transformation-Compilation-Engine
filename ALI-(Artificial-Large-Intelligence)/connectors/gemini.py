import os
from google import genai
from typing import Dict, Any
from connectors.gemini_governor import GeminiGovernor

def call_gemini(prompt: str, system_prompt: str = "You are a helpful assistant.", task_type: str = "ali_reasoning") -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")

    governor = GeminiGovernor()
    client = genai.Client(api_key=api_key)
    
    excluded_models = []
    max_retries = 3
    
    for attempt in range(max_retries):
        model = governor.get_available_model(task_type, exclude_models=excluded_models) or "gemini-2.5-flash"
        
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.5
                )
            )
            
            return {
                "answer": response.text,
                "tokens_used": 0,
                "model_used": model
            }
        except Exception as e:
            err_str = str(e).lower()
            error_type = "5xx"
            if "429" in err_str or "quota" in err_str or "exhausted" in err_str:
                error_type = "429"
            elif "timeout" in err_str:
                error_type = "timeout"
            elif "safety" in err_str:
                error_type = "safety"
                
            governor.mark_model_banned(model, error_type=error_type)
            excluded_models.append(model)
            
            if attempt == max_retries - 1:
                return {"error": str(e), "model_used": model}

