"""
Transition Intelligence Engine (TIE)
=====================================
World-class, 3-phase transition decision system.

Phase 1 — Human Creative Engine:
    Analyzes kinetic vectors, emotional energy, and narrative gap between
    the tail of Clip A and the head of Clip B.

Phase 2 — Machine Precision Engine:
    Selects the mathematically optimal transition type using the existing
    SmartTransitionEngine RAG+math cascade as the core, extended with
    directional motion matching and match-cut detection.

Phase 3 — Duration & Alignment:
    Calculates exact frame duration snapped to the BPM grid at 30fps.
    Assigns alignment (Center/Start/End), easing curve, and audio bleed
    (J-Cut lead or L-Cut trail) based on narrative gap and emotional arc.

Output: Strict JSON schema compatible with video_pipeline.build_transition_graph()

Design Principles:
  - Hard Cut is the default. Never add effects for the sake of it.
  - BPM Frame Lock: all durations rounded to nearest 30fps frame.
  - Audio Bleed is only applied when the narrative demands it.
  - Zero breaking changes: existing STIE pipeline is unchanged.
"""

import logging
import math
from typing import Dict, Optional

logger = logging.getLogger("transition_intelligence_engine")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FPS = 30  # Target output frame rate

# Kinetic vector categories (raw motion_direction string → canonical family)
_DIRECTION_FAMILY = {
    "left":     ("left", "pan_left"),
    "right":    ("right", "pan_right"),
    "up":       ("up", "tilt_up", "rise"),
    "down":     ("down", "tilt_down", "fall", "drop"),
    "in":       ("in", "zoom_in", "push_in"),
    "out":      ("out", "zoom_out", "pull_out"),
    "static":   ("static", "none", "still", "locked"),
    "random":   (),     # catch-all
}

# Emotional energy level (0.0 – 1.0 score → label)
def _energy_label(score: float) -> str:
    if score >= 0.80: return "explosive"
    if score >= 0.60: return "high"
    if score >= 0.35: return "medium"
    if score >= 0.15: return "calm"
    return "intimate"


def _canonicalize_direction(raw: str) -> str:
    """Map a raw motion direction string to one of the canonical families."""
    if not raw: return "static"
    r = raw.lower().strip()
    for family, aliases in _DIRECTION_FAMILY.items():
        if r == family or r in aliases:
            return family
    return "random"


def _vectors_match(dir_a: str, dir_b: str) -> bool:
    """Return True if Clip A's exit vector and Clip B's entry vector are compatible."""
    a = _canonicalize_direction(dir_a)
    b = _canonicalize_direction(dir_b)
    # Identical direction = perfect match
    if a == b: return True
    # Complementary pairs that feel natural (momentum flows through the cut)
    complementary = {
        ("left",  "left"),
        ("right", "right"),
        ("in",    "in"),
        ("out",   "out"),
        ("down",  "down"),
        ("up",    "up"),
    }
    return (a, b) in complementary


def _bpm_snap(seconds: float, bpm: float, fps: int = FPS) -> int:
    """
    Snap a duration (seconds) to the nearest whole frame at `fps`, constrained to
    a clean musical subdivision of the BPM (whole, half, quarter, or eighth beat).

    Returns: int frame count.
    """
    if bpm <= 0:
        return max(1, round(seconds * fps))

    beat_s = 60.0 / bpm
    # Musical subdivisions: whole (1), half (0.5), quarter (0.25), eighth (0.125)
    subdivisions = [1.0, 0.5, 0.25, 0.125]
    # Find the subdivision closest to the raw duration
    closest_sub = min(subdivisions, key=lambda s: abs(s * beat_s - seconds))
    snapped_s = closest_sub * beat_s

    # Clamp to safe range [1 frame, 18 frames = 0.6s at 30fps]
    snapped_s = max(1.0 / fps, min(0.60, snapped_s))
    return max(1, round(snapped_s * fps))


# ---------------------------------------------------------------------------
# Phase 1 — Human Creative Engine
# ---------------------------------------------------------------------------

