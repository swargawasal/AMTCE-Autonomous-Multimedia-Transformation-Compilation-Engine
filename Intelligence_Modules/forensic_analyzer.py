"""
Intelligence_Modules/forensic_analyzer.py
------------------------------------------
Forensic Video Analyzer — Vision-AI based frame inspection.

Performs TWO tasks on extracted video frames via Gemini Vision:

  TASK 1 — WATERMARK DETECTION
    Returns bounding boxes for any watermark / logo / branding detected.

  TASK 2 — CONTENT ANALYSIS
    Classifies content intent, confidence, recommended editing feature flags,
    and monetization safety rating.

Output format (strict JSON):
{
  "watermarks": [{"x":0,"y":0,"w":0,"h":0}],
  "content_strategy": {
    "intent": "...",
    "confidence": 0.0-1.0,
    "feature_flags": {
        "enable_price_tags": true/false,
        "enable_fashion_caption": true/false,
        "enable_cinematic_zoom": true/false,
        "enable_speed_ramps": true/false,
        "enable_fast_pacing": true/false,
        "enable_voiceover": true/false
    },
    "recommended_editing_style": "...",
    "safety": "safe|risky|blocked"
  }
}
"""

import os
import json
import logging
import re
import subprocess
import tempfile
import shutil
from typing import List, Optional
from gemini_governor import gemini_router
from Intelligence_Modules.gemini_governor import gemini_router

from dotenv import load_dotenv

# Load env
if os.path.exists(".env"):
    load_dotenv(".env", override=True)
else:
    load_dotenv("Credentials/.env", override=True)

logger = logging.getLogger("forensic_analyzer")

# ── Prompt ────────────────────────────────────────────────────────────────────

FORENSIC_PROMPT = """
You are a Professional Short-Form Content Director with two tasks.

You analyze video frames and decide how an AI editing engine should transform the footage into engaging content for:
- YouTube Shorts
- Instagram Reels
- Facebook Reels

---

TASK 1 — EDITING DIRECTION

Your role is to DIRECT the editing pipeline, not describe the video.

Determine:
1. The best content type from the footage.
2. The editing style required to maximize engagement.
3. Which editing features the engine should enable or disable.
4. Whether the content is safe for monetization.
5. Any watermarks or logos present (return bounding boxes).

The editing engine supports ONLY these controllable features:
  enable_price_tags
  enable_fashion_caption
  enable_cinematic_zoom
  enable_speed_ramps
  enable_fast_pacing
  enable_voiceover

Do NOT invent new feature flags.

Guidelines:
- Fashion / product clips → price tags and captions.
- Nature / travel → cinematic zoom and slower pacing.
- Action / energetic → fast pacing and speed ramps.
- Minimalist → disable most overlays.

Safety rules:
- "safe": brand-safe, monetization suitable.
- "risky": borderline, may need review.
- "blocked": should NOT be monetized.

---

TASK 2 — CONTENT DIRECTOR (Human Editor Intelligence)

Analyze the frames like a human content strategist and return a content_director block.

Determine:
1. detected_entities: list of what/who is visible. Format: ["person:female", "environment:outdoor", "action:walking"]
2. visual_event: one sentence describing what is happening visually.
3. viewer_attention: what object or person will attract viewer attention first.
4. internet_context: list of any recognizable cultural/social/internet references visible. Use NEUTRAL wording only. Example: "online discussions about the appearance together". Do NOT state rumors or accusations as facts.
5. possible_narratives: list of possible storytelling angles. Example: ["fashion_moment", "celebrity_highlight", "humor"]
6. recommended_narrative: choose the single best narrative angle.
7. tone: overall emotional tone. Example: "aspirational", "humorous", "dramatic", "informational"
8. editing_style: choose ONE from: fast_social, cinematic, dramatic, fashion_showcase, product_review, documentary, news, vlog
9. engagement_hook: one short sentence stating the first 3 seconds hook for maximum viewer retention.
10. feature_commands: echo back the feature flags you recommend (must only use allowed flags above).

---

Return ONLY valid JSON. No explanation. No markdown. Just the JSON object.

Required JSON format:
{{
  "watermarks": [
    {{"x": <int>, "y": <int>, "w": <int>, "h": <int>}}
  ],
  "intent": "<string>",
  "confidence": <float 0.0-1.0>,
  "editing_style": "<cinematic|fast_paced|documentary|vlog|product_review|fashion_showcase>",
  "feature_flags": {{
    "enable_price_tags": <bool>,
    "enable_fashion_caption": <bool>,
    "enable_cinematic_zoom": <bool>,
    "enable_speed_ramps": <bool>,
    "enable_fast_pacing": <bool>,
    "enable_voiceover": <bool>
  }},
  "platform_priority": ["youtube_shorts", "instagram_reels", "facebook_reels"],
  "safety": {{
    "classification": "safe|risky|blocked",
    "monetization_safe": <bool>
  }},
  "content_director": {{
    "detected_entities": [],
    "visual_event": "",
    "viewer_attention": "",
    "internet_context": [],
    "possible_narratives": [],
    "recommended_narrative": "",
    "tone": "",
    "editing_style": "",
    "engagement_hook": "",
    "feature_commands": {{
      "enable_fast_pacing": <bool>,
      "enable_cinematic_zoom": <bool>,
      "enable_speed_ramps": <bool>,
      "enable_voiceover": <bool>,
      "enable_price_tags": <bool>,
      "enable_news_style": <bool>
    }}
  }},
  "editing_plan": {{
    "mode": "AI_CONTROLLED",
    "segments": [
      {{ "clip_id": 0, "start": 0.0, "end": 3.5, "reason": "strong visual hook" }},
      {{ "clip_id": 0, "start": 7.2, "end": 10.5, "reason": "action transition" }}
    ],
    "transitions": "fade|glitch|whip_pan",
    "effects": ["zoom_in", "speed_ramp"],
    "duration_target": 15.0
  }}
}}

Frame dimensions: {width}x{height} pixels.
Number of frames provided: {frame_count}
"""

