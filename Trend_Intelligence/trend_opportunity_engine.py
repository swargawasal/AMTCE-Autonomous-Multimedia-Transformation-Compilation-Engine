"""
Trend_Intelligence/trend_opportunity_engine.py
-----------------------------------------------
Trend Opportunity Analyzer + Angle Innovation Engine.
Zero Extra Gemini API Calls.

Evaluates trend lifecycle stage, competition density, and opportunity score.
When competition is HIGH, the Angle Innovation Engine generates a fresh narrative
angle to avoid content duplication and boost the trend differently.

Trend lifecycle:
  emerging → viral → saturated → fading

Opportunity score formula:
  trend_opportunity_score =
      0.4 * trend_growth_rate
    + 0.3 * trend_volume
    - 0.3 * competition_density

Angle Innovation (when competition = HIGH):
  Selects from: humor, satire, explanation, reaction, comparison, story, unexpected_twist
  Generates a context-aware engagement hook based on detected trends.

Output stored in profile_data["trend_opportunity"]:
  {
    "trend_stage": "viral",
    "competition_level": "high",
    "opportunity_score": 0.63,
    "recommended_angle": "humor",
    "engagement_hook": "...",
    "feature_commands": { ... }
  }
"""

import logging
import random
from typing import Dict, List, Optional

logger = logging.getLogger("trend_opportunity_engine")

# ── Constants ──────────────────────────────────────────────────────────────────

# Trend stage thresholds (using opportunity score)
STAGE_THRESHOLDS = [
    ("fading",    0.0),
    ("emerging",  0.2),
    ("viral",     0.5),
    ("saturated", 0.75),
]

# Competition level from competition_density
def _competition_level(density: float) -> str:
    if density >= 0.65:
        return "high"
    elif density >= 0.35:
        return "medium"
    return "low"

# Angle strategies for the Innovation Engine
ANGLE_STRATEGIES = [
    "humor", "satire", "explanation", "reaction",
    "comparison", "story", "unexpected_twist",
]

# Angle → flags
ANGLE_FLAG_MAP: Dict[str, Dict[str, bool]] = {
    "humor":           {"enable_fast_pacing": True},
    "satire":          {"enable_fast_pacing": True, "enable_voiceover": True},
    "explanation":     {"enable_voiceover": True, "enable_news_style": True},
    "reaction":        {"enable_fast_pacing": True},
    "comparison":      {"enable_voiceover": True, "enable_cinematic_zoom": True},
    "story":           {"enable_voiceover": True, "enable_cinematic_zoom": True},
    "unexpected_twist":{"enable_speed_ramps": True, "enable_fast_pacing": True},
}

# Allowed flags (whitelist — Gemini never invents new ones)
ALLOWED_FLAGS = {
    "enable_fast_pacing", "enable_cinematic_zoom",
    "enable_speed_ramps", "enable_voiceover",
    "enable_price_tags", "enable_news_style",
}

# ── Angle Innovation hooks (templates indexed by angle + keyword) ──────────────
HOOK_TEMPLATES: Dict[str, Dict[str, str]] = {
    "humor": {
        "crypto":    "Everyone investing in it but nobody understands it.",
        "ai":        "AI girlfriend after you forget to charge her 💀",
        "celebrity": "When even they can't explain what happened.",
        "fashion":   "Outfit so loud it has its own fanbase.",
        "default":   "Nobody expected this — and that's exactly the problem.",
    },
    "satire": {
        "crypto":    "Experts predicted this. Nobody listened.",
        "ai":        "AI: doing your job while you take the blame.",
        "default":   "Imagine being this wrong on the internet.",
    },
    "explanation": {
        "crypto":    "Here's what that crypto trend actually means.",
        "ai":        "AI trend explained in 10 seconds.",
        "default":   "This trend has a reason — here it is.",
    },
    "reaction": {
        "default":   "My reaction watching this unfold in real time.",
    },
    "comparison": {
        "fashion":   "Same outfit. Different energy. You decide.",
        "default":   "Before vs After. Which version wins?",
    },
    "story": {
        "default":   "The story behind this moment is wilder than you think.",
    },
    "unexpected_twist": {
        "default":   "Nobody saw this coming. Neither did we.",
    },
}


