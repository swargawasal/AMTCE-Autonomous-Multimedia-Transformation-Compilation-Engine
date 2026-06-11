from typing import Dict, Any, List
from connectors.gemini import call_gemini
from connectors.deepseek import call_deepseek
from connectors.mistral import call_mistral
from emotion.adapter import adapt_prompt_with_emotion

def execute_vision_chain(prompt: str, images: List[Any], emotion_data: Dict[str, Any]) -> Dict[str, Any]:
    # Adjust prompt
    adjusted_prompt = adapt_prompt_with_emotion(prompt, emotion_data)
    
    # In a real implementation with `google-genai`, `contents` can accept a list of text + PIL images.
    # For now, we simulate passing it to Gemini.
    print("Executing Vision Model (Gemini)...")
    gemini_prompt = [adjusted_prompt] + images
    res_gemini = call_gemini(gemini_prompt, system_prompt="You are a Vision AI. Analyze the images and answer the prompt.", task_type="ali_vision")
    vision_analysis = res_gemini.get("answer", "")
    
    # DeepSeek reasons about what Gemini saw
    print("Executing Reasoning Model (DeepSeek)...")
    ds_prompt = f"User asked: {prompt}\n\nVision Analysis:\n{vision_analysis}\n\nPlease reason about this visual analysis and provide a deeper logical conclusion."
    res_ds = call_deepseek(ds_prompt, system_prompt="You are a logical reasoner. Analyze the provided visual context.")
    reasoning = res_ds.get("answer", "")
    
    # Mistral synthesizes
    print("Executing Synthesis Model (Mistral)...")
    mistral_prompt = f"Synthesize a final response based on the logical reasoning:\n{reasoning}"
    res_mistral = call_mistral(mistral_prompt, system_prompt="You are the final synthesizer. Make the answer clear and human-friendly.")
    
    return {
        "final_answer": res_mistral.get("answer", ""),
        "chain_trace": {
            "gemini_vision": vision_analysis,
            "deepseek_reasoning": reasoning,
            "mistral_synthesis": res_mistral.get("answer", "")
        }
    }
