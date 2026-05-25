"""Editor Brain controller that maps detected moments to an edit plan (EDL)."""

# AUDIT LOG PREFIXES (for pipeline_audit.py compatibility)
# 🎬 CREATIVE_EDITOR, 🥁 BEAT_ALIGNMENT, 📖 STORY_STRUCTURE, ⏱ SHOT_PACING, 🎨 ENERGY_STYLE, 🧠 VARIETY_CHECK, 🎬 EDITOR_CONFIDENCE


from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:
    from Content_Intelligence import persona_engine
    from Content_Intelligence.persona_engine import Persona
    PERSONA_AVAILABLE = True
except ImportError:
    persona_engine = None
    Persona = None
    PERSONA_AVAILABLE = False

# RAG Integration Imports
try:
    from analyzer.hybrid_analyzer import HybridAnalyzer
    HYBRID_ANALYZER_AVAILABLE = True
except ImportError:
    HybridAnalyzer = None
    HYBRID_ANALYZER_AVAILABLE = False

try:
    from decision.decision_engine import generate_with_rag
    DECISION_ENGINE_AVAILABLE = True
except ImportError:
    generate_with_rag = None
    DECISION_ENGINE_AVAILABLE = False

try:
    from rag.chroma_client import get_collection
    from rag.rag_bootstrap import ensure_collection_ready
    from rag.retriever import get_top_patterns
    RAG_AVAILABLE = True
except ImportError:
    get_collection = None
    ensure_collection_ready = None
    get_top_patterns = None
    RAG_AVAILABLE = False

try:
    from Visual_Refinement_Modules.style_validator import StyleValidator
    STYLE_VALIDATOR_AVAILABLE = True
except ImportError:
    StyleValidator = None
    STYLE_VALIDATOR_AVAILABLE = False


