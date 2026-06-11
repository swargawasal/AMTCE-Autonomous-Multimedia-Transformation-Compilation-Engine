from typing import Dict, Any
from connectors.mistral import call_mistral
from emotion.response_layer import apply_emotion_polish

def execute_synthesis_chain(raw_answers: Dict[str, str], emotion_data: Dict[str, Any]) -> str:
    """
    Takes multiple raw answers (e.g., from different models) and synthesizes them using Mistral.
    Then applies emotion polish.
    """
    combined = "\n\n".join([f"[{source}] said:\n{ans}" for source, ans in raw_answers.items()])
    
    prompt = f"Synthesize the following answers into a cohesive, single response:\n{combined}"
    res = call_mistral(prompt, system_prompt="You are the final synthesizer.", task_type="ali_synthesis")
    
    synthesized_answer = res.get("answer", "")
    
    # Final emotion polish
    final_answer = apply_emotion_polish(synthesized_answer, emotion_data)
    
    return final_answer
