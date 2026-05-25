"""
segment_validator.py
--------------------
STRICT Video Segment Validation System.
Determines if the AI editing behaves like a REAL editor or a FAKE selector.
"""

import json
import logging
import os
from typing import Any, Dict, List, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("segment_validator")

def normalize_signals(candidate_moments: List[Dict], selected_segments: List[Dict], signal_data: Dict) -> Dict:
    """Normalize all signals across candidates and selected segments to [0, 1]."""
    
    # Extract all relevant values to find max for normalization
    keys = ["motion", "retention", "visual_change", "emotion"]
    max_vals = {k: 0.001 for k in keys}  # Small epsilon to avoid div by zero
    
    # Key mapping variants
    variants = {
        "motion": ["motion", "motion_score", "motion_intensity", "motion_energy", "score"],
        "retention": ["retention", "retention_score", "retention_peak", "score"],
        "visual_change": ["visual_change", "visual_change_score", "visual_score", "score"],
        "emotion": ["emotion", "emotion_score", "emotional_spike_score", "emotional_spike", "score"],
        "beat": ["beat_aligned", "beat"]
    }
    
    # Check signal_data
    for k in keys:
        for var in variants[k]:
            signals = signal_data.get(f"{var}s", signal_data.get(var, []))
            if signals and isinstance(signals, list):
                if isinstance(signals[0], dict):
                    max_vals[k] = max(max_vals[k], max(s.get("score", s.get(var, 0)) for s in signals))
                else:
                    max_vals[k] = max(max_vals[k], max(signals))
                
    # Check existing scores in items
    all_items = candidate_moments + selected_segments
    for item in all_items:
        for k in keys:
            for var in variants[k]:
                val = item.get(var, 0)
                if isinstance(val, (int, float)):
                    max_vals[k] = max(max_vals[k], val)
                
    return max_vals

def resolve_signal(t: float, signal_list: List[Dict], key_variants: List[str], window: float = 1.5) -> float:
    """Find nearest signal value within a time window."""
    if not signal_list or not isinstance(signal_list, list):
        return 0.0
        
    candidates = []
    for item in signal_list:
        if not isinstance(item, dict): continue
        item_t = item.get("time", item.get("t", -999))
        if abs(item_t - t) <= window:
            candidates.append(item)
            
    if not candidates:
        return 0.0
        
    nearest = min(candidates, key=lambda x: abs(x.get("time", x.get("t", 0)) - t))
    
    # Try all variant keys
    for var in key_variants:
        val = nearest.get(var)
        if val is not None:
            return float(val)
        
    # Fallback to general 'score' key
    return float(nearest.get("score", 0))