class EditorBrain:
    """Decision layer between moment detection and rendering."""

    def __init__(self, validator: Optional[StyleValidator] = None):
        self.validator = validator or StyleValidator()

    def process_moments(
        self, moment_candidates: Sequence[Mapping[str, Any]]
    ) -> Dict[str, Any]:
        """Select persona, build an EDL, and validate it.

        Args:
            moment_candidates: iterable of moment dicts containing at minimum
                importance/score and timing metadata (start/end or duration).

        Returns:
            dict with persona name, generated EDL, and confidence score.
        """

        if not moment_candidates:
            empty_persona = persona_engine.load_personas()["ANALYST"]
            empty_plan = {"segments": [], "persona": empty_persona.name}
            default_rag = {"editing_style": "Default", "strategy_text": "No moments available."}
            return {"persona": empty_persona.name, "edl": empty_plan, "confidence": 0.0, "rag_strategy": default_rag}

        # FIX: Ensure all candidate elements are dict-like mappings, not lists or None
        valid_moments = [m for m in moment_candidates if isinstance(m, Mapping)]
        if not valid_moments:
            empty_persona = persona_engine.load_personas()["ANALYST"]
            empty_plan = {"segments": [], "persona": empty_persona.name}
            default_rag = {"editing_style": "Default", "strategy_text": "No valid moments."}
            return {"persona": empty_persona.name, "edl": empty_plan, "confidence": 0.0, "rag_strategy": default_rag}

        persona = self._select_persona_from_moments(valid_moments)
        ranked = self._rank_moments(valid_moments)
        edl = self._generate_edl(ranked, persona)

        # --- RAG INTEGRATION (SAFE INJECTION) ---
        rag_decision = None
        profile = {}
        try:
            if HYBRID_ANALYZER_AVAILABLE and RAG_AVAILABLE and DECISION_ENGINE_AVAILABLE:
                # 1. Analyze clip
                signals = self._extract_signals(valid_moments)
                profile = HybridAnalyzer().analyze(signals)

                # 2. Build query
                query = f"{profile['category']} {profile['energy']} {profile['pace']} {profile['style']} short-form editing strategy"

                # 3. Retrieve patterns
                collection = get_collection()
                ensure_collection_ready(collection)  # lazy-load dataset if empty
                patterns = get_top_patterns(collection, query, profile, k=3)

                # 4. Generate decision
                rag_output = generate_with_rag(profile, patterns)

                try:
                    # Extract JSON if Gemini wraps it in code blocks
                    json_str = rag_output
                    if "```json" in json_str:
                        json_str = json_str.split("```json")[1].split("```")[0].strip()
                    elif "```" in json_str:
                        json_str = json_str.split("```")[1].split("```")[0].strip()

                    rag_decision = json.loads(json_str)
                except Exception as e:
                    print(f"[RAG ERROR] JSON parsing failed: {e}")
                    # If it explicitly ran and failed JSON parsing, we can pass the raw text as fallback
                    rag_decision = {
                        "editing_style": "Generic Strategy",
                        "strategy_text": rag_output,
                        "error": "json_parsing_failed",
                    }
        except Exception as e:
            print(f"[RAG ERROR] Integration failed: {e}")
            rag_decision = {"editing_style": "Fallback Strategy", "strategy_text": "RAG Integration Failed.", "error": str(e)}
            profile = {}

        validation = self.validator.validate_all(edl, persona)
        confidence = self._compute_confidence(ranked, validation)

        return {
            "persona": persona.name,
            "edl": edl,
            "confidence": confidence,
            "validation": validation,
            "hybrid_profile": profile,
            "rag_strategy": rag_decision,
        }

    def _select_persona_from_moments(
        self, moments: Sequence[Mapping[str, Any]]
    ) -> Persona:
        """Aggregate moment metrics to choose a persona."""

        # Use simple averages; default to neutral values if missing.
        def avg(key: str, default: float) -> float:
            values = [m.get(key) for m in moments if m.get(key) is not None]
            if not values:
                return default
            return float(sum(float(v) for v in values) / len(values))

        analysis = {
            "energy_level": avg("energy_level", 0.5),
            "motion_intensity": avg("motion_intensity", 0.5),
            "emotion_score": avg("emotion_score", 0.5),
        }
        return persona_engine.select_persona(analysis)

    def _extract_signals(self, moments: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
        """Map moment metrics to RAG-compatible signal strings."""

        def avg(key: str, default: float) -> float:
            values = [m.get(key) for m in moments if m.get(key) is not None]
            return (
                float(sum(float(v) for v in values) / len(values))
                if values
                else default
            )

        def map_intense(val: float) -> str:
            return "high" if val >= 0.7 else "low" if val <= 0.3 else "medium"

        def map_pace(val: float) -> str:
            return "fast" if val >= 0.7 else "slow" if val <= 0.3 else "steady"

        energy = avg("energy_level", 0.5)
        motion = avg("motion_intensity", 0.5)

        return {
            "energy": map_intense(energy),
            "pace": map_pace(energy),  # pacing often follows energy context
            "motion_intensity": map_intense(motion),
            "cut_density": map_intense(
                motion
            ),  # cut density often tracks motion intensity
        }

    def _rank_moments(
        self, moments: Sequence[Mapping[str, Any]]
    ) -> List[Mapping[str, Any]]:
        """Rank moments by importance/score descending."""

        def score(m: Mapping[str, Any]) -> float:
            if m.get("importance") is not None:
                return float(m["importance"])
            if m.get("score") is not None:
                return float(m["score"])
            return 0.0

        return sorted(moments, key=score, reverse=True)

    def _generate_edl(
        self, ranked: Sequence[Mapping[str, Any]], persona: Persona
    ) -> Dict[str, Any]:
        """Generate a simple EDL structure from ranked moments."""
        segments: List[Dict[str, Any]] = []
        total = len(ranked)

        def duration_for(moment: Mapping[str, Any]) -> Optional[float]:
            if moment.get("duration") is not None:
                try:
                    return float(moment["duration"])
                except (TypeError, ValueError):
                    return None
            if "start" in moment and "end" in moment:
                try:
                    return float(moment["end"]) - float(moment["start"])
                except (TypeError, ValueError):
                    return None
            return None

        for idx, moment in enumerate(ranked):
            duration = duration_for(moment) or min(1.5, persona.max_shot_length)

            # Zoom strategy based on persona frequency.
            zoom_probability = {
                "high": 0.7,
                "low": 0.2,
                "none": 0.0,
            }.get(persona.zoom_frequency, 0.3)
            use_zoom = (idx / (total or 1)) < zoom_probability

            # Caption density aligned to persona style thresholds.
            caption_budget = {
                "bold": 0.6,
                "clean": 0.5,
                "minimal": 0.25,
            }.get(persona.caption_style, 0.5)
            use_caption = (idx / (total or 1)) < caption_budget

            segment = {
                "index": idx,
                "start": moment.get("start"),
                "end": moment.get("end"),
                "duration": duration,
                "importance": moment.get("importance") or moment.get("score"),
                "transition": persona.transition_style,
                "zoom_effect": "zoom" if use_zoom else None,
                "effects": ["zoom"] if use_zoom else [],
                "captions": moment.get("caption") if use_caption else None,
                "caption_style": persona.caption_style,
            }
            segments.append(segment)

        return {"segments": segments, "persona": persona.name}

    def _compute_confidence(
        self,
        ranked: Sequence[Mapping[str, Any]],
        validation: Mapping[str, Any],
    ) -> float:
        """Blend moment strength with validation score into a confidence value."""
        if not ranked:
            return 0.0
        scores: List[float] = []
        for m in ranked:
            if m.get("importance") is not None:
                scores.append(float(m["importance"]))
            elif m.get("score") is not None:
                scores.append(float(m["score"]))
        avg_score = sum(scores) / len(scores) if scores else 0.5
        validation_score = float(validation.get("score", 1.0))
        confidence = 0.5 * avg_score + 0.5 * validation_score
        return max(0.0, min(1.0, confidence))
