"""
Content_Intelligence/content_intelligence_engine.py
----------------------------------------------------
Content Intelligence Layer — Zero Extra API Calls.

Parses the `content_director` block already embedded in the forensic_analyzer
Gemini response, derives feature flags, and optionally incorporates engagement
strategy signals from the learning loop.

Pipeline position: Step 1.75 (after Forensic Analysis, before Smart Scene Editor)
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("content_intelligence")

# ── Default empty blocks returned on any failure ───────────────────────────────

DEFAULT_CD_BLOCK = {
    "detected_entities":       [],
    "visual_event":            "",
    "viewer_attention":        "",
    "internet_context":        [],
    "possible_narratives":     [],
    "recommended_narrative":   "",
    "tone":                    "",
    "editing_style":           "",
    "engagement_hook":         "",
    "feature_commands": {
        "enable_fast_pacing":    False,
        "enable_cinematic_zoom": False,
        "enable_speed_ramps":    False,
        "enable_voiceover":      False,
        "enable_price_tags":     False,
        "enable_news_style":     False,
    },
}

# Complete flag set (with fashion_caption for backward compat with forensic flags)
DEFAULT_CI_FLAGS = {
    "enable_fast_pacing":     False,
    "enable_cinematic_zoom":  False,
    "enable_speed_ramps":     False,
    "enable_voiceover":       False,
    "enable_price_tags":      False,
    "enable_news_style":      False,
    "enable_fashion_caption": False,
    "enable_caption":         False,
}

# ── Editing-style → Feature flag mapping (Feature Command System) ──────────────
# SmartSceneEditor reads these flags; Gemini never invents new ones.
STYLE_FLAG_MAP: Dict[str, Dict[str, bool]] = {
    "fast_social": {
        "enable_fast_pacing":   True,
    },
    "cinematic": {
        "enable_cinematic_zoom": True,
        "enable_speed_ramps":    True,
    },
    "dramatic": {
        "enable_cinematic_zoom": True,
        "enable_speed_ramps":    True,
    },
    "fashion_showcase": {
        "enable_price_tags":      True,
        "enable_fashion_caption": True,
        "enable_voiceover":       True,
    },
    "product_review": {
        "enable_price_tags":      True,
        "enable_fashion_caption": True,
        "enable_voiceover":       True,
    },
    "documentary": {
        "enable_voiceover":       True,
        "enable_cinematic_zoom":  True,
    },
    "news": {
        "enable_news_style": True,
        "enable_voiceover":  True,
    },
    "vlog": {
        "enable_voiceover":    True,
        "enable_fast_pacing":  True,
    },
}

# Narrative intent → flags (secondary signal when editing_style alone is insufficient)
NARRATIVE_FLAG_MAP: Dict[str, Dict[str, bool]] = {
    "celebrity_highlight": {
        "enable_caption":   True,
        "enable_voiceover": True,
    },
    "humor": {
        "enable_fast_pacing": True,
    },
    "motivational": {
        "enable_voiceover":      True,
        "enable_cinematic_zoom": True,
    },
    "fashion_moment": {
        "enable_price_tags":      True,
        "enable_fashion_caption": True,
        "enable_voiceover":       True,
    },
    "news_context": {
        "enable_news_style": True,
        "enable_voiceover":  True,
    },
}


class ContentIntelligenceEngine:
    """
    Parses the content_director block from the forensic result
    and derives feature flags for the pipeline.
    """

    def interpret_visual_context(
        self,
        frames: Optional[List[str]],
        forensic_result: Optional[dict],
        strategy_signals: Optional[dict] = None,
    ) -> Tuple[dict, dict]:
        """
        Extract content director data from the enriched forensic result.

        Args:
            frames:           Already-extracted frame paths (available for
                              future local processing; not used for API calls).
            forensic_result:  Full dict from forensic_analyzer.py with a
                              `content_director` key embedded.
            strategy_signals: Optional dict from EngagementIntelligence:
                              {"preferred_style": str, "confidence_boost": float}
                              Used to log context; does NOT override Gemini.

        Returns:
            Tuple of:
              - cd_block  (dict): the content_director section
              - ci_flags  (dict): merged feature flags to write into profile_data
        """
        try:
            if not forensic_result or not isinstance(forensic_result, dict):
                logger.warning("🧠 [CI] No forensic result — returning defaults")
                return DEFAULT_CD_BLOCK.copy(), DEFAULT_CI_FLAGS.copy()

            cd_raw = forensic_result.get("content_director", {})
            if not cd_raw or not isinstance(cd_raw, dict):
                logger.warning(
                    "🧠 [CI] No content_director block in forensic result — "
                    "returning defaults (forensic flags preserved)"
                )
                return DEFAULT_CD_BLOCK.copy(), DEFAULT_CI_FLAGS.copy()

            # Log strategy signal context (informational only — Gemini decides)
            if strategy_signals:
                logger.info(
                    f"🧠 [CI] Strategy signals: preferred={strategy_signals.get('preferred_style')} "
                    f"boost={strategy_signals.get('confidence_boost', 0):.2f} "
                    f"(informational — Gemini response takes priority)"
                )

            # Parse and validate the CD block
            cd_block = self._parse_cd_block(cd_raw)

            # Derive feature flags from editing_style + narrative
            ci_flags = self._derive_flags(cd_block)

            active = [k.replace("enable_", "") for k, v in ci_flags.items() if v]
            logger.info(
                f"🧠 [CI] narrative='{cd_block.get('recommended_narrative')}' "
                f"style='{cd_block.get('editing_style')}' "
                f"tone='{cd_block.get('tone')}' "
                f"hook='{cd_block.get('engagement_hook', '')[:60]}'"
            )
            logger.info(f"🧠 [CI] Feature flags derived: [{', '.join(active) or 'none'}]")

            return cd_block, ci_flags

        except Exception as e:
            logger.warning(f"🧠 [CI] interpret_visual_context error — {e}. Falling back.")
            return DEFAULT_CD_BLOCK.copy(), DEFAULT_CI_FLAGS.copy()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _parse_cd_block(self, raw: dict) -> dict:
        """Validate and normalise the raw content_director dict from Gemini."""

        def _list(key):
            v = raw.get(key, [])
            return [str(x) for x in v] if isinstance(v, list) else []

        # Parse Gemini-returned feature_commands (whitelist-filtered)
        raw_cmds = raw.get("feature_commands", {})
        if not isinstance(raw_cmds, dict):
            raw_cmds = {}
        allowed_flags = set(DEFAULT_CI_FLAGS.keys())
        feature_commands = {
            k: bool(v)
            for k, v in raw_cmds.items()
            if k in allowed_flags
        }
        # Fill missing flags with False
        for f in DEFAULT_CD_BLOCK["feature_commands"]:
            feature_commands.setdefault(f, False)

        return {
            "detected_entities":     _list("detected_entities"),
            "visual_event":          str(raw.get("visual_event", "")),
            "viewer_attention":      str(raw.get("viewer_attention", "")),
            "internet_context":      _list("internet_context"),
            "possible_narratives":   _list("possible_narratives"),
            "recommended_narrative": str(raw.get("recommended_narrative", "")),
            "tone":                  str(raw.get("tone", "")),
            "editing_style":         str(raw.get("editing_style", "")),
            "engagement_hook":       str(raw.get("engagement_hook", "")),
            "feature_commands":      feature_commands,
        }

    def _derive_flags(self, cd_block: dict) -> dict:
        """
        Build the final feature flag set for the pipeline:
          1. Start from Gemini's explicit feature_commands (highest priority).
          2. Add flags from editing_style via STYLE_FLAG_MAP.
          3. Add flags from recommended_narrative via NARRATIVE_FLAG_MAP.
        No flag is ever set to False by this layer once Gemini sets it True.
        """
        flags = DEFAULT_CI_FLAGS.copy()

        # Priority 1: Gemini's own feature_commands
        for k, v in cd_block.get("feature_commands", {}).items():
            if k in flags:
                flags[k] = flags[k] or bool(v)

        # Priority 2: editing_style mapping
        style = cd_block.get("editing_style", "").lower().strip()
        if style in STYLE_FLAG_MAP:
            for k, v in STYLE_FLAG_MAP[style].items():
                if k in flags:
                    flags[k] = flags[k] or v

        # Priority 3: narrative mapping
        narrative = cd_block.get("recommended_narrative", "").lower().strip()
        if narrative in NARRATIVE_FLAG_MAP:
            for k, v in NARRATIVE_FLAG_MAP[narrative].items():
                if k in flags:
                    flags[k] = flags[k] or v

        return flags


# ── Module-level singleton + convenience function ─────────────────────────────

_engine: Optional[ContentIntelligenceEngine] = None


def get_engine() -> ContentIntelligenceEngine:
    global _engine
    if _engine is None:
        _engine = ContentIntelligenceEngine()
    return _engine


def interpret_visual_context(
    frames: Optional[List[str]],
    forensic_result: Optional[dict],
    strategy_signals: Optional[dict] = None,
) -> Tuple[dict, dict]:
    """
    Convenience function for orchestrator.py.
    Returns (cd_block, ci_flags). Never raises.
    """
    return get_engine().interpret_visual_context(frames, forensic_result, strategy_signals)
