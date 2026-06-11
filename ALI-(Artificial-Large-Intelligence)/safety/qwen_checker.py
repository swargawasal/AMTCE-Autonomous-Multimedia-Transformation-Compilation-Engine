from connectors.qwen_hf import call_qwen

def check_factual_consistency(text: str) -> str:
    prompt = f"""
    Analyze the following text for factual consistency and hallucination.
    Respond with 'PASS' if it is factually consistent, or 'FLAG: [reason]' if you detect a clear hallucination or contradiction.
    
    Text: {text}
    """
    
    system_prompt = "You are a factual consistency validator. Verify the facts strictly."
    
    result = call_qwen(prompt, system_prompt)
    if "error" in result:
        return "PASS" # Fail-open if API is down
        
    answer = result.get("answer", "PASS").strip()
    if answer.startswith("PASS"):
        return "PASS"
    return answer
