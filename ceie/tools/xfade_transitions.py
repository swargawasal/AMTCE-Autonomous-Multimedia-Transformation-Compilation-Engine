"""
ceie/tools/xfade_transitions.py
--------------------------------
CapCut-grade FFmpeg xfade transition library.
Provides pixel-level custom transitions AND a multi-clip chain builder.

Two engines:
  - XFADE_CUSTOM: custom expr= expressions (pixel-math transitions)
  - XFADE_BUILTIN: FFmpeg native xfade types (no expr needed)

Usage:
    from ceie.tools.xfade_transitions import XfadeEngine
    engine = XfadeEngine()
    ffmpeg_filter = engine.single(clip1, clip2, "slide_left", duration=0.4, offset=4.6)
    ffmpeg_filter = engine.chain(clips, "circle_reveal", duration=0.5)
"""

import logging
import os
import subprocess
import tempfile
from typing import List, Optional

logger = logging.getLogger("xfade_transitions")

# ─────────────────────────────────────────────────────────────────────────────
# TRANSITION LIBRARY
# ─────────────────────────────────────────────────────────────────────────────

# Custom pixel-math expressions (CapCut-style)
XFADE_CUSTOM: dict[str, str] = {
    # Slide family
    "slide_left":    "if(gt(X,W*P),A,B)",
    "slide_right":   "if(lt(X,W*(1-P)),A,B)",
    "slide_up":      "if(gt(Y,H*P),A,B)",
    "slide_down":    "if(lt(Y,H*(1-P)),A,B)",

    # Reveal family
    "circle_reveal": "if(lte(hypot(X-W/2,Y-H/2),hypot(W/2,H/2)*P),B,A)",
    "diagonal_tl":   "if(lt((X/W+Y/H)/2,P),B,A)",
    "diagonal_tr":   "if(lt((1-X/W+Y/H)/2,P),B,A)",
    "zoom_in":       "if(between(X,W/2*(1-P),W-W/2*(1-P))*between(Y,H/2*(1-P),H-H/2*(1-P)),B,A)",

    # Wipe family
    "wipe_soft":     "if(gt(X/(W*P),1.1),A,if(lt(X/(W*P),0.9),B,A*(1-(X/(W*P)-0.9)/0.2)+B*((X/(W*P)-0.9)/0.2)))",

    # Blend family
    "crossfade":     "A*(1-P)+B*P",
    "pixel_dissolve":"if(gt(random(0),1-P),B,A)",

    # Energy family (beat drops, hard moments)
    "flash_white":   "if(lt(P,0.15),A*(1-P*6)+P*6,if(gt(P,0.85),(1-(P-0.85)*6)+B*((P-0.85)*6),B))",
    "dip_black":     "if(lt(P,0.5),mul(A,1-P*2),mul(B,(P-0.5)*2))",
    "punch":         "A*(1-pow(P,3))+B*pow(P,3)",
}

# FFmpeg native xfade types (40+ builtins — no expr needed)
XFADE_BUILTIN: list[str] = [
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite",
    "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "vertopen", "vertclose",
    "horzopen", "horzclose", "dissolve", "pixelize",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice",
    "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr",
    "squeezeh", "squeezev", "zoomin",
]

# Mood/context → recommended transition
MOOD_MAP: dict[str, list[str]] = {
    "cinematic":    ["fadeblack", "dissolve", "dip_black", "crossfade"],
    "vibrant":      ["circle_reveal", "zoom_in", "slide_left", "radial"],
    "dramatic":     ["flash_white", "dip_black", "pixel_dissolve", "diagonal_tl"],
    "warm":         ["fadegrays", "crossfade", "wipe_soft", "glow_fade"],
    "cool":         ["slideup", "slidedown", "diagonal_tr", "circleopen"],
    "ugc":          ["slide_left", "slide_right", "zoom_in", "circle_reveal"],
    "action":       ["slide_up", "punch", "flash_white", "pixel_dissolve"],
    "emotional":    ["fadeblack", "dissolve", "crossfade", "wipe_soft"],
    "educational":  ["wipe_soft", "crossfade", "slideleft", "fadewhite"],
}

# Beat strength → transition
BEAT_MAP: dict[str, list[str]] = {
    "drop":   ["flash_white", "dip_black", "punch", "pixel_dissolve"],
    "strong": ["slide_left", "slide_up", "circle_reveal", "zoom_in"],
    "medium": ["wipe_soft", "diagonal_tl", "crossfade", "slide_right"],
    "weak":   ["crossfade", "fade", "dissolve", "fadegrays"],
    "none":   ["crossfade", "fade"],
}


