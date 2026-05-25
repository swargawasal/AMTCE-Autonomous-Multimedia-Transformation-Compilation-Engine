"""
nzt_simulation_loop.py
──────────────────────────────────────────────────────────────────────────────
NZT SIMULATION LOOP — "Limitless" Iterative Planning Engine

Inspired by the movie concept: before committing to a single edit plan, the
engine generates N alternative intent variants, scores each one against the
real signal data (candidate_moments, flow_quality, semantic_strength, etc.),
and returns the variant with the highest predicted engagement score.

This operates PURELY on lightweight JSON dicts between CreativeBrain Pass 1
and UnifiedIntelligence Pass 2 — no video rendering, no frame I/O, no API
overhead for the base scoring loop.

Design Rules:
  - ZERO side effects if disabled (NZT_LOOP env var not set to "yes")
  - ALL exceptions are silently caught; caller always receives a valid intent
  - No new required imports — all stdlib + json
  - Self-contained: does not import from anywhere in the project except logging

Activation:
  Set env var  NZT_LOOP=yes  to enable.
  Set env var  NZT_VARIANTS=N  to control how many variants are generated (default 5).
  Set env var  NZT_JUDGE=yes  to enable optional Gemini judge call on top-2 finalists.

Usage (called inside creative_brain.py::derive_intent):
    from Intelligence_Modules.nzt_simulation_loop import NZTSimulationLoop
    winner = NZTSimulationLoop().select_best(base_intent, context, candidate_moments)
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nzt_simulation_loop")

# ── Constants ─────────────────────────────────────────────────────────────────
_NZT_MEMORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "rag", "nzt_memory.json"
)

_PACING_STYLES = [
    "fast_cut",
    "slow_build",
    "rhythm_driven",
    "story_driven",
    "reaction_focused",
]

_EMOTIONAL_ARCS = ["rising", "falling", "spike", "constant", "complex"]


# ── Helper: clamp ─────────────────────────────────────────────────────────────
def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


# ─────────────────────────────────────────────────────────────────────────────
# COMPONENT 1: IntentVariantGenerator
# Pure heuristic perturbation — no Gemini calls, < 1ms per variant
# ─────────────────────────────────────────────────────────────────────────────
class IntentVariantGenerator:
    """
    Given a base creative_intent dict, produces N alternative versions by
    perturbing hook time, climax time, pacing style, and emotional arc.

    All variants are grounded in the REAL candidate_moments timestamps so
    the system can never hallucinate a time that doesn't exist in the clip.
    """

    def generate(
        self,
        base_intent: Dict[str, Any],
        candidate_moments: List[Dict],
        n_variants: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Returns a list of intent dicts (including the base as variant 0).
        Each variant has an extra '_variant_id' key for traceability.
        """
        variants: List[Dict] = []

        # Always include the original as variant 0
        v0 = dict(base_intent)
        v0["_variant_id"] = 0
        v0["_variant_label"] = "base"
        variants.append(v0)

        if not candidate_moments or n_variants <= 1:
            return variants

        # Pre-sort moments by score descending for easy selection
        sorted_by_score = sorted(
            candidate_moments,
            key=lambda m: float(m.get("score", m.get("rank_base", 0.0))),
            reverse=True,
        )
        all_times = sorted(
            {round(float(m.get("time", m.get("timestamp", 0.0))), 2)
             for m in candidate_moments}
        )
        duration = float(max(all_times)) if all_times else 60.0
        early_cutoff = duration * 0.40
        late_cutoff = duration * 0.50

        early_times = [t for t in all_times if t <= early_cutoff]
        late_times = [t for t in all_times if t >= late_cutoff]

        # Top-scored moments (for position variants)
        top_early = [
            round(float(m.get("time", m.get("timestamp", 0.0))), 2)
            for m in sorted_by_score
            if float(m.get("time", m.get("timestamp", 0.0))) <= early_cutoff
        ]
        top_late = [
            round(float(m.get("time", m.get("timestamp", 0.0))), 2)
            for m in sorted_by_score
            if float(m.get("time", m.get("timestamp", 0.0))) >= late_cutoff
        ]

        base_hook   = float(base_intent.get("hook_time",   top_early[0] if top_early else 1.5))
        base_climax = float(base_intent.get("climax_time", top_late[0]  if top_late  else duration * 0.8))
        base_pacing = base_intent.get("pacing_style", "rhythm_driven")
        base_arc    = base_intent.get("emotional_arc", "rising")

        _variant_specs = [
            # V1: Earliest high-score hook (stop the scroll ASAP)
            {
                "hook_time":     top_early[0] if top_early else base_hook,
                "climax_time":   top_late[0]  if top_late  else base_climax,
                "pacing_style":  "fast_cut",
                "emotional_arc": "spike",
                "_variant_label": "earliest_hook",
            },
            # V2: Use the 2nd-best early moment as hook (avoid overused opener)
            {
                "hook_time":     top_early[1] if len(top_early) > 1 else base_hook,
                "climax_time":   top_late[-1] if top_late else base_climax,
                "pacing_style":  "rhythm_driven",
                "emotional_arc": "rising",
                "_variant_label": "alt_hook",
            },
            # V3: Slow build — hook later, climax at deepest moment
            {
                "hook_time":     early_times[len(early_times)//2] if early_times else base_hook,
                "climax_time":   top_late[0] if top_late else base_climax,
                "pacing_style":  "slow_build",
                "emotional_arc": "rising",
                "_variant_label": "slow_build",
            },
            # V4: Reaction-focused — open on a reaction moment
            {
                "hook_time":     self._find_reaction_moment(candidate_moments, early_cutoff, base_hook),
                "climax_time":   top_late[0] if top_late else base_climax,
                "pacing_style":  "reaction_focused",
                "emotional_arc": "complex",
                "_variant_label": "reaction_hook",
            },
            # V5: Story-driven — middle open, late climax
            {
                "hook_time":     early_times[0] if early_times else base_hook,
                "climax_time":   late_times[-1] if late_times else base_climax,
                "pacing_style":  "story_driven",
                "emotional_arc": "complex",
                "_variant_label": "story_driven",
            },
        ]

        for vid, spec in enumerate(_variant_specs[: max(0, n_variants - 1)], start=1):
            variant = dict(base_intent)  # copy all keys from base
            variant.update(spec)
            variant["_variant_id"] = vid
            variants.append(variant)

        return variants

    @staticmethod
    def _find_reaction_moment(
        moments: List[Dict], early_cutoff: float, fallback: float
    ) -> float:
        """Return the time of the highest expression_change in the early window."""
        reaction_candidates = [
            m for m in moments
            if float(m.get("time", m.get("timestamp", 0.0))) <= early_cutoff
            and float(m.get("expression_change", 0.0)) > 0.0
        ]
        if not reaction_candidates:
            return fallback
        best = max(
            reaction_candidates,
            key=lambda m: float(m.get("expression_change", 0.0)),
        )
        return round(float(best.get("time", best.get("timestamp", fallback))), 2)


# ─────────────────────────────────────────────────────────────────────────────
# COMPONENT 2: NZTScorer
# Pure-Python scoring — no API calls.
# Scores an intent variant against real signal data.
# ─────────────────────────────────────────────────────────────────────────────
class NZTScorer:
    """
    Predictive scorer.  Converts a (variant, context, candidate_moments) tuple
    into a float score in [0.0, 1.0].

    Scoring Pillars:
      A. Hook Alignment (0.35) — Does the hook land on a high-score moment?
      B. Climax Alignment (0.30) — Does the climax land on a high-score moment?
      C. Pacing Fit (0.20) — Does the chosen pacing match flow_quality + niche?
      D. Historical Win Rate (0.15) — Has this strategy won before in this niche?
    """

    def __init__(self, memory: Dict[str, Any]):
        self._memory = memory  # loaded nzt_memory.json

    def score(
        self,
        variant: Dict[str, Any],
        context: Dict[str, Any],
        candidate_moments: List[Dict],
    ) -> float:
        """Returns a normalized score [0.0, 1.0] for the given variant."""
        try:
            score_a = self._score_hook_alignment(variant, candidate_moments)
            score_b = self._score_climax_alignment(variant, candidate_moments)
            score_c = self._score_pacing_fit(variant, context)
            score_d = self._score_historical(variant, context)

            total = (
                0.35 * score_a
                + 0.30 * score_b
                + 0.20 * score_c
                + 0.15 * score_d
            )
            return round(_clamp(total), 4)
        except Exception as e:
            logger.debug(f"[NZTScorer] scoring error: {e}")
            return 0.5  # neutral fallback

    # ──────────────────────────────────────────────
    # Pillar A: Hook Alignment
    # ──────────────────────────────────────────────
    def _score_hook_alignment(
        self, variant: Dict, candidate_moments: List[Dict]
    ) -> float:
        """
        Score rises as the hook time gets closer to a high-score moment.
        Also gives a bonus if the hook moment has face_present=True.
        """
        hook_t = float(variant.get("hook_time", 0.0))
        if not candidate_moments:
            return 0.5

        # Find the closest moment to hook_t
        closest = min(
            candidate_moments,
            key=lambda m: abs(float(m.get("time", m.get("timestamp", 0.0))) - hook_t),
        )
        distance = abs(float(closest.get("time", closest.get("timestamp", 0.0))) - hook_t)
        moment_score = float(closest.get("score", closest.get("rank_base", 0.5)))
        face_bonus   = 0.10 if closest.get("face_present") else 0.0

        # Distance penalty: within 0.5s = full marks, decays over 3s
        distance_factor = max(0.0, 1.0 - (distance / 3.0))

        return _clamp(moment_score * distance_factor + face_bonus)

    # ──────────────────────────────────────────────
    # Pillar B: Climax Alignment
    # ──────────────────────────────────────────────
    def _score_climax_alignment(
        self, variant: Dict, candidate_moments: List[Dict]
    ) -> float:
        """
        Same as hook alignment but for the climax time.
        Gives extra weight to high motion + emotion at climax.
        """
        climax_t = float(variant.get("climax_time", 0.0))
        if not candidate_moments:
            return 0.5

        closest = min(
            candidate_moments,
            key=lambda m: abs(float(m.get("time", m.get("timestamp", 0.0))) - climax_t),
        )
        distance      = abs(float(closest.get("time", closest.get("timestamp", 0.0))) - climax_t)
        moment_score  = float(closest.get("score", closest.get("rank_base", 0.5)))
        motion_bonus  = float(closest.get("motion_intensity", closest.get("motion", 0.0))) * 0.10
        emotion_bonus = float(closest.get("emotion_score",    closest.get("emotion", 0.0))) * 0.10

        distance_factor = max(0.0, 1.0 - (distance / 3.0))
        return _clamp(moment_score * distance_factor + motion_bonus + emotion_bonus)

    # ──────────────────────────────────────────────
    # Pillar C: Pacing Fit
    # ──────────────────────────────────────────────
    def _score_pacing_fit(self, variant: Dict, context: Dict) -> float:
        """
        Returns a score for how well the chosen pacing style matches the
        content's flow_quality and niche_category.
        """
        pacing      = variant.get("pacing_style", "rhythm_driven")
        flow_quality= str(context.get("flow_quality", "UNKNOWN")).upper()
        niche       = str(context.get("niche_category", "generic")).lower()

        # Pacing → optimal flow quality mapping
        _optimal_flow: Dict[str, str] = {
            "fast_cut":         "HIGH",
            "slow_build":       "LOW",
            "rhythm_driven":    "MEDIUM",
            "story_driven":     "MEDIUM",
            "reaction_focused": "HIGH",
        }
        # Pacing → best niche keywords
        _niche_affinity: Dict[str, List[str]] = {
            "fast_cut":         ["meme", "comedy", "reaction", "dance", "challenge"],
            "slow_build":       ["travel", "landscape", "documental", "nature"],
            "rhythm_driven":    ["music", "fashion", "fitness", "food"],
            "story_driven":     ["podcast", "education", "tutorial", "motivational"],
            "reaction_focused": ["reaction", "review", "commentary", "unboxing"],
        }

        optimal = _optimal_flow.get(pacing, "MEDIUM")
        affinity_niches = _niche_affinity.get(pacing, [])

        flow_score  = 1.0 if flow_quality == optimal else (0.6 if flow_quality in ("MEDIUM", "UNKNOWN") else 0.2)
        niche_score = 1.0 if any(k in niche for k in affinity_niches) else 0.5

        return _clamp(0.6 * flow_score + 0.4 * niche_score)

    # ──────────────────────────────────────────────
    # Pillar D: Historical Win Rate
    # ──────────────────────────────────────────────
    def _score_historical(self, variant: Dict, context: Dict) -> float:
        """
        Checks nzt_memory.json for past win rates for this niche + pacing combo.
        Returns 0.5 (neutral) if no history exists yet.
        """
        niche  = str(context.get("niche_category", "generic")).lower()
        pacing = variant.get("pacing_style", "rhythm_driven")

        niche_data  = self._memory.get(niche, {})
        pacing_data = niche_data.get(pacing, {})
        total  = pacing_data.get("total", 0)
        wins   = pacing_data.get("wins", 0)

        if total < 3:
            return 0.5  # insufficient data — neutral

        win_rate = wins / total
        avg_score = float(pacing_data.get("avg_score", 0.5))
        return _clamp(0.6 * win_rate + 0.4 * avg_score)


# ─────────────────────────────────────────────────────────────────────────────
# COMPONENT 3: NZTMemory
# Reads and writes the persistent win/loss record
# ─────────────────────────────────────────────────────────────────────────────
class NZTMemory:
    """Thin wrapper around nzt_memory.json.  Thread-safe for single-process use."""

    def __init__(self, path: str = _NZT_MEMORY_PATH):
        self._path = path
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.debug(f"[NZTMemory] load failed (non-fatal): {e}")
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            if os.path.exists(self._path):
                os.remove(self._path)
            os.rename(tmp, self._path)
        except Exception as e:
            logger.debug(f"[NZTMemory] save failed (non-fatal): {e}")

    def get_data(self) -> Dict[str, Any]:
        return self._data

    def record_result(
        self,
        niche: str,
        pacing: str,
        won: bool,
        score: float,
    ) -> None:
        """Record the outcome of a simulation round into persistent memory."""
        try:
            n = str(niche).lower()
            p = str(pacing)
            if n not in self._data:
                self._data[n] = {}
            if p not in self._data[n]:
                self._data[n][p] = {"total": 0, "wins": 0, "avg_score": 0.5}

            entry = self._data[n][p]
            old_avg   = float(entry.get("avg_score", 0.5))
            old_total = int(entry.get("total", 0))

            entry["total"] = old_total + 1
            if won:
                entry["wins"] = int(entry.get("wins", 0)) + 1
            # Exponential Moving Average for score (α=0.3)
            entry["avg_score"] = round(0.7 * old_avg + 0.3 * score, 4)

            self._save()
        except Exception as e:
            logger.debug(f"[NZTMemory] record_result error (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# COMPONENT 4: NZTSimulationLoop — Main Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
class NZTSimulationLoop:
    """
    The "Limitless" Loop.

    Call select_best() AFTER CreativeBrain.derive_intent() has produced a
    base intent.  Returns the highest-scoring variant OR the original base
    intent unchanged if anything goes wrong.
    """

    def __init__(self) -> None:
        self._n_variants  = max(2, int(os.getenv("NZT_VARIANTS", "5")))
        self._use_judge   = os.getenv("NZT_JUDGE", "no").lower() == "yes"
        self._memory      = NZTMemory()
        self._generator   = IntentVariantGenerator()
        self._scorer      = NZTScorer(self._memory.get_data())

    def select_best(
        self,
        base_intent: Dict[str, Any],
        context: Dict[str, Any],
        candidate_moments: List[Dict],
    ) -> Dict[str, Any]:
        """
        Entry point.  Always returns a valid intent dict.
        Falls back to base_intent on any exception.
        """
        start_t = time.time()
        try:
            return self._run(base_intent, context, candidate_moments, start_t)
        except Exception as e:
            logger.warning(f"[NZT_LOOP] Unexpected error (returning base_intent): {e}")
            return base_intent

    def _run(
        self,
        base_intent: Dict[str, Any],
        context: Dict[str, Any],
        candidate_moments: List[Dict],
        start_t: float,
    ) -> Dict[str, Any]:
        niche = str(context.get("niche_category", "generic")).lower()

        # 1. Generate variants
        variants = self._generator.generate(
            base_intent, candidate_moments, n_variants=self._n_variants
        )

        if len(variants) <= 1:
            logger.info("[NZT_LOOP] Only 1 variant available (no candidate_moments?) — skipping loop.")
            return base_intent

        # 2. Score all variants
        scored: List[tuple] = []  # (score, variant)
        for v in variants:
            s = self._scorer.score(v, context, candidate_moments)
            scored.append((s, v))

        # Sort highest score first
        scored.sort(key=lambda x: x[0], reverse=True)

        # 3. Optional Gemini Judge on top-2 finalists
        winner_score, winner = scored[0]
        if self._use_judge and len(scored) >= 2:
            try:
                judged = self._gemini_judge(
                    scored[0][1], scored[1][1], context, candidate_moments
                )
                if judged is not None:
                    # Find which variant the judge picked
                    judge_id = judged.get("_variant_id", 0)
                    for s, v in scored[:2]:
                        if v.get("_variant_id") == judge_id:
                            winner_score, winner = s, v
                            break
                    logger.info(f"[NZT_LOOP] Gemini Judge selected variant V{judge_id}")
            except Exception as _je:
                logger.debug(f"[NZT_LOOP] Gemini Judge failed (non-fatal): {_je}")

        # 4. Log the competition
        elapsed = round(time.time() - start_t, 2)
        self._log_results(scored, elapsed)

        # 5. Record results in memory (winner wins, others lose)
        winner_pacing = winner.get("pacing_style", "rhythm_driven")
        self._memory.record_result(niche, winner_pacing, won=True,  score=winner_score)
        for s, v in scored[1:]:
            loser_pacing = v.get("pacing_style", "rhythm_driven")
            if loser_pacing != winner_pacing:
                self._memory.record_result(niche, loser_pacing, won=False, score=s)

        # 6. Clean up internal keys before returning
        winner_clean = {k: val for k, val in winner.items() if not k.startswith("_variant")}
        winner_clean["_nzt_winner"] = winner.get("_variant_label", "unknown")
        winner_clean["_nzt_score"]  = winner_score

        return winner_clean

    # ─────────────────────────────────────────────
    # Optional: Gemini Judge (text-only, cheap)
    # ─────────────────────────────────────────────
    def _gemini_judge(
        self,
        finalist_a: Dict,
        finalist_b: Dict,
        context: Dict,
        candidate_moments: List[Dict],
    ) -> Optional[Dict]:
        """
        Ask Gemini to pick which of two finalists is the better edit plan.
        Returns the chosen finalist dict, or None on any failure.
        """
        try:
            from Intelligence_Modules.gemini_governor import gemini_router

            def _fmt(v: Dict) -> str:
                return (
                    f"  hook={v.get('hook_time','?')}s  "
                    f"climax={v.get('climax_time','?')}s  "
                    f"pacing={v.get('pacing_style','?')}  "
                    f"arc={v.get('emotional_arc','?')}"
                )

            prompt = (
                f"You are a senior video editor judging two edit plans for a "
                f"{context.get('niche_category','generic')} video "
                f"({context.get('duration','?'):.1f}s, {len(candidate_moments)} scored moments).\n\n"
                f"Plan A (V{finalist_a.get('_variant_id',0)}):\n{_fmt(finalist_a)}\n"
                f"Plan B (V{finalist_b.get('_variant_id',1)}):\n{_fmt(finalist_b)}\n\n"
                f"Reply with a single JSON object: {{\"winner\": \"A\" or \"B\"}}. "
                f"No explanation. Just the JSON."
            )
            raw = gemini_router.generate(
                task_type="creative",
                prompt=prompt,
                module_name="nzt_judge",
                gen_config={"temperature": 0.1, "max_output_tokens": 32, "response_mime_type": "application/json"},
            )
            import re
            m = re.search(r'\{[^}]+\}', raw or "")
            if m:
                data = json.loads(m.group())
                winner_letter = str(data.get("winner", "A")).strip().upper()
                return finalist_a if winner_letter == "A" else finalist_b
        except Exception as e:
            logger.debug(f"[NZT_JUDGE] error: {e}")
        return None

    # ─────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────
    def _log_results(self, scored: List[tuple], elapsed: float) -> None:
        lines = [f"[NZT_LOOP] {len(scored)} variants scored in {elapsed}s:"]
        for rank, (s, v) in enumerate(scored):
            label  = v.get("_variant_label", f"v{v.get('_variant_id','?')}")
            pacing = v.get("pacing_style", "?")
            hook   = v.get("hook_time",   "?")
            climax = v.get("climax_time", "?")
            mark   = " ← WINNER" if rank == 0 else ""
            lines.append(
                f"  V{v.get('_variant_id','?')} ({label:<16}) "
                f"score={s:.3f}  hook={hook}s  climax={climax}s  pacing={pacing}{mark}"
            )
        logger.info("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# Self-test (run directly: python -m Intelligence_Modules.nzt_simulation_loop)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    _mock_moments = [
        {"time": 0.5,  "score": 0.42, "face_present": False, "motion_intensity": 0.3, "emotion_score": 0.2},
        {"time": 1.4,  "score": 0.91, "face_present": True,  "motion_intensity": 0.8, "emotion_score": 0.7, "expression_change": 0.6},
        {"time": 3.2,  "score": 0.55, "face_present": True,  "motion_intensity": 0.4, "emotion_score": 0.5},
        {"time": 5.8,  "score": 0.61, "face_present": False, "motion_intensity": 0.6, "emotion_score": 0.3},
        {"time": 8.0,  "score": 0.72, "face_present": True,  "motion_intensity": 0.9, "emotion_score": 0.8},
        {"time": 11.5, "score": 0.88, "face_present": True,  "motion_intensity": 0.7, "emotion_score": 0.9},
        {"time": 14.0, "score": 0.95, "face_present": True,  "motion_intensity": 0.95,"emotion_score": 0.95},
    ]
    _mock_context = {
        "title":             "Test Video",
        "duration":          15.0,
        "niche_category":    "fashion",
        "clip_count":        1,
        "flow_quality":      "HIGH",
        "semantic_strength": "HIGH",
    }
    _mock_base_intent = {
        "narrative_theme": "Showcase the model's outfit reveal",
        "emotional_arc":   "rising",
        "hook_strategy":   "Open on the reveal moment",
        "hook_time":       3.2,
        "climax_time":     11.5,
        "pacing_style":    "rhythm_driven",
        "cut_philosophy":  "Cut on beats, emphasise the reveal",
        "contrast_pairs":  [],
        "avoid_segments":  [],
        "confidence":      0.72,
    }

    print("\n-- NZT Self-Test ------------------------------------------")
    loop = NZTSimulationLoop()
    result = loop.select_best(_mock_base_intent, _mock_context, _mock_moments)
    print(f"\nWinner pacing : {result.get('pacing_style')}")
    print(f"Winner hook   : {result.get('hook_time')}s")
    print(f"Winner climax : {result.get('climax_time')}s")
    print(f"NZT label     : {result.get('_nzt_winner')}")
    print(f"NZT score     : {result.get('_nzt_score')}")
    print("-----------------------------------------------------------\n")
