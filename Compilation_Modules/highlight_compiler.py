"""
Compilation Mode Module — Long-Form Video Assembly

Implements highlight compilation mode for creating 6-10 minute long-form videos
from multiple mined moments. Supports both "highlight" (short-form) and 
"compilation" (long-form) pipeline modes.

Structure:
    Intro
    ↓
    Moment #N
    ↓
    Voiceover commentary
    ↓
    Moment #N-1
    ↓
    Voiceover commentary
    ↓
    Outro
"""

import os
import json
import logging
import subprocess
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("highlight_compiler")

# Mode constants
MODE_HIGHLIGHT = "highlight"
MODE_COMPILATION = "compilation"

# Default compilation settings
DEFAULT_TARGET_DURATION_MIN = 360  # 6 minutes
DEFAULT_TARGET_DURATION_MAX = 600  # 10 minutes
DEFAULT_MOMENT_COUNT = 10
DEFAULT_COMMENTARY_DURATION = 3.0  # seconds of voiceover per commentary slot


class HighlightCompiler:
    """
    Compiles long-form videos from multiple candidate moments.

    Features:
        - Sorts moments by score
        - Selects top N moments
        - Inserts commentary sections
        - Outputs 6-10 minute compilations
    """

    def __init__(self, profile_data: Dict[str, Any]):
        self.profile = profile_data
        self.mode: str = profile_data.get("compilation_mode", MODE_HIGHLIGHT)
        self.candidate_moments: List[Dict] = profile_data.get("candidate_moments", [])
        self.target_duration: int = profile_data.get(
            "compilation_duration",
            DEFAULT_TARGET_DURATION_MIN if self.mode == MODE_COMPILATION else 60
        )
        self.moment_count: int = profile_data.get(
            "compilation_moment_count",
            DEFAULT_MOMENT_COUNT if self.mode == MODE_COMPILATION else 5
        )

    def compile_top_moments(self, count: int = None, 
                           with_commentary: bool = True) -> List[Dict]:
        """
        Main compilation method: Sort moments by score, select top N,
        and insert commentary sections between moments.

        Args:
            count: Number of moments to include (default: self.moment_count)
            with_commentary: Whether to insert commentary sections

        Returns:
            List of compiled segments with commentary slots
        """
        count = count or self.moment_count

        if not self.candidate_moments:
            logger.warning("[COMPILATION] No candidate moments available")
            return []

        # 1. Sort by score descending
        sorted_moments = sorted(
            self.candidate_moments,
            key=lambda x: x.get("score", 0.0),
            reverse=True
        )

        # 2. Select top N moments with temporal deduplication
        selected = []
        occupied_slots = [] # List of (start, end) tuples
        
        for moment in sorted_moments:
            if len(selected) >= count:
                break
                
            m_time = moment.get("time", 0.0)
            m_dur = moment.get("duration_hint", 2.5)
            m_start = m_time - (m_dur / 2.0)
            m_end = m_time + (m_dur / 2.0)
            
            # Check for overlap with already selected moments
            overlap = False
            for s_start, s_end in occupied_slots:
                # If they overlap by more than 0.5s, reject
                if max(m_start, s_start) < min(m_end, s_end) - 0.5:
                    overlap = True
                    break
            
            if not overlap:
                selected.append(moment)
                occupied_slots.append((m_start, m_end))

        logger.info(f"[COMPILATION] Selected {len(selected)} unique moments (deduplicated) from {len(sorted_moments)} candidates.")

        # 3. Build compilation structure
        if self.mode == MODE_COMPILATION:
            return self._build_long_form_compilation(selected, with_commentary)
        else:
            return self._build_highlight_reel(selected)

    def _build_long_form_compilation(self, moments: List[Dict],
                                      with_commentary: bool = True) -> List[Dict]:
        """
        Build long-form compilation with intro/outro and commentary.

        Structure:
            [Intro]
            ↓
            [Moment #1] → [Commentary #1]
            ↓
            [Moment #2] → [Commentary #2]
            ↓
            ...
            ↓
            [Moment #N]
            ↓
            [Outro]
        """
        segments = []
        current_time = 0.0

        # [INTRO]
        intro_segment = self._create_intro_segment(current_time)
        if intro_segment:
            segments.append(intro_segment)
            current_time += intro_segment["duration"]
            logger.info(f"[COMPILATION] Added intro: {intro_segment['duration']:.1f}s")

        # [MOMENTS + COMMENTARY]
        for i, moment in enumerate(moments):
            # Moment segment
            moment_seg = self._create_moment_segment(moment, current_time, i + 1)
            segments.append(moment_seg)
            current_time += moment_seg["duration"]

            # Commentary between moments (not after the last moment)
            if with_commentary and i < len(moments) - 1:
                commentary = self._create_commentary_segment(current_time, i + 1, moment)
                if commentary:
                    segments.append(commentary)
                    current_time += commentary["duration"]

        # [OUTRO]
        outro_segment = self._create_outro_segment(current_time)
        if outro_segment:
            segments.append(outro_segment)
            current_time += outro_segment["duration"]
            logger.info(f"[COMPILATION] Added outro: {outro_segment['duration']:.1f}s")

        total_duration = sum(s["duration"] for s in segments)
        logger.info(
            f"[COMPILATION] Long-form assembly complete: "
            f"{len(segments)} segments, {total_duration:.1f}s total"
        )

        return segments

    def _build_highlight_reel(self, moments: List[Dict]) -> List[Dict]:
        """
        Build short-form highlight reel (quick concatenation).
        """
        segments = []
        current_time = 0.0

        for i, moment in enumerate(moments):
            moment_seg = self._create_moment_segment(moment, current_time, i + 1)
            segments.append(moment_seg)
            current_time += moment_seg["duration"]

        total_duration = sum(s["duration"] for s in segments)
        logger.info(
            f"[COMPILATION] Highlight reel complete: "
            f"{len(segments)} segments, {total_duration:.1f}s total"
        )

        return segments

    def _create_intro_segment(self, start_time: float) -> Optional[Dict]:
        """Create intro segment metadata."""
        intro_duration = 3.0  # 3 second intro

        return {
            "type": "intro",
            "start": round(start_time, 3),
            "end": round(start_time + intro_duration, 3),
            "duration": intro_duration,
            "video_time": 0.0,  # Can be a pre-made intro clip
            "transition_after": "fade_in"
        }

    def _create_outro_segment(self, start_time: float) -> Optional[Dict]:
        """Create outro segment metadata."""
        outro_duration = 3.0  # 3 second outro

        return {
            "type": "outro",
            "start": round(start_time, 3),
            "end": round(start_time + outro_duration, 3),
            "duration": outro_duration,
            "video_time": None,  # Can be a pre-made outro clip
            "transition_after": None
        }

    def _create_moment_segment(self, moment: Dict, 
                                timeline_start: float,
                                moment_number: int) -> Dict:
        """
        Create a segment entry for a mined moment.
        """
        m_time = moment.get("time", 0.0)
        m_duration = moment.get("duration_hint", 2.5)
        m_score = moment.get("score", 0.0)
        m_type = moment.get("type", "appearance")

        # Calculate segment bounds
        seg_start = max(0.0, m_time - m_duration / 2)
        seg_end = seg_start + m_duration

        return {
            "type": "moment",
            "moment_number": moment_number,
            "start": round(timeline_start, 3),
            "end": round(timeline_start + m_duration, 3),
            "duration": m_duration,
            "video_time": round(m_time, 3),  # Timestamp in source video
            "video_start": round(seg_start, 3),
            "video_end": round(seg_end, 3),
            "moment_type": m_type,
            "score": round(m_score, 3),
            "face_present": moment.get("face_present", False),
            "beat_aligned": moment.get("beat_aligned", False),
            "motion_intensity": moment.get("motion_intensity", 0.0),
            "transition_after": "whip_pan"  # Dynamic transitions between moments
        }

    def _create_commentary_segment(self, timeline_start: float,
                                    commentary_number: int,
                                    preceding_moment: Dict) -> Optional[Dict]:
        """
        Create a commentary/voiceover segment.
        """
        commentary_duration = DEFAULT_COMMENTARY_DURATION

        # Generate context-aware commentary text
        moment_type = preceding_moment.get("type", "appearance")
        score = preceding_moment.get("score", 0.0)

        # Commentary text suggestions based on moment type
        commentary_templates = {
            "appearance": f"Watch this moment unfold...",
            "reaction": f"Notice the reaction here...",
            "motion_peak": f"This action is key...",
            "beat": f"Right on the beat...",
            "dialogue": f"Listen carefully..."
        }

        commentary_text = commentary_templates.get(
            moment_type, 
            f"Moment {commentary_number} highlights..."
        )

        return {
            "type": "commentary",
            "commentary_number": commentary_number,
            "start": round(timeline_start, 3),
            "end": round(timeline_start + commentary_duration, 3),
            "duration": commentary_duration,
            "text": commentary_text,
            "transition_after": "fade"
        }

    def generate_compilation_plan(self, output_path: str = None) -> Dict[str, Any]:
        """
        Generate a complete compilation plan document.

        Returns:
            Dictionary with full compilation metadata
        """
        segments = self.compile_top_moments()

        plan = {
            "compilation_mode": self.mode,
            "created_at": datetime.now().isoformat(),
            "total_segments": len(segments),
            "total_duration_sec": sum(s["duration"] for s in segments),
            "target_duration_range": [
                DEFAULT_TARGET_DURATION_MIN if self.mode == MODE_COMPILATION else 30,
                DEFAULT_TARGET_DURATION_MAX if self.mode == MODE_COMPILATION else 90
            ],
            "segments": segments,
            "source_moments_used": len([s for s in segments if s["type"] == "moment"]),
            "commentary_slots": len([s for s in segments if s["type"] == "commentary"]),
            "has_intro": any(s["type"] == "intro" for s in segments),
            "has_outro": any(s["type"] == "outro" for s in segments)
        }

        # Export plan if path provided
        if output_path:
            with open(output_path, "w") as f:
                json.dump(plan, f, indent=2)
            logger.info(f"[COMPILATION] Plan exported: {output_path}")

        return plan


