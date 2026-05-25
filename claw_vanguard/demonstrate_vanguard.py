import asyncio
import os
import json
import logging
import sys

# Ensure local modules are discoverable
sys.path.append(os.getcwd())

from claw_vanguard.vanguard_director import VanguardDirector

# Setup Console Logging for the Demo
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("VanguardDemo")

async def run_fashion_demo():
    print("\n" + "═"*60)
    print("🚀 VANGUARD MISSION CONTROL: FASHION NICHE AUTO-PILOT")
    print("═"*60 + "\n")
    
    # 1. Initialize the Director (STRICT: Gemini 3.1 Pro + 3.0 Flash)
    director = VanguardDirector()
    
    # 2. Define the Mission (Fashion Niche)
    # We use real samples found in the downloads folder
    sample_clips = [
        "downloads/Avneet_kaur.mp4",
        "downloads/Disha_patani.mp4"
    ]
    
    # Ensure samples exist
    for clip in sample_clips:
        if not os.path.exists(clip):
            print(f"❌ Error: Required demo clip {clip} is missing from downloads/.")
            return

    mission_request = "Create a high-retention OOTD compilation using the provided fashion clips. Ensure visual consistency using the flash vision sensor."
    
    print(f"💎 Niche: Fashion")
    print(f"🎬 Clips: {len(sample_clips)}")
    print(f"🤖 Brain: Vanguard (STRICT Model Enforcement)")
    print("-" * 30)

    # 3. Execute the 4-Turn Mission Loop
    # Turn 1: Plan (Gemini 3.1 Pro)
    # Turn 2: Execute (FFmpeg)
    # Turn 3: Verify (Gemini 3.0 Flash Vision Sensor)
    # Turn 4: Fix (Gemini 3.1 Pro)
    
    try:
        # We manually call execute_mission for the demo
        result = await asyncio.to_thread(
            director.execute_mission, 
            niche="fashion", 
            video_request=mission_request,
            input_paths=sample_clips
        )
        
        print("\n" + "═"*60)
        print("📊 MISSION COMPLETE: VANGUARD REPORT")
        print("═"*60)
        
        # Load logs to show the user the coordination
        if os.path.exists("logs/mission_dashboard.json"):
            with open("logs/mission_dashboard.json", "r") as f:
                dashboard = json.load(f)
                
            for turn in dashboard[-4:]: # Show the last 4 turns (this mission)
                print(f"Turn {turn['turn']}: {turn['decision']}")
                print(f"   Model: {turn['model']}")
                print(f"   Result: {turn['result']}")
                print(f"   Latency: {turn['latency_ms']}ms")
                print("-" * 20)

        print(f"\n📂 Final Video: {result.output if result.success else 'FAILED'}")
        print("═"*60 + "\n")
        
    except Exception as e:
        logger.error(f"Mission Failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_fashion_demo())
