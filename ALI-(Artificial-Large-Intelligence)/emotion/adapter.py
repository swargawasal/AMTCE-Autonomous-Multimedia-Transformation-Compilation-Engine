from typing import Dict, Any

def get_emotion_adjustments(emotion_data: Dict[str, Any]) -> Dict[str, str]:
    emotion = emotion_data.get("emotion", "neutral")
    context = emotion_data.get("context_type", "professional")
    
    adjustments = {
        "tone": "neutral",
        "opening": "",
        "model_lead": "deepseek"
    }
    
    if emotion == "frustrated":
        adjustments["tone"] = "empathetic"
        adjustments["opening"] = "I understand this is frustrating..."
        adjustments["model_lead"] = "mistral"
    elif emotion == "excited":
        adjustments["tone"] = "energetic"
        adjustments["model_lead"] = "mistral"
    elif emotion in ["sad", "crisis"]:
        adjustments["tone"] = "compassionate"
        adjustments["model_lead"] = "mistral"
    elif emotion == "curious":
        adjustments["tone"] = "detailed and encouraging"
        adjustments["model_lead"] = "deepseek"
    elif context == "professional":
        adjustments["tone"] = "formal"
    
    return adjustments

def adapt_prompt_with_emotion(original_prompt: str, emotion_data: Dict[str, Any]) -> str:
    adjustments = get_emotion_adjustments(emotion_data)
    if adjustments["tone"] == "neutral":
        return original_prompt
        
    return f"""
[SYSTEM OVERRIDE: Emotional Intelligence Adapter]
Please adopt a {adjustments['tone']} tone.
{f'Consider starting with: "{adjustments["opening"]}"' if adjustments["opening"] else ''}

Original Request:
{original_prompt}
"""
