import asyncio
from typing import Dict, Any
from safety.gemini_checker import check_safety_gemini
from safety.qwen_checker import check_factual_consistency
from memory.brain_io import append_to_safety_log

async def run_safety_checks_async(text: str, session_id: str = "unknown") -> None:
    # Run both checks concurrently
    gemini_task = asyncio.to_thread(check_safety_gemini, text)
    qwen_task = asyncio.to_thread(check_factual_consistency, text)
    
    gemini_verdict, qwen_verdict = await asyncio.gather(gemini_task, qwen_task)
    
    final_verdict = "PASS"
    if gemini_verdict != "PASS" or qwen_verdict != "PASS":
        final_verdict = "FLAG"
        
        flag_data = {
            "session_id": session_id,
            "text_snippet": text[:200] + "...",
            "gemini_verdict": gemini_verdict,
            "qwen_verdict": qwen_verdict,
            "final_verdict": final_verdict
        }
        # Log to ruflow_brain/safety_log.json
        append_to_safety_log(flag_data)