def _get_hook_template(angle: str, keywords: List[str]) -> str:
    """Pick the best hook template for the given angle and keywords."""
    templates = HOOK_TEMPLATES.get(angle, {})
    for kw in keywords:
        if kw in templates:
            return templates[kw]
    return templates.get("default", "This changes everything.")


# ── Opportunity math ───────────────────────────────────────────────────────────

def _estimate_growth_rate(topics: List[str], keywords: List[str]) -> float:
    """
    Estimate growth rate heuristically from topic/keyword count and diversity.
    Returns 0.0–1.0. (No external API call — purely signal-based.)
    """
    # More unique topics = higher growth signal
    base = min(1.0, len(set(topics)) / 10.0)
    # Keyword richness adds diversity signal
    diversity = min(0.5, len(set(keywords)) / 20.0)
    return round(min(1.0, base + diversity), 3)


def _estimate_volume(topics: List[str]) -> float:
    """Normalise topic count to a 0.0–1.0 volume score."""
    return round(min(1.0, len(topics) / 15.0), 3)


def _estimate_competition(keywords: List[str], trend_strength: float) -> float:
    """
    High trend strength + many keywords → more competition.
    Returns 0.0–1.0.
    """
    kw_density = min(0.7, len(keywords) / 20.0)
    return round(min(1.0, kw_density * 0.6 + trend_strength * 0.4), 3)


def _determine_stage(opportunity_score: float, competition_density: float) -> str:
    """Map opportunity score + competition to a lifecycle stage."""
    if competition_density >= 0.65:
        return "saturated"
    if opportunity_score >= 0.5:
        return "viral"
    if opportunity_score >= 0.2:
        return "emerging"
    return "fading"


# ── Angle Innovation ───────────────────────────────────────────────────────────

def _innovate_angle(
    existing_angle: str,
    keywords: List[str],
    competition_level: str,
    trend_stage: str,
) -> dict:
    """
    When competition is high, generate a fresh narrative angle.
    If competition is low/medium, keep existing angle.
    Returns {angle, hook, flags}.
    """
    if competition_level != "high":
        # Keep existing angle, derive hook from templates if possible
        hook = _get_hook_template(existing_angle, keywords)
        flags = ANGLE_FLAG_MAP.get(existing_angle, {})
        return {"angle": existing_angle, "hook": hook, "flags": flags}

    # High competition → pick the freshest angle
    # Prefer angles NOT matching the existing one for novelty
    candidates = [a for a in ANGLE_STRATEGIES if a != existing_angle.lower().strip()]
    if not candidates:
        candidates = ANGLE_STRATEGIES

    # Weight: humor + unexpected_twist rank highest for high competition
    priority = ["humor", "unexpected_twist", "satire", "comparison", "reaction", "story", "explanation"]
    chosen = next((a for a in priority if a in candidates), candidates[0])

    hook = _get_hook_template(chosen, keywords)
    flags = ANGLE_FLAG_MAP.get(chosen, {})

    logger.info(
        f"🔀 [Angle Innovation] High competition detected. "
        f"Switching angle: {existing_angle or 'none'} → {chosen}"
    )

    return {"angle": chosen, "hook": hook, "flags": flags}


# ── Main Engine ────────────────────────────────────────────────────────────────

DEFAULT_OPPORTUNITY = {
    "trend_opportunity": {
        "competition_level": "low",
        "opportunity_score": 0.0,
        "angle_innovation_required": False
    },
    "competition_level": "low",
    "opportunity_score": 0.0,
    "angle_innovation_required": False,
    "trend_stage":        "unknown",
    "recommended_angle":  "",
    "engagement_hook":    "",
    "feature_commands":   {f: False for f in ALLOWED_FLAGS},
}


