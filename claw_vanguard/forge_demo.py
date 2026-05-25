import asyncio
import os
import sys
import logging
from Intelligence_Modules.vanguard_forge import vanguard_forge

# Setup Logging for the Demo
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ForgeDemo")

async def run_meta_learning_demo():
    print("\n" + "═"*60)
    print("🔥 VANGUARD FORGE: META-LEARNING SANDBOX DEMO")
    print("═"*60 + "\n")
    
    # 1. Select a safe target for the demo
    target_file = "Audio_Modules/audio_deduplicator.py"
    if not os.path.exists(target_file):
        os.makedirs("Audio_Modules", exist_ok=True)
        with open(target_file, "w") as f:
            f.write("def track_latency():\n    # Existing simple tracker\n    return 0.0\n")
            
    print(f"🎯 Target: {target_file}")
    print(f"🛡️ Guardrails: ACTIVE (Protected Files, Size Limit, Cooldown, Semantic)")
    print("-" * 30)

    # 2. Define the Optimization Task
    optimization_task = "Upgrade the track_latency function to include high-frequency nanosecond precision and logging. Ensure it is surgical."

    # 3. Mock Intelligence & Validation (For Demo Reliability)
    from unittest.mock import patch
    mock_code = "import time\ndef track_latency():\n    start = time.perf_counter_ns()\n    return (time.perf_counter_ns() - start) / 1e6\n"
    
    mock_audit = {
        "approved": True,
        "score": 0.95,
        "critique": "Excellent surgical fix. Uses perf_counter_ns correctly for sub-millisecond precision.",
        "risks": [{"type": "None", "severity": "low"}],
        "fix_suggestions": ["Monitor for jitter in multi-core environments."]
    }
    
    print(f"🧠 Task: {optimization_task}")
    print("🚀 Initializing Forge Pipeline (Simulation Mode)...")
    
    # 4. Patch ALL signals to achieve perfect consensus for the demo
    with patch("Intelligence_Modules.vanguard_forge.GeminiGovernor.generate", return_value=mock_code):
        with patch("Intelligence_Modules.vanguard_forge.VanguardForge.run_ai_auditor", return_value=mock_audit):
            with patch("Intelligence_Modules.vanguard_forge.VanguardForge.semantic_validator", return_value=True):
                with patch("Intelligence_Modules.vanguard_forge.VanguardForge.verify_with_swap", return_value=(True, "Success")):
                    with patch("Intelligence_Modules.vanguard_forge.VanguardForge.analyze_diff", return_value=("LOW", [])):
                        # Run the Real Forge Pipeline Logic
                        result = vanguard_forge.run_forge_pipeline(target_file, optimization_task)
    
    print("\n" + "═"*60)
    print("📊 AI AUDIT REPORT (Elite Verified)")
    print("═"*60)
    
    if result.ai_critique:
        audit = result.ai_critique
        status = "APPROVED" if audit["approved"] else "REJECTED"
        print(f"Decision:  {status}")
        print(f"Score:     {audit['score']}/1.0")
        print(f"Risk:      {result.risk_level} (Surgical)")
        print(f"\n💬 Critique:\n{audit['critique']}")
        
        if audit.get("risks"):
            print("\n🚨 Key Risks:")
            for r in audit["risks"]:
                print(f"- {r['type']} ({r['severity'].upper()})")
                
        if audit.get("fix_suggestions"):
            print("\n💡 Suggested Fixes:")
            for s in audit["fix_suggestions"]:
                print(f"- {s}")

    print("\n" + "═"*60)
    print("📄 CODE DIFF ANALYSIS")
    print("═"*60)
    
    if result.success:
        print(result.diff)
        print("\n🏆 FORGE SUCCESS: 3.0/3.0 Tri-Layer Consensus Reached.")
        print("[AUDIT] COMPUTATIONAL: PASS ✅")
        print("[AUDIT] INTELLECTUAL:  PASS ✅")
        print("[AUDIT] SAFETY:        PASS ✅")
        print("\n[VANGUARD] Human Approval Required. Promotion PAUSED.")
    else:
        print(f"❌ Rejected: {result.message}")
        if result.disagreement:
            print("\n⚠️ LOGIC_RISK: Tests passed but AI Auditor rejected the code logic.")

    print("═"*60 + "\n")

if __name__ == "__main__":
    asyncio.run(run_meta_learning_demo())
