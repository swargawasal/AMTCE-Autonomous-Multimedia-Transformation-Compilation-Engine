import logging
import os
import random
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger("rhythm_timeline_builder")

# ── Psycho-acoustic constants ─────────────────────────────────────────────────
# Human visual cortex integrates a new image in ~80-150ms; anticipate the beat
# by cutting BEFORE it so the new shot *lands* when the bass hits the ear.
BEAT_ANTICIPATION_DEFAULT_MS = 80

# Maximum fractional overlap allowed before a micro-shot is flagged as duplicate
# (0.30 = a shot sharing >30% of its time-range with an already-used shot is rejected)
DUPLICATE_OVERLAP_THRESHOLD = 0.30

# Vibe → preferred shot-duration range (min_s, max_s) — FALLBACK when no music_intelligence
# Matched to MusicDrivenEditor vibe profiles so both layers agree
_VIBE_SHOT_RANGE = {
    "explosive":  (2.0,  4.0),
    "hype":       (2.5,  5.0),
    "groove":     (3.0,  6.0),
    "cinematic":  (3.5,  7.0),
    "ambient":    (4.0,  8.0),
    None:         (3.0,  7.0),  # unknown — default quality window
}

# ── Musical section → bar multiplier ─────────────────────────────────────────
# Each section type gets a fraction of a musical bar as its preferred shot length.
# Applied when music_intelligence has a section map + bar_duration_sec.
SECTION_DURATION_MULTIPLIERS = {
    "intro":       1.0,   # 1 bar  — establish the vibe
    "verse":       1.0,   # 1 bar  — let the viewer settle
    "pre_chorus":  0.75,  # ¾ bar  — start ramping up
    "chorus":      0.5,   # ½ bar  — fast, energetic
    "drop":        0.5,   # ½ bar  — maximum energy, still legible
    "bridge":      1.5,   # 1½ bars — introspective, breathe
    "outro":       2.0,   # 2 bars  — slow close
    "instrumental":1.0,   # 1 bar  — default for pure music sections
}
MIN_SHOT_DURATION = 0.8  # Lowered to 0.8 to allow beat-synced micro-cuts (Score Optimization)


def normalize_scenes(scenes):
    normalized = []
    if not scenes: return []
    for s in scenes:
        if isinstance(s, dict):
            # Preserve ALL original fields — clip_id, beat_interval, beat_offset,
            # transition, color_mode, importance etc. — then ensure start/end are floats.
            entry = dict(s)
            
            # Safely get start/end even if they are explicitly None
            start_val = s.get("start")
            end_val = s.get("end")
            entry["start"] = float(start_val if start_val is not None else 0.0)
            entry["end"]   = float(end_val if end_val is not None else 0.0)
            normalized.append(entry)
        elif isinstance(s, (list, tuple)) and len(s) >= 2:
            start_val = s[0] if s[0] is not None else 0.0
            end_val = s[1] if s[1] is not None else 0.0
            normalized.append({
                "start": float(start_val),
                "end":   float(end_val)
            })
    return normalized