class TrendOpportunityEngine:
    """
    Evaluates trend lifecycle and opportunity score.
    Runs the Angle Innovation Engine when competition is HIGH.
    """

    def analyse(
        self,
        trend_context: Optional[dict] = None,
        existing_strategy: Optional[dict] = None,
    ) -> dict:
        """
        Compute a trend_opportunity result.

        Args:
            trend_context:    Dict from TrendEngine: { trend_context: {topics, keywords, trend_strength} }
            existing_strategy: The current universal_content_strategy from profile_data.

        Returns:
            Dict matching the trend_opportunity schema. Never raises.
        """
        try:
            tc = {}
            if trend_context and isinstance(trend_context, dict):
                tc = trend_context.get("trend_context", trend_context)

            topics         = tc.get("topics",         [])
            keywords       = tc.get("keywords",        [])
            trend_strength = float(tc.get("trend_strength", 0.0))

            # No trend signal at all → return minimal defaults
            if not topics and not keywords:
                logger.info("🔀 [TOE] No trend signal — skipping opportunity analysis")
                return DEFAULT_OPPORTUNITY.copy()

            # ── Compute metrics ────────────────────────────────────────────────
            growth_rate  = _estimate_growth_rate(topics, keywords)
            volume       = _estimate_volume(topics)
            competition  = _estimate_competition(keywords, trend_strength)

            opportunity_score = max(0.0, min(1.0,
                0.4 * growth_rate
              + 0.3 * volume
              - 0.3 * competition
            ))
            opportunity_score = round(opportunity_score, 3)

            comp_level  = _competition_level(competition)
            stage       = _determine_stage(opportunity_score, competition)

            # ── Existing angle from content strategy ───────────────────────────
            existing_angle = ""
            if existing_strategy and isinstance(existing_strategy, dict):
                existing_angle = str(existing_strategy.get("recommended_narrative", ""))

            # ── Angle Innovation ───────────────────────────────────────────────
            innovation = _innovate_angle(existing_angle, keywords, comp_level, stage)

            # Build clean feature_commands (whitelist-filtered)
            feature_commands = {f: False for f in ALLOWED_FLAGS}
            for k, v in innovation["flags"].items():
                if k in feature_commands:
                    feature_commands[k] = bool(v)

            result = {
                "trend_opportunity": {
                    "competition_level": comp_level,
                    "opportunity_score": opportunity_score,
                    "angle_innovation_required": bool(comp_level == "high")
                },
                "competition_level": comp_level,
                "opportunity_score": opportunity_score,
                "angle_innovation_required": bool(comp_level == "high"),
                "trend_stage":       stage,
                "recommended_angle": innovation["angle"],
                "engagement_hook":   innovation["hook"],
                "feature_commands":  feature_commands,
                # Debug signals (informational)
                "_debug": {
                    "growth_rate":   growth_rate,
                    "volume":        volume,
                    "competition":   round(competition, 3),
                }
            }

            logger.info(
                f"🔀 [TOE] stage={stage} competition={comp_level} "
                f"opportunity={opportunity_score:.3f} angle={innovation['angle']} "
                f"hook='{innovation['hook'][:60]}'"
            )

            return result

        except Exception as e:
            logger.warning(f"🔀 [TOE] analyse() failed: {e}. Returning defaults.")
            return DEFAULT_OPPORTUNITY.copy()


# ── Module singleton + convenience ─────────────────────────────────────────────

_engine: Optional[TrendOpportunityEngine] = None


def get_engine() -> TrendOpportunityEngine:
    global _engine
    if _engine is None:
        _engine = TrendOpportunityEngine()
    return _engine


def analyse_trend_opportunity(
    trend_context: Optional[dict] = None,
    existing_strategy: Optional[dict] = None,
) -> dict:
    """Convenience function for orchestrator.py. Never raises."""
    return get_engine().analyse(trend_context, existing_strategy)
