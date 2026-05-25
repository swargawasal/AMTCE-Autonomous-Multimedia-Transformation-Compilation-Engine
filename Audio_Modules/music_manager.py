import os
import glob
import random
import logging
import json
from typing import List, Tuple, Dict
import subprocess
from pathlib import Path

logger = logging.getLogger("music_manager")

class ContinuousMusicManager:
    """
    Manages a continuous music timeline for a batch of videos.
    State is specific to an instance (one compilation job).
    """
    def __init__(self, music_dir: str = "music"):
        self.music_dir = music_dir
        self.usage_file = "The_json/music_usage.json"
        
        # Load Usage State
        self.usage_data = self._load_usage()
        
        self.playlist: List[Path] = self._load_playlist()
        
        # State
        self.current_track_index = 0
        # Per-Track Cursor State (The "Bookmark" for each song)
        # { Path("music/song1.mp3"): 15.0 }
        self.track_offsets: Dict[Path, float] = {p: 0.0 for p in self.playlist}
        
        # [NEW] Recent usage history to prevent overlaps (capped at 5)
        self.recent_history = []
        
        # OPTIMIZED: Sort by usage first (least used at the top), then shuffle within usage tiers
        # This fulfills the "least used" requirement while maintaining variety
        if self.playlist:
            # Group by usage count
            usage_tiers = {}
            for p in self.playlist:
                count = self.usage_data.get(p.name, 0)
                if count not in usage_tiers:
                    usage_tiers[count] = []
                usage_tiers[count].append(p)
            
            # Sort tiers and shuffle each tier independently
            sorted_playlist = []
            for count in sorted(usage_tiers.keys()):
                tier_files = usage_tiers[count]
                random.shuffle(tier_files)
                sorted_playlist.extend(tier_files)
            
            self.playlist = sorted_playlist
            logger.info(f"🎵 Music Manager organized by usage. Least used tracks prioritized.")
            
            # Re-init offsets after re-ordering
            self.track_offsets = {p: 0.0 for p in self.playlist}
        
        self.track_durations: Dict[Path, float] = {} # Cache

    def _load_usage(self) -> Dict[str, int]:
        try:
            if os.path.exists(self.usage_file):
                with open(self.usage_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load music usage: {e}")
        return {}

    def _save_usage(self):
        try:
            os.makedirs(os.path.dirname(self.usage_file), exist_ok=True)
            with open(self.usage_file, 'w', encoding='utf-8') as f:
                json.dump(self.usage_data, f, indent=2)
        except Exception as e:
            logger.warning(f"⚠️ Failed to save music usage: {e}")

    def _load_playlist(self) -> List[Path]:
        """Loads and validates tracks from the designated music directory or pool."""
        base = Path(self.music_dir).resolve()
        
        # ── REDIRECTION: Divert Original_audio to 'active' pool ────────────────
        if base.name == "Original_audio":
            search_target = base / "active"
            search_target.mkdir(parents=True, exist_ok=True)
            # Safety Assertion (Dev/Debug Mode)
            assert search_target.name == "active", f"❌ MusicManager redirection failure! Target must be 'active', got: {search_target.name}"
        else:
            search_target = base

        if not search_target.exists():
            logger.warning(f"⚠️ Music directory path not found: {search_target}")
            return []

        # Strict Non-Recursive Glob (Prevents cooldown leakage)
        files = list(search_target.glob("*.mp3")) + list(search_target.glob("*.wav"))
        
        # Filter out corrupted or suspiciously small files (< 1KB)
        valid_files: List[Path] = []
        for f in files:
            try:
                if f.exists() and f.stat().st_size > 1024:
                    valid_files.append(f)
                else:
                    logger.warning(f"⚠️ Skipping corrupted or empty track: {f.name}")
            except Exception as e:
                logger.warning(f"⚠️ Could not access file {f.name}: {e}")
        
        # Log resolution
        if valid_files:
            logger.info(f"🎵 Music Manager loaded {len(valid_files)} tracks from '{search_target}':")
            for f in valid_files[:3]:
                 logger.info(f"    └─ {f.name}")
            if len(valid_files) > 3:
                logger.info(f"    └─ ... and {len(valid_files)-3} more.")
        else:
            logger.warning(f"⚠️ Active pool empty at {search_target} — BGM selection will be skipped (using source audio).")
            
        return valid_files

    def _get_audio_bpm(self, audio_path: str) -> float:
        """
        Estimate BPM from an audio file using BeatEngine.
        Returns BPM as float, or 0.0 if analysis fails.
        """
        try:
            from Audio_Modules.beat_engine import engine as _beat_engine
            # Ensure string for subprocess/I/O
            path_str = str(audio_path)
            beats = _beat_engine.analyze_beats(path_str)
            if len(beats) < 4:
                return 0.0
            # Calculate average inter-beat interval → BPM
            intervals = [beats[i+1] - beats[i] for i in range(len(beats)-1)]
            avg_interval = sum(intervals) / len(intervals)
            if avg_interval <= 0:
                return 0.0
            bpm = 60.0 / avg_interval
            
            # Use .name if it's a Path, else basename
            display_name = getattr(audio_path, 'name', os.path.basename(path_str))
            logger.info(f"🥁 [BPM] '{display_name}' → {bpm:.1f} BPM ({len(beats)} beats)")
            return round(bpm, 1)
        except Exception as _e:
            display_name = getattr(audio_path, 'name', os.path.basename(str(audio_path)))
            logger.warning(f"⚠️ [BPM] Analysis failed for {display_name}: {_e}")
            return 0.0

    def get_beat_matched_track(self, clip_audio_path: str) -> str | None:
        """
        PRIMARY MUSIC SELECTION:
        Analyzes the BPM of the clip's extracted audio, then finds the BGM track
        from the playlist whose BPM is closest to the clip's BPM.
        This ensures music sync with the visual's natural energy rhythm.

        Args:
            clip_audio_path: Path to the clip's extracted audio (from Original_audio/).

        Returns:
            Absolute path to best-matching BGM track, or None.
        """
        if not self.playlist or not clip_audio_path:
            return None

        clip_bpm = self._get_audio_bpm(clip_audio_path)
        if clip_bpm < 1.0:
            logger.warning("⚠️ [BEAT_MATCH] Clip BPM analysis returned 0 — falling back to genre match.")
            return None

        logger.info(f"🎯 [BEAT_MATCH] Clip BPM: {clip_bpm:.1f} — searching for matching BGM track...")

        best_track = None
        best_delta = float("inf")
        best_bpm = 0.0

        for track_path in self.playlist:
            track_bpm = self._get_audio_bpm(track_path)
            if track_bpm < 1.0:
                continue

            # Allow half-time and double-time matches (120 BPM clip matches 60 or 240 BGM)
            deltas = [
                abs(track_bpm - clip_bpm),
                abs(track_bpm - clip_bpm * 2),
                abs(track_bpm - clip_bpm / 2),
            ]
            delta = min(deltas)

            if delta < best_delta:
                best_delta = delta
                best_track = track_path
                best_bpm = track_bpm

        # Accept if within ±15 BPM tolerance (or doubled/halved)
        if best_track and best_delta <= 15.0:
            track_name = best_track.name
            logger.info(
                f"✅ [BEAT_MATCH] Best match: '{track_name}' "
                f"({best_bpm:.1f} BPM, delta={best_delta:.1f}) for clip BPM {clip_bpm:.1f}"
            )
            self.usage_data[track_name] = self.usage_data.get(track_name, 0) + 1
            self._save_usage()
            return str(best_track)
        elif best_track:
            track_name = best_track.name
            logger.info(
                f"⚠️ [BEAT_MATCH] Closest track '{track_name}' "
                f"delta={best_delta:.1f} BPM — too far, relaxing to nearest anyway."
            )
            # Accept the closest match even if outside tolerance (better than random)
            self.usage_data[track_name] = self.usage_data.get(track_name, 0) + 1
            self._save_usage()
            return str(best_track)

        return None

    def get_best_match(self, profile_data: Dict) -> str:
        """
        Intelligent Music Selector — mode controlled by MUSIC_SELECTION_MODE env var:

        beat_match  (default): BPM-match clip audio → genre → round-robin
        genre_match           : keyword/genre only   → round-robin
        """
        if not self.playlist:
            logger.error("❌ No active BGM available — system running without music!")
            return None

        # ── AUDIO POOL SELECTION (Original_audio) ─────────────────────────────
        if "Original_audio" in self.music_dir:
            try:
                from Audio_Modules.audio_pool_manager import pool_manager

                # BPM/energy of the source clip (used to score pool candidates)
                input_audio = profile_data.get("extracted_audio_path")
                v_bpm = 0.0
                v_energy = 0.5  # default middle energy

                if input_audio:
                    fname = os.path.basename(input_audio)
                    # [FIX] Use the schema-aware helper — metadata has a 'files' sub-key
                    meta = pool_manager._get_file_metadata(fname)
                    if meta:
                        v_bpm    = meta.get("bpm", 0.0)
                        v_energy = meta.get("energy", 0.5)
                        logger.info(
                            f"🎯 [POOL] Matching using indexed metadata: "
                            f"{fname} ({v_bpm} BPM, {v_energy:.2f} Energy)"
                        )
                    else:
                        # File not yet indexed — run live analysis
                        try:
                            from Audio_Modules.beat_engine import BeatEngine
                            analysis = BeatEngine().analyze_beats_with_drops(input_audio)
                            beats = analysis.get("beats", [])
                            if beats:
                                v_energy = sum(b["energy"] for b in beats) / len(beats)
                                if len(beats) >= 4:
                                    ivs = [beats[i+1]["time"] - beats[i]["time"] for i in range(len(beats)-1)]
                                    v_bpm = round(60.0 / (sum(ivs)/len(ivs)), 1)
                                logger.info(
                                    f"🎯 [POOL] Live analysis for {fname}: "
                                    f"{v_bpm} BPM, {v_energy:.2f} Energy"
                                )
                        except Exception as _ae:
                            logger.debug(f"[POOL] Live BPM analysis failed: {_ae}")

                # Use the AudioPoolManager to select the smartest alternative track 
                # from the active pool, excluding the input_audio so we actively 
                # discover other trending sounds!
                selected_path = pool_manager.select_best_audio(
                    video_bpm=v_bpm,
                    video_energy=v_energy,
                    exclude_path=input_audio,  # Do not pick its own audio!
                    recent_history=self.recent_history,
                    # Pass content category so Gemini-enriched tracks score correctly.
                    # Falls back to "" (neutral, identical to old behaviour) if absent.
                    content_category=(
                        profile_data.get("hybrid_profile", {}).get("category", "")
                        or profile_data.get("niche", "")
                        or profile_data.get("category", "")
                    ),
                )

                
                if selected_path:
                    s_name = os.path.basename(selected_path)
                    self.recent_history.append(s_name)
                    if len(self.recent_history) > 5:
                        self.recent_history.pop(0)
                    
                    logger.info(f"🏆 [POOL] Selected best audio: {s_name} (History size: {len(self.recent_history)})")
                    return selected_path
            except Exception as pe:
                logger.error(f"❌ Audio Pool selection failed, falling back: {pe}")

        # ── NORMAL SELECTION (music/ or Fallback) ─────────────────────────────
        _mode = os.getenv("MUSIC_SELECTION_MODE", "beat_match").strip().lower()
        logger.info(f"🎵 [MUSIC_ENGINE] Mode: {_mode}")

        # ── ROUND ROBIN (skip all intelligence) ───────────────────────────────
        if _mode == "round_robin":
            track_path = self.get_next_track_path() # Returns str
            if track_path:
                track_name = os.path.basename(track_path)
                self.usage_data[track_name] = self.usage_data.get(track_name, 0) + 1
                self._save_usage()
                self.current_track_index = (self.current_track_index + 1) % len(self.playlist)
                logger.info(f"🔄 [ROUND_ROBIN] Selected: {track_name}")
            return track_path

        # ── Tier 0: Beat-aware selection (beat_match mode only) ───────────────
        if _mode == "beat_match":
            _clip_title = (
                profile_data.get("title") or
                profile_data.get("job_id") or
                ""
            )
            _orig_audio_dir = "Original_audio"
            _clip_audio_path = None

            if _clip_title:
                _base = os.path.splitext(_clip_title)[0]
                _candidates = [
                    os.path.join(_orig_audio_dir, f"{_base}.mp3"),
                    os.path.join(_orig_audio_dir, f"{_base}.wav"),
                ]
                if os.path.exists(_orig_audio_dir):
                    for _fname in os.listdir(_orig_audio_dir):
                        _fbase = os.path.splitext(_fname)[0]
                        if _fbase.lower() == _base.lower() or _base.lower().startswith(_fbase.lower()):
                            _candidates.insert(0, os.path.join(_orig_audio_dir, _fname))
                            break

                for _cand in _candidates:
                    if os.path.exists(_cand) and os.path.getsize(_cand) > 1024:
                        _clip_audio_path = _cand
                        logger.info(f"🎵 [BEAT_MATCH] Using extracted audio: {_cand}")
                        break

            if not _clip_audio_path:
                _clip_audio_path = profile_data.get("extracted_audio_path")

            if _clip_audio_path:
                _beat_match = self.get_beat_matched_track(_clip_audio_path)
                if _beat_match:
                    return _beat_match

        # ── Tier 1: Keyword/genre match ───────────────────────────────────────
        try:
            from Audio_Modules.music_intelligence import classify_music

            keywords = []
            if profile_data.get('trend_text'): keywords.append(profile_data['trend_text'].lower())
            if profile_data.get('title'): keywords.append(profile_data['title'].lower())

            target_genre = "neutral"
            if any(k in " ".join(keywords) for k in ["viral", "phonk", "bass", "gym", "workout"]):
                target_genre = "mass"
            elif any(k in " ".join(keywords) for k in ["lofi", "chill", "relax", "aesthetic"]):
                target_genre = "lofi"
            elif any(k in " ".join(keywords) for k in ["luxury", "slow", "moody", "noir"]):
                target_genre = "romantic"

            for track_path in self.playlist:
                path_str = str(track_path)
                genre, conf = classify_music(path_str)
                if genre == target_genre and conf > 0.6:
                    track_name = track_path.name
                    logger.info(f"🎯 Music Match Found! Genre: {genre} Path: {track_name}")
                    self.usage_data[track_name] = self.usage_data.get(track_name, 0) + 1
                    self._save_usage()
                    return path_str

        except Exception as e:
            logger.warning(f"⚠️ Music matching intelligence failed: {e}")

        # ── Tier 2: Round-robin fallback ──────────────────────────────────────
        track_path = self.get_next_track_path()
        if track_path:
            track_name = os.path.basename(track_path)
            self.usage_data[track_name] = self.usage_data.get(track_name, 0) + 1
            self._save_usage()
            self.current_track_index = (self.current_track_index + 1) % len(self.playlist)

        return track_path

    def get_next_track_path(self) -> str:
        """Returns the string path of the next track to be played (for Beat Analysis)."""
        if not self.playlist: return None
        return str(self.playlist[self.current_track_index % len(self.playlist)])

    def _get_duration(self, path: Path) -> float:
        """Get duration with caching"""
        if path in self.track_durations:
            return self.track_durations[path]
            
        path_str = str(path)
        try:
             cmd = [
                 "ffprobe", "-v", "error", "-show_entries", "format=duration", 
                 "-of", "default=noprint_wrappers=1:nokey=1", path_str
             ]
             res = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
             dur = float(res.decode().strip())
             self.track_durations[path] = dur
             return dur
        except Exception as e:
            logger.warning(f"Failed to get duration for {path.name}: {e}")
            return 30.0 # Safety default

    def allocate_music(self, needed_duration: float) -> List[Dict]:
        """
        Allocates music in a ROUND-ROBIN fashion with STATE PERSISTENCE.
        Clip 1 -> Track A (0-15s)
        Clip 2 -> Track B (0-15s)
        Clip 3 -> Track A (15-30s) <- Continues where it left off!
        """
        if not self.playlist:
            return []

        # 1. Select Track (Round Robin)
        track_path = self.playlist[self.current_track_index]
        path_str = str(track_path.absolute())
        
        # 2. Get Saved State for THIS track
        current_offset = self.track_offsets.get(track_path, 0.0)
        total_track_dur = self._get_duration(track_path)
        
        # 3. Calculate Segment
        start_time = current_offset
        if (start_time + needed_duration) > total_track_dur:
            start_time = 0.0 # Reset to beginning of song
            logger.info(f"🔄 Track {track_path.name} looped/reset.")
            
        # 4. Update State for THIS track
        self.track_offsets[track_path] = start_time + needed_duration
        
        # 5. Move Global Cursor to NEXT track (Round Robin)
        self.current_track_index = (self.current_track_index + 1) % len(self.playlist)
        
        # 6. Increment Usage Count
        track_name = track_path.name
        self.usage_data[track_name] = self.usage_data.get(track_name, 0) + 1
        self._save_usage()
        
        logger.info(f"🎵 Allocated [RR]: {track_name} ({start_time:.1f}-{start_time+needed_duration:.1f}s) | Usage: {self.usage_data[track_name]}")
        
        return [{
            "path": path_str,
            "start": start_time,
            "duration": needed_duration
        }]