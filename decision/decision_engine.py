"""Decision engine wrapping Gemini generations for with/without RAG modes."""

from __future__ import annotations

import json
import os
from typing import Dict, List

from Intelligence_Modules.gemini_governor import gemini_router

TEMPERATURE = 0.2


def _format_profile(profile: Dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in profile.items())


def _format_patterns(patterns: List[Dict]) -> str:
    lines = []
    for idx, item in enumerate(patterns, start=1):
        meta = item.get("metadata", {})
        lines.append(f"Pattern {idx}:")
        lines.append(f"- Strategy: {meta.get('strategy', '')}")
        lines.append(f"- Hook: {meta.get('hook', '')}")
        if item.get("score") is not None:
            lines.append(f"- Score: {item['score']:.4f}")
        lines.append("")
    return "\n".join(lines).strip()


def generate_without_rag(profile: Dict) -> str:
    prompt = f"""You are a professional video editor.

Given this clip profile:
{_format_profile(profile)}

Return a JSON object with:
{{
  "hook": "...",
  "editing_style": "...",
  "cut_density": "high | medium | low",
  "transition_style": "cut | whip | smooth",
  "pacing": "fast | steady | slow",
  "effects": ["..."],
  "strategy_tags": ["..."],
  "reasoning": "..."
}}
"""
    return gemini_router.generate(
        task_type="master", prompt=prompt, module_name="decision_engine"
    )


def generate_with_rag(profile: Dict, patterns: List[Dict]) -> str:
    """
    RAG DECODER: Zero Gemini calls allowed.
    Strictly follows retrieved patterns.
    """
    if not patterns:
        # Emergency backup if patterns are missing
        patterns = [
            {"metadata": {"strategy": "default_engagement", "hook": "Dynamic opening"}, "score": 0.0}
        ]

    top_pattern = patterns[0]
    top_score = top_pattern.get("score", 1.0)
    rag_confidence = "HIGH"

    if top_score < 0.65:
        rag_confidence = "LOW"
        print(f"[RAG_CONFIDENCE] score={top_score:.4f} status=LOW")
        # Fallback to second best match if available, else deterministic template
        if len(patterns) > 1:
            top_pattern = patterns[1]
        else:
            top_pattern = {"metadata": {"strategy": "default_engagement", "hook": "Dynamic opening"}, "score": 0.0}
    else:
        print(f"[RAG_CONFIDENCE] score={top_score:.4f} status=HIGH")

    meta = top_pattern.get("metadata", {})

    # Deterministic mapping
    plan = {
        "hook": meta.get("hook", "Attention-grabbing visual hook."),
        "editing_style": meta.get("strategy", "Modern short-form sync."),
        "rag_category": meta.get("category", ""),   # ← Genre Guard uses this
        "cut_density": "high" if profile.get("energy") == "high" else "medium",
        "transition_style": "whip" if profile.get("pace") == "fast" else "smooth",
        "pacing": profile.get("pace", "steady"),
        "effects": ["Pattern-based color grading", "Dynamic zooming"],
        "strategy_tags": ["rag_governed", "zero_call_enforced"],
        "reasoning": f"Architect-enforced deterministic RAG output. No Gemini used. Pattern: {meta.get('strategy', 'Default')}",
        "rag_confidence": rag_confidence,
        "rag_score": round(top_score, 4),
    }

    print(f"[RAG] deterministic=True")
    print(f"[RAG] gemini_used=False")
    print(f"[RAG] category={meta.get('category', 'unknown')} | pacing={plan['pacing']} | cut_density={plan['cut_density']} | confidence={rag_confidence} | score={round(top_score, 4)}")
    return json.dumps(plan, indent=2)
