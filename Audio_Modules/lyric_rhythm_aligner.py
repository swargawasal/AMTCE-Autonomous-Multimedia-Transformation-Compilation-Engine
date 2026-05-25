"""
Audio_Modules/lyric_rhythm_aligner.py
======================================
Musical Intelligence Report — ONE Gemini call, maximum value.

Gemini receives the raw BGM audio file and returns a full structured
intelligence report that drives ALL downstream rhythm editing decisions:

    1. Lyric timestamps + emotional weight per word/phrase
    2. Section map   (intro / verse / pre-chorus / chorus / drop / bridge / outro)
    3. Tension arc   (0-1 score per second — drives hold vs. cut decisions)
    4. Shot directives (what visual to use at which moment)
    5. Vibe tags     (feeds CreativeBrain niche alignment)
    6. Emotional peak moments (single timestamps for instant-cut triggers)

Why ONE call? Because all 6 outputs share the same audio context window —
splitting them into 6 calls would cost 6× the quota for the same source material.

Controlled by: ENABLE_LYRIC_SYNC=true (default: true)
Gracefully returns empty structure on failure / instrumental audio.
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
from google import genai

logger = logging.getLogger("lyric_rhythm_aligner")

# ─── Config ───────────────────────────────────────────────────────────────────
ENABLE_LYRIC_SYNC = os.getenv("ENABLE_LYRIC_SYNC", "true").lower() in ("true", "1", "yes")

# Minimum audio file size — skip analysis on tiny/corrupt extracts
_MIN_AUDIO_BYTES = 32_768  # 32 KB

# Section types the system understands downstream
VALID_SECTIONS = {"intro", "verse", "pre_chorus", "chorus", "drop", "bridge", "outro", "instrumental"}

# Emotion → visual shot directive mapping (used when Gemini returns emotion labels)
_EMOTION_DIRECTIVE = {
    "love":        "face_closeup",
    "longing":     "face_closeup",
    "nostalgia":   "face_closeup",
    "joy":         "wide_energetic",
    "euphoria":    "wide_energetic",
    "hype":        "fast_action",
    "power":       "low_angle",
    "sadness":     "slow_zoom_in",
    "intimacy":    "face_closeup",
    "freedom":     "wide_landscape",
    "anger":       "fast_action",
    "celebration": "wide_energetic",
}

# ─── Prompt ───────────────────────────────────────────────────────────────────

_PROMPT = """You are a world-class music supervisor and video editor with expertise in rhythm-based editing.

Listen to this audio track carefully. Extract EVERYTHING needed to edit a viral short-form video to this music.

Return ONLY a single strict JSON object — no markdown, no explanation, no extra text.

JSON schema:
{
  "has_vocals": true | false,
  "language": "Hindi" | "English" | "Telugu" | "Tamil" | "Spanish" | "Instrumental" | ...,
  "tempo_bpm": <float — overall BPM estimate>,
  "bar_duration_sec": <float — duration of one musical bar in seconds>,
  "dominant_emotion": <string — single best emotion label: joy | love | hype | power | sadness | euphoria | nostalgia | celebration | anger | intimacy | freedom | neutral>,
  "energy_profile": "low" | "medium" | "high" | "building" | "explosive",

  "sections": [
    {
      "start": <float seconds>,
      "end": <float seconds>,
      "type": "intro" | "verse" | "pre_chorus" | "chorus" | "drop" | "bridge" | "outro" | "instrumental",
      "energy": <float 0.0–1.0>,
      "mood": <string — 1-2 word description e.g. "playful", "intense", "melancholic">,
      "recommended_cut_pace": "hold" | "slow" | "medium" | "fast" | "rapid_fire"
    }
  ],

  "tension_arc": [
    { "time": <float seconds>, "tension": <float 0.0–1.0> }
  ],

  "lyrics": [
    {
      "time": <float seconds — when this phrase starts>,
      "end": <float seconds — when this phrase ends>,
      "text": "<lyric text or phonetic approximation>",
      "emotion_weight": <float 0.0–1.0 — how emotionally charged this phrase is>,
      "emotion_tag": <string — joy | love | hype | power | sadness | euphoria | nostalgia | celebration | anger | intimacy | freedom | neutral>,
      "section": <string — which section this lyric falls in>
    }
  ],

  "emotional_peak_moments": [<float seconds>, ...],

  "shot_directives": [
    {
      "time": <float seconds>,
      "duration": <float seconds — how long this directive applies>,
      "directive": "face_closeup" | "wide_energetic" | "fast_action" | "slow_zoom_in" | "wide_landscape" | "low_angle" | "match_cut_motion" | "hold_on_subject",
      "priority": <int 1-5 — 5 is most important>,
      "reason": "<brief reason, e.g.: 'chorus drop — maximum energy'>"
    }
  ],

  "vibe_tags": [<string>, ...],

  "instrumental_sections": [
    { "start": <float seconds>, "end": <float seconds> }
  ]
}

