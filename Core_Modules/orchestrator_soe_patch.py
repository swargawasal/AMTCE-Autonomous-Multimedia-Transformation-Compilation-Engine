"""
orchestrator_soe_patch.py
Integration patch for Compiler_Modules/orchestrator.py.

Shows exactly where and how to wire SelfOptimizingEditor into the existing pipeline.
Three integration points:

  POINT A — module load (top of orchestrator.py)
  POINT B — after upload succeeds (write video log)
  POINT C — before EditorBrainV3.process() (inject hints)

The SOE system NEVER replaces a brain decision — it only adjusts weights and
provides a ranked preference list. The brain's confidence gate remains the
final authority on what gets published.
"""

# ═══════════════════════════════════════════════════════════════════
# POINT A — add at module level in orchestrator.py (alongside other imports)
# ═══════════════════════════════════════════════════════════════════
#
# from self_optimizing_editor import SelfOptimizingEditor
#
# _soe = SelfOptimizingEditor(
#     memory_path="editor_memory.json",
#     log_dir="video_logs",
#     credentials=None,          # set to your OAuth2 credentials object
#     mock_analytics=True,       # set False when ready for real API
# )


# ═══════════════════════════════════════════════════════════════════
# POINT B — after YouTube uploader returns video_id
# Insert this block immediately after the uploader.upload() call
# ═══════════════════════════════════════════════════════════════════

def _record_upload_to_soe(profile_data: dict, video_id: str, soe) -> None:
    """
    Record the upload provenance for future learning.
    Safe to call even if SOE is unavailable — logs a warning and continues.
    """
    try:
        brain_result = {
            "arc_type":   profile_data.get("editor_arc", ""),
            "persona":    profile_data.get("editor_persona", ""),
            "avg_energy": profile_data.get("editing_plan", {}).get("avg_energy", 0.5),
            "confidence": profile_data.get("editor_confidence", 0.5),
            "segments":   profile_data.get("editing_plan", {}).get("segments", []),
            "effects":    profile_data.get("editing_plan", {}).get("effects", []),
        }
        duration_s = profile_data.get("video_duration_s", 30.0)
        niche = profile_data.get("detected_niche", "")

        soe.record_upload(
            video_id=video_id,
            brain_result=brain_result,
            video_duration_s=duration_s,
            niche=niche,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("SOE record_upload failed: %s", e)


# ═══════════════════════════════════════════════════════════════════
# POINT C — BEFORE EditorBrainV3.process(), inject optimization hints
# ═══════════════════════════════════════════════════════════════════

def _apply_soe_hints(profile_data: dict, soe) -> dict:
    """
    Fetch current optimization signals and inject them as soft hints
    into profile_data so that CreativeDirector and RewardScorer can
    read them.

    Hints are injected as:
        profile_data["soe_hints"] = {
            "preferred_arc":    str | None,   # top-ranked arc from memory
            "preferred_persona": str | None,
            "arc_scores":       dict,         # full arc → score mapping
            "persona_scores":   dict,
            "top_transitions":  dict,         # arc → best transition
            "reward_overrides": dict,         # RewardScorer weight adjustments
            "memory_cold":      bool,
        }

    When memory_cold=True, all fields except memory_cold are empty/None.
    The brain operates with its default heuristics unchanged.
    """
    try:
        signals = soe.get_optimization_hints()

        arc_rankings = signals.get("arc_rankings", [])
        persona_rankings = signals.get("persona_rankings", [])

        preferred_arc = arc_rankings[0]["arc_type"] if arc_rankings else None
        preferred_persona = persona_rankings[0]["persona"] if persona_rankings else None

        profile_data["soe_hints"] = {
            "preferred_arc":     preferred_arc,
            "preferred_persona": preferred_persona,
            "arc_scores":        {r["arc_type"]: r["score"] for r in arc_rankings},
            "persona_scores":    {r["persona"]: r["score"] for r in persona_rankings},
            "top_transitions":   signals.get("top_transitions", {}),
            "reward_overrides":  signals.get("reward_weight_overrides", {}),
            "memory_cold":       signals.get("memory_cold", True),
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("SOE hint injection failed: %s", e)
        profile_data.setdefault("soe_hints", {"memory_cold": True})

    return profile_data


# ═══════════════════════════════════════════════════════════════════
# HOW CreativeDirector reads the hints (add to creative_director.py)
# ═══════════════════════════════════════════════════════════════════
#
# In CreativeDirector.choose_strategy(), after computing arc_type:
#
#   soe_hints = profile_data.get("soe_hints", {})
#   if not soe_hints.get("memory_cold"):
#       arc_scores = soe_hints.get("arc_scores", {})
#       if arc_type in arc_scores and arc_scores[arc_type] < 0.45:
#           # This arc has historically underperformed — try the memory's top pick
#           preferred = soe_hints.get("preferred_arc")
#           if preferred and preferred != arc_type:
#               logger.info(
#                   "CreativeDirector: SOE suggests %s over %s (scores %.2f vs %.2f)",
#                   preferred, arc_type,
#                   arc_scores.get(preferred, 0.5), arc_scores[arc_type]
#               )
#               arc_type = preferred  # soft override — only if score delta is significant
#
# IMPORTANT: only override if the memory-suggested arc's score is at least
# 0.10 higher than the detected arc's score. Avoid thrashing.


# ═══════════════════════════════════════════════════════════════════
# HOW RewardScorer reads the hints (add to reward_scorer.py)
# ═══════════════════════════════════════════════════════════════════
#
# Add an optional hints parameter to RewardScorer.score():
#
#   def score(self, plan: Dict, hints: Optional[Dict] = None) -> float:
#       ...
#       overrides = (hints or {}).get("reward_overrides", {})
#       w_dynamic_effect = overrides.get("has_dynamic_effect", 0.10)
#       w_final_role     = overrides.get("final_strong_role",  0.10)
#       ...
#       if has_dynamic_effect:
#           score += w_dynamic_effect   # was hardcoded 0.10
#       if roles and roles[-1] in (...):
#           score += w_final_role       # was hardcoded 0.10
#
# This keeps the scorer working identically when hints=None (backwards compatible).


# ═══════════════════════════════════════════════════════════════════
# CRON / SCHEDULED LEARNING PASS
# ═══════════════════════════════════════════════════════════════════
#
# Add a scheduled call in your bot/main.py to run the learning pass
# once per day (or after every N uploads):
#
#   import schedule
#   schedule.every().day.at("03:00").do(_soe.run_learning_pass)
#
# Or trigger it manually after a batch upload:
#   result = _soe.run_learning_pass(max_videos=50)
#   logger.info("Learning pass result: %s", result)
