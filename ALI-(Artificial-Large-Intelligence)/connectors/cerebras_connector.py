import os
from openai import OpenAI
from typing import Dict, Any

def call_cerebras(prompt: str, system_prompt: str = "You are a helpful assistant.", model: str = "gpt-oss-120b") -> Dict[str, Any]:
    api_key = os.getenv("CEREBRAS_API_KEY")
    if not api_key:
        raise ValueError("CEREBRAS_API_KEY not found in environment variables.")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.cerebras.ai/v1"
    )
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5
        )
        
        answer = response.choices[0].message.content
        return {
            "answer": answer,
            "tokens_used": response.usage.total_tokens if response.usage else 0,
            "model_used": model
        }
    except Exception as e:
        return {"error": str(e)}