RULES:
- tension_arc: provide one entry every 1 second (or every beat if BPM > 100). Tension rises into a chorus/drop, falls during verse/outro.
- sections: cover the entire track with no gaps. Every second must fall in exactly one section.
- lyrics: only fill if vocals are clearly present. For instrumental tracks, return [] and set has_vocals=false.
- emotional_peak_moments: timestamps where the music hits its hardest emotional/energy peak (typically the first chorus or drop). Maximum 5 entries.
- shot_directives: minimum 3, maximum 12 entries. Focus on the most critical edit decision moments.
- vibe_tags: 3-6 lowercase tags that describe the vibe (e.g. ["festive", "dance", "bollywood", "high_energy", "romantic"]).
- If the audio is very short (< 15s), still provide full structure based on what you can hear.
"""

# ─── Empty / fallback structure ───────────────────────────────────────────────

def _empty_report() -> Dict[str, Any]:
    return {
        "has_vocals": False,
        "language": "Unknown",
        "tempo_bpm": 0.0,
        "bar_duration_sec": 0.0,
        "dominant_emotion": "neutral",
        "energy_profile": "medium",
        "sections": [],
        "tension_arc": [],
        "lyrics": [],
        "emotional_peak_moments": [],
        "shot_directives": [],
        "vibe_tags": [],
        "instrumental_sections": [],
        "_source": "fallback",
    }


def _clean_json(text: str) -> str:
    """Strip markdown wrappers and extract first JSON object."""
    if not text:
        return ""
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            text = re.sub(r"```(?:json)?", "", text).replace("```", "")
    j_start = text.find("{")
    j_end   = text.rfind("}")
    if j_start != -1 and j_end > j_start:
        return text[j_start:j_end + 1].strip()
    return text.strip()


def _validate_and_enrich(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post-process Gemini output:
    - Clamp numeric fields to valid ranges
    - Normalize section types to known values
    - Add `shot_directive` hints derived from lyric emotion tags
    - Sort tension_arc and sections by time
    """
    # Clamp tempo
    report["tempo_bpm"] = max(0.0, float(report.get("tempo_bpm", 0.0)))
    report["bar_duration_sec"] = max(0.0, float(report.get("bar_duration_sec", 0.0)))

    # Auto-compute bar duration if Gemini skipped it
    if report["bar_duration_sec"] == 0.0 and report["tempo_bpm"] > 0:
        report["bar_duration_sec"] = round(4 * 60.0 / report["tempo_bpm"], 3)

    # Normalize sections
    for sec in report.get("sections", []):
        sec["start"] = float(sec.get("start", 0.0))
        sec["end"]   = float(sec.get("end",   0.0))
        sec["energy"] = max(0.0, min(1.0, float(sec.get("energy", 0.5))))
        if sec.get("type") not in VALID_SECTIONS:
            sec["type"] = "verse"  # safe default
    report["sections"] = sorted(report.get("sections", []), key=lambda x: x["start"])

    # Sort tension arc
    report["tension_arc"] = sorted(
        report.get("tension_arc", []),
        key=lambda x: float(x.get("time", 0.0))
    )

    # Clamp tension values
    for pt in report["tension_arc"]:
        pt["tension"] = max(0.0, min(1.0, float(pt.get("tension", 0.5))))

    # Sort lyrics
    report["lyrics"] = sorted(
        report.get("lyrics", []),
        key=lambda x: float(x.get("time", 0.0))
    )
    for lyric in report["lyrics"]:
        lyric["emotion_weight"] = max(0.0, min(1.0, float(lyric.get("emotion_weight", 0.5))))

    # Derive shot directives from lyrics if Gemini didn't provide them
    existing_directive_times = {d.get("time") for d in report.get("shot_directives", [])}
    derived = []
    for lyric in report.get("lyrics", []):
        if lyric.get("emotion_weight", 0) >= 0.7 and lyric.get("time") not in existing_directive_times:
            emotion = lyric.get("emotion_tag", "neutral")
            directive = _EMOTION_DIRECTIVE.get(emotion, "hold_on_subject")
            derived.append({
                "time":      lyric["time"],
                "duration":  lyric.get("end", lyric["time"] + 2.0) - lyric["time"],
                "directive": directive,
                "priority":  4,
                "reason":    f"High-emotion lyric: '{lyric.get('text', '')}' ({emotion})",
            })
    report["shot_directives"] = sorted(
        report.get("shot_directives", []) + derived,
        key=lambda x: float(x.get("time", 0.0))
    )

    # Sort emotional peaks
    report["emotional_peak_moments"] = sorted(
        [float(t) for t in report.get("emotional_peak_moments", [])]
    )

    report["_source"] = "gemini"
    return report


