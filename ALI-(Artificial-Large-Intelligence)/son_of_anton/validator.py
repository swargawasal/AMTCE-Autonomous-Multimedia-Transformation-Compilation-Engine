from connectors.gemini import call_gemini

def validate_solution(problem: str, solution: str) -> bool:
    prompt = f"Problem:\n{problem}\n\nProposed Solution:\n{solution}\n\nIs this solution factually and logically correct? Answer YES or NO."
    res = call_gemini(prompt, system_prompt="You are a strict validator.", task_type="ali_reasoning")
    answer = res.get("answer", "").strip().upper()
    return answer.startswith("YES")
