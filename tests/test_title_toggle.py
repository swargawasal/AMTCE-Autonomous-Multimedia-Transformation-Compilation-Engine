import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestTitleToggle(unittest.TestCase):
    def setUp(self):
        # Patch dependencies
        self.patcher_video = patch('Compiler_Modules.orchestrator.video_pipeline')
        self.patcher_audio = patch('Compiler_Modules.orchestrator.audio_pipeline')
        self.patcher_voice = patch('Compiler_Modules.orchestrator.voiceover')
        self.patcher_text = patch('Text_Modules.text_overlay.TextOverlay')
        self.patcher_timed = patch('Text_Modules.text_overlay.get_timed_overlay_filter')
        self.patcher_run = patch('subprocess.run')
        
        # Start patchers and keep track of mocks
        self.mock_video = self.patcher_video.start()
        self.mock_audio = self.patcher_audio.start()
        self.mock_voice = self.patcher_voice.start()
        self.mock_text = self.patcher_text.start()
        self.mock_timed = self.patcher_timed.start()
        self.mock_run = self.patcher_run.start()
        
        # Default mock behavior
        self.mock_video.get_video_info.return_value = {"duration": 10.0, "width": 1080, "height": 1920}
        self.mock_run.return_value = MagicMock(returncode=0)
        self.mock_voice.generate_voiceover.return_value = True

    def tearDown(self):
        # Stop all patchers
        self.patcher_video.stop()
        self.patcher_audio.stop()
        self.patcher_voice.stop()
        self.patcher_text.stop()
        self.patcher_timed.stop()
        self.patcher_run.stop()

    @patch.dict(os.environ, {"SHOW_USER_TITLE_OVERLAY": "no"})
    def test_title_disabled(self):
        from Compiler_Modules import orchestrator
        orchestrator.ADAPTIVE_BRAIN_AVAILABLE = False
        orchestrator.BRAIN_AVAILABLE = False
        orchestrator.WATERMARK_AVAILABLE = False
        
        success, meta = orchestrator.compile_video(
            uuid_str="test_uuid_off",
            input_path="input.mp4",
            output_path="output.mp4",
            title="Test Title",
            description="Test Description"
        )
        
        # Verify lane="top" was not called
        found_top = any(
            (call.args[1] == "top" if len(call.args) > 1 else call.kwargs.get("lane") == "top")
            for call in self.mock_timed.call_args_list
        )
        self.assertFalse(found_top, "Title overlay should be disabled")
        
        # Verify voiceover was not called for title (since title length > 10 is needed for has_voiceover if no script)
        # title="Test Title" is < 10, so let's try a longer one to be sure
        
    @patch.dict(os.environ, {"SHOW_USER_TITLE_OVERLAY": "no"})
    @patch("Compiler_Modules.orchestrator.VOICEOVER_AVAILABLE", False)
    def test_title_disabled_voice_blocked(self):
        from Compiler_Modules import orchestrator
        orchestrator.ADAPTIVE_BRAIN_AVAILABLE = False
        orchestrator.BRAIN_AVAILABLE = False
        orchestrator.WATERMARK_AVAILABLE = False
        
        success, meta = orchestrator.compile_video(
            uuid_str="test_uuid_off_voice",
            input_path="input.mp4",
            output_path="output.mp4",
            title="Test Title Long Enough To Trigger",
            description="Test Description"
        )
        
        # If title is disabled, vo_full_text should be empty if no script
        self.mock_voice.generate_voiceover.assert_not_called()

    @patch.dict(os.environ, {"SHOW_USER_TITLE_OVERLAY": "yes"})
    def test_title_enabled(self):
        from Compiler_Modules import orchestrator
        orchestrator.ADAPTIVE_BRAIN_AVAILABLE = False
        orchestrator.BRAIN_AVAILABLE = False
        orchestrator.WATERMARK_AVAILABLE = False
        
        success, meta = orchestrator.compile_video(
            uuid_str="test_uuid_on",
            input_path="input.mp4",
            output_path="output.mp4",
            title="Test Title Long Enough",
            description="Test Description"
        )
        
        found_top = any(
            (call.args[1] == "top" if len(call.args) > 1 else call.kwargs.get("lane") == "top")
            for call in self.mock_timed.call_args_list
        )
        self.assertTrue(found_top, "Title overlay should be enabled")
        self.mock_voice.generate_voiceover.assert_called()

if __name__ == "__main__":
    unittest.main()
