from typing import Dict, Any
from connectors.deepseek import call_deepseek
from connectors.gemini import call_gemini
from connectors.mistral import call_mistral
from connectors.qwen_hf import call_qwen
from emotion.adapter import adapt_prompt_with_emotion

def execute_complex_chain(prompt: str, emotion_data: Dict[str, Any]) -> Dict[str, Any]:
    # Adjust prompt based on emotion
    adjusted_prompt = adapt_prompt_with_emotion(prompt, emotion_data)
    
    # 1. Model A answers first (DeepSeek - Reasoning/Logic)
    print("Executing Model A (DeepSeek)...")
    res_a = call_deepseek(adjusted_prompt, system_prompt="You are Model A. Provide a deep, logical analysis of the problem.")
    answer_a = res_a.get("answer", "")
    
    # 2. Model B reads A + adds/corrects (Gemini - Context/Vision)
    print("Executing Model B (Gemini)...")
    prompt_b = f"User prompt: {prompt}\n\nModel A Answer:\n{answer_a}\n\nPlease review, add missing context, and correct any errors."
    res_b = call_gemini(prompt_b, system_prompt="You are Model B. Review Model A's answer and improve upon it.", task_type="ali_reasoning")
    answer_b = res_b.get("answer", "")
    
    # 3. Model C synthesizes A+B (Mistral - Emotion/Human Tone)
    print("Executing Model C (Mistral)...")
    prompt_c = f"User prompt: {prompt}\n\nModel A Answer:\n{answer_a}\n\nModel B Answer:\n{answer_b}\n\nPlease synthesize these into a coherent, final response."
    res_c = call_mistral(prompt_c, system_prompt="You are Model C. Synthesize the previous answers into a clear, unified response.")
    answer_c = res_c.get("answer", "")
    
    # 4. Model D final check (Qwen - Factual Verification / Multilingual)
    print("Executing Model D (Qwen)...")
    prompt_d = f"User prompt: {prompt}\n\nSynthesized Answer:\n{answer_c}\n\nPlease perform a final factual check and output the absolute final answer."
    res_d = call_qwen(prompt_d, system_prompt="You are Model D. Verify the facts and finalize the answer.")
    answer_d = res_d.get("answer", "")
    
    return {
        "final_answer": answer_d,
        "chain_trace": {
            "deepseek": answer_a,
            "gemini": answer_b,
            "mistral": answer_c,
            "qwen": answer_d
        }
    }
