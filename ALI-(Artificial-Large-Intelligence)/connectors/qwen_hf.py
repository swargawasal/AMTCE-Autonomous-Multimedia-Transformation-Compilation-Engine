"""
HuggingFace Connector — Governor-Aware
=======================================
Replaces the single-model call_qwen with a full intelligent connector
that uses HFGovernor to select the best model for the given task,
auto-rotates on errors, and reports success/failure back to the Governor.
"""

import os
import time
from huggingface_hub import InferenceClient
from typing import Dict, Any, Optional

from connectors.hf_governor import HFGovernor


def call_hf(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    task_type: str = "ali_reasoning",
    model: Optional[str] = None,      # explicit override; None = let Governor choose
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Intelligent HuggingFace call with automatic model rotation.

    Routing logic (in priority order):
      1. If `model` is explicitly passed → use it directly (no Governor).
      2. Otherwise → ask HFGovernor to pick the best model for `task_type`.
      3. On error (429 / timeout / 5xx) → ban that model, pick the next best one.
      4. Repeat up to `max_retries` times before returning the last error.

    Always uses multi-token pool rotation (HF_TOKEN, HF_TOKEN_2 ...) for the
    actual API call, so each retry can come from a different token.
    """
    governor = HFGovernor()
    excluded: list = []

    for attempt in range(max_retries):

        # ── Model Selection ─────────────────────────────────────────────────
        chosen_model = model if model else governor.get_available_model(
            task_type=task_type,
            exclude_models=excluded,
        )

        if not chosen_model:
            return {"error": "All HF models are currently banned or unavailable."}

        # ── Token Selection ─────────────────────────────────────────────────
        token = governor.get_next_token()
        if not token:
            return {"error": "No HF_TOKEN found in environment."}

        client = InferenceClient(api_key=token)

        # ── API Call ────────────────────────────────────────────────────────
        start = time.time()
        try:
            response = client.chat.completions.create(
                model=chosen_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.5,
                max_tokens=1024,
            )
            latency = time.time() - start
            answer  = response.choices[0].message.content
            tokens  = response.usage.total_tokens if response.usage else 0

            governor.record_success(chosen_model, latency)

            return {
                "answer":      answer,
                "tokens_used": tokens,
                "model_used":  chosen_model,
                "task_type":   task_type,
                "latency":     round(latency, 2),
            }

        except Exception as e:
            elapsed  = time.time() - start
            err_str  = str(e).lower()

            # Classify error type
            if "429" in err_str or "rate" in err_str or "quota" in err_str:
                error_type = "429"
            elif "timeout" in err_str or elapsed > 30:
                error_type = "timeout"
            elif "5" in err_str[:3] or "server" in err_str:
                error_type = "5xx"
            else:
                error_type = "429"  # conservative — ban briefly

            governor.mark_model_banned(
                model_id=chosen_model,
                error_type=error_type,
                task_type=task_type,
            )
            excluded.append(chosen_model)

            if attempt == max_retries - 1:
                return {
                    "error":      str(e),
                    "model_used": chosen_model,
                    "task_type":  task_type,
                }
            # else → loop again with the next best model


# ── Backwards Compatibility Alias ─────────────────────────────────────────────
# Old code that calls call_qwen(prompt, model="Qwen/...") still works.
def call_qwen(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    model: str = "Qwen/Qwen2.5-72B-Instruct",
) -> Dict[str, Any]:
    """
    Backwards-compatible wrapper.
    Calls call_hf with task_type='ali_reasoning' and the given model.
    """
    return call_hf(
        prompt=prompt,
        system_prompt=system_prompt,
        task_type="ali_reasoning",
        model=model,
    )
