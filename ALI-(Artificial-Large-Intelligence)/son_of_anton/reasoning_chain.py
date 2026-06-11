from connectors.deepseek import call_deepseek

def anton_deep_reason(problem: str) -> str:
    prompt = f"Solve the following hard problem comprehensively:\n{problem}"
    system_prompt = "You are Son of Anton. Apply brute-force logical reasoning."
    res = call_deepseek(prompt, system_prompt, model="deepseek-reasoner", temperature=0.7)
    return res.get("answer", "")
