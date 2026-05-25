"""
Creative Editor Bridge
======================
Ties the disconnected editing intelligence modules together to produce
a genuinely human-style, audio-driven edit.

Pipeline:
  A. Analyze BGM beats (BeatEngine)
  B. Thin beat grid → enforce min_cut_spacing (prevents 140-BPM hyper-cuts)
  C. Score + map scenes to beats (MusicDrivenEditor)
  D. VO pacing hint (words / 2.7 ≈ natural narration rate)
  E. Convert beat-driven timeline to standard segment format
  F. Inject energy-aware color hints per segment

Result is written back into profile_data so orchestrator downstream
modules (RhythmTimelineBuilder, build_trim_segments) can consume it.
"""

import logging
import random
from typing import Dict, List, Any, Optional

logger = logging.getLogger("creative_editor_bridge")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_CUT_SPACING = 0.7          # seconds – prevents hyper-dense cuts on fast songs
VO_WORDS_PER_SEC = 2.7         # natural narration pace (slightly slower than 3 w/s)
MIN_BEAT_DRIVEN_SEGMENTS = 2   # only use beat-driven timeline if we have enough segs
DEFAULT_FALLBACK_BEAT_SPACING = 0.8  # seconds – metronome grid when no BGM

def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

class CreativeEditorBridge:
    """
    Connects BGM beat analysis, scene-to-beat mapping, VO pacing, and
    energy-aware color grading into a single pre-render enrichment step.
    """

    # ── Beat Analysis ─────────────────────────────────────────────────────────

    def _analyze_bgm_beats(self, music_path: str) -> Dict:
        """Extract beats AND drops from BGM track using BeatEngine or pool cache."""
        import os
        filename = os.path.basename(music_path)
        
        try:
            from Audio_Modules.audio_pool_manager import pool_manager
            meta = pool_manager._get_file_metadata(filename)
            
            # ── V2+ Fast Path ──
            if meta and meta.get("version") == pool_manager.CURRENT_VERSION:
                # 1. Integrity Check
                current_hash = pool_manager._calculate_hash(music_path)
                if meta.get("audio_hash") != current_hash:
                    logger.warning(f"⚠️ Hash mismatch for {filename} (File changed) → forcing re-analysis.")
                else:
                    # 2. Cache Hit
                    beat_data = pool_manager.get_beat_data(filename)
                    if beat_data:
                        # Validation
                        drop_times = meta.get("drop_times", [])
                        if not beat_data["times"]:
                            logger.warning(f"⚠️ Cached beat_data for {filename} is empty → fallback.")
                        else:
                            logger.info(f"🎯 [POOL] Cache hit for {filename}: zero re-analysis.")
                            
                            # Reconstruct normalized list
                            normalized = []
                            for t, e in zip(beat_data["times"], beat_data["energies"]):
                                normalized.append({"time": float(t), "energy": float(e)})
                            
                            # Pre-tag drops
                            for b in normalized:
                                if any(abs(b["time"] - dt) < 0.1 for dt in drop_times):
                                    b["energy"] = 1.0
                            
                            return {
                                "beats": normalized,
                                "drops": drop_times,
                                "tempo": meta.get("bpm", 120.0)
                            }
            
            # ── Fallback Path ──
            logger.info(f"⚠️ Cache miss or version mismatch for {filename} → running BeatEngine.")
            from Audio_Modules.beat_engine import BeatEngine
            engine = BeatEngine()
            result = engine.analyze_beats_with_drops(music_path)
            raw_beats = result.get("beats", [])
            drops     = result.get("drops", [])
            if not raw_beats:
                logger.warning("🎵 BeatEngine returned no beats for BGM track.")
                return {}

            # Normalize to dicts
            normalized = []
            for b in raw_beats:
                if isinstance(b, dict):
                    normalized.append({
                        "time":   float(b.get("time",   0.0)),
                        "energy": float(b.get("energy", 0.5)),
                    })
                elif isinstance(b, (int, float)):
                    normalized.append({"time": float(b), "energy": 0.5})

            # Normalize drops
            drop_times = []
            for d in drops:
                if isinstance(d, (int, float)):
                    drop_times.append(float(d))
                elif isinstance(d, dict):
                    drop_times.append(float(d.get("time", 0.0)))

            # Tag beats near a drop
            for b in normalized:
                if any(abs(b["time"] - dt) < 0.1 for dt in drop_times):
                    b["energy"] = 1.0

            # Calculate tempo if missing
            tempo = result.get("tempo")
            if not tempo and len(normalized) >= 4:
                intervals = [normalized[i+1]["time"] - normalized[i]["time"] for i in range(len(normalized)-1)]
                avg_interval = sum(intervals) / len(intervals)
                if avg_interval > 0:
                    tempo = round(60.0 / avg_interval, 1)

            logger.info(f"🥁 BGM BeatEngine: {len(normalized)} beats, {len(drop_times)} drops (Tempo: {tempo}).")
            return {"beats": normalized, "drops": drop_times, "tempo": tempo}
        except Exception as e:
            logger.warning(f"⚠️ BGM beat analysis failed: {e}")
            return {}

    # ── Beat Thinning ─────────────────────────────────────────────────────────

    def _thin_beats(self, beats: List[Dict], min_spacing: float = MIN_CUT_SPACING) -> List[Dict]:
        """
        Enforce minimum spacing between beats.
        Accepts dicts: [{"time": 1.2, "energy": 0.5}, ...]
        Necessary for fast songs (≥ 120 BPM) where raw beats land every 0.4–0.5s.
        """
        if not beats:
            return []
        thinned = [beats[0]]
        for b in beats[1:]:
            # b is a dict: {"time": float, "energy": float}
            if b.get("time", 0) - thinned[-1].get("time", 0) >= min_spacing:
                thinned.append(b)
        removed = len(beats) - len(thinned)
        if removed > 0:
            logger.info(f"🎵 Beat thinning: {len(beats)} → {len(thinned)} beats "
                        f"(removed {removed} too-close beats, min_spacing={min_spacing}s)")
        return thinned

    # ── Beat Classification ───────────────────────────────────────────────────

    def _classify_beats(self, beats: List[Dict]) -> List[Dict]:
        """
        Classify beats strictly by normalized energy.
        Thresholds:
          energy > 0.85 → drop
          energy > 0.60 → strong
          else          → weak
        """
        classified = []
        n_drops = 0
        n_strong = 0
        n_weak = 0

        for b in beats:
            b_time = b.get("time", 0.0)
            energy = b.get("energy", None)

            if energy is None:
                beat_type = "weak"
            elif energy > 0.85:
                beat_type = "drop"
            elif energy > 0.60:
                beat_type = "strong"
            else:
                beat_type = "weak"

            # Targeted logging for drops only
            if beat_type == "drop":
                logger.info(f"⚡ Beat drop detected at {b_time:.2f}s")
                n_drops += 1
            elif beat_type == "strong":
                n_strong += 1
            else:
                n_weak += 1

            classified.append({"time": b_time, "strength": beat_type})

        logger.info(f"🎵 Beat classification complete: {n_drops} drops, "
                    f"{n_strong} strong, {n_weak} weak.")
        return classified

    # ── Scene Retrieval ───────────────────────────────────────────────────────

    def _get_scenes(self, profile_data: Dict) -> List[Dict]:
        """
        Pull the best available scene list from profile_data in priority order.
        AI plans and reconstructed timelines take precedence over raw shots.

        NOTE: The unified_intelligence fallback recovery path promotes segments
        from 'editing_plan.segments' → 'edited_segments' and 'editing_timeline'.
        Both keys MUST be checked here or the bridge will silently have no scenes.
        """
        def _to_scenes(data):
            """Extract {start, end, clip_id, ...} dicts, filtering degenerate entries."""
            out = []
            for s in data:
                if not isinstance(s, dict):
                    continue
                start = _safe_float(s.get("start"))
                end   = _safe_float(s.get("end"))
                if end > start:          # skip zero-duration entries
                    # [MULTI_CLIP FIX] Preserve clip_id so MusicDrivenEditor routes
                    # each scene to the correct source clip in the final render.
                    out.append({
                        "start":   start,
                        "end":     end,
                        "clip_id": s.get("clip_id", 0),
                        "role":    s.get("role", ""),
                    })
            return out

        # Priority 1: AI Editing Plan (from Master/Elite Intelligence)
        ep = profile_data.get("editing_plan")
        if isinstance(ep, dict) and ep.get("segments"):
            scenes = _to_scenes(ep["segments"])
            if scenes:
                logger.debug(f"[GET_SCENES_DEBUG] Using editing_plan.segments ({len(scenes)} scenes)")
                return scenes

        # Priority 2: Promoted segment keys (unified_intelligence fallback recovery)
        # The master analysis recovery path moves segments to 'edited_segments' and
        # 'editing_timeline' — check these BEFORE reconstructed_timeline because
        # the timeline reconstructor hasn't run yet when the bridge executes.
        for key in ("edited_segments", "editing_timeline"):
            data = profile_data.get(key)
            if data and isinstance(data, list):
                scenes = _to_scenes(data)
                if scenes:
                    logger.debug(f"[GET_SCENES_DEBUG] Using {key} ({len(scenes)} scenes)")
                    return scenes

        # Priority 3: Reconstructed Timeline or Selected Shots (Moment Authority path)
        for key in ("reconstructed_timeline", "selected_shots"):
            data = profile_data.get(key)
            if data and isinstance(data, list) and len(data) >= 2:
                scenes = _to_scenes(data)
                if scenes:
                    logger.debug(f"[GET_SCENES_DEBUG] Using {key} ({len(scenes)} scenes)")
                    return scenes

        # Priority 4: Raw Shots (Detector output)
        data = profile_data.get("shots")
        if data and isinstance(data, list) and len(data) >= 2:
            scenes = _to_scenes(data)
            if scenes:
                logger.debug(f"[GET_SCENES_DEBUG] Using raw shots ({len(scenes)} scenes)")
                return scenes

        # Priority 5: Synthesize scenes from candidate_moments (beat-proximity moments)
        # These always exist (29 of them) and cover the full source duration.
        moments = profile_data.get("candidate_moments", []) or profile_data.get("fused_moments", [])
        if moments and isinstance(moments, list):
            _dur_hint  = profile_data.get("video_duration", 20.0)
            _span      = _dur_hint / max(len(moments), 1)  # even-spread span per moment
            _clip_span = max(1.0, min(3.5, _span))          # clamp to 1-3.5s per scene
            scenes = []
            for m in moments:
                if not isinstance(m, dict):
                    continue
                t = _safe_float(m.get("timestamp", m.get("time", 0.0)))
                scenes.append({"start": round(t, 3), "end": round(t + _clip_span, 3)})
            if scenes:
                logger.info(f"[GET_SCENES_DEBUG] Synthesized {len(scenes)} scenes from candidate_moments (last-resort)")
                return scenes

        logger.warning("[GET_SCENES_DEBUG] No scenes found in ANY key — beat timeline will be skipped.")
        return []

    # ── Energy → Color Mode ───────────────────────────────────────────────────

    @staticmethod
    def _energy_to_color_mode(strength: str) -> str:
        """Map beat energy to a per-segment color grade preset."""
        return {
            "drop":   "vibrant",      # massive burst  — ultra-punchy
            "strong": "vibrant",      # downbeat       — punchy saturation
            "medium": "fashion",      # standard flow  — warm cinematic
            "weak":   "cinematic",    # breathing room — cool, moody
        }.get(strength, "fashion")

    @staticmethod
    def _strength_to_transition(strength: str) -> str:
        """
        Strict mapping of beat strength to a video transition type.
        """
        transition_map = {
            "drop": "flash",
            "strong": "fade",
            "weak": "cut"
        }
        return transition_map.get(strength, "cut")

    # ── VO Pacing ─────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_vo_target_duration(profile_data: Dict) -> Optional[float]:
        """
        Derive ideal video duration from voiceover script length.
        Formula: duration = word_count / 2.7  (natural narration pace)
        Returns None if no script is found.
        """
        mon_data = profile_data.get("monetization_data", {})
        script = (
            mon_data.get("editorial_script")
            or profile_data.get("editorial_script")
            or ""
        )
        if not script or len(script.strip()) < 5:
            return None
        word_count = len(script.strip().split())
        duration = round(word_count / VO_WORDS_PER_SEC, 2)
        # Clamp to a sensible range for short-form video (Reels/Shorts)
        duration = max(8.0, min(60.0, duration))
        logger.info(f"🎙️ VO pacing hint: {word_count} words → target_duration={duration}s "
                    f"(formula: words / {VO_WORDS_PER_SEC})")
        return duration

    # ── Beat-Driven Timeline → Segment Format ─────────────────────────────────

    def _convert_to_segments(self, beat_timeline: List[Dict], classified_beats: List[Dict] = None) -> List[Dict]:
        """
        Convert MusicDrivenEditor's beat_timeline to the standard segment format.
        Transition type is now derived from beat strength (not insert_transitions random choice).

        Strength → Transition mapping:
          drop   → glitch_pop  (scene rupture)
          strong → whip_pan    (fast swipe)
          medium → blur_cut    (smooth blur)
          weak   → cut         (hard cut)

        Also injects beat_interval per segment so build_transition_graph can
        compute a beat-synced transition duration instead of using the hardcoded 0.2s.
        """
        # Build a lookup: beat index → interval to the next beat
        _beat_intervals: List[float] = []
        if classified_beats:
            for _bi in range(len(classified_beats)):
                if _bi < len(classified_beats) - 1:
                    _interval = classified_beats[_bi + 1]["time"] - classified_beats[_bi]["time"]
                    _beat_intervals.append(max(0.1, float(_interval)))
                else:
                    _beat_intervals.append(0.95)  # last beat — use typical interval

        segments = []
        for idx, block in enumerate(beat_timeline):
            start = _safe_float(block.get("scene_start"))
            end   = _safe_float(block.get("scene_end"))
            dur   = end - start
            if dur < 0.3:
                continue  # skip degenerate segments

            # Strength from beat classification (tagged in run())
            strength   = block.get("_beat_strength", "weak")
            transition = self._strength_to_transition(strength)
            color_mode = self._energy_to_color_mode(strength)

            importance = _safe_float(block.get("scene_ref", {}).get("importance"), 0.5)
            is_drop    = (strength == "drop")
            
            # --- Contiguity Guard ---
            # If this segment ends where the NEXT would start, force clean transitions
            # so we don't flash/blur between contiguous footage.
            is_contiguous = False
            if idx < len(beat_timeline) - 1:
                nxt = beat_timeline[idx + 1]
                if abs(end - _safe_float(nxt.get("scene_start"))) < 0.08:
                    is_contiguous = True

            if is_contiguous:
                transition = "hard_cut"

            # [BEAT-SYNC] Pull beat_interval for this block's position
            beat_interval = _beat_intervals[idx] if idx < len(_beat_intervals) else 0.95

            segment = {
                "clip_id":       block.get("scene_ref", {}).get("clip_id", 0),  # [MULTI_CLIP FIX]
                "start":         round(start, 3),
                "end":           round(end,   3),
                "style":         transition,
                "transition":    transition,
                "color_mode":    color_mode,
                "importance":    round(importance, 3),
                "is_drop":       is_drop,
                "beat_interval": round(beat_interval, 3),  # consumed by build_transition_graph
                "beat_offset":   0.033,                    # 1-frame pre-beat cut offset
                "reason":        "bgm_beat_driven",
            }
            segments.append(segment)
            logger.info(f"🎞 Transition assigned: {transition} | beat_interval={beat_interval:.3f}s")

        return segments

    # ── Beat Proximity Scoring ────────────────────────────────────────────────

    def _compute_beat_match_scores(self, moments: List[Dict], beat_times: List[float]) -> List[Dict]:
        """
        Enriches candidate moments with a `beat_match_score` based on their proximity
        to the nearest audio transient. Used by downstream modules to prioritize cuts
        that land on beats.
        """
        if not beat_times:
            for m in moments:
                m["beat_match_score"] = 0.0
            return moments

        for m in moments:
            m_time = _safe_float(m.get("timestamp", m.get("time", 0.0)))
            min_dist = min(abs(m_time - bt) for bt in beat_times)
            # Inverse distance scoring: 0.0 distance = 1.0 score
            m["beat_match_score"] = round(1.0 / (1.0 + min_dist), 4)
            # Update boolean flag if very close (< 0.1s)
            if min_dist <= 0.1:
                m["beat_aligned"] = True
                
        return moments

    # ── Main Entry Point ──────────────────────────────────────────────────────

    def run(
        self,
        profile_data: Dict,
        music_path: Optional[str] = None,
        job_dir: Optional[str] = None,
    ) -> Dict:
        """
        Enrich profile_data with:
          - bgm_beats           : thinned beat timestamps [float]
          - bgm_classified_beats: [{time, strength}]
          - beat_timeline_segments: converted to standard segment format
          - vo_pacing_hints     : {target_duration: float}
          - beat_data_bgm       : {"beats": [...]} for RhythmTimelineBuilder

        Returns profile_data (mutated in-place and also returned for chaining).
        """
        logger.info("🎬 [CreativeEditorBridge] Starting enrichment pass...")

        profile_data.setdefault("bgm_classified_beats", [])

        # ── Step D: VO Pacing Hint (independent of music) ──────────────────
        vo_target = self._compute_vo_target_duration(profile_data)
        profile_data["vo_pacing_hints"] = {
            "target_duration": vo_target,
            "words_per_sec":   VO_WORDS_PER_SEC,
        }

        # ── Steps A-C: BGM Beat Analysis (only when a music track exists) ──
        import os
        if not music_path or not os.path.exists(music_path):
            logger.info("🎵 No BGM track available — generating fallback metronome grid.")
            scenes = self._get_scenes(profile_data)
            duration_hint = _safe_float(
                profile_data.get("video_duration")
                or (scenes[-1]["end"] if scenes else 0.0),
                0.0,
            )

            if scenes and duration_hint > 0:
                spacing = _safe_float(
                    profile_data.get("fallback_beat_spacing"),
                    DEFAULT_FALLBACK_BEAT_SPACING,
                )
                spacing = max(MIN_CUT_SPACING, min(1.2, spacing))

                # Synthetic metronome beats
                beats = []
                t = 0.0
                while t <= duration_hint:
                    beats.append({"time": round(t, 3), "energy": 0.65})
                    t += spacing

                classified_beats = self._classify_beats(beats)
                profile_data["bgm_beats"] = [b["time"] for b in beats]
                profile_data["bgm_classified_beats"] = classified_beats
                profile_data["bgm_drops"] = []
                profile_data["beat_data_bgm"] = {
                    "beats": beats,
                    "drops": [],
                    "tempo": round(60.0 / spacing, 1),
                }

                # Enrich candidate moments with beat proximity
                candidate_moments = profile_data.get("candidate_moments", [])
                if candidate_moments:
                    logger.info("🎵 Fallback beat match scoring for candidate moments.")
                    profile_data["candidate_moments"] = self._compute_beat_match_scores(
                        candidate_moments,
                        [b["time"] for b in beats],
                    )

                # Map scenes to synthetic beats (round-robin strength)
                segments = []
                for idx, s in enumerate(scenes):
                    strength = classified_beats[idx % len(classified_beats)]["strength"] if classified_beats else "weak"
                    transition = self._strength_to_transition(strength)
                    color_mode = self._energy_to_color_mode(strength)
                    segment = {
                        "clip_id": int(s.get("clip_id", 0)),  # [MULTI_CLIP FIX] preserve source clip
                        "start": round(_safe_float(s.get("start")), 3),
                        "end": round(_safe_float(s.get("end")), 3),
                        "style": transition,
                        "transition": transition,
                        "color_mode": color_mode,
                        "importance": 0.5,
                        "is_drop": False,
                        "beat_interval": round(spacing, 3),
                        "beat_offset": 0.033,
                        "reason": "bgm_fallback_metronome",
                    }
                    if segment["end"] - segment["start"] >= 0.3:
                        segments.append(segment)

                if len(segments) >= MIN_BEAT_DRIVEN_SEGMENTS:
                    profile_data["beat_timeline_segments"] = segments
                    logger.info(
                        f"✅ Fallback beat timeline ready: {len(segments)} segments "
                        f"(spacing={spacing:.2f}s)."
                    )
                else:
                    logger.warning(
                        f"⚠️ Fallback beat timeline skipped (only {len(segments)} usable segments)."
                    )
            else:
                logger.info("🎵 No scenes available for fallback mapping — keeping VO hints only.")
            return profile_data

        # Step A: Raw beat detection on BGM
        beat_data_bgm = self._analyze_bgm_beats(music_path)
        raw_beats = beat_data_bgm.get("beats", [])
        if not raw_beats:
            logger.warning("⚠️ BGM beat analysis yielded no beats — skipping beat timeline.")
            return profile_data

        # Step B: Thin the grid
        thinned_beats = self._thin_beats(raw_beats, min_spacing=MIN_CUT_SPACING)
        beat_data_bgm["beats"] = thinned_beats
        profile_data["beat_data_bgm"] = beat_data_bgm  # consumed by RhythmTimelineBuilder

        # Classify thinned beats — drop_times are no longer passed separately,
        # classification is now based purely on the energy array.
        classified_beats = self._classify_beats(thinned_beats)
        profile_data["bgm_beats"]            = [b["time"] for b in thinned_beats]
        profile_data["bgm_classified_beats"] = classified_beats
        profile_data["bgm_drops"]            = [b["time"] for b in classified_beats if b["strength"] == "drop"]

        # Step C: Retrieve scenes
        scenes = self._get_scenes(profile_data)
        if not scenes:
            logger.warning("⚠️ No scenes available for beat-driven mapping — skipping.")
            return profile_data

        # Enrich candidate moments with beat match scores before they go to TimelineReconstructor
        candidate_moments = profile_data.get("candidate_moments", [])
        if candidate_moments and thinned_beats:
            logger.info("🎵 Computing beat match scores for candidate moments...")
            beat_times = [b["time"] for b in thinned_beats]
            profile_data["candidate_moments"] = self._compute_beat_match_scores(candidate_moments, beat_times)

        # Step C cont.: Score + map scenes to beats via MusicDrivenEditor
        try:
            from Compiler_Modules.music_driven_editor import MusicDrivenEditor
            mde = MusicDrivenEditor()

            # Score scenes (uses motion_events if present in profile_data)
            motion_events = profile_data.get("motion_scores", [])
            scored_scenes = mde.score_scenes(scenes, motion_events)

            # ── Pull psycho-acoustic vibe from beat analysis ──────────────────
            bpm        = float(beat_data_bgm.get("tempo", 120.0) or 120.0)
            avg_energy = float(beat_data_bgm.get("avg_energy", 0.5) or 0.5)
            vibe       = beat_data_bgm.get("vibe", "groove")
            profile_data["bgm_vibe"]        = vibe
            profile_data["bgm_tempo"]       = bpm
            profile_data["bgm_avg_energy"]  = avg_energy
            logger.info(f"🎧 [CEB] BGM vibe={vibe} bpm={bpm:.0f} avg_energy={avg_energy:.2f}")

            # Map scenes to thinned classified beats (vibe-aware)
            beat_timeline = mde.map_scenes_to_beats(
                scored_scenes, classified_beats,
                bpm=bpm, avg_energy=avg_energy,
            )

            # Tag each block with its beat strength so _convert_to_segments can use it.
            for i, block in enumerate(beat_timeline):
                if i < len(classified_beats):
                    block["_beat_strength"] = classified_beats[i]["strength"]
                else:
                    block["_beat_strength"] = "weak"

            logger.info(f"🎵 MusicDrivenEditor mapped {len(beat_timeline)} beat-scene blocks.")

        except Exception as mde_err:
            logger.warning(f"⚠️ MusicDrivenEditor failed: {mde_err}")
            return profile_data

        # Step E: Convert to standard segment format
        segments = self._convert_to_segments(beat_timeline, classified_beats=classified_beats)

        if len(segments) >= MIN_BEAT_DRIVEN_SEGMENTS:
            profile_data["beat_timeline_segments"] = segments
            logger.info(
                f"✅ [CreativeEditorBridge] Beat-driven timeline ready: "
                f"{len(segments)} segments, "
                f"{sum(s['end']-s['start'] for s in segments):.1f}s total duration."
            )
        else:
            logger.warning(
                f"⚠️ Only {len(segments)} beat segments generated (need ≥ {MIN_BEAT_DRIVEN_SEGMENTS}). "
                "Skipping beat-driven timeline override."
            )

        logger.info("🎬 [CreativeEditorBridge] Enrichment pass complete.")
        return profile_data