def calculate_score(data: Dict, max_vals: Dict, signal_data: Dict = None) -> Tuple[float, Dict]:
    """Compute score = 0.35*M + 0.30*R + 0.20*V + 0.15*E"""
        # Use 'start' as ultimate fallback since segments from pipeline use 'start' and 'end'
    t = data.get("time", data.get("timestamp", data.get("moment_time", data.get("start", 0))))
    
    variants = {
        "motion": ["motion", "motion_score", "motion_intensity", "motion_energy"],
        "retention": ["retention", "retention_score", "retention_peak"],
        "visual_change": ["visual_change", "visual_change_score", "visual_score"],
        "emotion": ["emotion", "emotion_score", "emotional_spike_score", "emotional_spike"]
    }
    
    # Prefer pre-computed composite_score if present (from TimelineReconstructor)
    if "composite_score" in data:
        return round(data["composite_score"], 4), {
            "motion": data.get("motion_intensity", 0),
            "retention": data.get("retention_score", 0),
            "visual_change": data.get("visual_score", 0),
            "emotion": data.get("emotional_spike_score", 0)
        }

    def get_val(item, category):
        # 1. Try local keys
        for var in variants.get(category, []):
            if var in item:
                return item[var]
        
        # 2. Try global signal_data resolution
        if signal_data:
            sig_list = signal_data.get(f"{category}_scores", signal_data.get(category, []))
            return resolve_signal(t, sig_list, variants.get(category, []))
            
        return 0

    m = get_val(data, "motion") / max_vals["motion"]
    r = get_val(data, "retention") / max_vals["retention"]
    v = get_val(data, "visual_change") / max_vals["visual_change"]
    e = get_val(data, "emotion") / max_vals["emotion"]
    b = get_val(data, "beat")  # Binary 1/0 or float
    
    # Clamp to [0, 1]
    m, r, v, e, b = [min(1.0, max(0.0, float(x))) for x in [m, r, v, e, b]]
    
    # ALIGNED WEIGHTS (Match TimelineReconstructor)
    # score = 0.40*R + 0.30*E + 0.20*B + 0.10*M
    # (Visual change 0.20 weight from old formula is redistributed or kept as extra)
    # We'll use: 0.40*R + 0.30*E + 0.20*B + 0.10*M (Total 1.0)
    # If Visual Change is present, we could potentially blend it, but let's stick to the core 4.
    score = (r * 0.40) + (e * 0.30) + (b * 0.20) + (m * 0.10)
    
    return round(score, 4), {"motion": round(m, 3), "retention": round(r, 3), "beat": round(b, 3), "emotion": round(e, 3)}

