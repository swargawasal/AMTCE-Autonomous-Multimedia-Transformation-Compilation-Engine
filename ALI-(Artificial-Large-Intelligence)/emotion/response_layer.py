from typing import Dict, Any
from connectors.mistral import call_mistral

def apply_emotion_polish(final_answer: str, emotion_data: Dict[str, Any]) -> str:
    emotion = emotion_data.get("emotion", "neutral")
    context = emotion_data.get("context_type", "professional")
    
    if emotion == "neutral" and context == "professional":
        return final_answer
        
    prompt = f"""
    The following is an AI-generated answer. Rewrite or polish it to match the required emotional tone.
    Do NOT change the factual content, logic, or code. Only adapt the tone.
    
    Required Tone: {emotion} (Context: {context})
    
    Original Answer:
    {final_answer}
    """
    
    system_prompt = "You are an emotional intelligence response layer. Polish the text to match the tone."
    
    result = call_mistral(prompt, system_prompt)
    if "error" in result:
        return final_answer
        
    return result["answer"].strip()
