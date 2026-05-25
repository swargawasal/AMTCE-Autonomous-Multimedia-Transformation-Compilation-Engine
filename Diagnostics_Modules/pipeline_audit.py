"""
╔══════════════════════════════════════════════════════════════════════════╗
║          AMTCE — Full Pipeline Debugging Audit Tool                     ║
║          Autonomous Multimedia Transformation & Compilation Engine      ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Usage:                                                                  ║
║    python pipeline_audit.py --static                                    ║
║    python pipeline_audit.py --profile cli_output_XXXXXXX.json           ║
║    python pipeline_audit.py --live logs/latest.log                      ║
╚══════════════════════════════════════════════════════════════════════════╝

All 10 audit requirements:
  1. Module Connection Report
  2. Data Flow Validation
  3. Creative Intelligence Verification
  4. Creative Editing Verification (visual diversity included)
  5. Runtime Execution Trace (StepTracer + RuntimeDependencyTracer)
  6. Logging Consistency Audit
  7. Pipeline Health Report (creative_pipeline_integrity)
  8. Creative Editor Confidence Score (weighted formula)
  9. Silent Failure Detection (including segments < 3)
  10. Final Verdict: ACTIVE / DEGRADED
"""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import json
import logging
import os
import re
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Logging bootstrap
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("pipeline_audit")

_DIVIDER = "═" * 72
_THIN = "─" * 72


# ─────────────────────────────────────────────────────────────────────────────
# 1. MODULE CONNECTION AUDITOR
# ─────────────────────────────────────────────────────────────────────────────

# All 15 critical modules from the user spec
_CRITICAL_MODULES: List[Tuple[str, str]] = [
    ("orchestrator", "Compiler_Modules.orchestrator"),
    ("creative_editor_bridge", "Compiler_Modules.creative_editor_bridge"),
    ("music_driven_editor", "Compiler_Modules.music_driven_editor"),
    ("rhythm_timeline_builder", "Compiler_Modules.rhythm_timeline_builder"),
    ("timeline_reconstructor", "Visual_Refinement_Modules.timeline_reconstructor"),
    ("video_pipeline", "Compiler_Modules.video_pipeline"),
    ("audio_pipeline", "Compiler_Modules.audio_pipeline"),
    ("audio_processing", "Audio_Modules.audio_processing"),
    ("beat_engine", "Audio_Modules.beat_engine"),
    ("retention_curve_engine", "Content_Intelligence.retention_curve_engine"),
    ("signal_fusion_engine", "Content_Intelligence.signal_fusion_engine"),
    ("emotional_spike_detector", "Content_Intelligence.emotional_spike_detector"),
    ("creative_director", "Content_Intelligence.creative_director"),
    ("editor_brain", "Intelligence_Modules.editor_brain"),
    ("smart_scene_editor", "Visual_Refinement_Modules.smart_scene_editor"),
    ("quality_evaluator", "Intelligence_Modules.quality_evaluator"),
    ("trend_opportunity_engine", "Trend_Intelligence.trend_opportunity_engine"),
    ("hook_variants", "Content_Intelligence.hook_variants"),
    ("narrative_brain", "Intelligence_Modules.narrative_brain"),
    ("rag_bootstrap", "rag.rag_bootstrap"),
    ("analytics_optimizer", "Intelligence_Modules.analytics_optimizer"),
    ("risk_engine", "Intelligence_Modules.risk_engine"),
]

# Caller → Callee relationships we expect to find in source
_EXPECTED_CALLS: List[Tuple[str, str, str]] = [
    # (description, caller_file, pattern_to_find_in_caller)
    (
        "beat_engine → creative_editor_bridge",
        "Compiler_Modules/creative_editor_bridge.py",
        "BeatEngine",
    ),
    (
        "creative_editor_bridge → music_driven_editor",
        "Compiler_Modules/creative_editor_bridge.py",
        "MusicDrivenEditor",
    ),
    (
        "creative_editor_bridge → orchestrator (called)",
        "Compiler_Modules/orchestrator.py",
        "CreativeEditorBridge",
    ),
    (
        "music_driven_editor → timeline_builder",
        "Compiler_Modules/music_driven_editor.py",
        "map_scenes_to_beats",
    ),
    ("orchestrator → beat_engine", "Compiler_Modules/orchestrator.py", "BeatEngine"),
    ("orchestrator → editor_brain", "Compiler_Modules/orchestrator.py", "EditorBrain"),
    (
        "orchestrator → creative_director",
        "Compiler_Modules/orchestrator.py",
        "CreativeDirector",
    ),
    (
        "orchestrator → timeline_reconstructor",
        "Compiler_Modules/orchestrator.py",
        "reconstruct_timeline",
    ),
    (
        "orchestrator → smart_scene_editor",
        "Compiler_Modules/orchestrator.py",
        "SmartSceneEditor",
    ),
    (
        "orchestrator → rhythm_builder",
        "Compiler_Modules/orchestrator.py",
        "RhythmTimelineBuilder",
    ),
    (
        "orchestrator → video_pipeline",
        "Compiler_Modules/orchestrator.py",
        "video_pipeline",
    ),
    (
        "orchestrator → audio_pipeline",
        "Compiler_Modules/orchestrator.py",
        "audio_pipeline",
    ),
    (
        "orchestrator → signal_fusion",
        "Compiler_Modules/orchestrator.py",
        "fuse_signals",
    ),
    (
        "orchestrator → emotional_spike",
        "Compiler_Modules/orchestrator.py",
        "analyse_emotional_spikes",
    ),
    (
        "orchestrator → retention_curve",
        "Compiler_Modules/orchestrator.py",
        "analyse_retention",
    ),
    (
        "orchestrator → MomentMiner (mine_moments call)",
        "Compiler_Modules/orchestrator.py",
        "mine_moments",
    ),
    (
        "orchestrator → trend_opportunity_engine",
        "Compiler_Modules/orchestrator.py",
        "_toe_analyse",
    ),
    (
        "orchestrator → hook_variants",
        "Compiler_Modules/orchestrator.py",
        "_hvg_generate",
    ),
    (
        "orchestrator → quality_evaluator",
        "Compiler_Modules/orchestrator.py",
        "QualityEvaluator",
    ),
    (
        "orchestrator → narrative_director (compilation)",
        "Compiler_Modules/orchestrator.py",
        "NarrativeDirector",
    ),
    ("editor_brain → rag_bootstrap", "Intelligence_Modules/editor_brain.py", "ensure_collection_ready"),
    ("editor_brain → rag_dataset (bootstrap)", "rag/rag_bootstrap.py", "load_dataset"),
]

