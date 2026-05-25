"""
Smart Transition Intelligence Engine (STIE)
============================================
Gap-level, RAG-augmented transition decision engine.

Each gap between two shots is analysed across 5 visual signals, mapped to a
bucketed context key, and looked up in a persistent JSON memory store.

Decision cascade:
  1. RAG lookup  (5/5 → 4/5 → 3/5 field match threshold)
  2. Math formula fallback (cold-start)

Every decision is stored in the session. On Approve, record_outcome() raises its
approval_rate; on Reject, it lowers it so the system tries a different type next time.

Memory file: Monetization_Metrics/transition_memory.json
"""

import json
import logging
import math
import os
import random
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("smart_transition_engine")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
MEMORY_PATH = os.path.join(
    _PROJECT_ROOT, "Monetization_Metrics", "transition_memory.json"
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MIN_SAMPLES_FOR_RAG = 3       # need at least 3 approvals before trusting RAG
RAG_MATCH_THRESHOLD = 3       # minimum fields that must match (out of 5)
MIN_DURATION_S = 0.04         # 40 ms — 1 frame at 24 fps
MAX_DURATION_S = 0.60         # 600 ms — longest tolerable overlap

# All valid transition types (superset of what video_pipeline can render)
ALL_TYPES = [
    "clean",          # hard cut, no effect
    "whip_pan",       # boxblur smear
    "blur_cut",       # mild boxblur
    "punch_cut",      # contrast spike
    "zoom_pop",       # contrast+sat boost
    "glitch_pop",     # flash overexposure
    "fade",           # boxblur fade
    "glow_fade",      # warm brightness breath
    "slow_fade",      # long boxblur fade
    "zoom_blur_fade", # center crop zoom + boxblur
    "dip_black",      # dip to black
    "match_cut",      # timing-precise null (invisible)
]

# Beat strength → preferred transition family
_STRENGTH_FAMILY: Dict[str, List[str]] = {
    "drop":   ["glitch_pop", "punch_cut", "dip_black"],
    "strong": ["whip_pan", "zoom_pop", "blur_cut"],
    "medium": ["blur_cut", "fade", "glow_fade"],
    "weak":   ["clean", "fade", "slow_fade", "match_cut"],
    "none":   ["clean", "fade"],
}

# Color mood → preferred transition family  
_COLOR_FAMILY: Dict[str, List[str]] = {
    "warm":     ["glow_fade", "slow_fade", "fade"],
    "cool":     ["blur_cut", "match_cut", "clean"],
    "neutral":  ["clean", "blur_cut", "fade"],
    "dramatic": ["glitch_pop", "punch_cut", "zoom_pop"],
    "vibrant":  ["zoom_pop", "punch_cut", "whip_pan"],
    "cinematic":["slow_fade", "fade", "match_cut"],
    "fashion":  ["glow_fade", "zoom_blur_fade", "fade"],
}


# ---------------------------------------------------------------------------
# Bucketing helpers
# ---------------------------------------------------------------------------

def _bucket_motion(val: float) -> str:
    if val >= 0.75:  return "extreme"
    if val >= 0.50:  return "high"
    if val >= 0.25:  return "medium"
    return "low"


def _bucket_hold(dur: float) -> str:
    if dur < 0.8:   return "micro"
    if dur < 2.0:   return "short"
    if dur < 4.0:   return "medium"
    return "long"


def _make_key(motion: str, beat: str, scene: str, color: str, hold: str) -> str:
    return f"{motion}|{beat}|{scene}|{color}|{hold}"


def _key_fields(key: str) -> List[str]:
    return key.split("|")


def _field_overlap(k1: str, k2: str) -> int:
    """Count how many of the 5 bucket fields match between two keys."""
    f1 = _key_fields(k1)
    f2 = _key_fields(k2)
    return sum(a == b for a, b in zip(f1, f2))


# ---------------------------------------------------------------------------
# Main Engine
# ---------------------------------------------------------------------------

class SmartTransitionEngine:
    """
    Decides transition type + duration per gap, learns from every approve/reject.
    Thread-safe via a single lock over memory reads/writes.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._mem_lock = threading.Lock()
        self._memory: Dict = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            if os.path.exists(MEMORY_PATH):
                with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(
                    f"[STIE] Loaded transition memory: {len(data)} contexts"
                )
                return data
        except Exception as e:
            logger.warning(f"[STIE] Could not load memory ({e}) — starting fresh.")
        return {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
            with open(MEMORY_PATH, "w", encoding="utf-8") as f:
                json.dump(self._memory, f, indent=2)
        except Exception as e:
            logger.warning(f"[STIE] Could not save memory: {e}")

    # ── RAG Lookup ────────────────────────────────────────────────────────────

    def _rag_lookup(self, key: str) -> Optional[Tuple[str, float, str]]:
        """
        Cascade match: 5/5 → 4/5 → 3/5.
        Returns (best_type, best_duration_s, matched_key) or None.
        """
        best_match: Optional[Tuple[int, str, dict]] = None  # (overlap, key, entry)

        for stored_key, entry in self._memory.items():
            overlap = _field_overlap(key, stored_key)
            if overlap < RAG_MATCH_THRESHOLD:
                continue
            total_samples = entry.get("samples", 0)
            if total_samples < MIN_SAMPLES_FOR_RAG:
                continue
            if best_match is None or overlap > best_match[0]:
                best_match = (overlap, stored_key, entry)

        if best_match is None:
            return None

        _, matched_key, entry = best_match
        best_type = entry.get("best_type", "clean")
        best_dur  = float(entry.get("best_duration_s", 0.2))
        logger.info(
            f"[STIE][RAG HIT] key='{key}' matched='{matched_key}' "
            f"→ {best_type} @ {best_dur*1000:.0f}ms "
            f"(approval={entry.get('approval_rate', 0):.0%})"
        )
        return best_type, best_dur, matched_key

    # ── Math Formula ──────────────────────────────────────────────────────────

    def _math_decide(
        self,
        motion_intensity: float,
        beat_strength: str,
        scene_jump: bool,
        color_mood: str,
        seg_duration: float,
        beat_interval: float,
        is_drop: bool,
    ) -> Tuple[str, float]:
        """
        Pure-math cold-start fallback.

        Duration formula:
          base  = beat_interval × 0.25          (25% of one beat window)
          scale = (1 - motion × 0.5) × min(1.0, seg_dur / 3.0)
          dur   = base × scale → clamp [40ms, 600ms]

        Type selection:
          Intersect beat+color families, pick via scene_jump and motion bucket.
        """
        # ── Duration ──────────────────────────────────────────────────────
        base = (beat_interval if beat_interval > 0 else 0.5) * 0.25
        motion_scale = 1.0 - (min(1.0, motion_intensity) * 0.5)
        hold_scale   = min(1.0, seg_duration / 3.0)
        dur = base * motion_scale * hold_scale
        dur = max(MIN_DURATION_S, min(MAX_DURATION_S, dur))

        # ── Type ──────────────────────────────────────────────────────────
        if is_drop:
            candidates = ["glitch_pop", "dip_black", "punch_cut"]
        else:
            beat_fam  = _STRENGTH_FAMILY.get(beat_strength, _STRENGTH_FAMILY["none"])
            color_fam = _COLOR_FAMILY.get(color_mood, ["clean", "fade"])

            # Intersection (preferred), fallback to beat family
            intersection = [t for t in beat_fam if t in color_fam]
            candidates = intersection if intersection else beat_fam

            # Motion modifier
            if motion_intensity >= 0.7 and scene_jump:
                # Fast, scene change → prefer whip/blur
                candidates = ["whip_pan", "blur_cut", "zoom_blur_fade"] + candidates
            elif motion_intensity >= 0.7 and not scene_jump:
                # Fast, same scene → prefer zoom
                candidates = ["zoom_pop", "zoom_blur_fade"] + candidates
            elif motion_intensity < 0.3 and scene_jump:
                # Slow, scene change → prefer fade
                candidates = ["slow_fade", "glow_fade", "fade"] + candidates
            elif motion_intensity < 0.3 and not scene_jump:
                # Slow, same scene → invisible cut
                candidates = ["match_cut", "clean"] + candidates

            # Long hold → breathe into it
            if seg_duration >= 3.5 and "slow_fade" not in candidates[:2]:
                candidates = ["slow_fade"] + candidates

        chosen = candidates[0] if candidates else "clean"
        logger.info(
            f"[STIE][MATH] beat={beat_strength} motion={motion_intensity:.2f} "
            f"color={color_mood} jump={scene_jump} → {chosen} @ {dur*1000:.0f}ms"
        )
        return chosen, round(dur, 3)

    # ── Public: decide ────────────────────────────────────────────────────────

    def decide(
        self,
        motion_intensity: float = 0.5,
        beat_strength: str = "weak",
        scene_jump: bool = True,
        color_mood: str = "neutral",
        seg_duration: float = 2.0,
        beat_interval: float = 0.5,
        is_drop: bool = False,
    ) -> Dict:
        """
        Main entry point. Returns a decision dict:
          {
            "type":         str,
            "duration_s":   float,
            "intensity":    float,
            "reason":       str,
            "rag_hit":      bool,
            "context_key":  str,
          }
        Store context_key in the session so record_outcome() can update memory.
        """
        # Normalise inputs
        motion_intensity = max(0.0, min(1.0, float(motion_intensity)))
        beat_strength    = str(beat_strength).lower() if beat_strength else "none"
        color_mood       = str(color_mood).lower() if color_mood else "neutral"
        seg_duration     = max(0.1, float(seg_duration))
        beat_interval    = max(0.0, float(beat_interval))

        # Build bucket key
        m_bucket = _bucket_motion(motion_intensity)
        h_bucket = _bucket_hold(seg_duration)
        s_bucket = "different" if scene_jump else "same"
        key = _make_key(m_bucket, beat_strength, s_bucket, color_mood, h_bucket)

        rag_hit = False
        with self._mem_lock:
            rag_result = self._rag_lookup(key)

        if rag_result:
            t_type, t_dur, _ = rag_result
            rag_hit = True
            reason = f"rag:{key}"
        else:
            t_type, t_dur = self._math_decide(
                motion_intensity, beat_strength, scene_jump,
                color_mood, seg_duration, beat_interval, is_drop
            )
            reason = f"math:{key}"

        # Intensity: scale with motion + is_drop
        intensity = round(min(1.0, motion_intensity + (0.3 if is_drop else 0.0)), 2)

        return {
            "type":        t_type,
            "duration_s":  t_dur,
            "intensity":   intensity,
            "reason":      reason,
            "rag_hit":     rag_hit,
            "context_key": key,
        }

    # ── Public: record_outcome ────────────────────────────────────────────────

    def record_outcome(
        self,
        context_key: str,
        t_type: str,
        duration_s: float,
        approved: bool,
    ):
        """
        Update RAG memory after an Approve (approved=True) or Reject (approved=False).
        Called from main.py's approve/reject callbacks.
        """
        if not context_key or not t_type:
            return

        with self._mem_lock:
            entry = self._memory.setdefault(context_key, {
                "best_type": t_type,
                "best_duration_s": round(duration_s, 3),
                "approval_rate": 0.0,
                "samples": 0,
                "all_tested": {},
            })

            # Per-type stats
            t_stats = entry["all_tested"].setdefault(t_type, {
                "approvals": 0,
                "rejections": 0,
                "avg_duration_s": round(duration_s, 3),
            })

            if approved:
                t_stats["approvals"] += 1
            else:
                t_stats["rejections"] += 1

            # Weighted moving average for duration (only on approval)
            if approved:
                n = t_stats["approvals"]
                old = t_stats["avg_duration_s"]
                t_stats["avg_duration_s"] = round((old * (n - 1) + duration_s) / n, 3)

            # Recompute best_type (highest approval_rate, min 1 approval)
            best_type = t_type
            best_rate = -1.0
            best_dur  = duration_s
            for tt, ts in entry["all_tested"].items():
                tot = ts["approvals"] + ts["rejections"]
                if tot == 0:
                    continue
                rate = ts["approvals"] / tot
                if rate > best_rate or (
                    rate == best_rate and ts["approvals"] > entry["all_tested"].get(best_type, {}).get("approvals", 0)
                ):
                    best_rate = rate
                    best_type = tt
                    best_dur  = ts["avg_duration_s"]

            total_samples = sum(
                ts["approvals"] + ts["rejections"]
                for ts in entry["all_tested"].values()
            )
            entry["best_type"]       = best_type
            entry["best_duration_s"] = best_dur
            entry["approval_rate"]   = round(best_rate, 3) if best_rate >= 0 else 0.0
            entry["samples"]         = total_samples

            self._save()

        logger.info(
            f"[STIE][RECORD] key='{context_key}' type={t_type} "
            f"approved={approved} → new_best={entry['best_type']} "
            f"rate={entry['approval_rate']:.0%} samples={entry['samples']}"
        )

    # ── Public: get_report ────────────────────────────────────────────────────

    def get_report(self) -> Dict:
        """Returns the full memory dict for /stats_transitions display."""
        with self._mem_lock:
            return dict(self._memory)

    # ── Public: suggest_next_type ─────────────────────────────────────────────

    def suggest_next_type(self, context_key: str, exclude_type: str = None) -> str:
        """
        When a type is rejected, suggest the next best untested or least-rejected type.
        Used by record_outcome internally; also callable externally.
        """
        with self._mem_lock:
            entry = self._memory.get(context_key, {})
        tested = entry.get("all_tested", {})

        # Score all types in our family first
        fields = _key_fields(context_key)
        beat_strength = fields[1] if len(fields) > 1 else "weak"
        color_mood    = fields[3] if len(fields) > 3 else "neutral"
        candidates = (_STRENGTH_FAMILY.get(beat_strength, []) +
                      _COLOR_FAMILY.get(color_mood, []))
        # Add untested types from full list
        candidates += [t for t in ALL_TYPES if t not in candidates]

        for cand in candidates:
            if cand == exclude_type:
                continue
            if cand not in tested:
                return cand  # untested → try it
            t = tested[cand]
            tot = t["approvals"] + t["rejections"]
            rate = t["approvals"] / tot if tot > 0 else 0.0
            if rate > 0.3:
                return cand  # at least 30% approved

        return "clean"  # ultimate fallback


# Module-level singleton
engine = SmartTransitionEngine()
