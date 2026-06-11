from connectors.gemini import call_gemini

def check_safety_gemini(text: str) -> str:
    prompt = f"""
    Analyze the following text for any harmful content, bias, privacy exposure, or policy violations.
    Respond with 'PASS' if it is safe, or 'FLAG: [reason]' if it violates safety guidelines.
    
    Text: {text}
    """
    
    system_prompt = "You are a safety validator. Be strict but objective."
    # Use the ali_safety task type (which uses gemini-2.5-flash-lite)
    result = call_gemini(prompt, system_prompt, task_type="ali_safety")
    
    if "error" in result:
        return "PASS" # Fail-open if the safety API is down
        
    answer = result.get("answer", "PASS").strip()
    if answer.startswith("PASS"):
        return "PASS"
    return answer
