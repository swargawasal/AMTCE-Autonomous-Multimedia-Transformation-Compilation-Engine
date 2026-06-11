import os
import requests
from typing import Dict, Any, Optional

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

def call_deepseek(prompt: str, system_prompt: str = "You are a helpful assistant.", model: str = "deepseek-reasoner", temperature: float = 0.5) -> Dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not found in environment variables.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature
    }

    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        answer = data["choices"][0]["message"]["content"]
        reasoning_content = data["choices"][0]["message"].get("reasoning_content", "")
        
        return {
            "answer": answer,
            "reasoning_content": reasoning_content,
            "tokens_used": data.get("usage", {}).get("total_tokens", 0)
        }
    except Exception as e:
        return {"error": str(e)}
