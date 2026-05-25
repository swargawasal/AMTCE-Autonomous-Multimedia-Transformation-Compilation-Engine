"""
Visual_Refinement_Modules/timeline_reconstructor.py
----------------------------------------------------
Timeline Reconstruction Engine.

Breaks chronological order and rebuilds the video timeline using
moment-based narrative ordering so the final edit flows like a
human-crafted story rather than a straight source trim.

Priority Scoring Formula:
    priority_score = 0.40 * retention_score
                   + 0.30 * emotional_spike_score  (MomentMiner moment_score)
                   + 0.20 * beat_alignment
                   + 0.10 * motion_energy          (moment motion_intensity)

    retention_score        — matched peak from RetentionCurveEngine (0.0 when absent)
    emotional_spike_score  — pre-computed moment_score from MomentMiner
    beat_alignment         — 1.0 if beat_aligned else 0.0
    motion_energy          — motion_intensity from moment data

Narrative Structure (canonical output order):
    Hook → Build → Build → Climax → Resolution

    Hook      = globally strongest moment (highest priority_score, any timestamp)
    Build ×2  = next 2 strongest moments
    Climax    = strongest late moment (time ≥ 50% of duration)
    Resolution= chronologically last remaining moment

Segment Extraction Window (per spec):
    start = max(0.0,      moment_time - 1.2)
    end   = min(duration, moment_time + 1.8)

Pipeline position: After Creative Director, before SmartSceneEditor

Inputs (read from profile_data):
    candidate_moments  — MomentMiner output
    retention_peaks    — RetentionCurveEngine output
    creative_strategy  — CreativeDirector output (optional; used for context)
    beat_data          — {"beats": [float, ...]} from BeatEngine
    shots              — shot boundary list  (used for duration inference)
    duration           — float, seconds      (explicit field; highest priority)

Outputs:
    profile_data["reconstructed_timeline"]
        [
            {"start": 4.0,  "end": 6.9,  "role": "hook",     ...},
            {"start": 1.0,  "end": 4.0,  "role": "reaction", ...},
            {"start": 8.5,  "end": 11.5, "role": "build",    ...},
            ...
        ]

    reconstructed_timeline_debug.json  (written to job_dir)

Log format:
    [TIMELINE_RECONSTRUCTOR] moments_selected=10
    [TIMELINE_RECONSTRUCTOR] timeline_reordered=True
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from Visual_Refinement_Modules.rhythm_quality_guard import (
    VariableSlotSelector,
    EnergyProgressionGuard,
    DynamicTrimCalculator,
)

try:
    from config.runtime_flags import ALLOW_PYTHON_FALLBACK
except ImportError:
    ALLOW_PYTHON_FALLBACK = True  # Safe default if config not yet present

logger = logging.getLogger("timeline_reconstructor")

# ══════════════════════════════════════════════════════════════════════════════
#  Constants
# ══════════════════════════════════════════════════════════════════════════════

# Segment extraction window around each moment anchor (per spec)
SEGMENT_PRE  = 0.8  # seconds BEFORE moment anchor (was 2.0 — too blobby)
SEGMENT_POST = 1.5  # seconds AFTER  moment anchor (was 3.0 — too blobby)

# How many moments to carry into the narrative role-assignment pass
MIN_MOMENTS = 8
MAX_MOMENTS = 12

# Minimum source-time separation between two selected moments.
# Prevents nearly-identical segments from occupying two different slots.
MIN_DEDUP_GAP = 1.5  # seconds

# How close a retention peak must be to a candidate moment to be matched
RETENTION_MATCH_WINDOW = 1.0  # seconds

# Segments shorter than this after overlap resolution are discarded.
# Lowered to 0.4s: Gemini Flash regularly emits 0.6–0.9s segments.
# At 0.5s, a 0.6s segment trimmed by TRIM_GUARD (0.05s) is borderline.
# 0.4s gives safe headroom through nested overlap chains.
MIN_SEGMENT_DURATION = 0.4  # seconds

# Gap inserted between two trimmed segments to avoid frame-bleed on hard cuts
TRIM_GUARD = 0.05  # seconds

# ── Narrative structure ────────────────────────────────────────────────────────
# Canonical output order for sorting the story_map
NARRATIVE_ORDER: List[str] = ["hook", "reaction", "build", "climax", "resolution"]

# moment.type → preferred narrative role
TYPE_TO_ROLE: Dict[str, str] = {
    "appearance": "hook",
    "reaction": "reaction",
    "motion_peak": "climax",
    "beat": "build",
    "dialogue": "build",
}

# Role-pair → transition cut type applied to the OUT edge of the first segment
NARRATIVE_TRANSITIONS: Dict[Tuple[str, str], str] = {
    ("hook", "reaction"): "whip_pan",
    ("hook", "build"): "whip_pan",
    ("hook", "climax"): "speed_ramp_cut",
    ("reaction", "build"): "speed_ramp_cut",
    ("reaction", "climax"): "speed_ramp_cut",
    ("build", "build"): "hard_cut",
    ("build", "climax"): "zoom_blur",
    ("climax", "resolution"): "crossfade",
}

# Late-video fraction threshold used when selecting a "resolution" moment
# (the moment must be at or beyond  duration * RESOLUTION_LATE_THRESHOLD)
RESOLUTION_LATE_THRESHOLD = 0.65

# ══════════════════════════════════════════════════════════════════════════════
#  Quality-First Selection Gates — 7 Hard Rules
# ══════════════════════════════════════════════════════════════════════════════
# Rule 1: NEVER select a moment with composite_score below this threshold.
QUALITY_HARD_GATE = 0.20   # was 0.30 — accommodate high-value b-roll without faces
# Rule 2: Only consider moments in the top 40% of the full ranked pool.
TOP_K_PERCENTILE = 0.60    # widened from 0.40 — catch more quality moments
# Rule 6: Hook MUST come from the top 10% highest-scoring candidates.
HOOK_TOP_PERCENTILE = 0.20  # widened from 0.10 — short clips have few high-scoring moments

# Safe defaults
DEFAULT_TIMELINE: List[Dict] = []


# ══════════════════════════════════════════════════════════════════════════════
#  Moment normalization (prevents KeyError: 'role')
# ══════════════════════════════════════════════════════════════════════════════


def normalize_moment(moment: Dict) -> Dict:
    """
    Normalize a moment dict to guarantee required keys exist.

    Guarantees presence of:
        - time: float (default 0)
        - role: str (fallback from 'type' or defaults to 'build')
        - score: float (default 0.5)

    Args:
        moment: Raw moment dict from candidate_moments

    Returns:
        Normalized moment with all required keys
    """
    return {
        "time": float(moment.get("time", 0)),
        "role": moment.get("role", moment.get("type", "build")),
        "score": float(moment.get("score", 0.5)),
        # Preserve other fields
        "type": moment.get("type", "appearance"),
        "beat_aligned": moment.get("beat_aligned", False),
        "face_present": moment.get("face_present", False),
        "motion_intensity": moment.get("motion_intensity", 0.0),
        "composite_score": moment.get("composite_score", 0.0),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Duration inference
# ══════════════════════════════════════════════════════════════════════════════


def _estimate_duration(
    profile_data: Dict[str, Any],
    candidate_moments: List[Dict],
) -> float:
    """
    Infer video duration from the richest available source in profile_data.

    Priority:
      1. Explicit ``duration`` field
      2. shots list  → max(shot["end"])
      3. candidate_moments → max(moment["time"]) + 3 s buffer
      4. retention_peaks → max(peak["time"])  + 2 s buffer
      5. 30.0 s absolute fallback
    """
    dur = profile_data.get("duration", 0.0)
    if dur and isinstance(dur, (int, float)) and float(dur) > 0:
        return float(dur)

    shots = profile_data.get("shots", [])
    if shots and isinstance(shots, list):
        try:
            end_times = [s.get("end", 0.0) for s in shots if isinstance(s, dict)]
            if end_times:
                return max(end_times)
        except (ValueError, TypeError):
            pass

    if candidate_moments:
        try:
            return max(m.get("time", 0.0) for m in candidate_moments) + 3.0
        except (ValueError, TypeError):
            pass

    peaks = profile_data.get("retention_peaks", [])
    if peaks:
        try:
            return max(p.get("time", 0.0) for p in peaks) + 2.0
        except (ValueError, TypeError):
            pass

    return 30.0


# ══════════════════════════════════════════════════════════════════════════════
#  Step 1 — Merge candidate moments with retention peaks
# ══════════════════════════════════════════════════════════════════════════════


def _merge_and_score(
    candidate_moments: List[Dict],
    retention_peaks: List[Dict],
) -> List[Dict]:
    """
    Produce a unified pool of enriched moments, each carrying the three
    sub-scores needed for the composite formula.

    Sources:
      A. Every candidate moment (from MomentMiner) receives:
           - moment_score    — already computed by MomentMiner
           - retention_score — nearest retention peak within RETENTION_MATCH_WINDOW
           - beat_aligned    — taken directly from moment data

      B. Retention peaks with NO candidate moment within RETENTION_MATCH_WINDOW
         are added as synthetic entries (type="retention_peak"):
           - moment_score = 0.0
           - retention_score = peak.score
           - beat_aligned = False

    Returns a list sorted by composite_score descending.
    """
    enriched: List[Dict] = []
    matched_peak_indices: set = set()

    # ── Pass A: enrich every candidate moment ─────────────────────────────
    for moment in candidate_moments:
        if not isinstance(moment, dict):
            continue

        m_time = float(moment.get("time", 0.0))
        m_score = float(moment.get("score", 0.0))
        beat_aligned_raw = moment.get("beat_aligned", False)
        beat_val = 1.0 if beat_aligned_raw else 0.0

        # Find the nearest unmatched retention peak within the search window
        retention_score = 0.0
        best_dist = float("inf")
        best_peak_idx = -1

        for pi, peak in enumerate(retention_peaks):
            if not isinstance(peak, dict):
                continue
            dist = abs(peak.get("time", 0.0) - m_time)
            if dist <= RETENTION_MATCH_WINDOW and dist < best_dist:
                best_dist = dist
                best_peak_idx = pi
                retention_score = float(peak.get("score", 0.0))

        if best_peak_idx >= 0:
            matched_peak_indices.add(best_peak_idx)

        motion_energy = float(moment.get("motion_intensity", 0.0))
        # Priority formula: rhythm-first, emotional spike dominant, retention least trusted
        composite = (
            0.35 * m_score          # emotional_spike_score
            + 0.30 * beat_val       # beat alignment (elevated)
            + 0.20 * motion_energy  # attention/motion signal
            + 0.15 * retention_score  # retention
        )
        composite = round(min(1.0, max(0.0, composite)), 4)

        enriched.append(
            {
                "time": m_time,
                "type": moment.get("type", "appearance"),
                "clip_id": moment.get("clip_id", 0),  # [MULTI_CLIP] preserve source
                "moment_score": round(m_score, 4),
                "emotional_spike_score": round(m_score, 4),  # alias for scoring clarity
                "retention_score": round(retention_score, 4),
                "beat_aligned": bool(beat_aligned_raw),
                "composite_score": composite,
                "face_present": bool(moment.get("face_present", False)),
                "motion_intensity": round(motion_energy, 4),
                "motion_energy": round(motion_energy, 4),  # alias for scoring clarity
                "_source": "candidate_moment",
            }
        )

    # ── Pass B: add unmatched retention peaks as synthetic moments ─────────
    for pi, peak in enumerate(retention_peaks):
        if pi in matched_peak_indices:
            continue
        if not isinstance(peak, dict):
            continue

        p_score = float(peak.get("score", 0.0))
        # Retention-only entry uses new 0.40 retention coefficient
        composite = round(min(1.0, max(0.0, 0.40 * p_score)), 4)

        enriched.append(
            {
                "time": float(peak.get("time", 0.0)),
                "type": "retention_peak",
                "moment_score": 0.0,
                "emotional_spike_score": 0.0,
                "retention_score": round(p_score, 4),
                "beat_aligned": False,
                "composite_score": composite,
                "face_present": False,
                "motion_intensity": 0.0,
                "motion_energy": 0.0,
                "_source": "retention_peak_only",
            }
        )

    # Sort by composite_score descending, time ascending as tiebreaker
    enriched.sort(key=lambda x: (-x["composite_score"], x["time"]))
    return enriched


# ══════════════════════════════════════════════════════════════════════════════
#  Step 2 — Deduplicate and select top 8–12 moments
# ══════════════════════════════════════════════════════════════════════════════


def _deduplicate_and_select(
    enriched: List[Dict],
    min_n: int = MIN_MOMENTS,
    max_n: int = MAX_MOMENTS,
    min_gap: float = MIN_DEDUP_GAP,
) -> List[Dict]:
    """
    Quality-FIRST selection — 7 Hard Rules enforced.

    Rule 1 — Hard gate:  composite_score < QUALITY_HARD_GATE is filtered out.
    Rule 2 — Top-K:      only top TOP_K_PERCENTILE of the pool is eligible.
    Rule 3 — No fill:    NEVER pad with weak moments just to reach min_n.
    Rule 4 — Variety:    proximity dedup ONLY after quality filtering.
    Rule 5 — Minimum:    if fewer quality moments exist than min_n, reduce count.
    """
    total = len(enriched)
    if total == 0:
        return []

    # Adaptive gate: fashion/b-roll content with no face or emotion data
    # can only reach composite ~0.22. Auto-lower the gate so we don't discard
    # everything and fall back to 3 mechanical segments.
    sorted_scores = sorted(m["composite_score"] for m in enriched)
    pool_median = sorted_scores[len(sorted_scores) // 2] if sorted_scores else 0.0
    effective_gate = QUALITY_HARD_GATE
    if pool_median < QUALITY_HARD_GATE:
        effective_gate = max(0.05, pool_median * 0.8)
        logger.info(
            f"[SELECTION] Pool median={pool_median:.3f} < gate={QUALITY_HARD_GATE} "
            f"→ adaptive gate lowered to {effective_gate:.3f}"
        )

    # Rule 1: Hard quality filter — absolute floor (adaptive)
    quality_passed = [m for m in enriched if m["composite_score"] >= effective_gate]
    n_filtered_out = total - len(quality_passed)

    # Rule 2: Top-K filter — only the top 40% of the ORIGINAL ranked pool is eligible
    top_k_count = max(min_n, int(total * TOP_K_PERCENTILE))
    top_k_times = {m["time"] for m in enriched[:top_k_count]}
    eligible = [m for m in quality_passed if m["time"] in top_k_times]

    # If the intersection is too small (very short video), fall back to quality_passed only
    if len(eligible) < 2:
        eligible = quality_passed

    logger.info(
        f"[SELECTION] filtered_out={n_filtered_out} "
        f"(score<{QUALITY_HARD_GATE}) | "
        f"eligible_after_top_{int(TOP_K_PERCENTILE * 100)}_pct={len(eligible)}"
    )

    # Emergency fallback — never return empty (better some than none)
    if not eligible:
        logger.warning(
            "[SELECTION] No moments passed quality gate — "
            "falling back to top-3 regardless of score."
        )
        eligible = enriched[:3] if enriched else []

    # Rule 4: Proximity dedup — eligible is already sorted by score desc
    # When two moments are within min_gap, keep the higher-scored one (already first).
    def _try_dedup(gap_threshold: float) -> List[Dict]:
        sel = []
        atimes = []
        for moment in eligible:
            t = moment["time"]
            if any(abs(t - at) < gap_threshold for at in atimes):
                continue  # Rule 4: weaker nearby moment is skipped
            sel.append(moment)
            atimes.append(t)
            if len(sel) >= max_n:
                break
        return sel

    selected = _try_dedup(min_gap)
    
    # If we fell below min_n, aggressively reduce gap to salvage candidates
    current_gap = min_gap
    while len(selected) < min_n and current_gap >= 0.5:
        current_gap -= 0.25
        logger.debug(f"[SELECTION] Dropped below min_n={min_n}, lowering gap to {current_gap}s")
        selected = _try_dedup(current_gap)

    # Rule 5: Do NOT pad to reach min_n — return only what passed quality
    if selected:
        lowest = min(m["composite_score"] for m in selected)
        logger.info(
            f"[SELECTION] selected={len(selected)} | "
            f"selected_from_top_{int(TOP_K_PERCENTILE * 100)}_pct=True | "
            f"lowest_selected_score={lowest:.4f}"
        )
    else:
        logger.warning("[SELECTION] selected=0 — returning empty list")

    return selected


# ══════════════════════════════════════════════════════════════════════════════
#  Step 3 — Assign narrative roles → story_map
# ══════════════════════════════════════════════════════════════════════════════


def _assign_narrative_roles(
    candidates: List[Dict],
    duration: float,
) -> List[Dict]:
    """
    Quality-first narrative role assignment with Beat-Match gate and Energy Guard.
    """
    import random
    story_map: List[Dict] = []
    total = len(candidates)

    if not candidates:
        return story_map

    # ── Beat-Match Gate ─────────────────────────────────────────────────────────
    # Add a temporary bonus for beat alignment to elevate beat-matching moments
    for cand in candidates:
        cand["_selection_score"] = cand["composite_score"]
        if cand.get("beat_aligned", False) or cand.get("beat_match_score", 0.0) > 0.8:
            cand["_selection_score"] += 0.15

    # Force temporal variety by shuffling the candidate list before role assignment
    shuffled_candidates = list(candidates)
    random.shuffle(shuffled_candidates)
    
    used_times: set = set()

    def _make_entry(role: str, cand: Dict) -> Dict:
        return {
            "role": role,
            "time": cand["time"],
            "clip_id": cand.get("clip_id", 0),  # [MULTI_CLIP] preserve source clip
            "composite_score": cand["composite_score"],
            "original_type": cand.get("type", "appearance"),
            "moment_score": cand.get("moment_score", 0.0),
            "retention_score": cand.get("retention_score", 0.0),
            "beat_aligned": cand.get("beat_aligned", False),
            "face_present": cand.get("face_present", False),
            "trim_tightness": cand.get("trim_tightness", "medium"),
        }

    # Use VariableSlotSelector to get the dynamic list of roles
    slots_needed = VariableSlotSelector.choose(total, duration)
    
    # Extract hook (must be from top N%)
    hook_limit = max(1, int(total * HOOK_TOP_PERCENTILE))
    # Candidates is already sorted by composite_score, so candidates[:hook_limit] are the very best.
    # Resort them by selection score (to prefer beat-aligned ones)
    hook_pool = sorted(candidates[:hook_limit], key=lambda x: -x["_selection_score"])
    
    # Flatten the slots to fill
    slots_to_fill = []
    for role_name, count in slots_needed:
        if role_name == "hook":
            if hook_pool:
                story_map.append(_make_entry("hook", hook_pool[0]))
                used_times.add(hook_pool[0]["time"])
        else:
            for _ in range(count):
                slots_to_fill.append(role_name)

    # Sort remaining shuffled candidates by selection score (score + beat bonus)
    # They stay somewhat randomized when scores are equal, but beat-matched and high score float to top
    remaining_candidates = sorted(shuffled_candidates, key=lambda x: -x["_selection_score"])

    # Fill climax (prefers latter half)
    late_threshold = duration * 0.50 if duration > 0 else 0.0
    if "climax" in slots_to_fill:
        climax_found = False
        for cand in remaining_candidates:
            if cand["time"] in used_times:
                continue
            if cand["time"] >= late_threshold:
                story_map.append(_make_entry("climax", cand))
                used_times.add(cand["time"])
                climax_found = True
                slots_to_fill.remove("climax")
                break
        
        if not climax_found:
            for cand in remaining_candidates:
                if cand["time"] not in used_times:
                    story_map.append(_make_entry("climax", cand))
                    used_times.add(cand["time"])
                    slots_to_fill.remove("climax")
                    break

    # Fill remaining (builds, reactions, resolutions)
    for role_name in slots_to_fill:
        for cand in remaining_candidates:
            if cand["time"] not in used_times:
                story_map.append(_make_entry(role_name, cand))
                used_times.add(cand["time"])
                break

    # ── Sort into canonical narrative order ────────────────────────────────────
    def _sort_key(entry: Dict) -> tuple:
        role_idx = (
            NARRATIVE_ORDER.index(entry["role"])
            if entry["role"] in NARRATIVE_ORDER
            else 99
        )
        score = entry["composite_score"]
        if entry["role"] == "build":
            return (role_idx, score)
        return (role_idx, -score)

    story_map.sort(key=_sort_key)
    
    # ── Energy Progression Guard ───────────────────────────────────────────────
    story_map = EnergyProgressionGuard.validate_and_fix(story_map)

    # Log final selection quality
    if story_map:
        scores = [e["composite_score"] for e in story_map]
        logger.info(
            f"[SELECTION] narrative_roles_assigned={len(story_map)} | "
            f"hook_score={story_map[0]['composite_score']:.4f} | "
            f"min_role_score={min(scores):.4f} | "
            f"avg_role_score={sum(scores) / len(scores):.4f}"
        )

    # Cleanup temporary keys
    for cand in candidates:
        cand.pop("_selection_score", None)

    return story_map


def _normalize_story_map(story_map: List[Dict]) -> List[Dict]:
    """
    [STEP 1] Normalize story_map entries to guarantee 'time' and 'role'.
    Ensures compatibility with CreativeDirector ('type' -> 'role').
    Normalizes unknown roles to 'build' to prevent downstream crashes.
    """
    allowed_roles = ["hook", "build", "climax", "payoff", "reaction", "resolution"]
    normalized = []
    for e in story_map:
        if not isinstance(e, dict):
            continue
        role = e.get("role") or e.get("type") or "build"

        # Validation: normalize weird/unknown roles to "build"
        if role not in allowed_roles:
            role = "build"

        # Preserve original fields while ensuring role/time
        entry = e.copy()
        entry["time"] = float(e.get("time", 0.0))
        entry["role"] = role
        normalized.append(entry)

    return normalized


def build_segments_from_story_map(
    story_map: List[Dict],
    duration: float,
) -> List[Dict]:
    """
    Build segments directly from an existing story_map (e.g., from Creative Director).

    This preserves the Creative Director's narrative decisions instead of
    recomputing roles from candidate moments.

    Args:
        story_map: List of story entries with 'role', 'time', etc.
        duration: Video duration in seconds.

    Returns:
        List of segment dicts ready for overlap resolution and transitions.
    """
    segments: List[Dict] = []

    for entry in story_map:
        if not isinstance(entry, dict):
            continue

        m_time = float(entry.get("time", 0.0))
        role = entry.get("role") or entry.get("type", "moment")
        composite_score = float(entry.get("composite_score", entry.get("score", 0.0)))

        seg_start = round(max(0.0, m_time - SEGMENT_PRE), 3)
        seg_end = round(min(duration, m_time + SEGMENT_POST), 3)

        if seg_end <= seg_start:
            logger.warning(
                f"🔄 [TIMELINE_RECONSTRUCTOR] Degenerate segment for "
                f"role={role} t={m_time:.3f}s → "
                f"[{seg_start}, {seg_end}] discarded."
            )
            continue

        segments.append(
            {
                "start": seg_start,
                "end": seg_end,
                "role": role,
                "clip_id": entry.get("clip_id", 0),  # [MULTI_CLIP] preserve source clip
                "moment_time": round(m_time, 3),
                "composite_score": round(composite_score, 4),
                "original_type": entry.get(
                    "type", entry.get("original_type", "appearance")
                ),
                "beat_aligned": entry.get("beat_aligned", False),
                "face_present": entry.get("face_present", False),
                "transition_after": None,  # filled in later
            }
        )

    return segments


# ══════════════════════════════════════════════════════════════════════════════
#  Step 4 — Extract source-video segments around each moment anchor
# ══════════════════════════════════════════════════════════════════════════════


def _extract_segments(
    story_map: List[Dict],
    duration: float,
) -> List[Dict]:
    """
    Derive a source-video segment for every moment in the story_map.

    Extraction window is role-aware and moment-aware (DynamicTrimCalculator).
    Used by the PYTHON FALLBACK PATH ONLY. Gemini path uses explicit
    start/end from the story_map directly — window logic does not apply.

    Degenerate segments (end <= start) are silently dropped.
    """
    segments: List[Dict] = []

    for entry in story_map:
        m_time = float(entry.get("time", 0.0))
        role = entry.get("role", "build")

        # Use DynamicTrimCalculator instead of static ROLE_WINDOWS
        pre, post = DynamicTrimCalculator.compute(entry)
        
        seg_start = round(max(0.0, m_time - pre), 3)
        seg_end = round(min(duration, m_time + post), 3)

        if seg_end <= seg_start:
            logger.warning(
                f"🔄 [TIMELINE_RECONSTRUCTOR] Degenerate segment for "
                f"role={role} t={m_time:.3f}s → "
                f"[{seg_start}, {seg_end}] discarded."
            )
            continue

        segments.append(
            {
                "start": seg_start,
                "end": seg_end,
                "role": role,
                "clip_id": entry.get("clip_id", 0),
                "moment_time": round(m_time, 3),
                "composite_score": entry.get("composite_score", 0.0),
                "original_type": entry.get("original_type", "appearance"),
                "beat_aligned": entry.get("beat_aligned", False),
                "face_present": entry.get("face_present", False),
                "transition_after": None,
            }
        )

    return segments


# ══════════════════════════════════════════════════════════════════════════════
#  Step 5 — Overlap resolution (source-video timestamp collisions)
# ══════════════════════════════════════════════════════════════════════════════


def _resolve_overlaps(segments: List[Dict]) -> List[Dict]:
    """
    Ensure no two segments reference overlapping source-video time ranges.

    Algorithm:
      1. Sort a working copy of the segment list by source ``start`` time.
      2. Walk consecutive pairs; when [i].end > [i+1].start:
           a. Trim [i].end  to  [i+1].start - TRIM_GUARD
           b. If the trimmed [i] falls below MIN_SEGMENT_DURATION, mark it
              for removal (do NOT silently keep a 0.05 s stub).
      3. Discard all marked segments.
      4. Re-sort the surviving segments back into the original narrative
         (role-based) order so the non-chronological story flow is restored.

    NOTE: The original narrative ordering is maintained by sorting on the
    NARRATIVE_ORDER index of each segment's ``role`` after overlap resolution.
    """
    if len(segments) < 2:
        return segments

    # Work on source-time-sorted copy
    by_source = sorted(segments, key=lambda s: s["start"])
    to_remove: set = set()

    for i in range(len(by_source) - 1):
        curr = by_source[i]
        nxt = by_source[i + 1]

        if id(curr) in to_remove:
            continue

        if curr["end"] > nxt["start"]:
            # Overlap detected
            trimmed_end = round(nxt["start"] - TRIM_GUARD, 3)
            trimmed_dur = trimmed_end - curr["start"]

            if trimmed_dur >= MIN_SEGMENT_DURATION:
                logger.debug(
                    f"🔄 [TIMELINE_RECONSTRUCTOR] Overlap resolved: "
                    f"role={curr['role']} [{curr['start']:.2f}→{curr['end']:.2f}] "
                    f"trimmed to [{curr['start']:.2f}→{trimmed_end:.2f}]"
                )
                curr["end"] = trimmed_end
            else:
                # Trimmed segment is too short — discard the lower-scored one OR fallback to equal-split
                surviving_count = len(by_source) - len(to_remove)
                
                if surviving_count <= 3:
                    # EMERGENCY FALLBACK: "Equal Split" to prevent timeline collapse
                    overlap_start = nxt["start"]
                    overlap_end = curr["end"]
                    midpoint = round(overlap_start + (overlap_end - overlap_start) / 2, 3)
                    
                    curr_end_new = round(midpoint - (TRIM_GUARD / 2), 3)
                    nxt_start_new = round(midpoint + (TRIM_GUARD / 2), 3)
                    
                    logger.warning(
                        f"🔄 [TIMELINE_RECONSTRUCTOR] EMERGENCY FALLBACK (Equal Split): "
                        f"role={curr['role']} and role={nxt['role']} overlapped. "
                        f"Forced split at {midpoint:.2f} to prevent segments < 3."
                    )
                    
                    if curr_end_new > curr["start"]:
                        curr["end"] = curr_end_new
                    if nxt_start_new < nxt["end"]:
                        nxt["start"] = nxt_start_new
                else:
                    # Safe to discard the lower-scored one
                    if curr["composite_score"] <= nxt["composite_score"]:
                        logger.debug(
                            f"🔄 [TIMELINE_RECONSTRUCTOR] Overlap — discarding "
                            f"short role={curr['role']} seg (score={curr['composite_score']:.3f})"
                        )
                        to_remove.add(id(curr))
                    else:
                        # Push next segment's start forward instead
                        pushed_start = round(curr["end"] + TRIM_GUARD, 3)
                        if (
                            pushed_start < nxt["end"]
                            and (nxt["end"] - pushed_start) >= MIN_SEGMENT_DURATION
                        ):
                            logger.debug(
                                f"🔄 [TIMELINE_RECONSTRUCTOR] Overlap — pushing "
                                f"role={nxt['role']} start from {nxt['start']:.2f} → {pushed_start:.2f}"
                            )
                            nxt["start"] = pushed_start
                        else:
                            to_remove.add(id(nxt))

    # Remove discarded segments
    surviving = [s for s in segments if id(s) not in to_remove]

    # Restore canonical narrative order
    def _narrative_sort_key(seg: Dict) -> int:
        role = seg.get("role", "build")
        base = NARRATIVE_ORDER.index(role) if role in NARRATIVE_ORDER else 99
        # Within "build": lower score first (escalation)
        if role == "build":
            return base * 1000 + int((1.0 - seg.get("composite_score", 0.0)) * 999)
        return base * 1000

    surviving.sort(key=_narrative_sort_key)
    return surviving


# ══════════════════════════════════════════════════════════════════════════════
#  Step 6 — Assign narrative-aware transitions
# ══════════════════════════════════════════════════════════════════════════════


def _is_contiguous(seg_a: Dict, seg_b: Dict, threshold: float = 0.08) -> bool:
    """Returns True if two segments are adjacent in source-video time."""
    return abs(seg_a.get("end", 0.0) - seg_b.get("start", 0.0)) <= threshold


def _assign_transitions(segments: List[Dict]) -> List[Dict]:
    """
    Set the ``transition_after`` field on every segment based on the
    role pair formed by this segment and its immediate successor.

    The last segment in the sequence always gets transition_after = "fade_out".
    Any role pair not listed in NARRATIVE_TRANSITIONS defaults to "hard_cut".

    🔥 Contiguity Guard: If two segments are adjacent in the source video,
    force a 'hard_cut' to avoid visual artifacts (blur/flash/pan on continuous footage).
    """
    n = len(segments)
    for i, seg in enumerate(segments):
        if i == n - 1:
            seg["transition_after"] = "fade_out"
        elif not seg.get("transition_after"):  # [V5] Preserve Gemini's explicit transitions
            nxt = segments[i + 1]
            if _is_contiguous(seg, nxt):
                # 🔥 FORCE clean cut for contiguous segments
                transition = "hard_cut"
            else:
                curr_role = seg.get("role", "build")
                next_role = nxt.get("role", "build")
                transition = NARRATIVE_TRANSITIONS.get((curr_role, next_role), "hard_cut")

            seg["transition_after"] = transition
            # Also enforce in 'style' and 'transition' for downstream compatibility
            seg["style"] = transition
            seg["transition"] = transition
    return segments


# ══════════════════════════════════════════════════════════════════════════════
#  Debug export
# ══════════════════════════════════════════════════════════════════════════════


def _export_debug(
    reconstructed_timeline: List[Dict],
    story_map: List[Dict],
    enriched_pool: List[Dict],
    candidates_selected: List[Dict],
    duration: float,
    job_dir: Optional[str],
    strategy_applied: str = "unknown",
) -> None:
    """
    Write reconstructed_timeline_debug.json to job_dir (or cwd as fallback).

    File structure:
        {
            export_timestamp,
            duration_analysed,
            formula,
            moments_selected,        ← count of moments after dedup/select
            timeline_reordered,      ← True when output order ≠ source order
            summary: {
                segment_count,
                total_duration,
                roles_used,
                avg_composite_score,
            },
            story_map,               ← role assignments before segment extraction
            reconstructed_timeline,  ← final output segments
            enriched_pool_top20,     ← top 20 scored merged candidates (debug)
        }
    """
    # Detect whether narrative order differs from chronological source order
    source_times = [s.get("moment_time", 0.0) for s in reconstructed_timeline]
    timeline_reordered = source_times != sorted(source_times)

    # Role usage counts
    roles_used: Dict[str, int] = {}
    for seg in reconstructed_timeline:
        r = seg.get("role", "build")
        roles_used[r] = roles_used.get(r, 0) + 1

    # Total narrative duration
    total_dur = sum(max(0.0, s["end"] - s["start"]) for s in reconstructed_timeline)

    # Average composite score
    avg_score = 0.0
    if reconstructed_timeline:
        avg_score = round(
            sum(s.get("composite_score", 0.0) for s in reconstructed_timeline)
            / len(reconstructed_timeline),
            4,
        )

    debug_payload = {
        "export_timestamp": datetime.now().isoformat(),
        "duration_analysed": round(duration, 3),
        "strategy_applied": strategy_applied,
        "formula": "priority = 0.40*emotional_spike + 0.20*motion_energy + 0.20*beat_alignment + 0.20*retention (fallback path only)",
        "segment_pre": SEGMENT_PRE,
        "segment_post": SEGMENT_POST,
        "moments_selected": len(candidates_selected),
        "timeline_reordered": timeline_reordered,
        "summary": {
            "segment_count": len(reconstructed_timeline),
            "total_duration_s": round(total_dur, 3),
            "roles_used": roles_used,
            "avg_composite_score": avg_score,
        },
        "story_map": story_map,
        "reconstructed_timeline": reconstructed_timeline,
        "enriched_pool_top20": enriched_pool[:20],
    }

    out_dir = job_dir if (job_dir and os.path.isdir(job_dir)) else "."
    out_path = os.path.join(out_dir, "reconstructed_timeline_debug.json")

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(debug_payload, fh, indent=2)
        logger.info(f"🔄 [TIMELINE_RECONSTRUCTOR] Debug export → {out_path}")
    except OSError as exc:
        logger.warning(f"🔄 [TIMELINE_RECONSTRUCTOR] Could not write debug file: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  Main engine class
# ══════════════════════════════════════════════════════════════════════════════


class TimelineReconstructor:
    """
    Orchestrates the full non-chronological timeline rebuild pipeline:

        1. Merge candidate_moments + retention_peaks → enriched pool
        2. Composite score each moment
        3. Deduplicate and select top 8–12 candidates
        4. Assign narrative roles → story_map
           (Hook · Build · Build · Climax · Resolution)
        5. Extract source-video segments  [t-1.2, t+1.8]
        6. Resolve source-time overlaps
        7. Assign narrative-aware transitions
        8. Write profile_data["reconstructed_timeline"] + debug JSON

    Usage (direct):
        engine = TimelineReconstructor()
        result = engine.reconstruct(profile_data, job_dir="/path/to/job")
        tl     = result["reconstructed_timeline"]

    Usage (convenience):
        from Visual_Refinement_Modules.timeline_reconstructor import reconstruct_timeline
        result = reconstruct_timeline(profile_data, job_dir=job_dir)

    Writes to profile_data:
        profile_data["reconstructed_timeline"]  — final narrative segment list
    """

    # ------------------------------------------------------------------
    def reconstruct(
        self,
        profile_data: Dict[str, Any],
        job_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Public entry point.  Never raises — returns safe defaults on any error.

        Args:
            profile_data:  Pipeline profile dict.  Reads:
                             - candidate_moments   (MomentMiner)
                             - retention_peaks     (RetentionCurveEngine)
                             - creative_strategy   (CreativeDirector, optional)
                             - beat_data           (BeatEngine, optional)
                             - shots               (shot boundary list)
                             - duration            (float, seconds)
            job_dir:       Optional job directory for debug JSON export.

        Returns:
            {
                "reconstructed_timeline": list[dict],
                    [
                        {
                            "start":             float,   # source-video cut-in
                            "end":               float,   # source-video cut-out
                            "role":              str,     # narrative role
                            "moment_time":       float,   # anchor timestamp
                            "composite_score":   float,   # 0.0–1.0
                            "transition_after":  str,     # cut type to next seg
                        },
                        ...
                    ]
            }
        """
        try:
            return self._run(profile_data, job_dir)
        except Exception as exc:
            logger.warning(
                f"🔄 [TIMELINE_RECONSTRUCTOR] reconstruct() failed unexpectedly: {exc}. "
                "Returning safe defaults."
            )
            import traceback

            logger.debug(traceback.format_exc())
            profile_data.setdefault("reconstructed_timeline", DEFAULT_TIMELINE.copy())
            return {"reconstructed_timeline": DEFAULT_TIMELINE.copy()}

    # ------------------------------------------------------------------
    def _run(
        self,
        profile_data: Dict[str, Any],
        job_dir: Optional[str],
    ) -> Dict[str, Any]:

        # ── 0. Pull signals from profile_data ──────────────────────────────
        candidate_moments: List[Dict] = profile_data.get("candidate_moments", [])
        retention_peaks: List[Dict] = profile_data.get("retention_peaks", [])

        # ── 0.5. Normalize candidate moments (prevents KeyError: 'role') ─────
        if candidate_moments:
            candidate_moments = [normalize_moment(m) for m in candidate_moments]
            logger.info(
                f"[TIMELINE_RECONSTRUCTOR] normalized_moments={len(candidate_moments)}"
            )

        if not candidate_moments and not retention_peaks:
            logger.warning(
                "🔄 [TIMELINE_RECONSTRUCTOR] No candidate_moments or retention_peaks "
                "available — returning empty timeline."
            )
            profile_data.setdefault("reconstructed_timeline", DEFAULT_TIMELINE.copy())
            return {"reconstructed_timeline": DEFAULT_TIMELINE.copy()}

        # ── 1. Infer video duration ─────────────────────────────────────────
        duration = _estimate_duration(profile_data, candidate_moments)

        logger.info(
            f"🔄 [TIMELINE_RECONSTRUCTOR] Starting rebuild — "
            f"duration={duration:.1f}s | "
            f"candidate_moments={len(candidate_moments)} | "
            f"retention_peaks={len(retention_peaks)}"
        )

        # ── 2. Merge candidate moments + retention peaks → enriched pool ────
        enriched = _merge_and_score(candidate_moments, retention_peaks)
        logger.info(
            f"🔄 [TIMELINE_RECONSTRUCTOR] Enriched pool: {len(enriched)} moments"
        )

        if not enriched:
            logger.warning(
                "🔄 [TIMELINE_RECONSTRUCTOR] Enriched pool is empty — "
                "returning empty timeline."
            )
            profile_data.setdefault("reconstructed_timeline", DEFAULT_TIMELINE.copy())
            return {"reconstructed_timeline": DEFAULT_TIMELINE.copy()}

        # ── 3. Deduplicate and select top 8–12 candidates ──────────────────
        candidates = _deduplicate_and_select(
            enriched,
            min_n=MIN_MOMENTS,
            max_n=MAX_MOMENTS,
            min_gap=MIN_DEDUP_GAP,
        )
        logger.info(f"[TIMELINE_RECONSTRUCTOR] moments_selected={len(candidates)}")

        # ── 4. Route: Gemini (primary authority) vs Python (fallback only) ──
        #
        # ARCHITECTURE: This file is a VALIDATOR + EXECUTOR, not the editor.
        # Gemini is the editorial brain. Its output is FINAL unless degenerate.
        #
        # Gemini can provide segments in two formats:
        #
        #   FORMAT A (preferred — Gemini controls cuts directly):
        #     creative_strategy["segments"] = [
        #       {"start": 3.2, "end": 4.1, "role": "hook", "reason": "..."},
        #       {"start": 7.5, "end": 8.3, "role": "climax", "reason": "..."},
        #     ]
        #     → Used AS-IS. No window expansion. No overlap resolution.
        #       Gemini controls timing, pacing, rhythm.
        #
        #   FORMAT B (legacy — Gemini provides moment anchors + roles):
        #     creative_strategy["story_map"] = [
        #       {"time": 3.5, "role": "hook", ...},
        #     ]
        #     → Windows applied per ROLE_WINDOWS (role-aware, not flat).
        #       Still no overlap resolution — Gemini's intent preserved.
        #
        # Python fallback: runs ONLY when Gemini is absent or degenerate.
        #   → Overlap resolver runs. Expansion guard runs. Full Python path.

        strategy_applied = "fallback"
        segments = []
        story_map = None
        gemini_format = None

        creative_strategy = profile_data.get("creative_strategy", {})

        # ── Check FORMAT A: Gemini direct start/end segments ─────────────────
        gemini_segments_raw = (
            creative_strategy.get("segments")
            if isinstance(creative_strategy, dict) else None
        )
        if not gemini_segments_raw:
            gemini_segments_raw = profile_data.get("gemini_segments")
            
        if not gemini_segments_raw and isinstance(profile_data.get("editing_plan"), dict):
            gemini_segments_raw = profile_data["editing_plan"].get("segments")

        if gemini_segments_raw and isinstance(gemini_segments_raw, list):
            gemini_format = "direct_segments"
            valid = []
            for s in gemini_segments_raw:
                if not isinstance(s, dict):
                    continue
                try:
                    if s.get("start") is None or s.get("end") is None:
                        raise ValueError("Missing start/end")
                    start = float(s["start"])
                    end   = float(s["end"])
                except (KeyError, TypeError, ValueError):
                    logger.debug(
                        f"[TIMELINE_RECONSTRUCTOR] Skipping segment with invalid timestamps: {s}"
                    )
                    continue
                if (end - start) < MIN_SEGMENT_DURATION:
                    logger.warning(
                        f"[TIMELINE_RECONSTRUCTOR] Gemini segment too short "
                        f"({end-start:.3f}s < {MIN_SEGMENT_DURATION}s): {s} — skipped"
                    )
                    continue
                role = s.get("role", "build")
                valid.append({
                    "start":           round(start, 3),
                    "end":             round(end, 3),
                    "role":            role,
                    "clip_id":         s.get("clip_id", 0),
                    "moment_time":     round((start + end) / 2, 3),
                    "composite_score": round(float(s.get("score", s.get("composite_score", 0.0))), 4),
                    "original_type":   s.get("type", s.get("original_type", "appearance")),
                    "beat_aligned":    s.get("beat_aligned", False),
                    "face_present":    s.get("face_present", False),
                    "reason":          s.get("reason", ""),
                    "transition_after": s.get("transition", s.get("transition_after", None)), # [V5]
                })

            if len(valid) >= 2:
                segments = valid
                strategy_applied = "gemini_direct"
                story_map = [{"role": s["role"], "time": s["moment_time"]} for s in segments]
                logger.info(
                    f"[TIMELINE_RECONSTRUCTOR] strategy_applied=gemini_direct | "
                    f"segments_accepted={len(segments)}"
                )
            else:
                logger.warning(
                    f"[TIMELINE_RECONSTRUCTOR] Gemini direct segments unusable "
                    f"({len(valid)} valid) — trying story_map format"
                )
                gemini_format = None

        # ── Check FORMAT B: Gemini story_map (moment anchors + roles) ────────
        if not segments:
            if "story_map" in profile_data:
                story_map = profile_data["story_map"]
            elif isinstance(creative_strategy, dict):
                story_map = creative_strategy.get("story_map")

            if story_map and isinstance(story_map, list) and len(story_map) > 0:
                gemini_format = "story_map"
                story_map = _normalize_story_map(story_map)
                logger.info(
                    f"[TIMELINE_RECONSTRUCTOR] strategy=gemini_story_map | "
                    f"entries={len(story_map)}"
                )

                # Apply ROLE_WINDOWS (not flat SEGMENT_PRE/POST)
                raw_segments = _extract_segments(story_map, duration)

                valid_segments = [
                    s for s in raw_segments
                    if (s["end"] - s["start"]) >= MIN_SEGMENT_DURATION
                ]

                if len(valid_segments) >= 2:
                    segments = valid_segments
                    strategy_applied = "gemini_story_map"
                    profile_data["timeline_reordered"] = True
                    logger.info(
                        f"[TIMELINE_RECONSTRUCTOR] strategy_applied=gemini_story_map | "
                        f"segments_accepted={len(segments)}"
                    )
                else:
                    logger.warning(
                        f"[TIMELINE_RECONSTRUCTOR] Gemini story_map produced only "
                        f"{len(valid_segments)} valid segment(s) — falling back to Python. "
                        f"Raw: {[(s['start'], s['end']) for s in raw_segments]}"
                    )
                    story_map = None

        # ── PYTHON FALLBACK PATH ──────────────────────────────────────────────
        # Runs ONLY when Gemini is absent or both formats failed.
        if not segments:
            if not ALLOW_PYTHON_FALLBACK:
                logger.error("🚫 STRICT_MODE: Gemini failed — aborting (no fallback).")
        
                return {
                    "editing_quality": "failed",
                    "failure_reason": "gemini_failed_no_fallback",
                    "reconstructed_timeline": [],
                    "timeline_reordered": False,
                    "strategy_applied": "aborted"
                }

            logger.warning("⚠️ Fallback enabled — using python reconstruction (non-strict mode).")
            story_map = _assign_narrative_roles(candidates, duration)
            segments = _extract_segments(story_map, duration)
            strategy_applied = "fallback"
            logger.info(
                f"[TIMELINE_RECONSTRUCTOR] strategy_applied=fallback | "
                f"segments_generated={len(segments)}"
            )

        # Log narrative structure
        if story_map:
            roles_assigned = [e.get("role", "unknown") for e in story_map]
            logger.info(
                f"🔄 [TIMELINE_RECONSTRUCTOR] strategy_applied={strategy_applied} | "
                f"story_map: {' → '.join(roles_assigned)}"
            )
        else:
            logger.info(
                f"🔄 [TIMELINE_RECONSTRUCTOR] strategy_applied={strategy_applied}"
            )

        # ── 6. Overlap resolution — FALLBACK ONLY ────────────────────────────
        # Gemini controls its own timing. Modifying Gemini's cuts post-hoc
        # breaks rhythm, pacing, and editorial intent.
        if strategy_applied == "fallback":
            segments = _resolve_overlaps(segments)

        # ── 7. Assign narrative-aware transitions ──────────────────────────
        segments = _assign_transitions(segments)

        # ── 7.5. Minimum segment guard — FALLBACK ONLY ───────────────────────
        # Never expand Gemini output — if Gemini gave 2 segments, that was
        # an editorial decision. Only pad the Python fallback path.
        if strategy_applied == "fallback" and len(segments) < 3:
            logger.warning(
                f"⚠️ [NARRATIVE_FALLBACK] Only {len(segments)} segments generated — "
                f"triggering fallback expansion (target=3)"
            )

            # Get available candidate moments not already used in segments
            used_times = {s["moment_time"] for s in segments}
            available_candidates = [
                m for m in candidate_moments if m.get("time", 0.0) not in used_times
            ]

            if available_candidates:
                # Sort by score descending and take top 3
                sorted_candidates = sorted(
                    available_candidates,
                    key=lambda x: x.get("score", 0.0),
                    reverse=True,
                )[:3]

                # Enforce narrative roles: hook, build, climax
                fallback_roles = ["hook", "build", "climax"]

                for i, candidate in enumerate(sorted_candidates):
                    if len(segments) >= 3:
                        break

                    m_time = float(candidate.get("time", 0.0))
                    score = float(candidate.get("score", 0.5))

                    seg_start = round(max(0.0, m_time - SEGMENT_PRE), 3)
                    seg_end = round(min(duration, m_time + SEGMENT_POST), 3)

                    if seg_end > seg_start:
                        # Assign role from fallback_roles list
                        role = fallback_roles[len(segments)]
                        segments.append(
                            {
                                "start": seg_start,
                                "end": seg_end,
                                "role": role,
                                "clip_id": candidate.get("clip_id", 0),  # [MULTI_CLIP]
                                "moment_time": round(m_time, 3),
                                "composite_score": round(score, 4),
                                "original_type": candidate.get("type", "appearance"),
                                "beat_aligned": candidate.get("beat_aligned", False),
                                "face_present": candidate.get("face_present", False),
                                "transition_after": None,  # filled in later
                            }
                        )

                # Re-resolve overlaps after adding fallback segments
                segments = _resolve_overlaps(segments)
                segments = _assign_transitions(segments)

                logger.info(f"[NARRATIVE_FALLBACK] segments_generated={len(segments)}")

        # ── 8. Detect whether we actually reordered the timeline ───────────
        source_times = [s["moment_time"] for s in segments]
        timeline_reordered = source_times != sorted(source_times)

        logger.info(f"[TIMELINE_RECONSTRUCTOR] segments_generated={len(segments)}")
        logger.info(f"[TIMELINE_RECONSTRUCTOR] timeline_reordered={timeline_reordered}")
        logger.info(
            f"🔄 [TIMELINE_RECONSTRUCTOR] Final timeline: {len(segments)} segments | "
            f"reordered={timeline_reordered} | "
            f"total_duration={sum(max(0.0, s['end'] - s['start']) for s in segments):.1f}s"
        )

        if segments:
            for seg in segments:
                logger.debug(
                    f"    [{seg['role']:10s}] "
                    f"{seg['start']:.2f}s → {seg['end']:.2f}s  "
                    f"(anchor={seg['moment_time']:.2f}s  "
                    f"score={seg['composite_score']:.3f}  "
                    f"cut={seg['transition_after']})"
                )

        # ── 9. Export debug JSON ────────────────────────────────────────────
        _export_debug(
            reconstructed_timeline=segments,
            story_map=story_map,
            enriched_pool=enriched,
            candidates_selected=candidates,
            duration=duration,
            job_dir=job_dir,
            strategy_applied=strategy_applied,
        )

        # ── 10. Write back to profile_data ──────────────────────────────────
        _editing_quality = "degraded" if strategy_applied == "fallback" else "optimal"
        
        # [ADD SAFETY GUARD]
        if not ALLOW_PYTHON_FALLBACK and strategy_applied == "fallback":
            raise RuntimeError("CRITICAL: strategy_applied=='fallback' despite STRICT MODE.")

        profile_data["reconstructed_timeline"] = segments
        profile_data["timeline_reordered"] = timeline_reordered
        profile_data["strategy_applied"] = strategy_applied
        profile_data["editing_quality"] = _editing_quality

        return {
            "editing_quality": _editing_quality,
            "reconstructed_timeline": segments,
            "timeline_reordered": timeline_reordered,
            "strategy_applied": strategy_applied,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Module-level singleton + convenience function
# ══════════════════════════════════════════════════════════════════════════════

_engine: Optional[TimelineReconstructor] = None


def get_engine() -> TimelineReconstructor:
    """Return the module-level singleton reconstructor (lazy-initialised)."""
    global _engine
    if _engine is None:
        _engine = TimelineReconstructor()
    return _engine


def reconstruct_timeline(
    profile_data: Dict[str, Any],
    job_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function for orchestrator.py integration.

    Executes the full 8-step non-chronological timeline rebuild, writes
    profile_data["reconstructed_timeline"] in-place, and returns the result
    dict.  Never raises — safe defaults are returned on any failure.

    Args:
        profile_data:  Pipeline profile dict (reads + writes in-place).
                       Required keys: candidate_moments, retention_peaks.
                       Optional keys: creative_strategy, beat_data, shots,
                                      duration.
        job_dir:       Optional job directory for debug JSON export.

    Returns:
        {
            "reconstructed_timeline": list[dict]
                Each dict:
                    start            — source-video cut-in  (seconds)
                    end              — source-video cut-out (seconds)
                    role             — narrative role (hook/reaction/build/
                                       climax/resolution)
                    moment_time      — original moment anchor (seconds)
                    composite_score  — 0.5*retention + 0.3*moment + 0.2*beat
                    original_type    — MomentMiner type label
                    beat_aligned     — bool
                    face_present     — bool
                    transition_after — cut type to the next segment
        }

    Log entries emitted:
        [TIMELINE_RECONSTRUCTOR] moments_selected=N
        [TIMELINE_RECONSTRUCTOR] timeline_reordered=True|False
    """
    return get_engine().reconstruct(profile_data, job_dir)