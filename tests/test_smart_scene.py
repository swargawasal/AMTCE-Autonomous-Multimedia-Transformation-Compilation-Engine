import os
import logging
from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor

logging.basicConfig(level=logging.INFO)

def test_editor():
    os.environ["SMART_SCENE_EDITOR_ENABLED"] = "true"
    os.environ["SCENE_DETECTION_ENABLED"] = "true"
    os.environ["TREND_TRANSITIONS_ENABLED"] = "true"
    
    editor = SmartSceneEditor()
    # Mock video duration internally or create a dummy video
    # Let's override _get_video_info for testing
    editor._get_video_info = lambda x: 12.5
    
    instructions = editor.generate_timeline_instructions("mock_video.mp4")
    print("Timeline Instructions:")
    print(instructions)
    
if __name__ == "__main__":
    test_editor()