def compile_top_moments(profile_data: Dict[str, Any], 
                        count: int = 10,
                        with_commentary: bool = True) -> List[Dict]:
    """
    Convenience function for orchestrator integration.

    Args:
        profile_data: Pipeline profile data (must contain candidate_moments)
        count: Number of moments to include
        with_commentary: Whether to add commentary sections

    Returns:
        List of compiled segments
    """
    compiler = HighlightCompiler(profile_data)
    return compiler.compile_top_moments(count=count, with_commentary=with_commentary)


def generate_compilation_ffmpeg_script(segments: List[Dict],
                                        source_video: str,
                                        output_path: str,
                                        job_dir: str = None) -> str:
    """
    Generate FFmpeg filter_complex script for compilation rendering.

    Args:
        segments: Compiled segments from compile_top_moments()
        source_video: Path to source video
        output_path: Desired output path
        job_dir: Directory for temp files

    Returns:
        Path to generated FFmpeg script file
    """
    if not segments:
        logger.error("[COMPILATION] Cannot generate script: no segments provided")
        return None

    job_dir = job_dir or os.path.dirname(output_path) or "."
    script_path = os.path.join(job_dir, "compilation_filter_complex.txt")

    filter_parts = []
    input_labels = []
    stream_counter = 0

    for i, seg in enumerate(segments):
        seg_type = seg.get("type", "moment")
        video_time = seg.get("video_time")
        duration = seg.get("duration", 2.0)

        if seg_type == "moment" and video_time is not None:
            # Extract segment from source using trim
            video_start = seg.get("video_start", video_time - duration/2)
            video_end = seg.get("video_end", video_time + duration/2)

            filter_parts.append(
                f"[0:v]trim=start={video_start}:end={video_end},setpts=PTS-STARTPTS[v{stream_counter}];"
            )
            filter_parts.append(
                f"[0:a]atrim=start={video_start}:end={video_end},asetpts=PTS-STARTPTS[a{stream_counter}];"
            )
            input_labels.append(f"[v{stream_counter}][a{stream_counter}]")
            stream_counter += 1

        elif seg_type == "commentary":
            # Placeholder for commentary (would need actual audio file)
            # For now, insert a silent gap
            filter_parts.append(
                f"aevalsrc=0:c=stereo:s=44100:d={duration}[a{stream_counter}];"
            )
            filter_parts.append(
                f"color=c=black:s=1080x1920:d={duration}[v{stream_counter}];"
            )
            input_labels.append(f"[v{stream_counter}][a{stream_counter}]")
            stream_counter += 1

    # Concatenate all segments
    if input_labels:
        all_inputs = "".join(input_labels)
        filter_parts.append(
            f"{all_inputs}concat=n={stream_counter}:v=1:a=1[outv][outa]"
        )

    # Write filter_complex script
    with open(script_path, "w") as f:
        f.write("\n".join(filter_parts))

    logger.info(f"[COMPILATION] FFmpeg script generated: {script_path}")
    return script_path


def render_compilation(segments: List[Dict],
                       source_video: str,
                       output_path: str,
                       job_dir: str = None) -> bool:
    """
    Render the compilation using FFmpeg with filter_complex.

    Args:
        segments: Compiled segments
        source_video: Source video path
        output_path: Output video path
        job_dir: Temporary working directory

    Returns:
        bool: True if successful
    """
    try:
        script_path = generate_compilation_ffmpeg_script(
            segments, source_video, output_path, job_dir
        )

        if not script_path:
            return False

        # Build FFmpeg command with filter_complex_script
        cmd = [
            os.getenv("FFMPEG_BIN", "ffmpeg"),
            "-y",
            "-i", source_video,
            "-filter_complex_script", script_path,
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            output_path
        ]

        logger.info(f"[COMPILATION] Rendering: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode == 0:
            logger.info(f"[COMPILATION] Render complete: {output_path}")
            return True
        else:
            logger.error(f"[COMPILATION] FFmpeg failed: {result.stderr[:500]}")
            return False

    except Exception as e:
        logger.error(f"[COMPILATION] Render failed: {e}")
        return False