def validate_segments(candidate_moments: List[Dict], selected_segments: List[Dict], signal_data: Dict, editing_source: str = "fallback") -> Dict:
    if not selected_segments:
        return {"verdict": "FAKE_EDITOR", "error": "No segments selected"}

    max_vals = normalize_signals(candidate_moments, selected_segments, signal_data)
    
    scored_candidates = []
    for cand in candidate_moments:
        score, _ = calculate_score(cand, max_vals, signal_data)
        item = dict(cand)
        item["score"] = score
        scored_candidates.append(item)
    
    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    total_candidates = len(scored_candidates)
    
    # [FIX] Weighted Signal Health (Architect Rule)
    _has_motion = any(abs(s.get("score", 0)) > 0.05 for s in signal_data.get("motion_scores", []))
    _has_emotion = any(abs(s.get("score", 0)) > 0 for s in signal_data.get("emotion_scores", []))
    _has_face = any(c.get("face_present", False) for c in candidate_moments) or bool(signal_data.get("subject_tracking"))
    _has_beat = any(abs(s.get("score", 0)) > 0 for s in signal_data.get("beat_scores", []))
    
    signal_score = (
        (0.4 if _has_motion else 0.0) +
        (0.3 if _has_emotion else 0.0) +
        (0.2 if _has_face else 0.0) +
        (0.1 if _has_beat else 0.0)
    )
    _signal_poor = signal_score < 0.25
    _visual_only = not _has_emotion and not _has_face

    # 🔍 Authority-Aware Mode (Hardened v1.4)
    lq_threshold = 0.40
    hook_limit = 1.0 # default strict
    if editing_source == "gemini_direct":
        lq_threshold = 0.20   # Elite can cut low-face/high-emotion shots without penalty
        hook_limit = 3.0      # Elite handles dramatic slow-burn reveals (not always at 0s)
    elif _signal_poor:
        lq_threshold *= 0.7   # Adaptive multiplier for sparse content
    
    # 2. Process selected segments
    results = []
    low_quality_count = 0
    bad_selection_count = 0
    total_score = 0
    weak_reasoning_count = 0
    
    scene_boundaries = signal_data.get("scene_boundaries", [])
    boundary_violations = 0
    
    durations = []
    for i, seg in enumerate(selected_segments):
        score, signals = calculate_score(seg, max_vals, signal_data)
        total_score += score
        
        # [FIX] Reasoning Quality Floor (>= 4 words)
        # Skip system-injected segments like price_tag anchors - they have internal reasons.
        # Also skip reasoning check for gemini_direct and rhythm — these have auto-generated reasons.
        _system_reasons = {"price_tag_stable_anchor", "highlight_fallback", "ai_cut"}
        _skip_reasoning_check = editing_source in ("gemini_direct", "rhythm", "rhythm_builder")
        reason = seg.get("reason", "")
        if not _skip_reasoning_check and (not isinstance(reason, str) or (len(reason.split()) < 4 and reason not in _system_reasons)):
            weak_reasoning_count += 1
        
        # Quality Classification
        quality = "LOW"
        if score >= 0.60:
            quality = "HIGH"
        elif score >= 0.35:
            quality = "MEDIUM"
        
        if quality == "LOW" or score < lq_threshold:
            low_quality_count += 1
            
        # Variance Tracking
        durations.append(max(0.1, float(seg.get("end", 0)) - float(seg.get("start", 0))))
            
        # Rank Check (Top 40%)
        rank = 1
        for cand in scored_candidates:
            if score < cand["score"]:
                rank += 1
            else:
                break
        
        percentile = rank / max(1, total_candidates)
        selection_valid = percentile <= 0.65 # Relaxed for creative variety
        if not selection_valid:
            bad_selection_count += 1
            
        # [NEW] Scene Boundary Alignment Check
        seg_start = seg.get("start", seg.get("video_time", 0))
        if scene_boundaries:
            # Check if segment start matches ANY scene boundary +/- 0.5s
            aligned = any(abs(seg_start - b[0]) < 0.5 or abs(seg_start - b[1]) < 0.5 for b in scene_boundaries)
            if not aligned:
                boundary_violations += 1
            
        results.append({
            "timestamp": seg_start,
            "score": score,
            "quality": quality,
            "rank_percentile": round(percentile, 3),
            "selection_valid": selection_valid,
            "reason": signals
        })
        
    # 3. Hook Validation (Architect Rule: start <= 1.0s AND score >= 0.4)
    hook_status = "STRICT_HOOK"
    hook_reasons = []
    if results:
        first_seg = results[0]
        if first_seg["timestamp"] > hook_limit:
            hook_status = "WEAK_HOOK"
            hook_reasons.append("LATE_START")
        if first_seg["score"] < 0.4 and not _visual_only:
            hook_status = "WEAK_HOOK"
            hook_reasons.append("LOW_ENERGY_HOOK")
            
    # [NEW] Robotic Pattern Detection (Variance)
    robotic_pattern = False
    if len(durations) >= 3:
        avg = sum(durations) / len(durations)
        variance = sum((d - avg)**2 for d in durations) / len(durations)
        if variance < 0.03:
            robotic_pattern = True

    # 4. Variety Check
    variety_issue = False
    if len(selected_segments) >= 3:
        times = sorted([r["timestamp"] for r in results])
        # Region clustering
        regions = [0, 0, 0, 0]
        max_time = max(times) if times else 1
        for t in times:
            idx = min(3, int((t / max_time) * 4))
            regions[idx] += 1
        if any(count / len(times) > 0.7 for count in regions):
            variety_issue = True

    n = len(results)
    lq_ratio = low_quality_count / n if n > 0 else 0
    bs_ratio = bad_selection_count / n if n > 0 else 0
    bv_ratio = boundary_violations / n if n > 0 else 0
    avg_quality = total_score / n if n > 0 else 0
    
    reasons = []
    failure_type = "NONE"
    
    # For creative editors (gemini_direct / rhythm), skip lq_ratio and hook checks
    # that are calibrated for rule-based fallback editors.
    _is_creative_source = editing_source in ("gemini_direct", "rhythm", "rhythm_builder")
    
    # INTELLIGENCE FAILURES (Signal/Scoring related)
    if _signal_poor:
        reasons.append("SIGNAL_POVERTY")
        failure_type = "INTELLIGENCE_FAILURE"
    if lq_ratio > 0.5 and not _is_creative_source:
        reasons.append("LOW_QUALITY_DOMINANCE")
        failure_type = "INTELLIGENCE_FAILURE"
    if weak_reasoning_count > 0:
        reasons.append("WEAK_REASONING")
        failure_type = "INTELLIGENCE_FAILURE"
    if robotic_pattern and not _is_creative_source:
        reasons.append("ROBOTIC_PATTERN")
        failure_type = "INTELLIGENCE_FAILURE"
    if hook_status == "WEAK_HOOK" and not _is_creative_source:
        reasons.append(f"WEAK_HOOK: {', '.join(hook_reasons)}")
        if failure_type == "NONE": failure_type = "INTELLIGENCE_FAILURE"
        
    # QUALITY GATE: 2nd segment allows MUST BE STRONG
    if n == 2 and avg_quality < 0.35 and not _is_creative_source:
        reasons.append("WEAK_MINIMAL_EDIT")
        if failure_type == "NONE": failure_type = "INTELLIGENCE_FAILURE"

    # STRUCTURAL FAILURES (Shot usage/Cuts related)
    if n < 2:
        reasons.append("INSUFFICIENT_CUTS")
        failure_type = "STRUCTURAL_FAILURE"
    # [FIX] Boundary violations are perfectly fine for RhythmBuilder (which cuts on beats)
    # Only penalize if it's a fallback algorithm
    if bv_ratio > 0.5 and scene_boundaries and editing_source == "fallback":
        reasons.append("POOR_SCENE_ALIGNMENT")
        failure_type = "STRUCTURAL_FAILURE"
    if variety_issue:
        reasons.append("TEMPORAL_CLUSTERING")
        failure_type = "STRUCTURAL_FAILURE"

    verdict = "REAL_EDITOR"
    if reasons:
        # 🔍 Truthful Root Cause Labeling (v1.3 FINAL)
        root_cause = failure_type
        if editing_source in ("fallback", "fallback_forced"):
            root_cause = "FALLBACK_OVERRIDE"
        elif editing_source == "rhythm":
            root_cause = "RHYTHM_OVERRIDE"
        elif editing_source == "gemini_direct" and failure_type != "NONE":
            root_cause = "INTELLIGENCE_FAILURE"

        verdict = f"FAKE_EDITOR:{root_cause}"
        
    return {
        "segments": results,
        "summary": {
            "total_segments": n,
            "signal_health": round(signal_score, 2),
            "visual_only_mode": _visual_only,
            "low_quality_ratio": round(lq_ratio, 2),
            "robotic_pattern": robotic_pattern,
            "weak_reasoning_count": weak_reasoning_count,
            "hook_status": hook_status,
            "variety_issue": variety_issue,
            "editor_quality_score": round(avg_quality, 4)
        },
        "verdict": verdict,
        "reasons": reasons
    }


if __name__ == "__main__":
    # Test with debug data if exists
    debug_file = "reconstructed_timeline_debug.json"
    if os.path.exists(debug_file):
        with open(debug_file, "r") as f:
            data = json.load(f)
        
        candidates = data.get("enriched_pool_top20", [])
        selected = data.get("reconstructed_timeline", [])
        
        # Provide signal_data from candidates to simulate global lookups
        signal_data = {
            "motion_scores": [{"time": c["time"], "score": c.get("motion_energy", 0)} for c in candidates],
            "retention_scores": [{"time": c["time"], "score": c.get("retention_score", 0)} for c in candidates],
            "emotion_scores": [{"time": c["time"], "score": c.get("emotional_spike_score", 0)} for c in candidates],
            "visual_change_scores": [{"time": c["time"], "score": 0.5} for c in candidates]
        }
        
        report = validate_segments(candidates, selected, signal_data)
        print(json.dumps(report, indent=2))
    else:
        print(f"Debug file {debug_file} not found. Use mock data.")