# ── Default fallback ──────────────────────────────────────────────────────────

DEFAULT_RESULT = {
    "watermarks": [],
    "intent":        "unknown",
    "confidence":    0.0,
    "editing_style": "cinematic",
    "feature_flags": {
        "enable_price_tags":      False,
        "enable_fashion_caption": False,
        "enable_cinematic_zoom":  True,
        "enable_speed_ramps":     False,
        "enable_fast_pacing":     False,
        "enable_voiceover":       True,
    },
    "platform_priority": ["youtube_shorts", "instagram_reels", "facebook_reels"],
    "safety": {
        "classification":    "risky",
        "monetization_safe": False,
    },
    # Backward-compat wrapper so orchestrator.get("content_strategy") still works
    "content_strategy": {
        "intent":                    "unknown",
        "confidence":                0.0,
        "recommended_editing_style": "cinematic",
        "feature_flags": {
            "enable_price_tags":      False,
            "enable_fashion_caption": False,
            "enable_cinematic_zoom":  True,
            "enable_speed_ramps":     False,
            "enable_fast_pacing":     False,
            "enable_voiceover":       True,
        },
        "safety": "risky",
    },
}


# ── Main class ────────────────────────────────────────────────────────────────

class ForensicVideoAnalyzer:
    """
    Extracts frames from a video and sends them to Gemini Vision for forensic analysis.

    Usage:
        analyzer = ForensicVideoAnalyzer()
        result = analyzer.analyze(video_path)
        # result is a dict matching the JSON schema above
    """

    # How many frames to sample from the video (spread evenly)
    FRAME_COUNT = 5
    # Target resolution for frames sent to Gemini (keeps token count low)
    FRAME_WIDTH = 640
    FRAME_HEIGHT = 360

    def __init__(self):
        self.router = gemini_router
        self._available = True if gemini_router else False

        if not self.api_key:
            logger.warning("🔬 ForensicAnalyzer: GEMINI_API_KEY not set — will return defaults")
            return

        try:
                                    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
                        self._genai = genai
            self._available = True
            logger.info(f"🔬 ForensicAnalyzer: ACTIVE (model={model_name})")
        except Exception as e:
            logger.warning(f"🔬 ForensicAnalyzer: init failed — {e}")

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(self, video_path: str,
                frame_paths: Optional[List[str]] = None) -> dict:
        """
        Perform forensic analysis on a video (or pre-extracted frames).

        Args:
            video_path:   Path to the source video file.
            frame_paths:  Optional list of already-extracted frame image paths.
                          If provided, skip extraction.

        Returns:
            dict matching the forensic JSON schema.
            Always returns a valid dict — never raises.
        """
        try:
            if not self._available:
                logger.info("🔬 ForensicAnalyzer skipped (unavailable)")
                return DEFAULT_RESULT.copy()

            # ── Step 1: Extract frames ─────────────────────────────────────────
            tmp_dir = None
            own_frames = False
            if frame_paths and all(os.path.exists(p) for p in frame_paths):
                frames = frame_paths
            else:
                tmp_dir = tempfile.mkdtemp(prefix="forensic_frames_")
                frames = self._extract_frames(video_path, tmp_dir)
                own_frames = True

            if not frames:
                logger.warning("🔬 ForensicAnalyzer: no frames extracted — returning default")
                return DEFAULT_RESULT.copy()

            # ── Step 2: Build Gemini payload ──────────────────────────────────
            result = self._call_gemini(frames)

            # ── Step 3: Cleanup ───────────────────────────────────────────────
            if own_frames and tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

            return result

        except Exception as e:
            logger.error(f"🔬 ForensicAnalyzer: unexpected error — {e}")
            return DEFAULT_RESULT.copy()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _extract_frames(self, video_path: str, out_dir: str) -> List[str]:
        """
        Extract FRAME_COUNT frames evenly spread across the video using FFmpeg.
        Returns list of absolute paths to extracted JPEG files.
        """
        if not os.path.exists(video_path):
            logger.warning(f"🔬 Frame extraction: video not found — {video_path}")
            return []

        try:
            # Get duration via ffprobe
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=duration",
                 "-of", "json", video_path],
                capture_output=True, text=True, timeout=15
            )
            dur_data = json.loads(probe.stdout)
            duration = float(dur_data.get("streams", [{}])[0].get("duration", 10.0))
        except Exception:
            duration = 10.0

        # Sample N evenly-spaced timestamps
        n = self.FRAME_COUNT
        timestamps = [round((i + 0.5) * duration / n, 3) for i in range(n)]

        frame_paths = []
        ffmpeg = os.getenv("FFMPEG_BIN", "ffmpeg")
        for i, t in enumerate(timestamps):
            out_path = os.path.join(out_dir, f"forensic_frame_{i:02d}.jpg")
            cmd = [
                ffmpeg, "-y",
                "-ss", str(t),
                "-i", video_path,
                "-vframes", "1",
                "-vf", f"scale={self.FRAME_WIDTH}:{self.FRAME_HEIGHT}:force_original_aspect_ratio=decrease",
                "-q:v", "3",
                out_path
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=30)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                    frame_paths.append(out_path)
            except Exception as fe:
                logger.debug(f"🔬 Frame {i} extraction failed: {fe}")

        logger.info(f"🔬 Extracted {len(frame_paths)}/{n} forensic frames")
        return frame_paths

    def _call_gemini(self, frame_paths: List[str]) -> dict:
        """
        Send frames + prompt to Gemini Vision, parse and validate JSON response.
        Falls back gracefully to DEFAULT_RESULT on any error.
        """
        }

        # Build prompt with frame metadata
        prompt_text = FORENSIC_PROMPT.format(
            width=self.FRAME_WIDTH,
            height=self.FRAME_HEIGHT,
            frame_count=len(frame_paths)
        )

        # Build payload: prompt text + PIL images
        payload = [prompt_text]
        try:
            from PIL import Image
            for p in frame_paths:
                try:
                    img = Image.open(p)
                    payload.append(img)
                except Exception as ie:
                    logger.debug(f"🔬 Could not open frame {p}: {ie}")
        except ImportError:
            logger.warning("🔬 PIL not available — sending text-only forensic prompt")

        # Model fallback list
        try:
            res_txt = self.router.generate(
                task_type="vision",
                prompt=payload,
                module_name="forensic_analyzer",
                gen_config={"temperature": 0.2, "response_mime_type": "application/json"}
            )
            if not res_txt: return DEFAULT_RESULT.copy()
            return self._parse_response(res_txt)
        except Exception as e:
            logger.error(f"Forensic error: {e}")
            return DEFAULT_RESULT.copy()
    def _parse_response(self, raw: str) -> Optional[dict]:
        """
        Parse and validate Gemini JSON response.
        Handles BOTH schemas:
          - New (flat): intent/feature_flags/safety.classification at root level
          - Old (nested): content_strategy.intent / content_strategy.safety string
        Always returns both formats so orchestrator.py backward-compat is maintained.
        """
        try:
            match = re.search(r'(\{.*\})', raw, re.DOTALL)
            if not match:
                logger.warning("🔬 Forensic parse: no JSON object found in response")
                return None

            data = json.loads(match.group(1))

            # ── Watermarks ────────────────────────────────────────────────────
            raw_wm = data.get("watermarks", [])
            if not isinstance(raw_wm, list):
                raw_wm = []
            watermarks = []
            for wm in raw_wm:
                if isinstance(wm, dict):
                    watermarks.append({
                        "x": int(wm.get("x", 0)),
                        "y": int(wm.get("y", 0)),
                        "w": int(wm.get("w", 0)),
                        "h": int(wm.get("h", 0)),
                    })

            # ── Detect which schema the model returned ────────────────────────
            # New schema: feature_flags at root level
            # Old schema: feature_flags inside content_strategy
            if "feature_flags" in data:
                # NEW flat schema (director prompt)
                flags_raw = data.get("feature_flags", {})
                intent    = str(data.get("intent", "unknown"))
                confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
                editing_style = str(data.get("editing_style", "cinematic"))
                platform_priority = data.get("platform_priority",
                                    ["youtube_shorts", "instagram_reels", "facebook_reels"])

                # safety is now a nested object
                safety_obj = data.get("safety", {})
                if isinstance(safety_obj, dict):
                    safety_cls   = str(safety_obj.get("classification", "risky")).lower()
                    mon_safe     = bool(safety_obj.get("monetization_safe", safety_cls == "safe"))
                else:
                    # Model returned a string instead of object — tolerate it
                    safety_cls   = str(safety_obj).lower().strip()
                    mon_safe     = safety_cls == "safe"

            else:
                # OLD nested content_strategy schema (backward compat)
                cs = data.get("content_strategy", {})
                if not isinstance(cs, dict):
                    cs = {}
                flags_raw      = cs.get("feature_flags", {})
                intent         = str(cs.get("intent", "unknown"))
                confidence     = max(0.0, min(1.0, float(cs.get("confidence", 0.5))))
                editing_style  = str(cs.get("recommended_editing_style", "cinematic"))
                platform_priority = ["youtube_shorts", "instagram_reels", "facebook_reels"]
                safety_cls     = str(cs.get("safety", "risky")).lower().strip()
                mon_safe       = safety_cls == "safe"

            if not isinstance(flags_raw, dict):
                flags_raw = {}

            safety_cls = safety_cls if safety_cls in ("safe", "risky", "blocked") else "risky"

            feature_flags = {
                "enable_price_tags":      bool(flags_raw.get("enable_price_tags",      False)),
                "enable_fashion_caption": bool(flags_raw.get("enable_fashion_caption", False)),
                "enable_cinematic_zoom":  bool(flags_raw.get("enable_cinematic_zoom",  True)),
                "enable_speed_ramps":     bool(flags_raw.get("enable_speed_ramps",     False)),
                "enable_fast_pacing":     bool(flags_raw.get("enable_fast_pacing",     False)),
                "enable_voiceover":       bool(flags_raw.get("enable_voiceover",       True)),
            }

            result = {
                # ── New flat schema ────────────────────────────────────────────
                "watermarks":        watermarks,
                "intent":            intent,
                "confidence":        round(confidence, 3),
                "editing_style":     editing_style,
                "feature_flags":     feature_flags,
                "platform_priority": platform_priority,
                "safety": {
                    "classification":    safety_cls,
                    "monetization_safe": mon_safe,
                },
                # ── Backward-compat content_strategy wrapper ───────────────────
                # orchestrator.py reads .get("content_strategy", {}) for flags/safety/intent
                "content_strategy": {
                    "intent":                    intent,
                    "confidence":                round(confidence, 3),
                    "recommended_editing_style": editing_style,
                    "feature_flags":             feature_flags,
                    "safety":                    safety_cls,   # str — matches old code
                },
            }

            # ── Content Director block (new in this version) ───────────────────
            # Extract and validate. If missing, embed empty defaults (non-breaking).
            try:
                cd_raw = data.get("content_director", {})
                if isinstance(cd_raw, dict) and cd_raw:
                    def _str(k): return str(cd_raw.get(k, ""))
                    def _lst(k):
                        v = cd_raw.get(k, [])
                        return [str(x) for x in v] if isinstance(v, list) else []

                    allowed_flags = {
                        "enable_fast_pacing", "enable_cinematic_zoom",
                        "enable_speed_ramps", "enable_voiceover",
                        "enable_price_tags",  "enable_news_style",
                    }
                    raw_cmds = cd_raw.get("feature_commands", {})
                    feature_commands = {
                        k: bool(v)
                        for k, v in (raw_cmds.items() if isinstance(raw_cmds, dict) else [])
                        if k in allowed_flags
                    }
                    for f in allowed_flags:
                        feature_commands.setdefault(f, False)

                    result["content_director"] = {
                        "detected_entities":     _lst("detected_entities"),
                        "visual_event":          _str("visual_event"),
                        "viewer_attention":      _str("viewer_attention"),
                        "internet_context":      _lst("internet_context"),
                        "possible_narratives":   _lst("possible_narratives"),
                        "recommended_narrative": _str("recommended_narrative"),
                        "tone":                  _str("tone"),
                        "editing_style":         _str("editing_style"),
                        "engagement_hook":       _str("engagement_hook"),
                        "feature_commands":      feature_commands,
                    }
                    result["editing_plan"] = data.get("editing_plan", {})
                    logger.info(
                        f"🎬 ContentDirector: narrative={result['content_director']['recommended_narrative']} "
                        f"style={result['content_director']['editing_style']} "
                        f"tone={result['content_director']['tone']} "
                        f"hook='{result['content_director']['engagement_hook'][:60]}'"
                    )
                else:
                    logger.info("🎬 ContentDirector: no block in Gemini response — using defaults")
                    result["content_director"] = {}
            except Exception as _cde:
                logger.warning(f"🎬 ContentDirector parse error (non-critical): {_cde}")
                result["content_director"] = {}

            logger.info(
                f"🔬 Forensic result: intent={intent} "
                f"style={editing_style} safety={safety_cls} "
                f"watermarks={len(watermarks)} confidence={confidence:.2f} "
                f"monetizable={mon_safe}"
            )
            active_flags = [k.replace("enable_", "") for k, v in feature_flags.items() if v]
            logger.info(
                f"🔬 ForensicFlags: intent={intent}, style={editing_style}, "
                f"flags=[{', '.join(active_flags) or 'none'}]"
            )
            return result

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"🔬 Forensic parse error: {e}")
            return None


# ── Module-level singleton ────────────────────────────────────────────────────

_analyzer: Optional[ForensicVideoAnalyzer] = None


def get_analyzer() -> ForensicVideoAnalyzer:
    """Return the module-level singleton, creating it on first call."""
    global _analyzer
    if _analyzer is None:
        _analyzer = ForensicVideoAnalyzer()
    return _analyzer


def analyze_video(video_path: str, frame_paths: List[str] = None, intelligence_cache=None) -> Optional[dict]:
    """
    Main Orchestrator for Forensic Analysis.
    """
    if not frame_paths:
        return None

    return get_analyzer().analyze(video_path, frame_paths=frame_paths)

# Alias for legacy support
analyze = analyze_video
        try:
            res_txt = self.router.generate(
                task_type="vision",
                prompt=payload,
                module_name="forensic_analyzer",
                gen_config={"temperature": 0.2, "response_mime_type": "application/json"}
            )
            if not res_txt: return DEFAULT_RESULT.copy()
            return self._parse_response(res_txt)
        except Exception as e:
            logger.error(f"Forensic error: {e}")
            return DEFAULT_RESULT.copy()
