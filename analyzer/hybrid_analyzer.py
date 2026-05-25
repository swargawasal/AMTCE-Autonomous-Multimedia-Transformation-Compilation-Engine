"""Hybrid Clip Analyzer for RAG-driven video editing.

Combines local signal ground truth (energy, pace, motion) with optional
Gemini-powered semantic enrichment (category, style).
"""

from __future__ import annotations

import os
from typing import Dict, Any, Optional

import google.generativeai as genai
from Intelligence_Modules.gemini_governor import gemini_router


class HybridAnalyzer:
    """Combines local video signals with optional LLM semantic analysis."""

    def __init__(self):
        self.router = gemini_router

    def _normalize_score(self, value: Any) -> float:
        """Convert signal strings or floats to a 0-1 normalized score."""
        if isinstance(value, (int, float)):
            return min(1.0, max(0.0, float(value)))
        
        mapping = {"high": 1.0, "medium": 0.5, "steady": 0.5, "low": 0.2, "fast": 1.0, "slow": 0.2}
        return mapping.get(str(value).lower(), 0.5)

    def _get_deterministic_category(self, motion: float, pace: str) -> str:
        """Rule-based category inference based on architect's specs."""
        if motion > 0.7 and pace == "fast":
            return "fitness"
        elif motion > 0.6 and pace == "fast":
            return "fashion"
        elif motion < 0.3 and pace == "slow":
            return "podcast"
        elif pace == "steady":
            return "travel"
        return "generic"

    def _infer_with_gemini(self, local_profile: Dict[str, Any]) -> Dict[str, str]:
        prompt = f"""Analyze these video signals:
{local_profile}

Return ONLY a JSON object:
{{
  "category": "fashion | fitness | podcast | travel | generic",
  "style": "cinematic | energetic | minimalist | luxury | gritty"
}}"""

        try:
            res_txt = self.router.generate(
                task_type="analyzer",
                prompt=prompt,
                module_name="hybrid_analyzer",
                gen_config={"response_mime_type": "application/json"}
            )
            if not res_txt: return {}

            # Attempt to parse as JSON
            import json
            data = json.loads(res_txt)
            return {k.lower(): v.lower() for k, v in data.items() if isinstance(v, str)}
        except Exception as e:
            print(f"[DEBUG] [ANALYZER] Gemini inference failed: {e}")
        return {}

    def analyze(self, signals: Dict[str, Any]) -> Dict[str, Any]:
        """Perform hybrid analysis with strict confidence gating."""

        # 1. Normalize Signals
        motion_val = signals.get("motion_intensity", "medium")
        pace_val = signals.get("pace", "steady")
        density_val = signals.get("cut_density", "medium")

        motion_score = self._normalize_score(motion_val)
        pace_score = self._normalize_score(pace_val)
        density_score = self._normalize_score(density_val)

        # 2. Compute Signal Confidence
        signal_confidence = (
            motion_score * 0.4 +
            pace_score * 0.3 +
            density_score * 0.3
        )

        # 3. Deterministic Category Mapping
        det_category = self._get_deterministic_category(motion_score, pace_val)
        
        category = det_category
        style = "derived_from_signals"
        source = "deterministic"
        gemini_used = False

        # 4. Gating Rule
        if signal_confidence <= 0.8:
            gemini_used = True
            gemini_traits = self._infer_with_gemini({
                "energy": signals.get("energy", "medium"),
                "pace": pace_val,
                "motion": motion_val,
                "confidence": round(signal_confidence, 2)
            })
            
            if gemini_traits.get("category"):
                category = gemini_traits["category"]
                source = "gemini"
            if gemini_traits.get("style"):
                style = gemini_traits["style"]

        # 5. Final Profile Construction (Hard Rule: Never Empty)
        final_profile = {
            "category": category or "generic",
            "style": style or "professional",
            "category_source": source,
            "energy": signals.get("energy", "medium"),
            "pace": pace_val,
            "motion_intensity": motion_val,
            "cut_density": density_val,
            "platform": "short-form",
            "signal_confidence": round(signal_confidence, 2),
            "gemini_used": gemini_used
        }

        # 6. Mandatory Logging
        print(f"[ANALYZER] gemini_used={gemini_used}")
        print(f"[ANALYZER] confidence={round(signal_confidence, 2)}")
        print(f"[ANALYZER] category_source={source}")

        return final_profile
