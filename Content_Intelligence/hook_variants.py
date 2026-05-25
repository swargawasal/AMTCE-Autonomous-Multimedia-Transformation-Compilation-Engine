"""
Content_Intelligence/hook_variants.py
--------------------------------------
Multi-Variant Hook Generator — Zero Extra API Calls.

Generates multiple hook candidates and selects the strongest.
Provides guidance to SmartSceneEditor as a hint, not a forced cut.

Formula:
  variant_score = 0.5 * visual_hook_score + 0.3 * narrative_relevance + 0.2 * curiosity_gap
"""

import logging
import random
from typing import Dict, List, Optional

logger = logging.getLogger("hook_variants")

DEFAULT_VARIANT = {
    "hook_time_hint": 0.0,
    "confidence": 0.0
}

class MultiVariantHookGenerator:
    """
    Generates multiple hook candidates and returns the best one.
    """

    def generate(
        self,
        base_hook: Optional[dict] = None,
        content_strategy: Optional[dict] = None
    ) -> dict:
        """
        Evaluate hook candidates and return the best variant.

        Args:
            base_hook: Output from hook_engine (the visual hook).
                       e.g. {"hook_time": 2.1, "hook_score": 0.92, ...}
            content_strategy: Output from universal_content_director.

        Returns:
            Dict containing the "hook_variant" matching the pipeline spec.
        """
        try:
            if not base_hook or "hook_time" not in base_hook:
                logger.info("🎣 [HookVariants] No base hook provided. Returning default.")
                return {"hook_variant": DEFAULT_VARIANT.copy()}

            visual_hook_time = base_hook.get("hook_time", 0.0)
            visual_hook_score = base_hook.get("hook_score", 0.0)
            
            # Extract strategy signals (0.0 to 1.0 proxies)
            # Default to 0.5 if not strongly indicated
            narrative_relevance = 0.5
            curiosity_gap = 0.5
            
            if content_strategy:
                # E.g., if we have a strong engagement hook text, assume higher curiosity gap
                hook_text = content_strategy.get("engagement_hook", "")
                if hook_text and len(hook_text) > 10:
                    curiosity_gap = min(1.0, 0.5 + (len(hook_text) / 100.0))
                    
                trend_rel = content_strategy.get("trend_relevance", 0.5)
                narrative_relevance = trend_rel
                
            # Create candidates (slight time perturbations and score variations)
            candidates = []
            
            # Variant 1: Pure Visual (The base hook)
            candidates.append({
                "time": visual_hook_time,
                "score": self._score_variant(visual_hook_score, narrative_relevance, curiosity_gap)
            })
            
            # Variant 2: Narrative Lead (Slightly earlier to establish context)
            num_cand2_time = max(0.0, visual_hook_time - 0.5)
            candidates.append({
                "time": num_cand2_time,
                "score": self._score_variant(visual_hook_score * 0.8, narrative_relevance * 1.2, curiosity_gap)
            })
            
            # Variant 3: Curiosity Cliff (Slightly later to build tension)
            num_cand3_time = min(3.0, visual_hook_time + 0.3)
            candidates.append({
                "time": num_cand3_time,
                "score": self._score_variant(visual_hook_score * 0.9, narrative_relevance, curiosity_gap * 1.3)
            })
            
            # Select the best candidate
            best_candidate = max(candidates, key=lambda x: x["score"])
            
            confidence = round(min(1.0, best_candidate["score"]), 3)
            
            logger.info(
                f"🎣 [HookVariants] Selected hint at t={best_candidate['time']:.2f}s "
                f"with confidence {confidence}"
            )

            return {
                "hook_variant": {
                    "hook_time_hint": float(best_candidate["time"]),
                    "confidence": confidence
                }
            }

        except Exception as e:
            logger.warning(f"🎣 [HookVariants] Generation failed: {e}. Returning default.")
            return {"hook_variant": DEFAULT_VARIANT.copy()}

    def _score_variant(self, visual: float, narrative: float, curiosity: float) -> float:
        """
        variant_score = 0.5 * visual_hook_score + 0.3 * narrative_relevance + 0.2 * curiosity_gap
        """
        # Clamp inputs to [0, 1]
        v = max(0.0, min(1.0, float(visual)))
        n = max(0.0, min(1.0, float(narrative)))
        c = max(0.0, min(1.0, float(curiosity)))
        
        return 0.5 * v + 0.3 * n + 0.2 * c


# ── Module singleton + convenience ─────────────────────────────────────────────

_generator: Optional[MultiVariantHookGenerator] = None

def get_generator() -> MultiVariantHookGenerator:
    global _generator
    if _generator is None:
        _generator = MultiVariantHookGenerator()
    return _generator

def generate_hook_variant(base_hook: Optional[dict] = None, content_strategy: Optional[dict] = None) -> dict:
    return get_generator().generate(base_hook, content_strategy)