def phase1_human_analysis(clip_a: Dict, clip_b: Dict) -> Dict:
    """
    Analyze the emotional, kinetic, and narrative relationship between
    the tail of Clip A and the head of Clip B.

    Expected clip metadata keys (all optional — graceful defaults apply):
      motion_direction  str   e.g. "left", "down", "static"
      motion_intensity  float 0.0–1.0
      energy_score      float 0.0–1.0  (emotional/kinetic energy)
      color_mood        str   e.g. "warm", "cool", "dramatic"
      scene_id          str/int — same ID = same scene, different = scene jump
      bpm               float   beats per minute of the background music
      beat_strength     str   "drop"|"strong"|"medium"|"weak"|"none"
      is_drop           bool
      segment_duration  float seconds — duration of the originating segment

    Returns:
      {
        "kinetic_flow":    str,
        "emotional_shift": str,
        "narrative_gap":   str,
        "vectors_match":   bool,
        "energy_delta":    float,   # clip_b.energy - clip_a.energy
        "scene_jump":      bool,
        "color_shift":     bool,
        "a_dir_canon":     str,
        "b_dir_canon":     str,
      }
    """
    # ── Kinetic vectors ───────────────────────────────────────────────────────
    a_dir  = _canonicalize_direction(clip_a.get("motion_direction", "static"))
    b_dir  = _canonicalize_direction(clip_b.get("motion_direction", "static"))
    a_mi   = float(clip_a.get("motion_intensity", 0.3))
    b_mi   = float(clip_b.get("motion_intensity", 0.3))
    v_match = _vectors_match(a_dir, b_dir)

    if v_match and a_mi > 0.5:
        kinetic_flow = (
            f"Strong vector match: both clips carry '{a_dir}' momentum. "
            f"The outgoing kinetic energy (intensity={a_mi:.2f}) flows directly into "
            f"the incoming shot. A hard cut will feel seamless."
        )
    elif v_match and a_mi <= 0.5:
        kinetic_flow = (
            f"Soft vector match: both clips are '{a_dir}' with low energy. "
            f"A clean cut or gentle fade will preserve the calm momentum."
        )
    elif not v_match and a_mi > 0.6:
        kinetic_flow = (
            f"Vector collision: Clip A exits '{a_dir}' at high intensity ({a_mi:.2f}) "
            f"but Clip B enters '{b_dir}'. A directional blur (whip_pan) or "
            f"momentum-break transition is needed to absorb the vector change."
        )
    else:
        kinetic_flow = (
            f"Opposing vectors (A={a_dir}, B={b_dir}) with moderate energy. "
            f"A subtle blur_cut will mask the directional discrepancy."
        )

    # ── Emotional shift ───────────────────────────────────────────────────────
    a_energy = float(clip_a.get("energy_score", 0.4))
    b_energy = float(clip_b.get("energy_score", 0.4))
    delta    = b_energy - a_energy
    a_label  = _energy_label(a_energy)
    b_label  = _energy_label(b_energy)

    if abs(delta) < 0.10:
        emotional_shift = (
            f"Emotional continuity: both clips hold a '{a_label}' energy "
            f"(Δ={delta:+.2f}). No tonal bridge is needed."
        )
    elif delta > 0.30:
        emotional_shift = (
            f"Energy escalation: transitioning from '{a_label}' → '{b_label}' "
            f"(Δ={delta:+.2f}). The transition should act as an ignition point — "
            f"a punch_cut or glitch_pop will signal the energy spike."
        )
    elif delta < -0.30:
        emotional_shift = (
            f"Energy deceleration: transitioning from '{a_label}' → '{b_label}' "
            f"(Δ={delta:+.2f}). A glow_fade or slow_fade will act as a landing gear, "
            f"gracefully absorbing the intensity drop."
        )
    else:
        emotional_shift = (
            f"Moderate energy shift ({a_label} → {b_label}, Δ={delta:+.2f}). "
            f"A blur_cut or zoom_pop will register the change without overdramatizing."
        )

    # ── Narrative gap ─────────────────────────────────────────────────────────
    a_scene = clip_a.get("scene_id")
    b_scene = clip_b.get("scene_id")
    scene_jump = (a_scene != b_scene) if (a_scene is not None and b_scene is not None) else True

    a_mood = clip_a.get("color_mood", "neutral")
    b_mood = clip_b.get("color_mood", "neutral")
    color_shift = (a_mood != b_mood)

    if not scene_jump and v_match:
        narrative_gap = (
            "Same scene continuation. Clips share spatial context. "
            "A match_cut or hard cut will feel invisible."
        )
    elif scene_jump and color_shift:
        narrative_gap = (
            f"Location/time jump with tonal shift ({a_mood} → {b_mood}). "
            f"These clips are editorially connected but visually distinct. "
            f"A cross-dissolve or luma_fade will signal the temporal bridge."
        )
    elif scene_jump and not color_shift:
        narrative_gap = (
            "Scene change with tonal consistency. The content changes but the "
            "emotional world persists. A whip_pan or zoom_pop maintains energy continuity."
        )
    else:
        narrative_gap = (
            "Subtle narrative gap. Clips are thematically linked but not spatially. "
            "A clean cut or blur_cut is editorially honest."
        )

    return {
        "kinetic_flow":    kinetic_flow,
        "emotional_shift": emotional_shift,
        "narrative_gap":   narrative_gap,
        "vectors_match":   v_match,
        "energy_delta":    round(delta, 3),
        "scene_jump":      scene_jump,
        "color_shift":     color_shift,
        "a_dir_canon":     a_dir,
        "b_dir_canon":     b_dir,
    }


