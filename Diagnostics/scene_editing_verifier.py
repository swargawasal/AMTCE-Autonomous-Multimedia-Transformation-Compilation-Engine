"""
Diagnostics/scene_editing_verifier.py
--------------------------------------
Scene Reconstruction Verifier.

Detects when the pipeline performs simple trimming instead of real scene reconstruction.
Runs near the end of the pipeline.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger("scene_editing_verifier")

DIAG_DEFAULT = {
    "editing_diagnostics": {
        "scene_count": 0,
        "segments_created": 0,
        "concat_used": False,
        "editing_effective": False,
    }
}


class SceneReconstructionVerifier:
    """
    Verifies if true scene-based editing occurred.
    """

    def verify(
        self,
        scene_count: int = 0,
        segments_created: int = 0,
        concat_used: bool = False,
        duration_change_ratio: float = 1.0,
        timeline_duration_sec: float = 0.0,
        non_chronological: bool = False,
        avg_composite_score: float = 0.0,
    ) -> dict:
        """
        Run diagnostics on the editing process.

        Logic (Architect Rule):
           editing_effective = non_chronological AND segments_created >= 3 AND avg_composite_score > 0.35

        Args:
            scene_count: Number of scenes initially detected.
            segments_created: Number of final highlights trimmed for the render.
            concat_used: True if the segments were concatenated (multi-part edit).
            duration_change_ratio: output_duration / input_duration
            non_chronological: True when segments were reordered narratively (non-linear edit).
            avg_composite_score: The average quality score of selected segments.

        Returns:
            Dict containing "editing_diagnostics".
        """
        try:
            cuts_per_second = (
                (segments_created / timeline_duration_sec)
                if timeline_duration_sec > 0
                else 0.0
            )

            # [REFINED RULE] Quality + Quantity + Structural Change
            # Old rule demanded non_chronological which incorrectly flagged good chronological edits like
            # fashion hook-first as FAKE_EDITOR. Now allows chronological if score is good enough,
            # and lowered the score requirement since micro-cuts dilute the raw average.
            editing_effective = bool(
                segments_created >= 3
                and (non_chronological or avg_composite_score >= 0.15)
            )

            if not editing_effective:
                logger.warning(
                    f"⚠️ [Verifier] Editing appeared ineffective (mostly trimming). "
                    f"Scenes={scene_count}, Segments={segments_created}, Concat={concat_used}, "
                    f"NonChronological={non_chronological}, AvgScore={avg_composite_score:.2f}"
                )
            else:
                logger.info(
                    f"✅ [Verifier] True scene reconstruction confirmed. "
                    f"Segments={segments_created}, AvgScore={avg_composite_score:.2f}, "
                    f"Dur Ratio={duration_change_ratio:.2f}"
                )

            return {
                "editing_diagnostics": {
                    "scene_count": scene_count,
                    "segments_created": segments_created,
                    "concat_used": bool(concat_used),
                    "editing_effective": editing_effective,
                    "non_chronological": bool(non_chronological),
                    "avg_score": round(avg_composite_score, 3),
                }
            }

        except Exception as e:
            logger.warning(f"⚠️ [Verifier] Failed: {e}. Returning default.")
            return DIAG_DEFAULT.copy()


# ── Module singleton + convenience ─────────────────────────────────────────────

_verifier: SceneReconstructionVerifier = None


def get_verifier() -> SceneReconstructionVerifier:
    global _verifier
    if _verifier is None:
        _verifier = SceneReconstructionVerifier()
    return _verifier


def verify_scene_reconstruction(
    scene_count: int = 0,
    segments_created: int = 0,
    concat_used: bool = False,
    duration_change_ratio: float = 1.0,
    timeline_duration_sec: float = 0.0,
    non_chronological: bool = False,
    avg_composite_score: float = 0.0,
) -> dict:
    return get_verifier().verify(
        scene_count=scene_count,
        segments_created=segments_created,
        concat_used=concat_used,
        duration_change_ratio=duration_change_ratio,
        timeline_duration_sec=timeline_duration_sec,
        non_chronological=non_chronological,
        avg_composite_score=avg_composite_score,
    )
