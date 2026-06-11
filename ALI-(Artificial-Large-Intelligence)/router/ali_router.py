import json
from typing import Dict, Any
from connectors.mistral import call_mistral

def classify_task(prompt: str) -> Dict[str, Any]:
    system_prompt = """
    You are the ALI task router. Classify the user's prompt into one of the following categories:
    - "code" (programming, logic, math, debugging)
    - "vision" (images, visual descriptions requested)
    - "multilingual" (translation, non-English)
    - "factual" (history, science, objective facts)
    - "creative" (storytelling, opinion, casual chat)
    
    Return ONLY valid JSON:
    {
        "category": "code | vision | multilingual | factual | creative"
    }
    """
    
    result = call_mistral(prompt, system_prompt)
    if "error" in result:
        return {"category": "creative", "error": result["error"]}
        
    try:
        content = result["answer"].strip()
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)
        return data
    except Exception:
        return {"category": "creative", "error": "parse_error"}

def get_routing_plan(category: str, emotion_data: Dict[str, Any]) -> Dict[str, str]:
    emotion = emotion_data.get("emotion", "neutral")
    context = emotion_data.get("context_type", "professional")
    
    # Base mapping
    if category == "code":
        lead = "deepseek"
    elif category == "vision":
        lead = "gemini"
    elif category == "multilingual":
        lead = "qwen"
    elif category == "factual":
        lead = "qwen"
    else:
        lead = "mistral"
        
    # Emotion overrides
    if emotion in ["sad", "crisis"]:
        lead = "mistral"
    elif emotion == "frustrated" and category == "code":
        # Handled in chain, but DeepSeek solves, Mistral wraps
        lead = "deepseek"
    elif emotion == "curious":
        lead = "deepseek"
        
    return {
        "lead_model": lead,
        "category": category,
        "emotion_override": emotion if emotion != "neutral" else None
    }
