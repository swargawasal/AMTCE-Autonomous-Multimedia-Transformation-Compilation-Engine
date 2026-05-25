"""Editor Brain V3: orchestrates signal → perception → meaning → strategy → story → pacing → reward → validation."""

from typing import Any, Dict, List, Mapping

from Core_Modules.temporal_signal_builder import TemporalSignalBuilder
from Content_Intelligence.perception_engine import PerceptionEngine
from Content_Intelligence.meaning_engine import MeaningEngine
from Content_Intelligence.creative_director import CreativeDirector
from Content_Intelligence.story_builder import StoryBuilder
from Content_Intelligence.pacing_engine import PacingEngine
from Monetization_Metrics.reward_scorer import RewardScorer
from Visual_Refinement_Modules.style_validator import StyleValidator
from Content_Intelligence.persona_engine import load_personas
from Content_Intelligence.narrative_coherence_engine import NarrativeCoherenceEngine


class EditorBrainV3:
    """Decision layer producing persona, segments, arc, and confidence."""

    def __init__(self):
        self.signal_builder = TemporalSignalBuilder()
        self.perception = PerceptionEngine()
        self.meaning = MeaningEngine()
        self.director = CreativeDirector()
        self.story = StoryBuilder()
        self.pacing = PacingEngine()
        self.scorer = RewardScorer()
        self.validator = StyleValidator()
        self.coherence = NarrativeCoherenceEngine()
        self.personas = load_personas()

    def process(self, detectors: Mapping[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        temporal_stream = self.signal_builder.build(
            detectors.get("emotion", []),
            detectors.get("motion", []),
            detectors.get("audio", []),
        )

        perceived = self.perception.detect(temporal_stream)
        meanings = self.meaning.infer(perceived, temporal_stream)
        strategy = self.director.choose_strategy(meanings, temporal_stream)

        pacing_hint = self.pacing.detect(temporal_stream)
        plan = self.story.build(
            strategy["arc_type"],
            meanings,
            strategy["persona"],
            pacing_hint=pacing_hint,
        )

        # Narrative coherence check (non-fatal)
        coherence_score = 1.0
        try:
            coherence = self.coherence.validate(plan.get("segments", []), temporal_stream, strategy["arc_type"])
            plan["segments"] = coherence.get("segments", plan.get("segments", []))
            coherence_score = coherence.get("coherence_score", 1.0)
        except Exception:
            coherence = {"valid": False, "issues": ["coherence_exception"]}

        persona = self.personas.get(strategy["persona"], self.personas["HYPE"])
        validation = self.validator.validate_all(plan, persona)
        confidence = self.scorer.score(plan, meanings, coherence_score=coherence_score)
        # Blend in validation for final confidence
        confidence = round((confidence + validation.get("score", 0)) / 2, 3)

        return {
            "persona": persona.name,
            "segments": plan.get("segments", []),
            "arc": plan.get("arc", strategy["arc_type"]),
            "confidence": confidence,
            "validation": validation,
        }
