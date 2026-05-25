"""Pipeline health monitor to guard against invalid editing outputs.

This check runs TWICE in the pipeline:
  1. PRE-Gemini  (stage='pre_gemini')  — EditorBrain may only have 1-2 segments.
                                         Low segment count is EXPECTED at this point.
  2. POST-render (stage='final')       — All intelligence has run; < 3 segments is a real error.

Pass ``stage='pre_gemini'`` from the early health gate to suppress false-positive
'segments_collapsed_under_3' warnings that occur before Gemini master analysis populates
the full editing plan.
"""

from typing import Dict, List, Any, Optional


def check(
    profile_data: Dict[str, Any],
    stage: str = "pre_gemini",
) -> Dict[str, Any]:
    """
    Evaluates whether the pipeline's current state (profile_data) is structurally
    sound enough to proceed.

    Args:
        profile_data: The live pipeline profile dict.
        stage: ``'pre_gemini'`` (default) or ``'final'``.
               In 'pre_gemini' mode a low segment count is demoted to a WARNING
               instead of an ERROR because Gemini hasn't populated the full plan yet.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # ------------------------------------------------------------------
    # 1. Editing plan presence
    # ------------------------------------------------------------------
    editing_plan = profile_data.get("editing_plan") or {}
    if not editing_plan:
        errors.append("missing_editing_plan")

    # ------------------------------------------------------------------
    # 2. Segment count
    # ------------------------------------------------------------------
    segments = (
        profile_data.get("reconstructed_timeline")
        or profile_data.get("editing_timeline")
        or profile_data.get("beat_timeline_segments")
        or editing_plan.get("segments")
        or []
    )

    if not segments:
        errors.append("empty_segments")
    elif len(segments) < 3:
        # Pre-Gemini: EditorBrain may produce only 1-2 segments from sparse fused_moments.
        # This is expected — Gemini will expand the plan.  Demote to warning.
        # Post-final: a rendered output with < 3 segments is genuinely broken.
        if stage == "pre_gemini":
            warnings.append("segments_collapsed_under_3")
        else:
            errors.append("segments_collapsed_under_3")

    # ------------------------------------------------------------------
    # 3. Editor confidence
    # Pre-Gemini: confidence is 0 when EditorBrain hasn't run yet — that's fine.
    # ------------------------------------------------------------------
    confidence = (
        profile_data.get("editor_confidence")
        or profile_data.get("confidence", 0.0)
        or 0.0
    )
    if stage != "pre_gemini" and (confidence is None or confidence <= 0):
        errors.append("non_positive_confidence")

    return {
        "healthy": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "segment_count": len(segments),
        "confidence": confidence,
        "stage": stage,
    }
