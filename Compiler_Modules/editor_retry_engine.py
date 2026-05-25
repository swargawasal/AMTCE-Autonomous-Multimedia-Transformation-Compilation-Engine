import logging
from typing import Dict, Any

logger = logging.getLogger("editor_retry_engine")

class EditorRetryEngine:
    """Diagnoses timeline failures and builds targeted retry prompts for Gemini."""
    
    MAX_RETRIES = 3

    def diagnose_failure(self, rejection_reason: str, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Maps an orchestrator rejection code into a structured diagnosis."""
        
        candidates = profile_data.get("candidate_moments", [])
        top_candidates = sorted(
            candidates, 
            key=lambda x: float(x.get("score", x.get("rank_base", 0.0))), 
            reverse=True
        )[:3]
        
        diagnosis = {
            "code": rejection_reason,
            "message": "Unknown failure",
            "suggested_action": "Try selecting different moments with higher face and motion scores.",
            "top_available_moments": [
                f"time={m.get('time', m.get('timestamp', 0.0)):.2f}s (score={float(m.get('score', m.get('rank_base', 0.0))):.2f})" 
                for m in top_candidates
            ]
        }

        if rejection_reason in ("NO_EDIT", "none"):
            diagnosis["message"] = "You returned NO_EDIT or an empty timeline, claiming the content lacked sufficient signal."
            diagnosis["suggested_action"] = "Re-evaluate the provided candidates. You MUST produce an edit. Pick the moments with the highest available scores even if they are not perfect."
        
        elif rejection_reason == "NO_HOOK_OR_CLIMAX":
            diagnosis["message"] = "Your response breached the schema rule: The timeline MUST contain at least one segment with role='hook' and one segment with role='climax'."
            diagnosis["suggested_action"] = "Assign role='hook' to the strongest early moment, and role='climax' to the highest-scoring emotional peak."
            
        elif rejection_reason == "WEAK_HOOK":
            diagnosis["message"] = "The segment you assigned as the 'hook' lacked high face presence, motion, or curiosity scores, making it too weak to capture attention."
            diagnosis["suggested_action"] = "Select a DIFFERENT moment for the hook. Prioritise moments that have high face detail or intense motion."
            
        elif rejection_reason == "BROKEN_FLOW":
            diagnosis["message"] = "The narrative flow score of your timeline was too low (< 0.3). The sequence of clips was incoherent or disjointed."
            diagnosis["suggested_action"] = "Ensure chronological consistency or strong cause-and-effect between adjacent segments. Do not jump erratically between disconnected scenes."
            
        elif rejection_reason in ("ABSOLUTE_WEAK_CONTENT", "FLAT_ENERGY", "NO_CONTRAST"):
            diagnosis["message"] = f"The timeline was rejected for low visual variance ({rejection_reason})."
            diagnosis["suggested_action"] = "Select moments that contrast sharply with each other in energy or visuals to create a dynamic edit."
        
        elif rejection_reason == "CLIMAX_FAIL":
            diagnosis["message"] = "The climax you selected was mathematically weaker than the hook (payoff ratio < 1.0) and lacked semantic parity."
            diagnosis["suggested_action"] = "The climax MUST be the highest-scoring event in the entire sequence. Pick a stronger moment for the climax than the hook."

        elif rejection_reason == "HOOK_REJECTED":
            diagnosis["message"] = "The hook was rejected by quality gates."
            diagnosis["suggested_action"] = "Choose the strongest available candidate moment in the first few seconds with face presence or action."
            
        return diagnosis

    def build_retry_prompt_injection(self, diagnosis: Dict[str, Any], attempt: int) -> str:
        """Constructs the exact text to append to the Gemini prompt on retry."""
        
        hint = (
            f"\n\n======================================================\n"
            f"🚨 PREVIOUS ATTEMPT REJECTED (Retry {attempt} of {self.MAX_RETRIES})\n"
            f"======================================================\n"
            f"Your last response was REJECTED by the pipeline validators.\n\n"
            f"FAILURE REASON: {diagnosis['code']}\n"
            f"DETAILS: {diagnosis['message']}\n"
            f"CORRECTION REQUIRED: {diagnosis['suggested_action']}\n"
        )
        
        if diagnosis.get("top_available_moments"):
            moments_str = ", ".join(diagnosis["top_available_moments"])
            hint += f"\nHINT: Consider these top-scoring moments if you are stuck: {moments_str}\n"
            
        hint += "\nDO NOT repeat the same mistake. Generate a NEW, CORRECTED timeline."
        
        return hint

    def should_retry(self, attempt: int, editing_source: str, rejection_reason: str) -> bool:
        """Determines if another retry is allowed and appropriate."""
        if attempt >= self.MAX_RETRIES:
            logger.warning(f"🚫 [RETRY_ENGINE] Max retries ({self.MAX_RETRIES}) exhausted for '{rejection_reason}'.")
            return False
            
        if rejection_reason in ("SYSTEM_ERROR", "TIMEOUT", "CRASH"):
            return False
            
        return True