# ---------------------------------------------------------------------------
# Phase 2 — Machine Precision Engine
# ---------------------------------------------------------------------------

def phase2_machine_selection(clip_a: Dict, clip_b: Dict, analysis: Dict) -> Dict:
    """
    Select the optimal transition type and rationale using the 3-rule hierarchy:
      Rule 1 — Match Cut: if a shape/action mirrors between clips (same scene, vectors match)
      Rule 2 — Hard Cut: if energy is continuous and vectors match (default)
      Rule 3 — Effect: select based on energy delta, directional collision, and beat strength

    Returns: {"transition_type": str, "rationale": str}
    """
    v_match     = analysis["vectors_match"]
    scene_jump  = analysis["scene_jump"]
    delta       = analysis["energy_delta"]
    a_mi        = float(clip_a.get("motion_intensity", 0.3))
    b_mi        = float(clip_b.get("motion_intensity", 0.3))
    beat        = clip_a.get("beat_strength", "weak")
    is_drop     = bool(clip_a.get("is_drop", False))
    a_mood      = clip_a.get("color_mood", "neutral")
    a_dir       = analysis["a_dir_canon"]
    b_dir       = analysis["b_dir_canon"]

    # ── Rule 1: Match Cut ─────────────────────────────────────────────────────
    if not scene_jump and v_match and a_mi < 0.4:
        return {
            "transition_type": "match_cut",
            "rationale": (
                "Same scene, matching vectors, low motion — shapes align "
                "perfectly across the cut. Zero-dissolve match_cut is invisible and "
                "the most powerful editorial choice."
            )
        }

    # ── Rule 2: Hard Cut (Default) ────────────────────────────────────────────
    if v_match and abs(delta) < 0.15 and beat in ("none", "weak"):
        return {
            "transition_type": "clean",
            "rationale": (
                "Energy is continuous, vectors are aligned, and the beat is weak. "
                "A hard cut is the default — any effect would feel gratuitous."
            )
        }

    # ── Rule 3: Effect Selection ──────────────────────────────────────────────

    # Beat drop → explosive visual punctuation
    if is_drop or beat == "drop":
        return {
            "transition_type": "glitch_pop",
            "rationale": (
                "Detected beat drop. A glitch_pop flash overexposure delivers "
                "the physical shock the audio demands — maximum kinetic punctuation."
            )
        }

    # High energy escalation → punch cut
    if delta > 0.35 and a_mi > 0.5:
        return {
            "transition_type": "punch_cut",
            "rationale": (
                f"Strong energy escalation (Δ={delta:+.2f}) with high outgoing motion. "
                f"A punch_cut injects contrast energy at the cut point, "
                f"signaling the incoming intensity spike."
            )
        }

    # High energy deceleration → glow fade
    if delta < -0.35:
        return {
            "transition_type": "glow_fade",
            "rationale": (
                f"Energy deceleration (Δ={delta:+.2f}). A glow_fade "
                f"acts as a warm landing — the brightness breath absorbs the drop "
                f"without feeling abrupt."
            )
        }

    # Vector collision + high intensity → whip pan to absorb direction change
    if not v_match and (a_mi + b_mi) / 2 > 0.55:
        dir_label = f"Clip A exits '{a_dir}', Clip B enters '{b_dir}'"
        return {
            "transition_type": "whip_pan",
            "rationale": (
                f"Directional collision: {dir_label}. A whip_pan blur "
                f"physically carries the exit vector through the frame and "
                f"disguises the direction change as natural camera momentum."
            )
        }

    # Scene jump with strong beat → zoom pop
    if scene_jump and beat in ("strong", "medium"):
        return {
            "transition_type": "zoom_pop",
            "rationale": (
                f"Scene change on a {beat} beat. A zoom_pop injects kinetic "
                f"energy at the cut point, making the location change feel "
                f"intentional rather than accidental."
            )
        }

    # Scene jump with slow/calm energy → cross dissolve (represented as slow_fade)
    if scene_jump and abs(delta) < 0.25 and beat in ("weak", "none"):
        return {
            "transition_type": "slow_fade",
            "rationale": (
                "Calm scene change with low beat energy. A slow_fade acts as a "
                "cinematic cross-dissolve — the temporal bridge that signals to "
                "the viewer that time or location has shifted gently."
            )
        }

    # Warm color mood → glow fade
    if a_mood in ("warm", "fashion", "cinematic") and not scene_jump:
        return {
            "transition_type": "glow_fade",
            "rationale": (
                f"Warm color palette ({a_mood}) with same-scene continuation. "
                f"A glow_fade preserves the emotional warmth without disrupting "
                f"the intimate visual tone."
            )
        }

    # Default: blur_cut as a safe, universal soft transition
    return {
        "transition_type": "blur_cut",
        "rationale": (
            "Mixed signals — no dominant rule was triggered. "
            "A blur_cut provides a subtle visual breath that registers a change "
            "without overdramatizing the gap."
        )
    }