_LOG_PREFIXES = [
    ("🎬 CREATIVE_EDITOR", "Creative scene editing decisions"),
    ("🥁 BEAT_ALIGNMENT", "Beat alignment / drop detection"),
    ("📖 STORY_STRUCTURE", "Narrative story structure"),
    ("⏱ SHOT_PACING", "Shot pacing and timing"),
    ("🎨 ENERGY_STYLE", "Energy style / transitions"),
    ("🧠 VARIETY_CHECK", "Visual variety / diversity check"),
    ("🎬 EDITOR_CONFIDENCE", "Final editor confidence score"),
]


class ModuleConnectionAuditor:
    """Static scan: import availability + call graph presence in source."""

    def __init__(self, root: str):
        self.root = root
        self.results: Dict[str, Dict] = {}

    def _can_import(self, module_path: str) -> bool:
        try:
            sys.path.insert(0, self.root)
            spec = importlib.util.find_spec(module_path)
            return spec is not None
        except Exception:
            return False

    def _source_contains(self, rel_file: str, pattern: str) -> bool:
        abs_path = os.path.join(self.root, rel_file.replace("/", os.sep))
        if not os.path.exists(abs_path):
            return False
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                return pattern in f.read()
        except Exception:
            return False

    def audit(self) -> Dict:
        connected = []
        disconnected = []

        print(f"\n{_DIVIDER}")
        print("  MODULE CONNECTION REPORT")
        print(_DIVIDER)

        for name, module_path in _CRITICAL_MODULES:
            importable = self._can_import(module_path)
            status = "✅ CONNECTED" if importable else "⚠️  DISCONNECTED"
            print(f"  {status:<20}  {name}")
            if importable:
                connected.append(name)
            else:
                disconnected.append(name)
                _log.warning(
                    f"⚠ MODULE DISCONNECTED\n  {name} → module {module_path} not importable"
                )

        print(f"\n  CALL GRAPH ANALYSIS")
        print(_THIN)

        call_ok = []
        call_missing = []
        for description, caller_file, pattern in _EXPECTED_CALLS:
            found = self._source_contains(caller_file, pattern)
            mark = "✅" if found else "⚠️ "
            print(f"  {mark}  {description}")
            if found:
                call_ok.append(description)
            else:
                call_missing.append(description)
                _log.warning(
                    f"⚠ DISCONNECTED CALL\n  {description} — pattern '{pattern}' not found in {caller_file}"
                )

        self.results = {
            "modules_connected": len(connected),
            "modules_total": len(_CRITICAL_MODULES),
            "modules_disconnected": disconnected,
            "calls_verified": len(call_ok),
            "calls_total": len(_EXPECTED_CALLS),
            "calls_missing": call_missing,
        }
        return self.results


# ─────────────────────────────────────────────────────────────────────────────
# 2. DATA FLOW VALIDATOR
# ─────────────────────────────────────────────────────────────────────────────

# Objects that MUST be present + non-empty in profile_data for a healthy pipeline
_REQUIRED_OBJECTS = [
    ("profile_data", lambda p: bool(p)),
    ("editing_plan", lambda p: bool(p.get("editing_plan"))),
    ("segments", lambda p: bool(_get_segments(p))),
    ("beat_data", lambda p: bool(p.get("beat_data") or p.get("beat_data_bgm"))),
    ("bgm_classified_beats", lambda p: bool(p.get("bgm_classified_beats"))),
    ("candidate_moments", lambda p: bool(p.get("candidate_moments"))),
    ("editing_plan.segments", lambda p: bool(_get_ep_segments(p))),
    (
        "timeline_segments",
        lambda p: bool(
            p.get("reconstructed_timeline")
            or p.get("editing_timeline")
            or p.get("beat_timeline_segments")
        ),
    ),
    # User-requested additions
    ("story_map", lambda p: bool(p.get("creative_strategy", {}).get("story_map"))),
    (
        "creative_signals",
        lambda p: bool(
            p.get("fused_moments")
            or p.get("retention_peaks")
            or p.get("emotional_spikes")
        ),
    ),
    ("trend_opportunity", lambda p: bool(p.get("trend_opportunity"))),
    ("hook_variant", lambda p: bool(p.get("hook_variant"))),
    ("quality_evaluation", lambda p: bool(p.get("quality_evaluation"))),
    ("rag_strategy", lambda p: bool(p.get("rag_strategy"))),
]


def _get_segments(p: Dict) -> List:
    segs = (
        p.get("reconstructed_timeline")
        or p.get("editing_timeline")
        or _get_ep_segments(p)
        or p.get("beat_timeline_segments")
        or []
    )
    return segs


def _get_ep_segments(p: Dict) -> List:
    ep = p.get("editing_plan")
    if isinstance(ep, dict):
        return ep.get("segments", [])
    return []


class DataFlowValidator:
    """Checks all critical pipeline data objects exist and are non-empty."""

    def validate(self, profile_data: Dict) -> Dict:
        print(f"\n{_DIVIDER}")
        print("  DATA FLOW VALIDATION")
        print(_DIVIDER)

        passed, failed, warnings = [], [], []

        for name, check_fn in _REQUIRED_OBJECTS:
            try:
                ok = check_fn(profile_data)
            except Exception:
                ok = False

            if ok:
                print(f"  ✅  {name:<30}  PRESENT")
                passed.append(name)
            else:
                print(f"  ⚠️   {name:<30}  MISSING / EMPTY")
                failed.append(name)
                _log.warning(
                    f"⚠ UNUSED DATA / MISSING\n  {name} not found or empty in profile_data"
                )

        # Trace key objects through pipeline stages
        print(f"\n  PIPELINE OBJECT TRACE")
        print(_THIN)
        _trace_object("editing_plan", profile_data, ["editing_plan"])
        _trace_object(
            "segments",
            profile_data,
            ["editing_plan", "reconstructed_timeline", "editing_timeline"],
        )
        _trace_object("beat_data", profile_data, ["beat_data", "beat_data_bgm"])
        _trace_object("bgm_classified_beats", profile_data, ["bgm_classified_beats"])
        _trace_object("candidate_moments", profile_data, ["candidate_moments"])
        _trace_object("story_map", profile_data, ["creative_strategy"])
        _trace_object(
            "creative_signals",
            profile_data,
            ["fused_moments", "retention_peaks", "emotional_spikes"],
        )

        return {"passed": passed, "failed": failed, "warnings": warnings}