# ─── Main API ─────────────────────────────────────────────────────────────────

def analyze_music(audio_path: str) -> Dict[str, Any]:
    """
    Run the full Musical Intelligence Report on `audio_path`.

    Returns a dict matching _empty_report() structure on any failure.
    This function NEVER raises — all errors are caught and logged.

    Args:
        audio_path: Absolute or relative path to an MP3/WAV audio file.

    Returns:
        Musical intelligence report dict.
    """
    if not ENABLE_LYRIC_SYNC:
        logger.info("[LYRIC_ALIGNER] ENABLE_LYRIC_SYNC=false — skipping.")
        return _empty_report()

    if not audio_path or not os.path.exists(audio_path):
        logger.warning(f"[LYRIC_ALIGNER] Audio file not found: {audio_path}")
        return _empty_report()

    file_size = os.path.getsize(audio_path)
    if file_size < _MIN_AUDIO_BYTES:
        logger.warning(f"[LYRIC_ALIGNER] Audio too small ({file_size}B) — skipping.")
        return _empty_report()

    try:
        # api_key = os.getenv("GEMINI_API_KEY") # Redundant: handled by gemini_router
        pass
    except Exception as _cfg_e:
        logger.warning(f"[LYRIC_ALIGNER] genai configure failed: {_cfg_e}")
        return _empty_report()

    try:
        from Intelligence_Modules.gemini_governor import gemini_router
    except Exception as _gov_e:
        logger.warning(f"[LYRIC_ALIGNER] gemini_router import failed: {_gov_e}")
        return _empty_report()

    # Upload the audio file to Gemini File API
    audio_part = None
    _uploaded_file = None
    try:
        from google import genai as _genai_client_mod
        import os as _os
        _api_key = _os.getenv("GEMINI_API_KEY", "")
        _client = _genai_client_mod.Client(api_key=_api_key)
        logger.info(f"[LYRIC_ALIGNER] Uploading audio to Gemini File API: {os.path.basename(audio_path)}")
        _uploaded_file = _client.files.upload(file=audio_path)
        # Wait for processing
        _wait = 0
        while getattr(getattr(_uploaded_file, "state", None), "name", "ACTIVE") == "PROCESSING" and _wait < 30:
            time.sleep(1)
            _uploaded_file = _client.files.get(name=_uploaded_file.name)
            _wait += 1
        _state = getattr(getattr(_uploaded_file, "state", None), "name", "ACTIVE")
        if _state != "ACTIVE":
            logger.warning(f"[LYRIC_ALIGNER] File not ACTIVE after {_wait}s — state={_state}")
            return _empty_report()
        audio_part = _uploaded_file
        logger.info(f"[LYRIC_ALIGNER] File upload complete: {_uploaded_file.name}")
    except Exception as _upload_e:
        logger.warning(f"[LYRIC_ALIGNER] File upload failed: {_upload_e}")
        return _empty_report()

    # Make the single Gemini call
    try:
        t_start = time.time()
        logger.info(f"[LYRIC_ALIGNER] 🎵 Sending Musical Intelligence request to Gemini...")

        # Pass [audio_part, prompt_text] as the content list
        raw_response = gemini_router.generate(
            task_type="analysis",
            prompt=[audio_part, _PROMPT],
            module_name="lyric_rhythm_aligner",
        )

        latency = time.time() - t_start
        logger.info(f"[LYRIC_ALIGNER] ✅ Response received in {latency:.1f}s ({len(raw_response or '')} chars)")

        if not raw_response or len(raw_response.strip()) < 10:
            logger.warning("[LYRIC_ALIGNER] Empty response from Gemini.")
            return _empty_report()

        cleaned = _clean_json(raw_response)
        report  = json.loads(cleaned)

        if not isinstance(report, dict):
            logger.warning("[LYRIC_ALIGNER] Gemini returned non-dict JSON.")
            return _empty_report()

        report = _validate_and_enrich(report)

        # Summary log
        n_sec  = len(report.get("sections", []))
        n_lyr  = len(report.get("lyrics", []))
        n_dir  = len(report.get("shot_directives", []))
        n_arc  = len(report.get("tension_arc", []))
        n_peak = len(report.get("emotional_peak_moments", []))
        logger.info(
            f"[LYRIC_ALIGNER] 🎶 Musical Intelligence Report | "
            f"vocals={report.get('has_vocals')} lang={report.get('language')} "
            f"bpm={report.get('tempo_bpm')} emotion={report.get('dominant_emotion')} "
            f"sections={n_sec} lyrics={n_lyr} directives={n_dir} "
            f"tension_arc={n_arc}pts peaks={n_peak}"
        )
        return report

    except json.JSONDecodeError as _jde:
        logger.error(f"[LYRIC_ALIGNER] JSON parse error: {_jde}")
        logger.debug(f"[LYRIC_ALIGNER] Raw response: {(raw_response or '')[:500]}")
        return _empty_report()
    except Exception as _e:
        logger.warning(f"[LYRIC_ALIGNER] Gemini call failed (non-fatal): {_e}")
        return _empty_report()
    finally:
        # Clean up the uploaded file from Gemini to avoid storage accumulation
        if _uploaded_file:
            try:
                _client.files.delete(name=_uploaded_file.name)
                logger.debug(f"[LYRIC_ALIGNER] Cleaned up uploaded file: {_uploaded_file.name}")
            except Exception:
                pass


