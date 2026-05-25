import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import time

# Add project root to sys.path
sys.path.append(os.path.abspath(os.getcwd()))

from Compiler_Modules.video_pipeline import render_pipeline

class TestRenderPerformance(unittest.TestCase):

    @patch("Compiler_Modules.video_pipeline.subprocess.run")
    @patch("Compiler_Modules.video_pipeline.os.path.exists")
    @patch("Compiler_Modules.video_pipeline.os.remove")
    def test_pipeline_optimizations(self, mock_remove, mock_exists, mock_run):
        """
        Verify that the render pipeline:
        1. Uses hardware decode (-hwaccel auto)
        2. Consolidates multiple filter chains into a single optimized [v_core]
        3. Uses -filter_complex_script to avoid CLI limits
        """
        mock_exists.return_value = True
        
        # Mock FFmpeg output for duration parsing
        class MockCompletedProcess:
            stderr = b"frame=   30 fps= 60 q=28.0 size= 2048kB time=00:00:01.00 bitrate=4194.3kbits/s"
        mock_run.return_value = MockCompletedProcess()

        # Define a test case with cinematic overlay and text
        text_filter = "drawtext=text='PROMO':x=100:y=100"
        
        result = render_pipeline(
            input_path="mock_input.mp4",
            output_path="mock_output.mp4",
            timeline_instructions={
                "cuts": [0, 5],
                "effects": ["cinematic"],  # Triggers eq=contrast
                "text_filters": [text_filter],
                "zoom_effects": [],
                "speed_ramps": [],
                "transitions": []
            }
        )

        assert result is True
        cmd_called = mock_run.call_args[0][0]
        
        # 1. Hardware decode verification
        assert "-hwaccel" in cmd_called, "Hardware decode flag missing"
        assert "auto" in cmd_called, "Hardware decode 'auto' missing"
        
        # 2. Filter consolidation verification
        # We should see the text filter merged with the eq filter (cinematic) inside the [v_core] chain.
        # It should NOT create a [v_text] map since they are merged.
        filter_complex_idx = cmd_called.index("-filter_complex_script") + 1
        filter_str = cmd_called[filter_complex_idx]
        
        # If filter_str is a path to a file, read it
        if os.path.exists(filter_str) and filter_str.endswith(".txt"):
            with open(filter_str, "r", encoding="utf-8") as f:
                filter_str = f.read()
        
        assert "eq=contrast" in filter_str, "Core visual filter missing"
        assert text_filter in filter_str, "Text filter missing"
        
        # The text filter and eq should be in the same string separated by commas, bound to v_core
        # e.g., eq=...,drawtext=...[v_core]
        assert "[v_core]" in filter_str, "Merged v_core output label missing"
        assert "[v_text]" not in filter_str, "Unoptimized intermediate v_text label was generated!"

        # 3. Mapped output should trace straight from v_core (no intermediate text maps)
        v_map_idx = cmd_called.index("-map") + 1
        v_map = cmd_called[v_map_idx]
        assert v_map == "[v_core]", f"Expected final output map to be [v_core], got {v_map}"


    @patch("Compiler_Modules.video_pipeline.subprocess.run")
    @patch("Compiler_Modules.video_pipeline.os.path.exists")
    @patch("Compiler_Modules.video_pipeline.os.remove")
    def test_conditional_filter_construction(self, mock_remove, mock_exists, mock_run):
        """
        Verify that if no zoom, speed ramps, or transitions exist, the graph
        remains clean and simple without empty labels.
        """
        mock_exists.return_value = True
        class MockCompletedProcess:
            stderr = b"frame=   30 fps= 60 q=28.0 size= 2048kB time=00:00:01.00 bitrate=4194.3kbits/s"
        mock_run.return_value = MockCompletedProcess()

        result = render_pipeline(
            input_path="mock_input.mp4",
            output_path="mock_output.mp4",
            timeline_instructions={
                "cuts": [1.0, 2.0],
                "zoom_effects": [],
                "speed_ramps": [],
                "transitions": []
            }
        )
        
        assert result is True
        cmd_called = mock_run.call_args[0][0]
        
        filter_complex_idx = cmd_called.index("-filter_complex_script") + 1
        filter_str = cmd_called[filter_complex_idx]
        
        # If filter_str is a path to a file, read it
        if os.path.exists(filter_str) and filter_str.endswith(".txt"):
            with open(filter_str, "r", encoding="utf-8") as f:
                filter_str = f.read()
        
        # We should definitively NOT see zoom, speed, or transition labels
        assert "[v_zoom" not in filter_str, "Zoom label found when zoom list was empty"
        assert "[a_ramps]" not in filter_str, "Audio ramps label found when ramp list was empty"
        assert "xfade" not in filter_str, "XFade filter found when transitions list was empty"

if __name__ == "__main__":
    unittest.main()
