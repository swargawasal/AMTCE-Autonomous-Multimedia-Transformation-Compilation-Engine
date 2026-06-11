"""
UBI — Preloader
===============
FOCUSED users only: pre-fires a cheap Gemini API call as soon as the
frontend sends a /typing event (user started composing a message).

Goal: reduce perceived latency for power users.

How it works:
  1. Frontend emits POST /typing { session_id, partial_text }
  2. Preloader fires a low-cost Gemini Flash Lite call with the predicted topic
  3. Result is stored in _PRELOAD_CACHE keyed by session_id
  4. When the actual /chat arrives, chain_node checks the cache first
  5. If user's actual question diverges from prediction → cache is discarded

Safety:
  - Only fires for FOCUSED users (kurtosis_type == "FOCUSED")
  - Cancels the previous preload task if a new /typing event arrives
  - Cache TTL: 60 seconds (avoids stale answers being served)
  - Uses cheapest model tier: Gemini Flash Lite
"""

import asyncio
import time
from typing import Dict, Any, Optional

# In-memory preload cache: { session_id: { answer, predicted_topic, expires_at } }
_PRELOAD_CACHE: Dict[str, Dict[str, Any]] = {}

# Active preload task per session
_ACTIVE_TASKS: Dict[str, asyncio.Task] = {}

CACHE_TTL = 60   # seconds before a preloaded answer is discarded
MIN_PARTIAL_LEN = 8   # don't fire preload for very short partial inputs


async def _fire_preload(session_id: str, predicted_topic: str, partial_text: str):
    """Internal coroutine that actually fires the Gemini call."""
    try:
        from connectors.gemini import call_gemini

        # Craft a light anticipatory prompt
        prompt = (
            f"The user is composing a question about '{predicted_topic}'. "
            f"Their partial input so far: '{partial_text}'. "
            f"Provide a concise, high-quality answer to the most likely full question "
            f"they are about to ask. Keep the answer under 200 words."
        )

        result = await asyncio.to_thread(
            call_gemini,
            prompt,
            "You are a predictive answer engine. Be concise and accurate.",
            "ali_reasoning",
        )

        if "answer" in result and "error" not in result:
            _PRELOAD_CACHE[session_id] = {
                "answer":           result["answer"],
                "predicted_topic":  predicted_topic,
                "model_used":       result.get("model_used", "gemini"),
                "expires_at":       time.time() + CACHE_TTL,
                "partial_text":     partial_text,
            }

    except asyncio.CancelledError:
        pass  # User changed direction — normal, not an error
    except Exception as e:
        pass  # Preload failures are always silent — never block the user


async def trigger_preload(
    session_id: str,
    user_id: str,
    partial_text: str,
) -> Dict[str, Any]:
    """
    Called by the /typing API endpoint.

    Steps:
      1. Load user profile → check if FOCUSED
      2. Run predictor to get predicted topic
      3. Cancel any previous preload task for this session
      4. Fire new preload task in background
    """
    from ubi.pattern_learner import get_user_profile
    from ubi.predictor      import predict

    profile = await get_user_profile(user_id)
    utype   = profile.get("kurtosis_type", "MODERATE")

    # Only FOCUSED users get preloading
    if utype != "FOCUSED":
        return {"preload_fired": False, "reason": f"user_type={utype}"}

    if len(partial_text.strip()) < MIN_PARTIAL_LEN:
        return {"preload_fired": False, "reason": "partial_too_short"}

    prediction     = await predict(user_id)
    predicted_topic = prediction.get("predicted_topic", "general")
    confidence      = prediction.get("confidence_score", 0.0)

    # Don't bother preloading if confidence is too low
    if confidence < 0.35:
        return {"preload_fired": False, "reason": f"confidence_too_low={confidence:.2f}"}

    # Cancel existing preload for this session
    existing = _ACTIVE_TASKS.get(session_id)
    if existing and not existing.done():
        existing.cancel()

    # Launch background task
    task = asyncio.create_task(
        _fire_preload(session_id, predicted_topic, partial_text)
    )
    _ACTIVE_TASKS[session_id] = task

    return {
        "preload_fired":    True,
        "predicted_topic":  predicted_topic,
        "confidence":       confidence,
        "user_type":        utype,
    }


def get_preloaded_answer(session_id: str, actual_question: str) -> Optional[Dict[str, Any]]:
    """
    Called by chain_node before executing the full multi-LLM chain.

    Validates that the cached answer is:
      1. Not expired
      2. Still topically relevant to the actual question
        (uses simple keyword overlap check — no embedding cost here)

    Returns the cached result dict if valid, else None.
    """
    cached = _PRELOAD_CACHE.get(session_id)
    if not cached:
        return None

    # TTL check
    if time.time() > cached.get("expires_at", 0):
        del _PRELOAD_CACHE[session_id]
        return None

    # Relevance check: does the actual question share keywords with the preloaded topic?
    predicted_topic = cached.get("predicted_topic", "").lower()
    actual_lower    = actual_question.lower()

    topic_words = set(predicted_topic.split())
    question_words = set(actual_lower.split())
    overlap = topic_words & question_words

    # Require at least one meaningful word in common (or topic appears in question)
    relevant = bool(overlap) or predicted_topic in actual_lower
    if not relevant:
        del _PRELOAD_CACHE[session_id]
        return None

    result = dict(cached)
    del _PRELOAD_CACHE[session_id]   # consume once
    return result


def cancel_preload(session_id: str) -> None:
    """Cancel any active preload for a session (e.g. when user clears input)."""
    task = _ACTIVE_TASKS.get(session_id)
    if task and not task.done():
        task.cancel()
    _PRELOAD_CACHE.pop(session_id, None)
