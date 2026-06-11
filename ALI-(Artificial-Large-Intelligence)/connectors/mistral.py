import os
from mistralai.client import Mistral
from typing import Dict, Any

def call_mistral(prompt: str, system_prompt: str = "You are a helpful assistant.", model: str = "mistral-large-latest") -> Dict[str, Any]:
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY not found in environment variables.")

    client = Mistral(api_key=api_key)
    
    try:
        response = client.chat.complete(
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
            "tokens_used": response.usage.total_tokens if response.usage else 0
        }
    except Exception as e:
        return {"error": str(e)}