# ─────────────────────────────────────────────────────────────────────────────
# ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class XfadeEngine:
    """
    Builds FFmpeg xfade filter strings and applies them via subprocess.

    Two modes:
      single()  → filter string for two clips (use in larger filter_complex)
      apply()   → full FFmpeg command execution for N clips
    """

    def resolve(self, name: str) -> tuple[str, bool]:
        """
        Returns (filter_fragment, is_custom).
        filter_fragment is either 'transition=custom:expr=...' or 'transition=name'
        """
        if name in XFADE_CUSTOM:
            return f"transition=custom:expr='{XFADE_CUSTOM[name]}'", True
        if name in XFADE_BUILTIN:
            return f"transition={name}", False
        logger.warning(f"[xfade] Unknown transition '{name}' — falling back to crossfade")
        return f"transition=custom:expr='{XFADE_CUSTOM['crossfade']}'", True

    def recommend(self, mood: str = "ugc", beat: str = "medium") -> str:
        """Pick best transition based on mood + beat context."""
        beat_opts = BEAT_MAP.get(beat, BEAT_MAP["medium"])
        mood_opts = MOOD_MAP.get(mood, MOOD_MAP["ugc"])
        # Intersection preferred
        overlap = [t for t in beat_opts if t in mood_opts]
        return overlap[0] if overlap else beat_opts[0]

    def single_filter(
        self,
        stream_a: str,
        stream_b: str,
        transition: str,
        duration: float = 0.4,
        offset: float = 0.0,
        out_label: str = "[vout]",
    ) -> str:
        """
        Returns a single xfade filter fragment for use inside filter_complex.

        Example:
            "[v0][v1]xfade=transition=custom:duration=0.4:offset=4.6:expr='if(gt(X,W*P),A,B)'[vout]"
        """
        frag, _ = self.resolve(transition)
        return f"{stream_a}{stream_b}xfade={frag}:duration={duration}:offset={offset}{out_label}"

    def build_chain(
        self,
        num_clips: int,
        transition: str,
        duration: float = 0.4,
        clip_duration: float = 60.0,
    ) -> str:
        """
        Builds a chained xfade filter_complex fragment for N clips.
        Normalizes timebase and framerate to prevent timebase mismatch errors.
        """
        filters = []
        # Pre-normalize all input video streams to default timebase and 30fps
        for idx in range(num_clips):
            filters.append(f"[{idx}:v]settb=AVTB,fps=30[v{idx}]")

        prev = "[v0]"
        for i in range(1, num_clips):
            offset = round((i * clip_duration) - duration, 3)
            out = f"[xv{i}]" if i < num_clips - 1 else "[vout]"
            frag, _ = self.resolve(transition)
            filters.append(
                f"{prev}[v{i}]xfade={frag}:duration={duration}:offset={offset}{out}"
            )
            prev = f"[xv{i}]"
        return ";".join(filters)

    def apply(
        self,
        input_clips: list[str],
        output_path: str,
        transition: str = "slide_left",
        duration: float = 0.4,
        clip_duration: Optional[float] = None,
        audio_concat: bool = True,
    ) -> bool:
        """
        Full FFmpeg execution: N clips → 1 output with xfade transitions between all.

        Args:
            input_clips:   List of absolute paths to ordered clip files.
            output_path:   Path for the output video.
            transition:    Transition name (from XFADE_CUSTOM or XFADE_BUILTIN).
            duration:      Transition duration in seconds.
            clip_duration: Duration of each clip. If None, auto-detected from first clip.
            audio_concat:  Whether to concat audio streams too.
        """
        if len(input_clips) < 2:
            logger.error("[xfade] Need at least 2 clips to apply transitions.")
            return False

        # Auto-detect clip duration if not provided
        if clip_duration is None:
            clip_duration = self._get_duration(input_clips[0])
            if clip_duration is None:
                logger.error("[xfade] Could not detect clip duration.")
                return False

        n = len(input_clips)
        inputs = []
        for p in input_clips:
            inputs += ["-i", p]

        # Build video filter chain
        video_chain = self.build_chain(n, transition, duration, clip_duration)

        # Build audio concat
        audio_inputs = "".join(f"[{i}:a]" for i in range(n))
        audio_chain = f"{audio_inputs}concat=n={n}:v=0:a=1[aout]"

        filter_complex = f"{video_chain};{audio_chain}" if audio_concat else video_chain
        map_audio = ["-map", "[aout]"] if audio_concat else []

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            *map_audio,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path,
        ]

        logger.info(f"[xfade] Applying '{transition}' across {n} clips → {output_path}")
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info(f"[xfade] ✅ Done → {output_path}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"[xfade] ❌ FFmpeg failed:\n{e.stderr}")
            return False

    def _get_duration(self, path: str) -> Optional[float]:
        """Use ffprobe to get video duration in seconds."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True, text=True, check=True,
            )
            return float(result.stdout.strip())
        except Exception as e:
            logger.error(f"[xfade] ffprobe failed: {e}")
            return None


# Module-level singleton
engine = XfadeEngine()
