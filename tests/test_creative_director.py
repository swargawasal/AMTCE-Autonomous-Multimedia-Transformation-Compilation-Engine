import sys
import logging
import json

sys.path.append(r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)")
from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor
from Content_Intelligence.creative_director import CreativeDirector

logging.basicConfig(level=logging.INFO)

input_video = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Download_Modules\downloads\Avneet_kaur.mp4"

def generate_mock_profile():
    return {
        "duration": 50.0,
        "motion_scores": [],
        "attention_scores": [],
        "hook_analysis": {"hook_score": 0.5, "hook_time": 5.0},
        "transformation_score": 0.8
    }

editor = SmartSceneEditor()
editor.enabled = True
editor.scene_detection_enabled = True

profile = generate_mock_profile()

print("--- 1. WITHOUT Creative Director ---")
res_without = editor.generate_timeline_instructions(input_video, hook_analysis=profile["hook_analysis"])
cuts_without = res_without.get("cuts", [])
scenes_without = res_without.get("scenes", [])
print(f"Segments produced: {len(scenes_without)}")
print([{ "start": round(s.get("start",0), 1), "end": round(s.get("end",0), 1) } for s in scenes_without])


print("\n--- 2. WITH Creative Director ---")
director = CreativeDirector(profile)
strategy = director.build_strategy()

res_with = editor.generate_timeline_instructions(input_video, hook_analysis=profile["hook_analysis"], creative_strategy=strategy)
cuts_with = res_with.get("cuts", [])
scenes_with = res_with.get("scenes", [])
print(f"Segments produced: {len(scenes_with)}")
print([{ "start": round(s.get("start",0), 1), "end": round(s.get("end",0), 1) } for s in scenes_with])