def get_tension_at(tension_arc: List[Dict], time_sec: float) -> float:
    """
    Interpolate tension score at a specific timestamp from the tension arc.
    Returns 0.5 (neutral) if the arc is empty.
    """
    if not tension_arc:
        return 0.5
    arc = sorted(tension_arc, key=lambda x: x.get("time", 0.0))
    if time_sec <= arc[0].get("time", 0.0):
        return float(arc[0].get("tension", 0.5))
    if time_sec >= arc[-1].get("time", 0.0):
        return float(arc[-1].get("tension", 0.5))
    for i in range(len(arc) - 1):
        t0 = float(arc[i].get("time", 0.0))
        t1 = float(arc[i + 1].get("time", 0.0))
        if t0 <= time_sec <= t1:
            if t1 == t0:
                return float(arc[i].get("tension", 0.5))
            alpha = (time_sec - t0) / (t1 - t0)
            v0 = float(arc[i].get("tension", 0.5))
            v1 = float(arc[i + 1].get("tension", 0.5))
            return round(v0 + alpha * (v1 - v0), 3)
    return 0.5


def get_section_at(sections: List[Dict], time_sec: float) -> Optional[Dict]:
    """
    Return the section dict that contains `time_sec`, or None.
    """
    for sec in sections:
        if float(sec.get("start", 0)) <= time_sec < float(sec.get("end", 0)):
            return sec
    return None


def get_directive_at(directives: List[Dict], time_sec: float) -> Optional[Dict]:
    """
    Return the highest-priority shot directive active at `time_sec`, or None.
    """
    active = [
        d for d in directives
        if float(d.get("time", 0)) <= time_sec < float(d.get("time", 0)) + float(d.get("duration", 2.0))
    ]
    if not active:
        return None
    return max(active, key=lambda x: int(x.get("priority", 1)))
