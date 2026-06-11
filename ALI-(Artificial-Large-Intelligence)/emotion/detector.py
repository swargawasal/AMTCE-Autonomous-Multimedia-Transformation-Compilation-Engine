import json
from typing import Dict, Any
from connectors.mistral import call_mistral

def detect_emotion(text: str) -> Dict[str, Any]:
    prompt = f"""
    Analyze the following text and detect the emotional tone and context.
    Return ONLY a valid JSON object matching this schema:
    {{
      "emotion": "frustrated | excited | sad | curious | urgent | neutral | joyful | angry | confused",
      "intensity": 0.0 to 1.0,
      "context_type": "professional | personal | creative | crisis"
    }}
    
    Text: "{text}"
    """
    
    system_prompt = "You are an emotion detection engine. Output ONLY valid JSON."
    
    result = call_mistral(prompt, system_prompt)
    if "error" in result:
        return {"emotion": "neutral", "intensity": 0.0, "context_type": "professional", "error": result["error"]}
        
    try:
        content = result["answer"].strip()
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)
        return data
    except Exception as e:
        return {"emotion": "neutral", "intensity": 0.0, "context_type": "professional", "error": "parse_error"}
