"""
Visual_Refinement_Modules/rhythm_quality_guard.py
--------------------------------------------------
Enforces creative rhythm discipline during timeline reconstruction.
Fixes the "cowardly editing" problem by protecting narrative flow and energy progression.

Classes:
    VariableSlotSelector: Replaces the rigid 5-slot template.
    EnergyProgressionGuard: Ensures climax score >= average build score.
    DynamicTrimCalculator: Calculates trim tightness per-moment based on signals.
"""

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger("rhythm_quality_guard")

# Default narrative order
NARRATIVE_ORDER: List[str] = ["hook", "reaction", "build", "climax", "resolution"]

class VariableSlotSelector:
    """
    Selects 3 to 9 narrative slots based on the quality of available moments,
    eliminating the rigid 5-slot template.
    """
    
    @staticmethod
    def choose(candidate_count: int, duration: float) -> List[Tuple[str, int]]:
        """
        Returns a list of tuples: (role_name, count)
        """
        # Base minimal edit (3 segments)
        if candidate_count < 4 or duration < 5.0:
            logger.info("[RHYTHM_GUARD] Selecting minimal 3-slot template")
            return [("hook", 1), ("build", 1), ("climax", 1)]
            
        # Short video or limited candidates (4-5 segments)
        if candidate_count < 7 or duration < 12.0:
            logger.info("[RHYTHM_GUARD] Selecting standard 5-slot template")
            return [("hook", 1), ("reaction", 1), ("build", 1), ("climax", 1), ("resolution", 1)]
            
        # Medium length / rich candidates (6-7 segments)
        if candidate_count < 10 or duration < 20.0:
            logger.info("[RHYTHM_GUARD] Selecting expanded 6-slot template")
            return [("hook", 1), ("reaction", 1), ("build", 2), ("climax", 1), ("resolution", 1)]
            
        # Long video with rich candidates (8-9 segments)
        build_cnt = min(5, candidate_count - 4) # max 5 builds
        logger.info(f"[RHYTHM_GUARD] Selecting rich {build_cnt+4}-slot template")
        return [("hook", 1), ("reaction", 1), ("build", build_cnt), ("climax", 1), ("resolution", 1)]


class EnergyProgressionGuard:
    """
    Guards against "false climaxes" by verifying that the climax segment
    is actually stronger than the build segments leading up to it.
    """
    
    @staticmethod
    def validate_and_fix(story_map: List[Dict]) -> List[Dict]:
        """
        Takes a role-assigned story_map and ensures climax score >= average build score.
        If it fails, the highest scoring build is swapped with the climax.
        """
        if not story_map:
            return []
            
        climax_idx = next((i for i, seq in enumerate(story_map) if seq["role"] == "climax"), -1)
        if climax_idx == -1:
            return story_map  # No climax to guard
            
        climax = story_map[climax_idx]
        climax_score = climax["composite_score"]
        
        build_indices = [i for i, seq in enumerate(story_map) if seq["role"] == "build"]
        if not build_indices:
            return story_map  # No builds to compare against
            
        builds = [story_map[i] for i in build_indices]
        avg_build_score = sum(b["composite_score"] for b in builds) / len(builds)
        
        if climax_score >= avg_build_score:
            logger.info(f"[ENERGY_GUARD] OK: climax_score ({climax_score:.3f}) >= avg_build ({avg_build_score:.3f})")
            return story_map
            
        logger.warning(
            f"[ENERGY_GUARD] VIOLATION: climax_score ({climax_score:.3f}) < avg_build ({avg_build_score:.3f}). "
            f"Initiating swap."
        )
        
        # Find strongest build
        strongest_build_idx = max(build_indices, key=lambda i: story_map[i]["composite_score"])
        strongest_build = story_map[strongest_build_idx]
        
        if strongest_build["composite_score"] > climax_score:
            # Perform swap
            logger.info(
                f"[ENERGY_GUARD] SWAPPED: climax ({climax_score:.3f}) <-> "
                f"build ({strongest_build['composite_score']:.3f})"
            )
            story_map[climax_idx]["role"] = "build"
            story_map[strongest_build_idx]["role"] = "climax"
            
            # Re-sort to maintain narrative order mapping
            def _sort_key(entry: Dict) -> tuple:
                role_idx = (
                    NARRATIVE_ORDER.index(entry["role"])
                    if entry["role"] in NARRATIVE_ORDER
                    else 99
                )
                score = entry["composite_score"]
                if entry["role"] == "build":
                    return (role_idx, score) # ascending
                return (role_idx, -score)    # descending
                
            story_map.sort(key=_sort_key)
            
        return story_map


class DynamicTrimCalculator:
    """
    Calculates dynamic segment extraction windows per-moment, replacing
    the static (and mechanical) ROLE_WINDOWS.
    """
    
    @staticmethod
    def compute(moment: Dict) -> Tuple[float, float]:
        """
        Returns (pre_seconds, post_seconds) based on moment signals.
        """
        role = moment.get("role", "build")
        has_face = moment.get("face_present", False)
        is_beat = moment.get("beat_aligned", False)
        is_high_motion = float(moment.get("motion_intensity", moment.get("motion_energy", 0.0))) > 0.6
        has_strong_score = float(moment.get("composite_score", 0.0)) > 0.8
        
        pre, post = 1.0, 2.0  # default base
        tightness = "medium"
        
        # 1. Base sizing by role
        if role == "hook":
            pre, post = 0.3, 1.2
        elif role == "climax":
            pre, post = 0.5, 1.8
        elif role == "reaction":
            pre, post = 0.8, 1.5
        elif role == "resolution":
            pre, post = 1.8, 3.0
        elif role == "build":
            pre, post = 1.5, 2.5
            
        # 2. Dynamic adjustments
        # Beat hits should be cut tight on entry
        if is_beat:
            pre = max(0.1, pre - 0.3)
            tightness = "tight"
            
        # Faces / emotional moments need a little breathing room to register expression
        if has_face and role != "hook":
            post += 0.4
            pre += 0.2
            if not is_beat:
                tightness = "diffuse"
                
        # High motion needs to be punchy
        if is_high_motion:
            post = min(post, 1.6)
            
        # Extremely strong moments (hook/climax peak) should dominate visually
        if has_strong_score and role in ["hook", "climax"]:
            post += 0.5
            
        # Record the computed tightness for potential feedback logic
        moment["trim_tightness"] = tightness
            
        return round(pre, 2), round(post, 2)
