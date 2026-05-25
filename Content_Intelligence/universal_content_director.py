"""
Content_Intelligence/universal_content_director.py
----------------------------------------------------
Universal Content Director — Human Content Strategist Simulation.
Zero Extra Gemini API Calls.

Inputs:
  - forensic_result: The enriched dict from forensic_analyzer.py
    (already contains content_director block from Gemini)
  - trend_context:   Structured dict from Trend_Intelligence/trend_engine.py
  - user_trend_input: Raw list of user-provided trend hints (for display/logging)
  - frames:          Already-extracted frame paths (not used for API calls)

Output: content_strategy JSON + feature_flags dict

Design philosophy:
  Gemini (in forensic_analyzer) has already "seen" the video and provided:
    - narrative, tone, editing_style, feature_commands
  This module enriches those decisions with trend relevance scores and
  builds the final unified content_strategy without any new Gemini calls.

  User trend hints are interpreted ONLY as signals — they never directly
  control editing. Trend relevance is computed by keyword matching between
  trend topics and detected visual entities/narrative.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("universal_content_director")

# ── Feature flag whitelist ─────────────────────────────────────────────────────
ALLOWED_FLAGS = {
    "enable_fast_pacing",
    "enable_cinematic_zoom",
    "enable_speed_ramps",
    "enable_voiceover",
    "enable_price_tags",
    "enable_news_style",
    "enable_fashion_caption",  # backward-compat
    "enable_caption",          # backward-compat
}

# ── Editing style → Feature flag mapping ──────────────────────────────────────
STYLE_FLAG_MAP: Dict[str, Dict[str, bool]] = {
    "fast_social":     {"enable_fast_pacing": True},
    "cinematic":       {"enable_cinematic_zoom": True, "enable_speed_ramps": True},
    "dramatic":        {"enable_cinematic_zoom": True, "enable_speed_ramps": True},
    "fashion_showcase":{"enable_price_tags": True, "enable_fashion_caption": True, "enable_voiceover": True},
    "product_review":  {"enable_price_tags": True, "enable_fashion_caption": True, "enable_voiceover": True},
    "documentary":     {"enable_voiceover": True, "enable_cinematic_zoom": True},
    "news":            {"enable_news_style": True, "enable_voiceover": True},
    "vlog":            {"enable_voiceover": True, "enable_fast_pacing": True},
}

# ── Narrative → Feature flag mapping ─────────────────────────────────────────
NARRATIVE_FLAG_MAP: Dict[str, Dict[str, bool]] = {
    "celebrity_highlight": {"enable_caption": True, "enable_voiceover": True},
    "humor":               {"enable_fast_pacing": True},
    "motivational":        {"enable_voiceover": True, "enable_cinematic_zoom": True},
    "fashion_moment":      {"enable_price_tags": True, "enable_fashion_caption": True, "enable_voiceover": True},
    "news_context":        {"enable_news_style": True, "enable_voiceover": True},
}

# ── Trend + narrative "boost" strategy ────────────────────────────────────────
# When a trend keyword matches a known topic type, bias toward a narrative + hook
TREND_NARRATIVE_BOOSTS: Dict[str, dict] = {
    "crypto":     {"narrative": "humor",    "hook_suffix": "Nobody understands it, but everyone's buying."},
    "ai":         {"narrative": "news_context", "hook_suffix": "This AI trend is changing everything."},
    "fashion":    {"narrative": "fashion_moment", "hook_suffix": "This look just broke the internet."},
    "celebrity":  {"narrative": "celebrity_highlight", "hook_suffix": "This moment went viral for a reason."},
    "meme":       {"narrative": "humor",    "hook_suffix": "The internet can't stop talking about this."},
    "gossip":     {"narrative": "celebrity_highlight", "hook_suffix": "Everyone's reacting to this."},
    "motivation": {"narrative": "motivational", "hook_suffix": "This is exactly what you needed today."},
    "news":       {"narrative": "news_context",  "hook_suffix": "Breaking: Here's what you missed."},
    "slang":      {"narrative": "humor",    "hook_suffix": "If you know, you know 😂"},
    "viral":      {"narrative": "celebrity_highlight", "hook_suffix": "This is going viral for a reason."},
}

DEFAULT_STRATEGY = {
    "visual_summary":       "",
    "detected_entities":    [],
    "viewer_attention":     "",
    "trend_relevance":      0.0,
    "trend_topics_matched": [],
    "possible_narratives":  [],
    "recommended_narrative":"",
    "tone":                 "",
    "editing_style":        "",
    "engagement_hook":      "",
    "feature_commands": {f: False for f in ALLOWED_FLAGS if not f.startswith("enable_fashion") and not f.startswith("enable_caption")},
}


class UniversalContentDirector:
    """
    Combines forensic Gemini output + trend context → unified content strategy.
    Zero extra API calls.
    """

    def generate_content_strategy(
        self,
        frames: Optional[List[str]],
        forensic_result: Optional[dict],
        trend_context: Optional[dict] = None,
        user_trend_input: Optional[List[str]] = None,
    ) -> Tuple[dict, dict]:
        """
        Build the full content_strategy and derive feature flags.

        Returns:
            (strategy_dict, feature_flags_dict)  — never raises
        """
        try:
            if not forensic_result or not isinstance(forensic_result, dict):
                logger.warning("🎯 [UCD] No forensic result — returning defaults")
                return DEFAULT_STRATEGY.copy(), {f: False for f in ALLOWED_FLAGS}

            # ── Extract Gemini's content_director block ────────────────────────
            cd = forensic_result.get("content_director", {})
            if not isinstance(cd, dict):
                cd = {}

            # ── Extract trend signals ──────────────────────────────────────────
            tc = {}
            if trend_context and isinstance(trend_context, dict):
                tc = trend_context.get("trend_context", {})

            trend_topics   = tc.get("topics", [])
            trend_keywords = tc.get("keywords", [])
            trend_strength = float(tc.get("trend_strength", 0.0))

            # ── Visual entities from content_director ──────────────────────────
            entities = cd.get("detected_entities", [])
            if not isinstance(entities, list):
                entities = []

            # ── Compute trend relevance via keyword overlap ────────────────────
            trend_relevance, matched_topics = self._compute_trend_relevance(
                trend_topics, trend_keywords, entities,
                cd.get("recommended_narrative", ""),
                cd.get("tone", ""),
            )

            # ── Determine trend boost (if any) ────────────────────────────────
            trend_boost = self._get_trend_boost(trend_keywords, matched_topics)

            # ── Build recommended narrative ────────────────────────────────────
            # Gemini's choice takes priority; trend boost only fills gaps
            recommended_narrative = cd.get("recommended_narrative", "")
            if not recommended_narrative and trend_boost:
                recommended_narrative = trend_boost.get("narrative", "")
                logger.info(f"🎯 [UCD] Trend boost applied narrative: {recommended_narrative}")

            # ── Build engagement hook ──────────────────────────────────────────
            engagement_hook = cd.get("engagement_hook", "")
            if not engagement_hook and trend_boost:
                engagement_hook = trend_boost.get("hook_suffix", "")

            # ── Assemble final strategy ────────────────────────────────────────
            strategy = {
                "visual_summary":        cd.get("visual_event", ""),
                "detected_entities":     entities,
                "viewer_attention":      cd.get("viewer_attention", ""),
                "trend_relevance":       round(trend_relevance, 3),
                "trend_topics_matched":  matched_topics,
                "possible_narratives":   cd.get("possible_narratives", []),
                "recommended_narrative": recommended_narrative,
                "tone":                  cd.get("tone", ""),
                "editing_style":         cd.get("editing_style", ""),
                "engagement_hook":       engagement_hook,
                "internet_context":      cd.get("internet_context", []),
                "trend_strength":        round(trend_strength, 3),
                "feature_commands":      cd.get("feature_commands", {}),
            }

            # ── Derive flags ───────────────────────────────────────────────────
            flags = self._derive_flags(strategy)

            # ── Log summary ───────────────────────────────────────────────────
            logger.info(
                f"🎯 [UCD] Strategy: narrative={strategy['recommended_narrative']} "
                f"style={strategy['editing_style']} "
                f"trend_relevance={trend_relevance:.2f} "
                f"hook='{engagement_hook[:60]}'"
            )
            if user_trend_input:
                logger.info(f"🎯 [UCD] User hints considered: {user_trend_input[:3]}")
            if matched_topics:
                logger.info(f"🎯 [UCD] Matched trends: {matched_topics}")

            active = [k.replace("enable_", "") for k, v in flags.items() if v]
            logger.info(f"🎯 [UCD] Active flags: [{', '.join(active) or 'none'}]")

            return strategy, flags

        except Exception as e:
            logger.warning(f"🎯 [UCD] generate_content_strategy error: {e}. Falling back.")
            return DEFAULT_STRATEGY.copy(), {f: False for f in ALLOWED_FLAGS}

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _compute_trend_relevance(
        self,
        trend_topics: List[str],
        trend_keywords: List[str],
        entities: List[str],
        narrative: str,
        tone: str,
    ) -> Tuple[float, List[str]]:
        """
        Score how relevant the detected visual content is to current trends.
        Returns (relevance_score 0.0-1.0, list of matched trend topics).
        """
        if not trend_topics and not trend_keywords:
            return 0.0, []

        matched = []
        score = 0.0

        # Build a searchable text from visual signals
        visual_text = " ".join(
            [str(e).lower() for e in entities]
            + [narrative.lower(), tone.lower()]
        )

        for topic in trend_topics:
            topic_words = re.findall(r"[a-zA-Z]+", topic.lower())
            for word in topic_words:
                if len(word) > 2 and word in visual_text:
                    matched.append(topic)
                    score += 0.3
                    break  # one match per topic is enough

        # Keyword overlap
        for kw in trend_keywords:
            if len(kw) > 2 and kw in visual_text:
                score += 0.1

        relevance = min(1.0, score)
        return relevance, list(dict.fromkeys(matched))  # deduplicate

    def _get_trend_boost(
        self,
        keywords: List[str],
        matched_topics: List[str],
    ) -> Optional[dict]:
        """
        Pick the highest-priority trend boost signal based on keywords.
        Returns a boost dict or None.
        """
        for kw in keywords:
            for boost_key, boost in TREND_NARRATIVE_BOOSTS.items():
                if boost_key in kw.lower():
                    return boost
        for topic in matched_topics:
            topic_lower = topic.lower()
            for boost_key, boost in TREND_NARRATIVE_BOOSTS.items():
                if boost_key in topic_lower:
                    return boost
        return None

    def _derive_flags(self, strategy: dict) -> dict:
        """Build feature flags from Gemini commands + editing_style + narrative."""
        flags = {f: False for f in ALLOWED_FLAGS}

        # Priority 1: Gemini's explicit feature_commands (whitelist-filtered)
        for k, v in strategy.get("feature_commands", {}).items():
            if k in flags:
                flags[k] = flags[k] or bool(v)

        # Priority 2: editing_style mapping
        style = strategy.get("editing_style", "").lower().strip()
        for k, v in STYLE_FLAG_MAP.get(style, {}).items():
            if k in flags:
                flags[k] = flags[k] or v

        # Priority 3: narrative mapping
        narrative = strategy.get("recommended_narrative", "").lower().strip()
        for k, v in NARRATIVE_FLAG_MAP.get(narrative, {}).items():
            if k in flags:
                flags[k] = flags[k] or v

        return flags


# ── Module singleton + convenience ────────────────────────────────────────────

_director: Optional[UniversalContentDirector] = None


def get_director() -> UniversalContentDirector:
    global _director
    if _director is None:
        _director = UniversalContentDirector()
    return _director


def generate_content_strategy(
    frames: Optional[List[str]] = None,
    forensic_result: Optional[dict] = None,
    trend_context: Optional[dict] = None,
    user_trend_input: Optional[List[str]] = None,
) -> Tuple[dict, dict]:
    """Convenience function for orchestrator.py. Never raises."""
    return get_director().generate_content_strategy(
        frames, forensic_result, trend_context, user_trend_input
    )
