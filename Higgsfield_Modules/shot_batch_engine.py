"""
Shot Batch Engine — Autonomous Credit-Governed Reel Builder
============================================================
Implements a shot-by-shot video generation system that works within
Higgsfield's free-tier credit constraints.

Architecture:
    - A full 21-30s reel is broken into 3-second shots (7-10 shots)
    - Each shot costs 6 credits; free tier max is ~10 credits
    - After each generation, the engine enters WAITING state
    - A monthly (or weekly) credit check fires the next shot automatically
    - Completed shots are FFmpeg-stitched into the final reel

Shot Lifecycle:
    PLANNED -> QUEUED -> GENERATING -> COMPLETED -> STITCHED

Credit Reality (Higgsfield Free Tier, confirmed May 2026):
    - 10 welcome credits (sign-up bonus)
    - Periodic refresh: 1-2 credits every few weeks/months
    - Cheapest video: 6 credits per shot (3-4s @ Nano Banana Pro)
    - Monthly check loop: waits until balance >= 6 before next shot

Reel Strategy:
    Target 21s reel = 7 shots of 3s each
    Shot 1: NOW (with 6 of our 10 credits)
    Shot 2: When credits refresh to 6+ again
    ...until all 7 shots complete, then stitch final reel

Usage:
    from Higgsfield_Modules.shot_batch_engine import ShotBatchEngine

    engine = ShotBatchEngine(persona_name="Disha Patani")
    engine.plan_reel(concept="crimson red saree at neon-lit urban street")
    engine.fire_next_shot()          # runs if credits allow, else schedules
    engine.check_and_fire()          # called by monthly scheduler
    engine.status()                  # returns current reel progress
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("higgsfield.shot_batch")

# ── Constants ──────────────────────────────────────────────────────────────────
BATCH_STATE_DIR = os.path.join("Influencer_Output", "higgsfield", "shot_batches")
CREDIT_PER_SHOT  = 6     # credits consumed per 3s video shot
MAX_FREE_CREDITS = 10    # Higgsfield free tier max (sign-up bonus)
CHECK_INTERVAL_DAYS = 30 # Check for credit refresh monthly


# ── Shot Plan Templates ───────────────────────────────────────────────────────
# Each shot has: index, storyboard description, duration (s), status, output_path

SHOT_TEMPLATES = {
    "fashion_reel_7shot": [
        {
            "index":       1,
            "label":       "Hook — Fabric Macro Close-Up",
            "description": "0-3s: Extreme macro close-up of fabric texture. Camera pulls back slowly to reveal the full outfit. Photorealistic, high cinematic quality. MUST stop the scroll in frame 1.",
            "hook_type":   "fabric_macro",
            "duration":    3,
        },
        {
            "index":       2,
            "label":       "Walk-In — Power Stride",
            "description": "0-3s: Subject walks confidently toward the camera. Shoulders back. Purposeful stride. Fabric flowing. Natural wind in hair. Golden ratio composition.",
            "hook_type":   "power_walk",
            "duration":    3,
        },
        {
            "index":       3,
            "label":       "Outfit Reveal — Full Body",
            "description": "0-3s: Slow pull-back from face to full body. Complete outfit reveal. Elegant controlled movement. Camera holds at reveal peak.",
            "hook_type":   "outfit_reveal",
            "duration":    3,
        },
        {
            "index":       4,
            "label":       "Accessory Zoom — Signature Detail",
            "description": "0-3s: Tight zoom on signature accessory or jewelry. Sparkle in light. Then zooms out to mid-body framing. Shows craftsmanship.",
            "hook_type":   "accessory_zoom",
            "duration":    3,
        },
        {
            "index":       5,
            "label":       "360 Spin Reveal — All Angles",
            "description": "0-3s: Subject rotates slowly 270-360 degrees. Camera captures the full spin. Back detail and silhouette emphasized. Smooth orbital camera.",
            "hook_type":   "spin_reveal",
            "duration":    3,
        },
        {
            "index":       6,
            "label":       "Slow Motion Wind — Cinematic",
            "description": "0-3s: Slow-motion hair and fabric caught in breeze. Ethereal fashion-film quality. Golden ratio. Dramatic fabric billow.",
            "hook_type":   "slow_mo_wind",
            "duration":    3,
        },
        {
            "index":       7,
            "label":       "Closing CTA — Confident Gaze",
            "description": "0-3s: Model looks directly at camera. Intense confident expression. Subtle smile. Direct eye contact. Slow push-in ending on face. Perfect loop point.",
            "hook_type":   "mirror_reveal",
            "duration":    3,
        },
    ]
}


class ShotBatchEngine:
    """
    Autonomous shot-by-shot reel builder that works within free-tier credit limits.
    Persists state between sessions so it can be resumed weeks/months later.
    """

    def __init__(self, persona_name: str = "Disha Patani"):
        self.persona_name = persona_name
        self.state_path   = os.path.join(BATCH_STATE_DIR, f"{persona_name.replace(' ', '_')}_batch.json")
        os.makedirs(BATCH_STATE_DIR, exist_ok=True)
        self.state = self._load_state()

        # Import engine lazily to avoid circular imports
        from Higgsfield_Modules.higgsfield_engine import get_engine, _load_persona_config
        self.engine        = get_engine(persona_name)
        self.persona_cfg   = _load_persona_config(persona_name)

        logger.info(
            f"[SHOT_BATCH] Loaded for '{persona_name}' | "
            f"Credits: {self.engine.get_balance()} | "
            f"Shots done: {self._count_completed()}/{len(self.state.get('shots', []))}"
        )

    # ── State Management ───────────────────────────────────────────────────────
    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "persona":           self.persona_name,
            "concept":           "",
            "niche":             "fashion",
            "reel_duration":     21,
            "shots":             [],
            "completed_shots":   [],
            "final_reel":        None,
            "created_at":        datetime.now().isoformat(),
            "last_checked":      None,
            "next_check_due":    None,
            "status":            "no_plan",
        }

    def _save_state(self) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)
        logger.info(f"[SHOT_BATCH] State saved -> {self.state_path}")

    def _count_completed(self) -> int:
        return len([s for s in self.state.get("shots", []) if s.get("status") == "COMPLETED"])

    # ── Reel Planning ──────────────────────────────────────────────────────────
    def plan_reel(
        self,
        concept:    str,
        niche:      str = "fashion",
        outfit:     str = "",
        setting:    str = "rooftop",
        lighting:   str = "golden_hour",
        fabric:     str = "silk_saree",
        template:   str = "fashion_reel_7shot",
    ) -> dict:
        """
        Pre-plan all shots upfront using the storyboard template.
        Generates Seedance 2.0 prompts for each shot and saves the full plan.
        """
        from Influencer_Modules.prompt_engineer import build_prompts

        template_shots = SHOT_TEMPLATES.get(template, SHOT_TEMPLATES["fashion_reel_7shot"])
        planned = []

        for t in template_shots:
            # Build the full Seedance 2.0 storyboard prompt for this shot
            prompts = build_prompts(
                niche       = niche,
                outfit_desc = outfit or concept,
                setting     = setting,
                lighting    = lighting,
                hook_type   = t["hook_type"],
                fabric      = fabric,
                num_images  = 1,
                duration    = t["duration"],
            )
            planned.append({
                "index":           t["index"],
                "label":           t["label"],
                "description":     t["description"],
                "hook_type":       t["hook_type"],
                "duration":        t["duration"],
                "seedance_prompt": prompts["seedance_prompt"],
                "status":          "PLANNED",
                "output_path":     None,
                "generated_at":    None,
                "credits_spent":   0,
            })

        self.state["concept"]    = concept
        self.state["niche"]      = niche
        self.state["shots"]      = planned
        self.state["status"]     = "PLANNED"
        self.state["next_check_due"] = datetime.now().isoformat()
        self._save_state()

        total_cost = len(planned) * CREDIT_PER_SHOT
        logger.info(
            f"[SHOT_BATCH] Reel planned: {len(planned)} shots | "
            f"Total credits needed: {total_cost} | "
            f"Current balance: {self.engine.get_balance()}"
        )
        return {
            "shots_planned":  len(planned),
            "total_cost":     total_cost,
            "current_balance": self.engine.get_balance(),
            "shots_fundable_now": self.engine.get_balance() // CREDIT_PER_SHOT,
            "plan":           planned,
        }

    # ── Shot Firing ────────────────────────────────────────────────────────────
    def fire_next_shot(self, force: bool = False) -> dict:
        """
        Fire the next pending shot if credits allow.
        Returns status dict with result and remaining credits.
        """
        pending = [s for s in self.state.get("shots", []) if s["status"] == "PLANNED"]

        if not pending:
            completed = self._count_completed()
            total     = len(self.state.get("shots", []))
            if completed == total and total > 0:
                return self._stitch_final_reel()
            return {
                "status":  "no_pending",
                "message": "All shots are complete or no reel has been planned.",
            }

        next_shot = pending[0]
        balance   = self.engine.get_balance()

        if balance < CREDIT_PER_SHOT and not force:
            due = self.state.get("next_check_due", "unknown")
            return {
                "status":       "waiting_for_credits",
                "shot_index":   next_shot["index"],
                "shot_label":   next_shot["label"],
                "balance":      balance,
                "need":         CREDIT_PER_SHOT,
                "short_by":     CREDIT_PER_SHOT - balance,
                "next_check":   due,
                "message":      (
                    f"Not enough credits for Shot {next_shot['index']} ({next_shot['label']}). "
                    f"Have {balance}, need {CREDIT_PER_SHOT}. "
                    f"Next monthly check: {due}"
                ),
            }

        logger.info(
            f"[SHOT_BATCH] Firing Shot {next_shot['index']}: {next_shot['label']} | "
            f"Balance: {balance}"
        )

        result = self.engine.try_generate(
            prompt          = next_shot["seedance_prompt"],
            generation_type = "video",
            duration        = next_shot["duration"],
            force           = force,
        )

        if result["status"] == "generated":
            # Mark shot as COMPLETED
            next_shot["status"]       = "COMPLETED"
            next_shot["output_path"]  = result["output_path"]
            next_shot["generated_at"] = datetime.now().isoformat()
            next_shot["credits_spent"] = CREDIT_PER_SHOT
            self.state["completed_shots"].append(result["output_path"])

            # Set next check date
            next_check = (datetime.now() + timedelta(days=CHECK_INTERVAL_DAYS)).isoformat()
            self.state["next_check_due"] = next_check
            self.state["last_checked"]   = datetime.now().isoformat()
            self.state["status"]         = "IN_PROGRESS"
            self._save_state()

            completed = self._count_completed()
            total     = len(self.state["shots"])

            return {
                "status":      "shot_generated",
                "shot_index":  next_shot["index"],
                "shot_label":  next_shot["label"],
                "output_path": result["output_path"],
                "balance":     result["balance"],
                "shots_done":  completed,
                "shots_total": total,
                "shots_left":  total - completed,
                "next_check":  next_check,
                "message":     (
                    f"Shot {next_shot['index']}/{total} complete: '{next_shot['label']}'. "
                    f"Remaining balance: {result['balance']} credits. "
                    f"Next check (for Shot {next_shot['index']+1}): {next_check}"
                ),
            }
        else:
            return {
                "status":    "shot_failed",
                "shot_index": next_shot["index"],
                "engine_result": result,
            }

    # ── Monthly Credit Check ───────────────────────────────────────────────────
    def check_and_fire(self) -> dict:
        """
        Check if credits allow, then fire as many pending shots as possible in a loop.
        Called by AMTCE's scheduler on a monthly cron tick (or manually).
        """
        now         = datetime.now()
        last_check  = self.state.get("last_checked")
        due         = self.state.get("next_check_due")

        if due:
            due_dt = datetime.fromisoformat(due)
            if now < due_dt:
                # If we have credit headroom, check if we can fire now rather than waiting 30 days!
                balance = self.engine.get_balance()
                if balance < CREDIT_PER_SHOT:
                    days_left = (due_dt - now).days
                    return {
                        "status":    "not_due",
                        "due":       due,
                        "days_left": days_left,
                        "message":   f"Next check in {days_left} days. No action needed (insufficient balance: {balance}).",
                    }
                else:
                    logger.info(f"[SHOT_BATCH] Balance is {balance} (>= {CREDIT_PER_SHOT}) even though check is not due. Running credit loop now!")

        # Record this check
        self.state["last_checked"] = now.isoformat()
        self._save_state()

        results = []
        while True:
            pending = [s for s in self.state.get("shots", []) if s["status"] == "PLANNED"]
            if not pending:
                break
            
            balance = self.engine.get_balance()
            logger.info(f"[SHOT_BATCH] Credit loop check | Balance: {balance} | Pending shots: {len(pending)}")
            
            if balance < CREDIT_PER_SHOT:
                logger.info(f"[SHOT_BATCH] Insufficient credits ({balance}/{CREDIT_PER_SHOT}) to continue the loop.")
                break
                
            shot_res = self.fire_next_shot()
            results.append(shot_res)
            
            # If the shot generation failed or didn't decrease credit properly, break to avoid infinite loops
            if shot_res.get("status") not in ("shot_generated", "reel_complete"):
                logger.warning(f"[SHOT_BATCH] Shot firing returned non-success status: {shot_res.get('status')}. Stopping recursion loop.")
                break
                
            if shot_res.get("status") == "reel_complete":
                break

        if not results:
            balance = self.engine.get_balance()
            # Schedule next check in 30 days
            next_check = (now + timedelta(days=CHECK_INTERVAL_DAYS)).isoformat()
            self.state["next_check_due"] = next_check
            self._save_state()
            return {
                "status":     "insufficient_credits",
                "balance":    balance,
                "need":       CREDIT_PER_SHOT,
                "next_check": next_check,
                "message":    (
                    f"Credits not refreshed yet ({balance}/{CREDIT_PER_SHOT}). "
                    f"Will check again on {next_check}."
                ),
            }
            
        # Return summary of recursive run
        return {
            "status": "recursive_run_complete",
            "results": results,
            "message": f"Credit recursion loop processed {len(results)} shot(s)."
        }

    # ── Final Reel Stitching ───────────────────────────────────────────────────
    def _stitch_final_reel(self) -> dict:
        """
        FFmpeg-stitch all completed shots into the final reel.
        """
        completed_paths = [
            s["output_path"]
            for s in sorted(self.state["shots"], key=lambda x: x["index"])
            if s.get("status") == "COMPLETED" and s.get("output_path")
        ]

        if not completed_paths:
            return {"status": "no_shots", "message": "No completed shots to stitch."}

        reel_dir    = os.path.join("Influencer_Output", "higgsfield", "final_reels")
        os.makedirs(reel_dir, exist_ok=True)

        ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_path  = os.path.join(reel_dir, f"{self.persona_name.replace(' ', '_')}_{ts}_reel.mp4")
        concat_file = os.path.join(reel_dir, f"concat_{ts}.txt")

        with open(concat_file, "w", encoding="utf-8") as f:
            for p in completed_paths:
                f.write(f"file '{os.path.abspath(p)}'\n")

        try:
            result = subprocess.run(
                ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", final_path],
                capture_output=True, text=True, shell=(os.name == "nt")
            )
            if result.returncode == 0:
                self.state["final_reel"] = final_path
                self.state["status"]     = "COMPLETE"
                self._save_state()
                os.remove(concat_file)
                return {
                    "status":     "reel_complete",
                    "reel_path":  final_path,
                    "shots_used": len(completed_paths),
                    "message":    f"Final reel stitched: {final_path}",
                }
            else:
                return {
                    "status":  "stitch_failed",
                    "error":   result.stderr[:300],
                    "message": "FFmpeg stitch failed. Check shots individually.",
                }
        except FileNotFoundError:
            return {
                "status":  "ffmpeg_missing",
                "message": "FFmpeg not found. Install ffmpeg and add to PATH.",
            }

    # ── Status Report ──────────────────────────────────────────────────────────
    def status(self) -> str:
        """Human-readable status for Telegram bot / logs."""
        shots   = self.state.get("shots", [])
        done    = self._count_completed()
        total   = len(shots)
        balance = self.engine.get_balance()
        planned_duration = sum(s["duration"] for s in shots if s.get("status") == "COMPLETED")

        lines = [
            f"[REEL BATCH] Persona: {self.persona_name}",
            f"[REEL BATCH] Concept: {self.state.get('concept', 'not planned')}",
            f"[REEL BATCH] Progress: {done}/{total} shots complete ({planned_duration}s captured)",
            f"[REEL BATCH] Credits: {balance} available | {CREDIT_PER_SHOT} per shot",
            f"[REEL BATCH] Status: {self.state.get('status', 'unknown')}",
            f"[REEL BATCH] Next check: {self.state.get('next_check_due', 'not scheduled')}",
            f"[REEL BATCH] Final reel: {self.state.get('final_reel', 'not yet stitched')}",
        ]

        for s in shots:
            icon = "DONE" if s["status"] == "COMPLETED" else "WAIT"
            lines.append(f"  [{icon}] Shot {s['index']}: {s['label']} ({s['duration']}s)")

        return "\n".join(lines)