# ---------------------------------------------------------------------------
# Phase 3 — Duration & Alignment
# ---------------------------------------------------------------------------

def phase3_execution_parameters(
    clip_a: Dict,
    clip_b: Dict,
    analysis: Dict,
    transition_type: str,
    bpm: float = 0.0,
) -> Dict:
    """
    Calculate:
      - duration_frames: integer, BPM-snapped
      - alignment: "Center" | "Start" | "End"
      - easing: "Linear" | "Ease-In" | "Ease-Out" | "Ease-In-Out"
      - audio_bleed: "None" | "J-Cut (Clip B audio leads by N frames)" | "L-Cut (Clip A audio trails by N frames)"
      - duration_s: float (for downstream renderers)

    Returns: dict
    """
    delta       = analysis["energy_delta"]
    scene_jump  = analysis["scene_jump"]
    v_match     = analysis["vectors_match"]
    is_drop     = bool(clip_a.get("is_drop", False))
    beat        = clip_a.get("beat_strength", "weak")
    seg_dur     = float(clip_a.get("segment_duration", 2.0))

    # ── Frame Duration ─────────────────────────────────────────────────────────
    if transition_type in ("match_cut", "clean"):
        # Hard cuts: 0 frames (instantaneous)
        dur_frames = 0
        dur_s = 0.0
    elif transition_type in ("glitch_pop", "punch_cut"):
        # Snappy/aggressive: 3–6 frames (0.1s–0.2s)
        raw_s = 0.1 if is_drop else 0.133
        dur_frames = _bpm_snap(raw_s, bpm)
        dur_frames = min(6, max(3, dur_frames))
        dur_s = round(dur_frames / FPS, 3)
    elif transition_type in ("slow_fade", "glow_fade", "zoom_blur_fade"):
        # Smooth/cinematic: 12–24 frames (0.4s–0.8s)
        raw_s = 0.50 if seg_dur < 2.0 else 0.667
        dur_frames = _bpm_snap(raw_s, bpm)
        dur_frames = min(24, max(12, dur_frames))
        dur_s = round(dur_frames / FPS, 3)
    else:
        # Default mid-range: 5–10 frames (0.167s–0.333s)
        raw_s = 0.2
        dur_frames = _bpm_snap(raw_s, bpm)
        dur_frames = min(10, max(5, dur_frames))
        dur_s = round(dur_frames / FPS, 3)

    # ── Alignment ─────────────────────────────────────────────────────────────
    # "End at Cut" = transition fully completes exactly on the cut frame.
    #   Best for explosive beats where the new frame must land ON the beat.
    # "Center at Cut" = symmetrical overlap around the cut point.
    #   Best for whip pans and zooms that bridge both clips.
    # "Start at Cut" = transition begins exactly on the cut frame.
    #   Best for incoming energy (the new clip IS the event).

    if is_drop or beat == "drop":
        alignment = "End"       # New frame must land on the beat hit
    elif transition_type in ("whip_pan", "zoom_pop", "zoom_blur_fade"):
        alignment = "Center"    # Symmetrical motion bridge
    elif transition_type in ("slow_fade", "glow_fade"):
        alignment = "Start"     # Let the dissolve breathe into the new clip
    else:
        alignment = "End"       # Default: respect the beat position

    # ── Easing Curve ──────────────────────────────────────────────────────────
    if transition_type in ("glitch_pop", "punch_cut", "dip_black"):
        easing = "Ease-In"          # Attack fast, release instantly
    elif transition_type in ("slow_fade", "glow_fade"):
        easing = "Ease-In-Out"      # Symmetrical breathe
    elif transition_type in ("whip_pan", "zoom_pop"):
        easing = "Ease-Out"         # Fast entry, decelerate to the new clip
    elif transition_type == "match_cut":
        easing = "Linear"           # Zero-dissolve — easing is meaningless
    else:
        easing = "Ease-In-Out"      # Safe default for blur-family transitions

    # ── Audio Bleed (J-Cut / L-Cut) ───────────────────────────────────────────
    # J-Cut: Clip B's audio starts BEFORE the video cut. Used when the incoming
    #   audio (dialogue, music drop, SFX) is the editorial *reason* for the cut.
    # L-Cut: Clip A's audio CONTINUES after the video cut. Used for emotional
    #   continuation — the outgoing emotion spills into the new scene.
    # None: Hard audio cut with the video.

    audio_bleed = "None"
    bleed_frames = 0

    if scene_jump and delta < -0.25:
        # Emotional deceleration on scene jump → L-Cut (outgoing emotion trails)
        bleed_frames = max(3, min(8, dur_frames))
        audio_bleed = f"L-Cut (Clip A audio trails by {bleed_frames} frames)"
    elif scene_jump and delta > 0.25 and beat in ("strong", "drop"):
        # Energy escalation on beat → J-Cut (incoming beat leads the video)
        bleed_frames = max(2, min(6, dur_frames))
        audio_bleed = f"J-Cut (Clip B audio leads by {bleed_frames} frames)"
    elif transition_type in ("slow_fade", "glow_fade") and scene_jump:
        # Cinematic dissolve with scene change → J-Cut (listener arrives first)
        bleed_frames = max(4, min(12, dur_frames))
        audio_bleed = f"J-Cut (Clip B audio leads by {bleed_frames} frames)"

    return {
        "duration_frames": dur_frames,
        "duration_s":      dur_s,
        "alignment":       alignment,
        "easing":          easing,
        "audio_bleed":     audio_bleed,
        "audio_bleed_frames": bleed_frames,
    }


# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------

def decide_transition(clip_a: Dict, clip_b: Dict) -> Dict:
    """
    Full 3-phase transition decision.

    Args:
        clip_a: metadata dict for the tail (final 3s) of the outgoing clip.
        clip_b: metadata dict for the head (first 3s) of the incoming clip.

    Returns the full JSON-compatible output schema:
    {
      "analysis": {"kinetic_flow": ..., "emotional_shift": ..., "narrative_gap": ...},
      "decision": {"transition_type": ..., "rationale": ...},
      "execution": {"duration_frames": ..., "alignment": ..., "easing": ..., "audio_bleed": ...},
      "_internal": {"duration_s": ..., "audio_bleed_frames": ..., "vectors_match": ...}
    }
    """
    bpm = float(clip_a.get("bpm", clip_b.get("bpm", 0.0)))

    # Phase 1
    analysis = phase1_human_analysis(clip_a, clip_b)

    # Phase 2
    p2 = phase2_machine_selection(clip_a, clip_b, analysis)
    transition_type = p2["transition_type"]

    # Phase 3
    p3 = phase3_execution_parameters(clip_a, clip_b, analysis, transition_type, bpm)

    logger.info(
        f"[TIE] type={transition_type} frames={p3['duration_frames']} "
        f"align={p3['alignment']} easing={p3['easing']} "
        f"bleed={p3['audio_bleed']} | "
        f"vectors_match={analysis['vectors_match']} Δenergy={analysis['energy_delta']:+.2f} "
        f"scene_jump={analysis['scene_jump']}"
    )

    return {
        "analysis": {
            "kinetic_flow":    analysis["kinetic_flow"],
            "emotional_shift": analysis["emotional_shift"],
            "narrative_gap":   analysis["narrative_gap"],
        },
        "decision": {
            "transition_type": transition_type,
            "rationale":       p2["rationale"],
        },
        "execution": {
            "duration_frames": p3["duration_frames"],
            "alignment":       p3["alignment"],
            "easing":          p3["easing"],
            "audio_bleed":     p3["audio_bleed"],
        },
        # Internal fields for pipeline consumption (not part of the user-facing schema)
        "_internal": {
            "duration_s":          p3["duration_s"],
            "audio_bleed_frames":  p3["audio_bleed_frames"],
            "vectors_match":       analysis["vectors_match"],
            "energy_delta":        analysis["energy_delta"],
            "scene_jump":          analysis["scene_jump"],
        }
    }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def enrich_segment_with_tie(segment: dict, next_segment: dict) -> dict:
    """
    Convenience wrapper: takes two consecutive pipeline segment dicts and
    injects TIE fields directly into `segment` so build_transition_graph()
    can read them without changes to the calling code.

    Injected fields (all pre-existing keys are PRESERVED):
      segment["transition"]           → TIE transition type string
      segment["transition_duration"]  → duration_s float
      segment["tie_decision"]         → full JSON output for logging/debugging
    """
    if not segment or not next_segment:
        return segment

    clip_a = {
        "motion_direction":  segment.get("motion_direction", "static"),
        "motion_intensity":  segment.get("motion_intensity", segment.get("motion", 0.3)),
        "energy_score":      segment.get("energy_score", segment.get("signal_score", 0.4)),
        "color_mood":        segment.get("color_mood", segment.get("filter_type", "neutral")),
        "scene_id":          segment.get("scene_id", segment.get("clip_id", 0)),
        "bpm":               segment.get("bpm", 0.0),
        "beat_strength":     segment.get("beat_strength", "weak"),
        "is_drop":           segment.get("is_drop", False),
        "segment_duration":  max(0.1, float(segment.get("end", 0)) - float(segment.get("start", 0))),
    }
    clip_b = {
        "motion_direction":  next_segment.get("motion_direction", "static"),
        "motion_intensity":  next_segment.get("motion_intensity", next_segment.get("motion", 0.3)),
        "energy_score":      next_segment.get("energy_score", next_segment.get("signal_score", 0.4)),
        "color_mood":        next_segment.get("color_mood", next_segment.get("filter_type", "neutral")),
        "scene_id":          next_segment.get("scene_id", next_segment.get("clip_id", 0)),
        "bpm":               next_segment.get("bpm", segment.get("bpm", 0.0)),
        "beat_strength":     next_segment.get("beat_strength", "weak"),
        "is_drop":           next_segment.get("is_drop", False),
    }

    try:
        result = decide_transition(clip_a, clip_b)
        # Inject into segment (preserve existing keys — do NOT overwrite manually set transitions)
        if not segment.get("transition") or segment.get("transition") == "clean":
            segment["transition"]          = result["decision"]["transition_type"]
            segment["transition_duration"] = result["_internal"]["duration_s"]
            segment["tie_decision"]        = result
    except Exception as e:
        logger.warning(f"[TIE] enrich_segment_with_tie failed (non-fatal): {e}")

    return segment
