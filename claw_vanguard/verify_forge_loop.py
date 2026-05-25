import asyncio
import logging
import os
from claw_vanguard.vanguard_forge import vanguard_forge

# Setup Logging
logging.basicConfig(level=logging.INFO)

async def test_live_repair_loop():
    target = "Audio_Modules/test_tool.py"
    task = "Fix the TypeError in process_audio_data by adding a None check and returning an empty list."
    
    print(f"🚀 [FORGE_VERIFY] Starting live repair on {target}...")
    
    # We mock the AI response for the simulation to be fast and reliable
    from unittest.mock import patch
    mock_opt = """import os
import logging

logger = logging.getLogger("test_tool")

def process_audio_data(data: list):
    \"\"\"
    FIXED: Safely handles None or invalid data.
    \"\"\"
    if not data or not isinstance(data, list):
        return []
    return [d.upper() for d in data if hasattr(d, 'upper')]
"""
    
    mock_audit = {
        "approved": True,
        "score": 0.98,
        "critique": "Excellent surgical fix. Correctly handles None and non-iterable types.",
        "risks": [],
        "fix_suggestions": []
    }
    
    with patch("Intelligence_Modules.vanguard_forge.GeminiGovernor.generate", return_value=mock_opt):
        with patch("Intelligence_Modules.vanguard_forge.VanguardForge.run_ai_auditor", return_value=mock_audit):
            with patch("Intelligence_Modules.vanguard_forge.VanguardForge.verify_with_swap", return_value=(True, "Tests passed")):
                result = vanguard_forge.run_forge_pipeline(target, task)
    
    if result.success:
        print(f"✅ [FORGE_VERIFY] SUCCESS: Proposal {result.proposal_id} generated.")
        print(f"📁 History: logs/forge_history/{result.proposal_id}.json")
    else:
        print(f"❌ [FORGE_VERIFY] FAILED: {result.message}")

if __name__ == "__main__":
    asyncio.run(test_live_repair_loop())
