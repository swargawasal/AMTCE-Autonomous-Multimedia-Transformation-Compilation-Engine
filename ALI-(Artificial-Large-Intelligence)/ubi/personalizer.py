"""
UBI — Personalizer
==================
Reads kurtosis_type from the user's profile and injects calibrated
depth / tone / style metadata into every model prompt.

FOCUSED  (k > 3) → Expert mode: skip basics, use precise terminology,
                    include citations/edge-cases, max depth.
MODERATE (1–3)   → Balanced mode: define jargon once, medium depth.
RANDOM   (k < 1) → General mode: simple language, analogies, broad strokes.

The personalizer also injects:
  - topic_hint: the predicted next topic (from predictor.py)
  - confidence_score: from the predictor, so models can calibrate hedging
  - user_type: raw kurtosis_type string

All of this is packed into a PersonalizationContext dict that is merged
into the ALIState as `ubi_context` and consumed by the chain / synthesis nodes.
"""

from typing import Dict, Any

# ── Depth profiles per user type ──────────────────────────────────────────────
_DEPTH_PROFILES: Dict[str, Dict[str, Any]] = {
    "FOCUSED": {
        "depth":        "expert",
        "skip_basics":  True,
        "use_jargon":   True,
        "show_tradeoffs": True,
        "tone":         "precise and technical",
        "response_style": (
            "Skip introductory explanations. Assume expert-level knowledge. "
            "Focus on edge cases, performance implications, and implementation details. "
            "Use technical terminology without defining it. Include concrete code examples."
        ),
    },
    "MODERATE": {
        "depth":        "intermediate",
        "skip_basics":  False,
        "use_jargon":   True,
        "show_tradeoffs": True,
        "tone":         "clear and balanced",
        "response_style": (
            "Balance depth with clarity. Define technical terms briefly on first use. "
            "Provide practical examples alongside theory. "
            "Include relevant tradeoffs without overwhelming detail."
        ),
    },
    "RANDOM": {
        "depth":        "general",
        "skip_basics":  False,
        "use_jargon":   False,
        "show_tradeoffs": False,
        "tone":         "friendly and approachable",
        "response_style": (
            "Use simple, everyday language. Explain concepts from first principles. "
            "Use analogies and real-world examples. Avoid jargon or define it clearly. "
            "Keep the tone warm and encouraging."
        ),
    },
}


# ── Prompt injection builder ───────────────────────────────────────────────────
def build_personalization_prefix(context: Dict[str, Any]) -> str:
    """
    Build a system-prompt prefix that injects UBI personalisation.
    Prepended to the system_prompt string before any model call.
    """
    utype      = context.get("user_type", "MODERATE")
    profile    = _DEPTH_PROFILES.get(utype, _DEPTH_PROFILES["MODERATE"])
    topic_hint = context.get("topic_hint", "")
    confidence = context.get("confidence_score", 0.0)

    lines = [
        f"[UBI PERSONALISATION]",
        f"User behaviour type: {utype}",
        f"Response style: {profile['response_style']}",
    ]

    if topic_hint and confidence > 0.4:
        lines.append(
            f"Predicted focus area: '{topic_hint}' (confidence {confidence:.0%}). "
            f"Proactively address this if relevant to the question."
        )
    elif topic_hint and confidence > 0.2:
        lines.append(
            f"User may be exploring '{topic_hint}' — acknowledge if naturally relevant."
        )

    lines.append("[END UBI]")
    return "\n".join(lines)


# ── Main personalizer function ─────────────────────────────────────────────────
async def personalize(
    user_id: str,
    user_input: str,
) -> Dict[str, Any]:
    """
    Build the full PersonalizationContext for this user and input.

    Calls:
      - pattern_learner.get_user_profile()  → kurtosis_type, top_topics
      - predictor.predict()                 → predicted_topic, confidence_score

    Returns:
        {
          user_type:            "FOCUSED" | "MODERATE" | "RANDOM",
          kurtosis_score:       float,
          depth:                "expert" | "intermediate" | "general",
          topic_hint:           str,
          confidence_score:     float,
          personalization_prefix: str,   # inject into system_prompt
          top_topics:           List[str]
        }
    """
    from ubi.pattern_learner import get_user_profile
    from ubi.predictor      import predict

    profile    = await get_user_profile(user_id)
    prediction = await predict(user_id)

    utype      = profile.get("kurtosis_type", "MODERATE")
    k_score    = profile.get("kurtosis_score", 0.0)
    depth_cfg  = _DEPTH_PROFILES.get(utype, _DEPTH_PROFILES["MODERATE"])

    context = {
        "user_type":        utype,
        "kurtosis_score":   k_score,
        "depth":            depth_cfg["depth"],
        "topic_hint":       prediction.get("predicted_topic", ""),
        "confidence_score": prediction.get("confidence_score", 0.0),
        "top_topics":       prediction.get("top_topics", []),
    }

    context["personalization_prefix"] = build_personalization_prefix(context)

    return context