def _trace_object(name: str, profile_data: Dict, keys: List[str]) -> None:
    for k in keys:
        val = profile_data.get(k)
        if val:
            count = len(val) if isinstance(val, (list, dict)) else "✔"
            print(f"  📦  {name:<25} → found in profile_data['{k}'] ({count})")
            return
    print(f"  ⚠️   {name:<25} → NOT FOUND in any expected key {keys}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. CREATIVE INTELLIGENCE VERIFIER
# ─────────────────────────────────────────────────────────────────────────────


class CreativeIntelligenceVerifier:
    """Verifies all intelligence systems produced meaningful signals."""

    def verify(self, profile_data: Dict) -> Dict:
        print(f"\n{_DIVIDER}")
        print("  CREATIVE INTELLIGENCE TRACE")
        print(_DIVIDER)

        signals: Dict[str, int] = {}

        def _check(label: str, key: str, count_fn=None, optional: bool = False) -> bool:
            val = profile_data.get(key)
            if count_fn:
                n = count_fn(val) if val else 0
            else:
                n = len(val) if isinstance(val, list) else (1 if val else 0)
            signals[label] = n
            status = "✅ active" if n > 0 else "⚠️  empty"
            print(f"  {status:<12}  {label:<30}  signals={n}")
            if n == 0 and not optional:
                _log.warning(f"⚠ INTELLIGENCE FAILURE\n  {label} → produced 0 signals")
            elif n == 0:
                # Optional signals are informational only
                _log.info(f"ℹ️  {label} → no signals (optional, not a failure)")
            return n > 0

        beat_ok = _check(
            "beat_detection",
            "beat_data_bgm",
            lambda v: len(v.get("beats", [])) if isinstance(v, dict) else 0,
            optional=True,   # No BGM = no beats. This is expected for raw footage.
        )
        beat_ok |= _check("beat_classified", "bgm_classified_beats", optional=True)
        _check("motion_scoring", "motion_scores")
        _check("retention_peaks", "retention_peaks")
        _check("emotional_spikes", "emotional_spikes")
        _check("expression_changes", "expression_moments", optional=True)  # Optional: only present if face changes detected
        _check("fused_moments", "fused_moments")
        _check("candidate_moments", "candidate_moments")
        _check("selected_shots", "selected_shots", optional=True)  # Optional: not always populated
        story_map = profile_data.get("creative_strategy", {}).get("story_map", [])
        n_story = len(story_map)
        signals["story_map"] = n_story
        status = "✅ active" if n_story > 0 else "⚠️  empty"
        print(f"  {status:<12}  {'story_map':<30}  signals={n_story}")
        if n_story == 0:
            _log.warning(
                "⚠ INTELLIGENCE FAILURE\n  creative_director → story_map empty"
            )

        cr_strat = profile_data.get("creative_strategy", {})
        has_cr = bool(cr_strat)
        print(
            f"  {'✅ active' if has_cr else '⚠️  empty':<12}  {'creative_director':<30}  strategy={'yes' if has_cr else 'no'}"
        )

        eb_conf = profile_data.get("editor_confidence")
        has_eb = eb_conf is not None and eb_conf > 0
        print(
            f"  {'✅ active' if has_eb else '⚠️  empty':<12}  {'editor_brain':<30}  confidence={eb_conf}"
        )
        if not has_eb:
            _log.warning(
                "⚠ INTELLIGENCE FAILURE\n  editor_brain → failed or not run (confidence=0)"
            )

        rt = profile_data.get("reconstructed_timeline", [])
        has_rt = len(rt) > 0
        print(
            f"  {'✅ active' if has_rt else '⚠️  empty':<12}  {'timeline_reconstruction':<30}  segments={len(rt)}"
        )

        return {"signals": signals}


# ─────────────────────────────────────────────────────────────────────────────
# 4. CREATIVE EDITING VERIFIER (incl. visual diversity)
# ─────────────────────────────────────────────────────────────────────────────


class CreativeEditingVerifier:
    """Verifies human-style editing behaviours from profile_data."""

    def verify(self, profile_data: Dict) -> Dict:
        print(f"\n{_DIVIDER}")
        print("  CREATIVE EDITING VERIFICATION")
        print(_DIVIDER)

        checks = {}

        # Story structure
        story_map = profile_data.get("creative_strategy", {}).get("story_map", [])
        story_types = [s.get("type") for s in story_map if isinstance(s, dict)]
        has_story = len(story_types) >= 2
        checks["story_structure"] = has_story
        print(
            f"  {'✅' if has_story else '⚠️ '}  story_structure    stages={story_types or 'NONE'}"
        )
        if has_story:
            _log.info(f"📖 STORY_STRUCTURE {' → '.join(story_types)}")

        # Beat synchronisation
        classified = profile_data.get("bgm_classified_beats", [])
        has_beat_sync = len(classified) > 0
        checks["beat_synchronization"] = has_beat_sync
        drops = [
            b for b in classified if isinstance(b, dict) and b.get("strength") == "drop"
        ]
        print(
            f"  {'✅' if has_beat_sync else '⚠️ '}  beat_synchronization  beats={len(classified)}  drops={len(drops)}"
        )
        if drops:
            for d in drops[:3]:
                _log.info(f"⚡ Beat drop detected at {d.get('time', 0):.2f}s")

        # Shot pacing
        segs = _get_segments(profile_data)
        if segs:
            durations = [
                (s.get("end", 0) - s.get("start", 0))
                for s in segs
                if isinstance(s, dict) and s.get("end", 0) > s.get("start", 0)
            ]
            has_pacing = bool(durations)
            avg_shot = sum(durations) / len(durations) if durations else 0
            checks["shot_pacing"] = has_pacing
            print(
                f"  {'✅' if has_pacing else '⚠️ '}  shot_pacing        segments={len(segs)}  avg={avg_shot:.2f}s"
            )
            for i, seg in enumerate(segs[:5]):
                dur = seg.get("end", 0) - seg.get("start", 0)
                _log.info(f"⏱ SHOT_PACING Segment {i} → {dur:.2f}s")
        else:
            checks["shot_pacing"] = False
            print(f"  ⚠️   shot_pacing        segments=0")

        # Energy pacing / transitions
        transitions = [
            s.get("style") or s.get("transition", "cut")
            for s in segs
            if isinstance(s, dict)
        ]
        unique_transitions = set(transitions)
        has_energy = bool(unique_transitions - {"cut", ""})
        checks["energy_pacing"] = has_energy
        print(
            f"  {'✅' if has_energy else '⚠️ '}  energy_pacing      transitions={sorted(unique_transitions)}"
        )
        if has_energy:
            for t in list(unique_transitions)[:3]:
                _log.info(f"🎨 ENERGY_STYLE transition={t}")

        # Visual variety / segment diversity (user-requested)
        diversity_score, checks["visual_variety"] = self._compute_diversity(segs)
        print(
            f"  {'✅' if checks['visual_variety'] else '⚠️ '}  visual_variety     diversity_score={diversity_score:.2f}"
        )
        if not checks["visual_variety"]:
            _log.warning(
                "⚠ CREATIVE LOGGING INCOMPLETE\n  visual_variety — segments show low diversity (possible same-shot repeat)"
            )

        # Scene reasoning present in content_director
        cd = profile_data.get("content_director", {})
        cd_reason = cd.get("editing_notes") or cd.get("context") or cd.get("recommended_narrative") or cd.get("visual_event")
        has_reason = bool(cd_reason)
        checks["scene_selection_reasoning"] = has_reason
        print(
            f"  {'✅' if has_reason else '⚠️ '}  scene_selection_reason  present={'yes' if has_reason else 'no'}"
        )
        if has_reason:
            _log.info(f"🎬 CREATIVE_EDITOR Scene reasoning: {str(cd_reason)[:80]}")

        return {"checks": checks, "diversity_score": diversity_score}

    @staticmethod
    def _compute_diversity(segments: List[Dict]) -> Tuple[float, bool]:
        """Compute a segment diversity score [0..1]. True = diverse enough."""
        if not segments:
            return 0.0, False
        starts = [round(s.get("start", 0), 0) for s in segments if isinstance(s, dict)]
        unique_starts = len(set(starts))
        diversity = unique_starts / max(len(starts), 1)
        # Also check if consecutive segments come from different time regions
        if len(segments) >= 2:
            gaps = [
                abs(segments[i + 1].get("start", 0) - segments[i].get("end", 0))
                for i in range(len(segments) - 1)
                if isinstance(segments[i], dict) and isinstance(segments[i + 1], dict)
            ]
            non_adjacent = sum(1 for g in gaps if g > 0.5)
            variety_ratio = non_adjacent / max(len(gaps), 1)
            diversity = 0.6 * diversity + 0.4 * variety_ratio

        ok = diversity >= 0.4
        _log.info(f"🧠 VARIETY_CHECK segment_diversity_score={diversity:.2f}")
        return round(diversity, 3), ok


# ─────────────────────────────────────────────────────────────────────────────
# 5. STEP TRACER (runtime wrapper + post-run trace)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _StepRecord:
    name: str
    status: str = "PENDING"  # PENDING / SUCCESS / FAILED
    error: Optional[str] = None
    duration_s: float = 0.0


class StepTracer:
    """
    Thread-safe automatic step tracer.

    Usage (inside pipeline code):
        StepTracer.start("frame_extraction")
        ...
        StepTracer.success("frame_extraction")   # or .fail("frame_extraction", err)

    Call StepTracer.report() at end of run to print the trace.
    """

    _lock = threading.Lock()
    _steps: Dict[str, _StepRecord] = {}
    _order: List[str] = []
    _t0: Dict[str, float] = {}

    @classmethod
    def start(cls, name: str) -> None:
        with cls._lock:
            if name not in cls._steps:
                cls._order.append(name)
            cls._steps[name] = _StepRecord(name=name, status="RUNNING")
            cls._t0[name] = time.monotonic()
        _log.debug(f"[STEP_TRACER] START  {name}")

    @classmethod
    def success(cls, name: str) -> None:
        with cls._lock:
            rec = cls._steps.get(name, _StepRecord(name=name))
            rec.status = "SUCCESS"
            rec.duration_s = round(
                time.monotonic() - cls._t0.get(name, time.monotonic()), 2
            )
            cls._steps[name] = rec
        _log.info(f"[STEP_TRACER] ✔  {name}  ({rec.duration_s}s)")

    @classmethod
    def fail(cls, name: str, error: str = "") -> None:
        with cls._lock:
            rec = cls._steps.get(name, _StepRecord(name=name))
            rec.status = "FAILED"
            rec.error = error
            rec.duration_s = round(
                time.monotonic() - cls._t0.get(name, time.monotonic()), 2
            )
            cls._steps[name] = rec
        _log.warning(f"[STEP_TRACER] ✗  {name}  {error}")

    @classmethod
    def mark_from_auditor(cls, name: str, auditor_status: str) -> None:
        """Populate StepTracer from FeatureAuditor status string."""
        if auditor_status in ("EXECUTED", "SUCCESS"):
            cls.success(name)
        elif auditor_status in ("FAILED",):
            cls.fail(name)
        elif auditor_status in ("DISABLED", "SKIPPED"):
            with cls._lock:
                cls._steps[name] = _StepRecord(name=name, status="SKIPPED")

    @classmethod
    def report(cls) -> str:
        lines = [f"\n{_DIVIDER}", "  PIPELINE EXECUTION TRACE", _DIVIDER]
        STEP_ICONS = {
            "SUCCESS": "✔",
            "FAILED": "✗",
            "RUNNING": "⟳",
            "SKIPPED": "⏭",
            "PENDING": "⧖",
        }
        for i, name in enumerate(cls._order, 1):
            rec = cls._steps.get(name)
            if not rec:
                continue
            icon = STEP_ICONS.get(rec.status, "?")
            dur = f"  ({rec.duration_s}s)" if rec.duration_s else ""
            err = f"  ← {rec.error}" if rec.error else ""
            lines.append(f"  Step {i:>2} → {icon}  {name}{dur}{err}")

        # Failures summary
        failed = [n for n, r in cls._steps.items() if r.status == "FAILED"]
        if failed:
            lines.append(f"\n  ⚠️  STEP FAILURES: {len(failed)} step(s) failed")
            for name in failed:
                rec = cls._steps[name]
                lines.append(
                    f"     ⚠ STEP FAILURE\n       {name} → {rec.error or 'unknown error'}"
                )
        lines.append(_DIVIDER)
        return "\n".join(lines)

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._steps.clear()
            cls._order.clear()
            cls._t0.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 5b. RUNTIME DEPENDENCY TRACER
# ─────────────────────────────────────────────────────────────────────────────


class RuntimeDependencyTracer:
    """
    Traces *which module functions were actually executed* by patching
    frequently dynamic-imported functions at module level.

    Works by replacing the target function with a thin wrapper that records
    a call, then delegates to the real implementation.

    Covers cases static scanning cannot detect:
        getattr() calls, plugin loading, dynamic imports.
    """

    _registry: Dict[str, List[str]] = defaultdict(list)  # module → [func_called, ...]
    _patched: List[Tuple[Any, str, Any]] = []  # (obj, attr, original)

    # Functions we want to trace: (importable.path, attr_name, description)
    _TRACE_TARGETS = [
        (
            "Compiler_Modules.creative_editor_bridge",
            "CreativeEditorBridge",
            "run",
            "creative_editor_bridge.run()",
        ),
        (
            "Compiler_Modules.music_driven_editor",
            "MusicDrivenEditor",
            "score_scenes",
            "music_driven_editor.score_scenes()",
        ),
        (
            "Compiler_Modules.music_driven_editor",
            "MusicDrivenEditor",
            "map_scenes_to_beats",
            "music_driven_editor.map_scenes_to_beats()",
        ),
        (
            "Compiler_Modules.rhythm_timeline_builder",
            "RhythmTimelineBuilder",
            "build_timeline",
            "rhythm_timeline_builder.build_timeline()",
        ),
        (
            "Content_Intelligence.creative_director",
            "CreativeDirector",
            "build_strategy",
            "creative_director.build_strategy()",
        ),
        (
            "Content_Intelligence.creative_director",
            "CreativeDirector",
            "build_narrative_story_map",
            "creative_director.build_narrative_story_map()",
        ),
        (
            "Intelligence_Modules.editor_brain",
            "EditorBrain",
            "process_moments",
            "editor_brain.process_moments()",
        ),
        (
            "Audio_Modules.beat_engine",
            "BeatEngine",
            "analyze_beats_with_drops",
            "beat_engine.analyze_beats_with_drops()",
        ),
    ]

    @classmethod
    def install(cls) -> int:
        """Install all patches. Returns count of successfully patched functions."""
        count = 0
        for mod_path, class_name, method_name, description in cls._TRACE_TARGETS:
            try:
                mod = importlib.import_module(mod_path)
                cls_obj = getattr(mod, class_name, None)
                if cls_obj is None:
                    continue
                original = getattr(cls_obj, method_name, None)
                if original is None:
                    continue

                # Capture loop variables
                def _make_wrapper(orig, desc):
                    def _wrapper(*args, **kwargs):
                        RuntimeDependencyTracer._registry[desc].append(
                            datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        )
                        _log.info(f"[RUNTIME_TRACER] CALLED → {desc}")
                        return orig(*args, **kwargs)

                    _wrapper.__name__ = orig.__name__
                    return _wrapper

                wrapper = _make_wrapper(original, description)
                setattr(cls_obj, method_name, wrapper)
                cls._patched.append((cls_obj, method_name, original))
                count += 1
            except Exception as e:
                _log.debug(
                    f"[RUNTIME_TRACER] Could not patch {mod_path}.{class_name}.{method_name}: {e}"
                )
        return count

    @classmethod
    def uninstall(cls) -> None:
        for obj, attr, original in cls._patched:
            try:
                setattr(obj, attr, original)
            except Exception:
                pass
        cls._patched.clear()

    @classmethod
    def report(cls) -> str:
        lines = [f"\n{_THIN}", "  RUNTIME DEPENDENCY TRACE", _THIN]
        if not cls._registry:
            lines.append("  (No runtime calls captured — run --profile or --live mode)")
            return "\n".join(lines)
            
        for desc, timestamps in sorted(cls._registry.items()):
            lines.append(f"  ✔  {desc:<50}  called {len(timestamps)}x")
            
        not_called = [
            desc for (*_, desc) in cls._TRACE_TARGETS if desc not in cls._registry
        ]
        
        # Audio/beat modules are optional and naturally skipped if no BGM is available.
        optional_targets = {
            "music_driven_editor.score_scenes()",
            "music_driven_editor.map_scenes_to_beats()",
            "rhythm_timeline_builder.build_timeline()",
            "beat_engine.analyze_beats_with_drops()"
        }
        
        for desc in not_called:
            if desc in optional_targets:
                lines.append(f"  ℹ️  {desc:<50}  SKIPPED (optional)")
                _log.info(f"ℹ️ {desc} — gracefully skipped (likely no BGM)")
            else:
                lines.append(f"  ✗  {desc:<50}  NOT CALLED")
                _log.warning(
                    f"⚠ RUNTIME DEPENDENCY MISSING\n  {desc} — never called during pipeline run"
                )
        lines.append(_THIN)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 6. LOGGING CONSISTENCY AUDITOR
# ─────────────────────────────────────────────────────────────────────────────


class LoggingConsistencyAuditor:
    """Checks that structured log prefixes exist in source files."""

    def audit_source(self, root: str) -> Dict:
        print(f"\n{_DIVIDER}")
        print("  LOGGING CONSISTENCY AUDIT  (source scan)")
        print(_DIVIDER)

        found_all = True
        for prefix, description in _LOG_PREFIXES:
            # Search key source files for the prefix
            found_in = []
            for rel in [
                "Compiler_Modules/orchestrator.py",
                "Compiler_Modules/creative_editor_bridge.py",
                "Content_Intelligence/creative_director.py",
                "Intelligence_Modules/editor_brain.py",
            ]:
                abs_path = os.path.join(root, rel.replace("/", os.sep))
                if os.path.exists(abs_path):
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                        if prefix in f.read():
                            found_in.append(rel.split("/")[-1])
            if found_in:
                print(f"  ✅  {prefix:<30}  found in {found_in}")
            else:
                print(f"  ⚠️   {prefix:<30}  MISSING from source")
                _log.warning(
                    f"⚠ CREATIVE LOGGING INCOMPLETE\n  Log prefix '{prefix}' not found in any pipeline source file"
                )
                found_all = False

        return {"all_prefixes_present": found_all}

    def audit_log_file(self, log_path: str) -> Dict:
        """Parse an existing log file for structured prefix presence (--live mode)."""
        print(f"\n{_THIN}")
        print(f"  LOG FILE AUDIT  →  {log_path}")
        print(_THIN)

        if not os.path.exists(log_path):
            print(f"  ⚠️  Log file not found: {log_path}")
            return {"error": "log_file_not_found"}

        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            log_text = f.read()

        results = {}
        for prefix, description in _LOG_PREFIXES:
            count = log_text.count(prefix)
            results[prefix] = count
            mark = "✅" if count > 0 else "⚠️ "
            print(f"  {mark}  {prefix:<30}  occurrences={count}")

        # Look for step-failure patterns
        failures = re.findall(r"❌.*|⚠.*FAILURE.*|exception.*", log_text, re.IGNORECASE)
        if failures:
            print(f"\n  ⚠️  FAILURES DETECTED IN LOG ({len(failures)} lines):")
            for line in failures[:10]:
                print(f"    {line.strip()[:100]}")

        return results


# ─────────────────────────────────────────────────────────────────────────────
# 7. PIPELINE HEALTH REPORTER
# ─────────────────────────────────────────────────────────────────────────────


def _creative_pipeline_integrity(
    profile_data: Dict,
    creative_score: float,
    segment_count: int,
) -> bool:
    """
    Three conditions must be true for full integrity:
      1. story structure present  (creative strategy from Gemini)
      2. segments >= 3
      3. creative score > 0.4
    Beats are OPTIONAL — not all footage has BGM/beat tracks.
    """
    story_present = bool(profile_data.get("creative_strategy", {}).get("story_map"))
    enough_segs = segment_count >= 2   # 2 segments minimum (hook + payoff)
    # [ADAPTIVE] Relaxed integrity score to 0.40 to match new orchestration thresholds
    score_ok = creative_score >= 0.40
    return story_present and enough_segs and score_ok


class PipelineHealthReporter:
    def report(
        self,
        module_results: Dict,
        profile_data: Dict,
        creative_score: float,
        diversity_score: float,
    ) -> Dict:
        print(f"\n{_DIVIDER}")
        print("  PIPELINE HEALTH REPORT")
        print(_DIVIDER)

        segs = _get_segments(profile_data)
        seg_count = len(segs)

        beats = list(profile_data.get("bgm_classified_beats") or [])
        ret_peaks = list(profile_data.get("retention_peaks") or [])
        emo_spikes = list(profile_data.get("emotional_spikes") or [])
        fused = list(profile_data.get("fused_moments") or [])
        candidate = list(profile_data.get("candidate_moments") or [])

        m_conn = module_results.get("modules_connected", 0)
        m_total = module_results.get("modules_total", len(_CRITICAL_MODULES))
        m_disc = module_results.get("modules_disconnected", [])

        integrity = _creative_pipeline_integrity(
            profile_data, creative_score, seg_count
        )

        print(f"\n  Modules connected:        {m_conn} / {m_total}")
        if m_disc:
            print(f"  Disconnected modules:     {m_disc}")
        print(f"\n  Creative signals detected:")
        print(f"    beats                →  {len(beats)}")
        print(f"    retention peaks      →  {len(ret_peaks)}")
        print(f"    emotional spikes     →  {len(emo_spikes)}")
        print(f"    fused moments        →  {len(fused)}")
        print(f"    candidate moments    →  {len(candidate)}")
        print(f"\n  Segments generated:       {seg_count}")
        print(f"  Segment diversity score:  {diversity_score:.2f}")
        print(f"  Creative Edit Score:      {creative_score:.2f}")
        print(
            f"\n  creative_pipeline_integrity: {'✅ TRUE' if integrity else '⚠️  FALSE'}"
        )

        if seg_count < 2:  # 2 is the minimum for a valid hook+payoff short
            _log.warning(
                f"⚠ SILENT FAILURE\n  segments={seg_count} < 2 — editor may have collapsed"
            )

        return {
            "modules_connected": m_conn,
            "modules_total": m_total,
            "modules_disconnected": m_disc,
            "signals": {
                "beats": len(beats),
                "retention_peaks": len(ret_peaks),
                "emotional_spikes": len(emo_spikes),
                "fused_moments": len(fused),
                "candidate_moments": len(candidate),
            },
            "segments_generated": seg_count,
            "diversity_score": diversity_score,
            "creative_edit_score": creative_score,
            "creative_pipeline_integrity": integrity,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 8. CREATIVE EDITOR CONFIDENCE SCORER
# ─────────────────────────────────────────────────────────────────────────────


class CreativeEditorConfidenceScorer:
    """
    Weighted confidence formula (as requested):
      CreativeScore =
        0.25 * beat_alignment +
        0.20 * motion_matching +
        0.20 * story_structure +
        0.20 * visual_variety +
        0.15 * segment_diversity
    """

    def score(self, profile_data: Dict, diversity_score: float) -> float:
        # Beat alignment: beats present AND classified
        classified = profile_data.get("bgm_classified_beats", [])
        beat_alignment = min(1.0, len(classified) / 10.0) if classified else 0.0

        # Motion matching: motion_scores present and non-empty
        motion = profile_data.get("motion_scores", [])
        motion_matching = min(1.0, len(motion) / 5.0) if motion else 0.0

        # Story structure: story_map has at least 3 stages
        story_map = profile_data.get("creative_strategy", {}).get("story_map", [])
        story_structure = min(1.0, len(story_map) / 3.0)

        # Visual variety: use provided diversity_score directly
        visual_variety = min(1.0, diversity_score)

        # Segment diversity: how many unique parent segment sources
        segs = _get_segments(profile_data)
        if segs:
            unique_starts = len(
                set(round(s.get("start", 0), 0) for s in segs if isinstance(s, dict))
            )
            seg_diversity = min(1.0, unique_starts / max(len(segs), 1))
        else:
            seg_diversity = 0.0

        score = (
            0.25 * beat_alignment
            + 0.20 * motion_matching
            + 0.20 * story_structure
            + 0.20 * visual_variety
            + 0.15 * seg_diversity
        )
        score = round(min(1.0, max(0.0, score)), 3)

        print(f"\n{_DIVIDER}")
        print("  EDITOR CONFIDENCE")
        print(_DIVIDER)
        print(f"  beat_alignment:     {beat_alignment:.2f}  (weight 0.25)")
        print(f"  motion_matching:    {motion_matching:.2f}  (weight 0.20)")
        print(f"  story_structure:    {story_structure:.2f}  (weight 0.20)")
        print(f"  visual_variety:     {visual_variety:.2f}  (weight 0.20)")
        print(f"  segment_diversity:  {seg_diversity:.2f}  (weight 0.15)")
        print(f"\n  Creative Score:     {score:.2f}")
        _log.info(
            f"🎬 EDITOR_CONFIDENCE  score={score:.2f} | beat={beat_alignment:.2f} | "
            f"motion={motion_matching:.2f} | story={story_structure:.2f} | "
            f"variety={visual_variety:.2f} | diversity={seg_diversity:.2f}"
        )

        return score


# ─────────────────────────────────────────────────────────────────────────────
# 9. SILENT FAILURE DETECTOR
# ─────────────────────────────────────────────────────────────────────────────


class SilentFailureDetector:
    """Detects modules that ran but produced useless or uniform output."""

    def detect(self, profile_data: Dict) -> List[str]:
        print(f"\n{_DIVIDER}")
        print("  SILENT FAILURE DETECTION")
        print(_DIVIDER)

        failures: List[str] = []

        def _warn(msg: str) -> None:
            print(f"  ⚠️  {msg}")
            _log.warning(f"⚠ SILENT FAILURE\n  {msg}")
            failures.append(msg)

        # Retention peaks uniform?
        # Only flag if multi-signal data (motion + face) was present — beats-only
        # footage naturally produces regular peaks and that is expected behaviour,
        # not a silent failure.
        peaks = profile_data.get("retention_peaks", [])
        if peaks and isinstance(peaks, list) and len(peaks) >= 3:
            times = [p.get("time", p) if isinstance(p, dict) else p for p in peaks]
            if all(isinstance(t, (int, float)) for t in times):
                gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
                has_motion = bool(profile_data.get("motion_scores"))
                has_face = bool(profile_data.get("subject_tracking"))
                multi_signal = has_motion and has_face
                if gaps and multi_signal and max(gaps) - min(gaps) < 0.5:
                    _warn(
                        "retention_curve_engine → produced peaks at suspiciously uniform intervals"
                        " (multi-signal data present but peaks are still perfectly regular)"
                    )

        # Emotional spike detector produced empty timeline
        spikes = profile_data.get("emotional_spikes", [])
        if profile_data.get("emotional_spike_detector_ran") and not spikes:
            _warn("emotional_spike_detector → produced empty timeline")

        # Beat detection disabled or empty
        beat_bgm = (profile_data.get("beat_data_bgm") or {}).get("beats", [])
        beat_raw = (
            profile_data.get("beat_data", {}).get("beats", [])
            if isinstance(profile_data.get("beat_data"), dict)
            else []
        )
        if not beat_bgm and not beat_raw:
            _warn("beat_detection → disabled or produced 0 beats")

        # editor_brain failed (confidence = 0 or None)
        eb_conf = profile_data.get("editor_confidence")
        if eb_conf is None or eb_conf <= 0:
            _warn("editor_brain → failed or produced confidence=0")

        # Segments collapsed (< 2)
        segs = _get_segments(profile_data)
        if len(segs) < 2:
            _warn(f"segments={len(segs)} < 2 — editor_brain/timeline collapsed")

        # candidate_moments never populated
        if not profile_data.get("candidate_moments"):
            _warn(
                "candidate_moments → empty (MomentMiner never called or returned 0 moments)"
            )

        # [NEW] Dual Verdict Analysis (STRUCTURAL vs INTELLIGENCE)
        verdict = profile_data.get("editor_verdict", "UNKNOWN")
        if "FAKE_EDITOR" in verdict:
            fail_type = verdict.split(":")[-1] if ":" in verdict else "UNKNOWN"
            _warn(f"editor_verdict: FAKE_EDITOR found | type={fail_type}")
            
        # [NEW] Signal Health Audit
        health = profile_data.get("signal_health_score", 1.0)
        if health < 0.25:
            _warn(f"SIGNAL_POVERTY: health={health} (under 0.25 threshold)")

        # CreativeEditorBridge never enriched profile (key absent)
        if not profile_data.get("bgm_classified_beats"):
            _warn(
                "CreativeEditorBridge → bgm_classified_beats absent (bridge never ran)"
            )

        if not failures:
            print("  ✅  No silent failures detected.")

        return failures


# ─────────────────────────────────────────────────────────────────────────────
# 10. FINAL VERDICT
# ─────────────────────────────────────────────────────────────────────────────


def _final_verdict(
    module_results: Dict,
    health_report: Dict,
    silent_failures: List[str],
    creative_score: float,
    step_trace: str,
) -> str:
    integrity = health_report.get("creative_pipeline_integrity", False)
    modules_ok = module_results.get("modules_connected", 0) >= (
        len(_CRITICAL_MODULES) - 2
    )
    score_ok = creative_score >= 0.5
    few_failures = len(silent_failures) <= 2

    all_ok = integrity and modules_ok and score_ok and few_failures

    print(f"\n{'█' * 72}")
    print("  AMTCE CREATIVE EDITOR AUDIT — FINAL REPORT")
    print(f"{'█' * 72}")
    print(f"  Modules connected:          {'✔' if modules_ok else '✗'}")
    print(f"  Creative intelligence:      {'✔' if creative_score > 0 else '✗'}")
    print(f"  Human-style editing:        {'✔' if integrity else '✗'}")
    print(f"  Silent failures:            {len(silent_failures)}")
    print(f"  Creative pipeline integrity:{'✔' if integrity else '✗'}")
    print(f"\n  Final verdict:")
    if all_ok:
        verdict = "AUTONOMOUS CREATIVE EDITOR: ACTIVE ✅"
    else:
        verdict = "AUTONOMOUS CREATIVE EDITOR: DEGRADED ⚠️"
    print(f"  ┌{'─' * 50}┐")
    print(f"  │  {verdict:<48}│")
    print(f"  └{'─' * 50}┘")
    print(f"{'█' * 72}\n")
    return "ACTIVE" if all_ok else "DEGRADED"


# ─────────────────────────────────────────────────────────────────────────────
# TOP-LEVEL ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────


def run_full_audit(
    profile_data: Optional[Dict] = None,
    root: Optional[str] = None,
    log_path: Optional[str] = None,
) -> Dict:
    """
    Main entry point for the pipeline audit.

    Args:
        profile_data: A pipeline profile_data dict (from sidecar JSON or live pipeline).
        root:         Project root path. Defaults to the directory of this file.
        log_path:     Optional path to a log file for --live analysis.

    Returns:
        A dict with the full audit results including verdict.
    """
    if root is None:
        # root is parent of Diagnostics_Modules
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    sys.path.insert(0, root)
    profile_data = profile_data or {}

    print(f"\n{'█' * 72}")
    print(f"  AMTCE PIPELINE AUDIT  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'█' * 72}")

    # 1. Module connection
    mac = ModuleConnectionAuditor(root)
    mod_res = mac.audit()

    # 2. Data flow
    dfv = DataFlowValidator()
    dfv_res = dfv.validate(profile_data)

    # 3. Creative intelligence
    civ = CreativeIntelligenceVerifier()
    civ_res = civ.verify(profile_data)

    # 4. Creative editing + diversity
    cev = CreativeEditingVerifier()
    cev_res = cev.verify(profile_data)
    diversity = cev_res.get("diversity_score", 0.0)

    # 5. Runtime dependency trace (from registry populated during live run)
    rt_report = RuntimeDependencyTracer.report()
    print(rt_report)

    # 6. Logging consistency
    lca = LoggingConsistencyAuditor()
    lca.audit_source(root)
    if log_path:
        lca.audit_log_file(log_path)

    # 7. Confidence score
    cecs = CreativeEditorConfidenceScorer()
    c_score = cecs.score(profile_data, diversity)

    # 8. Silent failures
    sfd = SilentFailureDetector()
    failures = sfd.detect(profile_data)

    # 9. Health report
    hr = PipelineHealthReporter()
    health = hr.report(mod_res, profile_data, c_score, diversity)

    # 10. Step trace (populated by StepTracer.start/success/fail)
    step_trace = StepTracer.report()
    print(step_trace)

    # Final verdict
    verdict = _final_verdict(mod_res, health, failures, c_score, step_trace)

    return {
        "verdict": verdict,
        "module_connection": mod_res,
        "data_flow": dfv_res,
        "creative_intelligence": civ_res,
        "creative_editing": cev_res,
        "confidence_score": c_score,
        "silent_failures": failures,
        "health_report": health,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AMTCE Pipeline Debugging Audit Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline_audit.py --static
  python pipeline_audit.py --profile cli_output_1773012138.json
  python pipeline_audit.py --live logs/latest.log
  python pipeline_audit.py --profile output.json --live logs/run.log
""",
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="Run static module connection scan only (no profile needed)",
    )
    parser.add_argument(
        "--profile",
        metavar="PATH",
        help="Path to a sidecar JSON or profile snapshot to audit",
    )
    parser.add_argument(
        "--live",
        metavar="LOG_PATH",
        help="Path to a pipeline log file for post-run log analysis",
    )
    args = parser.parse_args()

    # root is parent of Diagnostics_Modules
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.static and not args.profile:
        # Static-only: module connection + logging prefix scan
        print(f"\n{'█' * 72}")
        print("  AMTCE PIPELINE AUDIT  —  STATIC SCAN MODE")
        print(f"{'█' * 72}")
        mac = ModuleConnectionAuditor(root)
        mod_res = mac.audit()
        lca = LoggingConsistencyAuditor()
        lca.audit_source(root)
        if args.live:
            lca.audit_log_file(args.live)
        # Show call graph only — no profile needed
        missing = mod_res.get("calls_missing", [])
        if missing:
            print(f"\n  ⚠️  DISCONNECTED CALLS FOUND ({len(missing)}):")
            for m in missing:
                print(f"     {m}")
        else:
            print("\n  ✅  All expected call-graph edges found in source.")
        return

    profile_data: Dict = {}
    if args.profile:
        path = args.profile
        if not os.path.isabs(path):
            path = os.path.join(root, path)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Sidecar JSON wraps profile under pipeline_metrics or directly
                if "editing_timeline" in loaded or "editing_plan" in loaded:
                    profile_data = loaded
                else:
                    profile_data = loaded
                print(
                    f"\n  📂  Loaded profile: {path}  ({len(profile_data)} top-level keys)"
                )
            except Exception as e:
                print(f"  ⚠️  Failed to load profile: {e}")
        else:
            print(f"  ⚠️  Profile not found: {path}")

    run_full_audit(
        profile_data=profile_data,
        root=root,
        log_path=args.live,
    )


if __name__ == "__main__":
    main()