class RhythmTimelineBuilder:
    """
    Constructs a human-like, fast-paced editing timeline using micro-segments.
    Integrates motion, attention, subject presence, and musical beats.
    """
    
    def __init__(self):
        self.min_duration = 3.0     # minimum cut — quality over quantity; no micro-clips
        self.max_duration = 8.0     # max before micro-split — allows emotional holds
        self.target_duration_min = 8.0
        self.target_duration_max = 60.0   # actual cap set per-call from VO hint
        # Human transitions: mix of hard cuts (clean, musical) and creative moves
        self.allowed_transitions = ["hard_cut", "whip_pan", "blur_cut", "zoom_pop", "match_cut"]

    def analyze_beats(self, path: str) -> List[float]:
        """Wrapper for Audio_Modules.beat_engine.analyze_beats"""
        try:
            from Audio_Modules.beat_engine import BeatEngine
            engine = BeatEngine()
            return engine.analyze_beats(path)
        except Exception as e:
            logger.warning(f"🥁 RhythmTimelineBuilder: analyze_beats failed: {e}")
            return []

    def _get_duration(self, video_path: str) -> float:
        """Helper to get video duration via ffprobe."""
        import subprocess
        try:
            ffprobe_bin = os.getenv("FFPROBE_BIN", "ffprobe")
            cmd = [
                ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", video_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(res.stdout.strip())
        except Exception as e:
            logger.warning(f"🥁 RhythmTimelineBuilder: failed to get duration for {video_path}: {e}")
            return 0.0


    def _extract_micro_shots(
        self,
        scenes: List[Dict],
        vibe: Optional[str] = None,
        music_intelligence: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Splits longer scenes into micro-segments.

        Priority:
        1. music_intelligence section map + bar_duration_sec → phrase-length-aware durations
        2. vibe bucket (fallback for tracks without a section map)
        3. class min/max defaults
        """
        micro_shots = []

        # Check if we have a meaningful musical structure to work with
        _mi_sections   = (music_intelligence or {}).get("sections", [])
        _bar_duration  = float((music_intelligence or {}).get("bar_duration_sec", 0.0))
        _use_mi        = bool(_mi_sections and _bar_duration > 0)

        # Fallback vibe range
        min_s, max_s = _VIBE_SHOT_RANGE.get(vibe, _VIBE_SHOT_RANGE[None])
        chop_min = max(MIN_SHOT_DURATION, min_s)
        chop_max = min(self.max_duration, max_s)

        def _section_at(t: float) -> Optional[Dict]:
            for sec in _mi_sections:
                if float(sec.get("start", 0)) <= t < float(sec.get("end", 0)):
                    return sec
            return None

        def _ideal_duration(t: float) -> float:
            if _use_mi:
                sec = _section_at(t)
                sec_type = (sec or {}).get("type", "verse")
                multiplier = SECTION_DURATION_MULTIPLIERS.get(sec_type, 1.0)
                ideal = _bar_duration * multiplier
                return max(MIN_SHOT_DURATION, min(self.max_duration, ideal))
            # Vibe fallback: random in range
            return random.uniform(chop_min, chop_max)

        for i, scene in enumerate(scenes):
            try:
                start = float(scene.get("start", 0))
                end   = float(scene.get("end", 0))
                c_id  = scene.get("clip_id", 0)
            except (ValueError, TypeError):
                continue

            duration = end - start
            current_start = start

            while duration > chop_min:
                chop_dur = _ideal_duration(current_start)
                # Don't overshoot the scene end
                chop_dur = min(chop_dur, duration)
                if chop_dur < MIN_SHOT_DURATION:
                    break
                micro_shots.append({
                    "clip_id":      c_id,
                    "start":        round(current_start, 3),
                    "end":          round(current_start + chop_dur, 3),
                    "parent_scene": i,
                })
                current_start += chop_dur
                duration = end - current_start

            # Absorb leftover tail if it's long enough, or if it's the entire scene
            if duration >= MIN_SHOT_DURATION or current_start == start:
                if duration > 0.1:  # Avoid 0-length clips
                    micro_shots.append({
                        "clip_id":      c_id,
                        "start":        round(current_start, 3),
                        "end":          round(end, 3),
                        "parent_scene": i,
                    })
        return micro_shots


    def _score_shots(
        self,
        micro_shots:       List[Dict],
        scenes:            List[Dict],
        motion_data:       List[Dict],
        attention_data:    List[Dict],
        subject_data:      List[Dict],
        vibe:              Optional[str] = None,
        music_intelligence: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Score each micro shot with music-aware weights.

        New signals (when music_intelligence is available):
          - phrase_alignment  : how well the shot's position aligns with a musical phrase boundary
          - tension_alignment : short shots preferred at high tension, long shots during low tension
          - lyric_emotion     : face/subject-presence bonus at emotionally charged lyric moments
        """
        sv_lo, sv_hi = _VIBE_SHOT_RANGE.get(vibe, _VIBE_SHOT_RANGE[None])
        scored_shots = []

        _mi            = music_intelligence or {}
        _mi_sections   = _mi.get("sections", [])
        _mi_tension    = _mi.get("tension_arc", [])
        _mi_lyrics     = _mi.get("lyrics", [])
        _mi_peaks      = _mi.get("emotional_peak_moments", [])
        _bar_dur       = float(_mi.get("bar_duration_sec", 0.0))
        _has_mi        = bool(_mi_sections or _mi_tension)

        # Helpers
        def _tension_at(t: float) -> float:
            if not _mi_tension:
                return 0.5
            arc = sorted(_mi_tension, key=lambda x: x.get("time", 0.0))
            if t <= arc[0].get("time", 0.0):
                return float(arc[0].get("tension", 0.5))
            if t >= arc[-1].get("time", 0.0):
                return float(arc[-1].get("tension", 0.5))
            for i in range(len(arc) - 1):
                t0 = float(arc[i].get("time", 0.0))
                t1 = float(arc[i + 1].get("time", 0.0))
                if t0 <= t <= t1 and t1 > t0:
                    alpha = (t - t0) / (t1 - t0)
                    return round(
                        arc[i].get("tension", 0.5) + alpha * (
                            arc[i + 1].get("tension", 0.5) - arc[i].get("tension", 0.5)
                        ), 3
                    )
            return 0.5

        def _section_at(t: float) -> Optional[Dict]:
            for sec in _mi_sections:
                if float(sec.get("start", 0)) <= t < float(sec.get("end", 0)):
                    return sec
            return None

        def _lyric_emotion_at(t_start: float, t_end: float) -> float:
            """Return max emotion_weight of any lyric whose window overlaps [t_start, t_end]."""
            best = 0.0
            for lyr in _mi_lyrics:
                l_s = float(lyr.get("time", 0.0))
                l_e = float(lyr.get("end", l_s + 1.5))
                if l_s < t_end and l_e > t_start:
                    best = max(best, float(lyr.get("emotion_weight", 0.0)))
            return best

        for i, shot in enumerate(micro_shots):
            start = shot["start"]
            end   = shot["end"]

            parent_idx   = shot.get("parent_scene", 0)
            parent_scene = scenes[parent_idx] if parent_idx < len(scenes) else {}
            parent_score = float(
                parent_scene.get("composite_score",
                parent_scene.get("importance",
                parent_scene.get("score", 0.5)))
            )

            motion_intensity = self._get_overlap_score(start, end, motion_data,    "intensity")
            attention_focus  = self._get_overlap_score(start, end, attention_data, "focus")
            subject_presence = self._get_overlap_score(start, end, subject_data,   "confidence")

            duration = max(0.0, end - start)

            # ── Music-aware signals ──────────────────────────────────────────
            if _has_mi:
                tension = _tension_at(start)
                section = _section_at(start)
                sec_type = (section or {}).get("type", "verse")
                lyric_emotion = _lyric_emotion_at(start, end)

                # phrase_alignment: is the shot's start close to a section boundary?
                # (Shots that start at section transitions are natural cut points)
                sec_start = float((section or {}).get("start", start))
                dist_from_boundary = abs(start - sec_start)
                phrase_boundary_window = max(0.5, _bar_dur * 0.25) if _bar_dur > 0 else 0.5
                phrase_alignment = max(0.0, 1.0 - (dist_from_boundary / phrase_boundary_window))

                # tension_alignment: at high tension → prefer shorter shots (score=1 if short),
                # at low tension → prefer longer shots
                ideal_dur_at_tension = (
                    MIN_SHOT_DURATION + (self.max_duration - MIN_SHOT_DURATION) * (1.0 - tension)
                )
                dur_delta = abs(duration - ideal_dur_at_tension)
                tension_alignment = max(0.0, 1.0 - (dur_delta / max(1.0, ideal_dur_at_tension)))

                # Is this shot near an emotional peak?
                near_peak = any(abs(start - p) < 1.5 for p in _mi_peaks)
                peak_bonus = 0.15 if near_peak else 0.0

                score = (
                    0.30 * parent_score       +
                    0.15 * motion_intensity   +
                    0.10 * attention_focus    +
                    0.10 * subject_presence   +
                    0.15 * phrase_alignment   +   # musical phrase alignment
                    0.10 * tension_alignment  +   # short on drops, long in build-up
                    0.10 * lyric_emotion            # face shot on emotional lyrics
                ) + peak_bonus + random.uniform(-0.02, 0.02)

            else:
                # Legacy scoring (no music intelligence available)
                if duration < self.min_duration:
                    duration_preference = 0.5
                elif sv_lo <= duration <= sv_hi:
                    duration_preference = 1.0
                elif duration < sv_lo:
                    duration_preference = 0.75
                else:
                    duration_preference = 0.80

                novelty = random.uniform(0.80, 1.0)
                score = (
                    0.50 * parent_score       +
                    0.20 * motion_intensity   +
                    0.10 * attention_focus    +
                    0.10 * subject_presence   +
                    0.05 * novelty            +
                    0.05 * duration_preference
                ) + random.uniform(-0.02, 0.02)

            shot["score"]        = round(score, 3)
            shot["_tension"]     = _tension_at(start) if _has_mi else 0.5
            shot["_section"]     = (_section_at(start) or {}).get("type", "unknown") if _has_mi else "unknown"
            scored_shots.append(shot)

        return scored_shots


    def _get_overlap_score(self, start: float, end: float, events: List[Any], val_key: str) -> float:
        """Helper to find signal overlap for scoring. Handles dicts, tuples, and lists."""
        if not events: return 0.5 # default moderate score
        max_val = 0.0
        
        # Flatten if it's a list of lists (like subject_tracking)
        flat_events = []
        for e in events:
            if isinstance(e, list): flat_events.extend(e)
            else: flat_events.append(e)
            
        for ev in flat_events:
            ev_start, ev_end, val = 0.0, 0.0, 0.5
            
            if isinstance(ev, dict):
                # Try time-based keys
                _start = ev.get("start")
                if _start is None: _start = ev.get("time_sec")
                if _start is None: _start = ev.get("frame", 0) / 30.0
                ev_start = float(_start if _start is not None else 0.0)
                
                _end = ev.get("end")
                ev_end = float(_end if _end is not None else ev_start + 0.5)
                
                _val = ev.get(val_key)
                if _val is None: _val = ev.get("score")
                if _val is None: _val = ev.get("confidence", 0.5)
                val = float(_val if _val is not None else 0.5)
            elif isinstance(ev, tuple) or isinstance(ev, list):
                if len(ev) >= 2:
                    ev_start = float(ev[0] if ev[0] is not None else 0.0)
                    ev_end = ev_start + 0.5
                    val = float(ev[1] if ev[1] is not None else 0.5)
                    
            if start < ev_end and end > ev_start:
                max_val = max(max_val, val)
                
        return max_val

    def _snap_to_beats(self, shots: List[Dict], beat_grid: List[float]) -> List[Dict]:
        """Align shot boundaries to nearest beat, with human-style anticipation offset.
        
        A human editor cuts BEFORE the beat lands so the viewer's visual cortex
        (150ms processing lag) experiences the cut and audio impact simultaneously.
        BEAT_ANTICIPATION_MS controls how many ms before the beat we cut (default: 60ms).
        """
        if not beat_grid:
            return shots

        # Anticipation offset: cut this many seconds BEFORE the beat
        anticipation = float(os.getenv("BEAT_ANTICIPATION_MS", "60")) / 1000.0
            
        aligned = []
        for shot in shots:
            start = shot["start"]
            end = shot["end"]
            
            # Find nearest beat for start — then pull it back by anticipation offset
            nearest_start = min(beat_grid, key=lambda b: abs(b - start))
            if abs(nearest_start - start) < 0.4:  # Snap threshold
                start = max(0.0, nearest_start - anticipation)  # cut BEFORE the beat
                
            # Find nearest beat for end — same anticipation logic
            nearest_end = min(beat_grid, key=lambda b: abs(b - end))
            if abs(nearest_end - end) < 0.4:
                end = max(start + self.min_duration, nearest_end - anticipation)
                
            # Ensure snapped duration is still valid
            if end - start >= self.min_duration:
                shot["start"] = round(start, 4)
                shot["end"] = round(end, 4)
            aligned.append(shot)
            
        return aligned

    def _decide_transition(self, current_shot: Dict, next_shot: Dict) -> Dict:
        """
        [STIE v2 + TIE] Gap-level transition decision.

        Returns a decision dict compatible with existing code:
          {"type": str, "duration_s": float, "intensity": float,
           "reason": str, "rag_hit": bool, "context_key": str,
           "tie_data": dict (optional)}

        Falls back to a simple dict with type='clean' if unavailable.
        """
        _clean = {"type": "clean", "duration_s": 0.04, "intensity": 0.0,
                  "reason": "last_segment", "rag_hit": False, "context_key": ""}
        if not next_shot:
            return _clean

        # --- Transition Intelligence Engine (TIE) ---
        if os.getenv("ENABLE_TIE", "yes").lower() == "yes":
            try:
                from Compiler_Modules.transition_intelligence_engine import decide_transition
                
                # Build rich metadata payloads for TIE
                clip_a = {
                    "motion_direction": current_shot.get("motion_direction", "static"),
                    "motion_intensity": current_shot.get("motion_intensity", current_shot.get("motion", 0.5)),
                    "energy_score": current_shot.get("energy_score", current_shot.get("score", 0.5)),
                    "color_mood": current_shot.get("color_mode", "neutral"),
                    "scene_id": current_shot.get("parent_scene", 0),
                    "bpm": current_shot.get("bpm", 0.0),
                    "beat_strength": current_shot.get("_beat_strength") or current_shot.get("strength", "weak"),
                    "is_drop": bool(current_shot.get("is_drop", False)),
                    "segment_duration": max(0.1, current_shot.get("end", 0) - current_shot.get("start", 0)),
                }
                
                clip_b = {
                    "motion_direction": next_shot.get("motion_direction", "static"),
                    "motion_intensity": next_shot.get("motion_intensity", next_shot.get("motion", 0.5)),
                    "energy_score": next_shot.get("energy_score", next_shot.get("score", 0.5)),
                    "color_mood": next_shot.get("color_mode", "neutral"),
                    "scene_id": next_shot.get("parent_scene", 0),
                    "bpm": next_shot.get("bpm", current_shot.get("bpm", 0.0)),
                    "beat_strength": next_shot.get("_beat_strength") or next_shot.get("strength", "weak"),
                    "is_drop": bool(next_shot.get("is_drop", False)),
                }
                
                tie_res = decide_transition(clip_a, clip_b)
                
                return {
                    "type": tie_res["decision"]["transition_type"],
                    "duration_s": tie_res["_internal"]["duration_s"],
                    "intensity": clip_a["motion_intensity"],
                    "reason": "TIE: " + tie_res["decision"]["rationale"],
                    "rag_hit": False,
                    "context_key": "",
                    "tie_data": tie_res,  # Pass full JSON for downstream renderers
                }
            except Exception as e:
                logger.debug(f"[TIE] fallback to STIE ({e})")

        # --- Legacy STIE decision ---
        beat_strength  = (current_shot.get("_beat_strength")
                          or current_shot.get("strength", "weak"))
        is_drop        = bool(current_shot.get("is_drop", False))
        motion         = float(current_shot.get("importance", 0.5))   # best proxy available
        scene_jump     = current_shot.get("parent_scene") != next_shot.get("parent_scene")
        color_mood     = current_shot.get("color_mode", "neutral")
        seg_duration   = max(0.1, current_shot.get("end", 0) - current_shot.get("start", 0))
        beat_interval  = float(current_shot.get("beat_interval", 0.5))

        try:
            from Compiler_Modules.smart_transition_engine import engine as _stie
            decision = _stie.decide(
                motion_intensity=motion,
                beat_strength=beat_strength,
                scene_jump=scene_jump,
                color_mood=color_mood,
                seg_duration=seg_duration,
                beat_interval=beat_interval,
                is_drop=is_drop,
            )
            return decision
        except Exception as _e:
            logger.debug(f"[STIE] fallback ({_e})")
            # Math-only fallback
            strength = beat_strength
            t_type = {"drop": "glitch_pop", "strong": "whip_pan", "weak": "clean"}.get(strength, "punch_cut")
            return {"type": t_type, "duration_s": 0.2, "intensity": 0.5,
                    "reason": "fallback", "rag_hit": False, "context_key": ""}

    def build_timeline(
        self,
        scenes:                List[Dict] = None,
        motion_events:         List[Dict] = None,
        attention_events:      List[Dict] = None,
        beat_grid:             List[float] = None,
        hook_time:             float = 0.0,
        subject_data:          List[Dict] = None,
        target_duration_hint:  float = None,
        clips:                 List[str] = None,
        beat_maps:             List[List[float]] = None,
        vibe:                  Optional[str] = None,
        music_intelligence:    Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Psycho-acoustic, duplication-free, music-intelligence-driven timeline builder.

        New: `music_intelligence` (from profile_data["music_intelligence"]) drives:
          - Phrase-length-aware shot splitting (section map + bar duration)
          - Tension-arc-driven shot scoring (high tension = shorter shots)
          - Lyric emotion bonus at emotionally charged moments
          - Tension-arc-driven sequence ordering (build → peak → release)
        """
        if clips is not None:
            logger.info(f"🥁 RhythmTimelineBuilder: Received batch of {len(clips)} clips. Synthesizing scenes...")
            scenes = []
            for i, cp in enumerate(clips):
                dur = self._get_duration(cp)
                if dur > 0:
                    scenes.append({
                        "clip_id": i,
                        "start": 0.0,
                        "end": dur,
                        "score": 0.8 # Multi-clip batch priority
                    })
            # Merge beat maps if present
            if beat_maps:
                beat_grid = []
                for bm in beat_maps:
                    if bm: beat_grid.extend(bm)
                beat_grid = sorted(list(set(beat_grid)))
                
        logger.info("🥁 RhythmTimelineBuilder: Starting micro-extraction...")
        # Issue 1: Normalize scenes first
        scenes = normalize_scenes(scenes)

        # Step 1: Micro Shot Extraction — phrase-length-aware if music_intelligence present
        micro_shots = self._extract_micro_shots(scenes, vibe=vibe, music_intelligence=music_intelligence)

        # Step 2: Shot Scoring — tension + section + lyric aware
        scored_shots = self._score_shots(
            micro_shots, scenes,
            motion_events or [], attention_events or [], subject_data or [],
            vibe=vibe,
            music_intelligence=music_intelligence,
        )
        
        top_shots = scored_shots
        # per temporal band (early/mid/late) to guarantee timeline coverage.
        TOP_PER_BAND = 15
        
        # Step 3: Beat Grid Alignment
        # beat_grid may arrive as [float] or [{"time": float, "energy": float}] — normalise
        if beat_grid:
            _bg_normalized = []
            for _b in beat_grid:
                if isinstance(_b, dict):
                    _bg_normalized.append(float(_b.get("time", 0.0)))
                elif isinstance(_b, (int, float)):
                    _bg_normalized.append(float(_b))
            beat_grid = _bg_normalized

            # ── BEAT GRID SANITY FILTER ─────────────────────────────────────
            # If beats are < 0.3s apart on average, the grid is noise (bad tempo
            # detection, sub-beat harmonics, etc.) and will compress all shots into
            # tiny windows. Discard such grids entirely — fall back to no snapping.
            if len(beat_grid) >= 2:
                _sorted_bg = sorted(beat_grid)
                _avg_interval = (_sorted_bg[-1] - _sorted_bg[0]) / max(1, len(_sorted_bg) - 1)
                if _avg_interval < 0.3:
                    logger.warning(
                        f"⚠️ [BEAT_GRID] Avg beat interval {_avg_interval:.3f}s < 0.3s — "
                        "noise-level grid detected. Disabling beat snapping."
                    )
                    beat_grid = []  # nullify — don't snap to noise
            # ────────────────────────────────────────────────────────────────

            if beat_grid:
                top_shots = self._snap_to_beats(top_shots, beat_grid)
            
        # Step 4: Timeline Construction – shape a mini "story arc" instead of just trimming
        timeline = []
        current_duration = 0.0
        recent_durations: List[float] = []
        recent_parent_scenes: List[int] = []

        # Estimate total duration so we can avoid blindly chopping off the tail.
        # For your Telegram flow we prioritize RE-SHAPING over shortening, so
        # we target the full clip duration when it is known.
        try:
            total_duration = max(s.get("end", 0.0) for s in scenes) if scenes else max(
                s.get("end", 0.0) for s in top_shots
            )
        except ValueError:
            total_duration = 0.0

        # Always aim for the full duration when we know it;
        # only fall back to the class default if duration probing fails.
        # If a VO pacing hint is supplied, respect it as the editorial target.
        if target_duration_hint and target_duration_hint > 0:
            target_max = target_duration_hint
            logger.info(f"🎙️ RhythmTimelineBuilder: using VO pacing target_duration_hint={target_max:.1f}s")
        else:
            target_max = total_duration if total_duration else self.target_duration_max

        # Lightly classify by both energy (score) and position in time so we
        # don't just live in the first half of the clip.
        early_band: List[Dict] = []
        mid_band: List[Dict] = []
        late_band: List[Dict] = []

        if top_shots:
            t1 = total_duration / 3.0 if total_duration else 0.0
            t2 = 2 * total_duration / 3.0 if total_duration else 0.0

            for s in top_shots:
                st = s.get("start", 0.0)
                if st <= t1:
                    early_band.append(s)
                elif st <= t2:
                    mid_band.append(s)
                else:
                    late_band.append(s)

        # Sort each temporal band by score (high → low) and enforce Top-K per band
        early_band = sorted(early_band, key=lambda x: x["score"], reverse=True)[:TOP_PER_BAND]
        mid_band   = sorted(mid_band, key=lambda x: x["score"], reverse=True)[:TOP_PER_BAND]
        late_band  = sorted(late_band, key=lambda x: x["score"], reverse=True)[:TOP_PER_BAND]
        
        top_shots = early_band + mid_band + late_band

        # Find best hook shot (nearest to hook_time, but also high‑energy if possible)
        hook_candidates = sorted(top_shots, key=lambda x: (abs(x["start"] - hook_time), -x["score"]))
        if hook_candidates:
            hook_shot = hook_candidates.pop(0)
            timeline.append(hook_shot)
            current_duration += (hook_shot["end"] - hook_shot["start"])
            recent_durations.append(max(0.0, hook_shot["end"] - hook_shot["start"]))
            recent_parent_scenes.append(hook_shot.get("parent_scene", -1))
            for band in (early_band, mid_band, late_band):
                if hook_shot in band:
                    band.remove(hook_shot)

        # ── TENSION-ARC SEQUENCE ORDERING ────────────────────────────────────
        # Replace the static early→mid→late cycle with tension-driven ordering:
        # - Build phase (tension < 0.4): prefer early-source shots (establish)
        # - Peak / Drop (tension >= 0.7): prefer highest-score shots from any band
        # - Release (tension 0.4-0.7): alternate mid and late for variety
        #
        # When music_intelligence is absent, fall back to the original static cycle.
        _mi_tension_arc = (music_intelligence or {}).get("tension_arc", [])
        _mi_peaks       = (music_intelligence or {}).get("emotional_peak_moments", [])

        def _tension_sequence_at(t: float) -> str:
            """Return 'build'|'peak'|'release' based on tension arc at time t."""
            if not _mi_tension_arc:
                return "release"  # use legacy cycle
            arc = sorted(_mi_tension_arc, key=lambda x: x.get("time", 0.0))
            t_val = 0.5
            for i in range(len(arc) - 1):
                t0 = float(arc[i].get("time", 0.0))
                t1 = float(arc[i + 1].get("time", 0.0))
                if t0 <= t <= t1 and t1 > t0:
                    a = (t - t0) / (t1 - t0)
                    t_val = arc[i].get("tension", 0.5) + a * (
                        arc[i + 1].get("tension", 0.5) - arc[i].get("tension", 0.5)
                    )
                    break
            if t_val >= 0.7:  return "peak"
            if t_val <= 0.4:  return "build"
            return "release"

        bands_cycle = ["mid", "late", "early", "mid"]  # legacy fallback
        band_idx = 0

        def _pick_next_from_band(name: str) -> Any:
            if name == "early":
                bucket = early_band
            elif name == "mid":
                bucket = mid_band
            else:
                bucket = late_band
            if not bucket:
                # Fallback to any remaining shot
                remaining = early_band or mid_band or late_band
                return remaining[0] if remaining else None
            return bucket[0]

        # Fill remaining sequence using tension-arc or legacy temporal pattern
        while current_duration < target_max:
            # Tension-arc-driven band selection
            if _mi_tension_arc:
                phase = _tension_sequence_at(current_duration)
                if phase == "build":
                    # Build phase: prefer early shots (establish the subject)
                    band_name = "early"
                elif phase == "peak":
                    # Peak / Drop: pick the highest-scoring shot from any band
                    all_remaining = early_band + mid_band + late_band
                    if not all_remaining:
                        break
                    shot = max(all_remaining, key=lambda x: x["score"])
                    band_name = None  # We've already chosen the shot
                else:
                    # Release: alternate mid / late for variety
                    band_name = "mid" if band_idx % 2 == 0 else "late"
                    band_idx += 1

                if band_name is not None:
                    shot = _pick_next_from_band(band_name)
            else:
                # Legacy static cycle
                band_name = bands_cycle[band_idx % len(bands_cycle)]
                band_idx += 1
                shot = _pick_next_from_band(band_name)

            if not shot:
                break

            dur = shot["end"] - shot["start"]

            # ── GLOBAL DEDUPLICATION (core glitch-loop fix) ─────────────────
            # Track every accepted shot as (clip_id, src_start, src_end).
            # Reject any candidate whose source-range overlaps > DUPLICATE_OVERLAP_THRESHOLD
            # with an already-accepted shot from the same clip.
            overlap = False
            c_id    = shot.get("clip_id", 0)
            s_st    = shot.get("start", 0.0)
            s_en    = shot.get("end",   0.0)
            s_dur   = max(0.001, s_en - s_st)

            for prev in timeline:
                if prev.get("clip_id") != c_id:
                    continue
                p_st = prev.get("start", 0.0)
                p_en = prev.get("end",   0.0)
                olen = max(0.0, min(s_en, p_en) - max(s_st, p_st))
                if olen / s_dur > DUPLICATE_OVERLAP_THRESHOLD:
                    overlap = True
                    break

            # Rhythm guardrails:
            # - Avoid more than 3 ultra-short cuts in a row (<0.7s)
            # - Avoid more than 4 very long holds in a row (>5.0s = longer than our max anyway)
            if recent_durations:
                window = recent_durations[-3:]
                if dur < 0.7 and window and all(d < 0.7 for d in window):
                    # Remove this candidate from its band so we don't get stuck
                    if shot in early_band: early_band.remove(shot)
                    if shot in mid_band: mid_band.remove(shot)
                    if shot in late_band: late_band.remove(shot)
                    continue
                if dur > 5.0 and window and all(d > 5.0 for d in window):
                    if shot in early_band: early_band.remove(shot)
                    if shot in mid_band: mid_band.remove(shot)
                    if shot in late_band: late_band.remove(shot)
                    continue

            # Variety guardrail: avoid bouncing on the same parent_scene when we have alternatives
            if recent_parent_scenes and (early_band or mid_band or late_band):
                last_parent = recent_parent_scenes[-1]
                if shot.get("parent_scene") == last_parent and random.random() < 0.4:
                    if shot in early_band: early_band.remove(shot)
                    if shot in mid_band: mid_band.remove(shot)
                    if shot in late_band: late_band.remove(shot)
                    continue

            # Stop only when we would clearly exceed the target_max duration
            # for this clip, not the fixed class default. This was previously
            # using self.target_duration_max and effectively hard‑clamping
            # highlights to ~18s even for longer sources.
            if overlap or current_duration + dur > target_max + 1.0:
                if shot in early_band: early_band.remove(shot)
                if shot in mid_band: mid_band.remove(shot)
                if shot in late_band: late_band.remove(shot)
                continue

            # Accept shot into timeline and update buckets
            timeline.append(shot)
            current_duration += dur
            recent_durations.append(max(0.0, dur))
            recent_parent_scenes.append(shot.get("parent_scene", -1))

            if shot in early_band: early_band.remove(shot)
            if shot in mid_band: mid_band.remove(shot)
            if shot in late_band: late_band.remove(shot)
                
        # Ensure we cover the tail on short clips if possible: if nothing in the
        # timeline reaches near the end, try to append one late-band shot that
        # ends in the last second of the source.
        if total_duration and timeline:
            last_end = max(s["end"] for s in timeline)
            if last_end < total_duration - 0.8 and late_band:
                tail_candidate = max(late_band, key=lambda x: x["end"])
                if tail_candidate["end"] > last_end:
                    timeline.append(tail_candidate)
                    current_duration += max(0.0, tail_candidate["end"] - tail_candidate["start"])

        # COVERAGE GUARANTEE: If any temporal band was never visited, force-add
        # at least one shot from it. A human editor touches the whole video.
        bands_visited = {s.get("parent_scene", -1) for s in timeline}
        _all_band_shots = {"early": early_band, "mid": mid_band, "late": late_band}
        for _bname, _bshots in _all_band_shots.items():
            if _bshots:
                _band_parents = {s.get("parent_scene", -1) for s in _bshots}
                if not bands_visited.intersection(_band_parents):
                    # This band has NO representation — force add its best shot
                    _best = max(_bshots, key=lambda x: x["score"])
                    timeline.append(_best)
                    current_duration += max(0.0, _best["end"] - _best["start"])
                    logger.info(f"🎬 [COVERAGE_GUARANTEE] Force-added {_bname} band shot {_best['start']:.1f}s-{_best['end']:.1f}s")

        # Log coverage ratio
        if total_duration and total_duration > 0:
            _covered = sum(max(0.0, s.get("end", 0) - s.get("start", 0)) for s in timeline)
            logger.info(f"📊 [COVERAGE] {_covered:.1f}s / {total_duration:.1f}s = {100*_covered/total_duration:.0f}% of source utilized")

        # Step 5 & 6: Transition Decision & Final Formatting
        # IMPORTANT: We deliberately KEEP the selection order instead of
        # re-sorting chronologically, so the final cut can jump forwards
        # and backwards in time like a human highlight edit instead of
        # behaving like a simple head-trim of the source.

        # SAFETY NET: If, for any reason, the above logic fails to generate a
        # clearly reshaped sequence (e.g. all shots are clustered at the head),
        # we fall back to a deterministic "coverage grid" that samples the
        # entire clip from start to end in evenly spaced windows.
        def _fallback_grid_segments() -> List[Dict]:
            if not total_duration or total_duration <= 0.0:
                return []
            grid_segments = []
            # Aim for 8–10 segments across the whole duration
            grid_count = 8
            window = max(self.min_duration + 0.2, total_duration / grid_count)
            t = 0.0
            for i in range(grid_count):
                st = round(t, 3)
                en = round(min(total_duration, st + window), 3)
                if en - st < self.min_duration:
                    break
                grid_segments.append({
                    "clip_id": 0,
                    "start": st,
                    "end": en,
                    "style": "clean"
                })
                t += window
                if t >= total_duration:
                    break
            return grid_segments

        # Decide whether our timeline looks "reshaped" enough; if not, override
        # with fallback grid segments that force coverage of the full clip.
        # [REMOVED] Restrictive (60% coverage) rule that was flattening creative edits.
        # We now trust the existing selection unless it's literally empty.
        if not timeline:
            logger.warning("⚠️ RhythmTimelineBuilder: timeline empty; applying fallback grid reshaping.")
            base_for_final = _fallback_grid_segments()
        else:
            base_for_final = timeline

        final_timeline = []
        for i in range(len(base_for_final)):
            curr = base_for_final[i]
            nxt  = base_for_final[i + 1] if i < len(base_for_final) - 1 else None

            # [STIE] Gap-level RAG-augmented transition decision
            decision = self._decide_transition(curr, nxt)
            style    = decision["type"]

            # [BEAT-SYNC] Compute beat_interval from the beat_grid when available.
            _bi = curr.get("beat_interval")
            if not _bi and beat_grid and len(beat_grid) > 1:
                _seg_start = curr.get("start", 0.0)
                _bg_sorted = sorted(beat_grid)
                _prev_beat = next(
                    (b for b in reversed(_bg_sorted) if b <= _seg_start),
                    _bg_sorted[0]
                )
                _next_beat = next(
                    (b for b in _bg_sorted if b > _seg_start),
                    _bg_sorted[-1]
                )
                _bi = round(max(0.1, _next_beat - _prev_beat), 3)

            seg_out = {
                "clip_id":             curr.get("clip_id", 0),
                "start":               round(curr["start"], 3),
                "end":                 round(curr["end"], 3),
                "style":               style,
                "transition":          style,
                # [STIE] Store the computed duration so build_transition_graph
                # uses this exact value instead of recalculating from beat_interval.
                "transition_duration": decision["duration_s"],
                "transition_intensity":decision["intensity"],
                "transition_context":  decision["context_key"],
                "transition_rag_hit":  decision["rag_hit"],
                "tie_decision":        decision.get("tie_data"), # TIE payload for J-Cuts / Easing
                "reason": (
                    curr.get("reason")
                    or f"STIE:{decision['reason']} @ {decision['duration_s']*1000:.0f}ms"
                ),
            }
            # Carry beat-sync metadata
            if _bi:
                seg_out["beat_interval"] = round(float(_bi), 3)
                seg_out["beat_offset"]   = curr.get("beat_offset", 0.033)
            # Carry color_mode and importance
            for _k in ("color_mode", "importance", "is_drop", "_beat_strength"):
                if _k in curr:
                    seg_out[_k] = curr[_k]

            final_timeline.append(seg_out)

        _rag_hits = sum(1 for s in final_timeline if s.get("transition_rag_hit"))
        logger.info(
            f"✅ RhythmTimelineBuilder: {len(final_timeline)} segments | "
            f"{current_duration:.1f}s | STIE RAG hits: {_rag_hits}/{len(final_timeline)}"
        )
        return final_timeline