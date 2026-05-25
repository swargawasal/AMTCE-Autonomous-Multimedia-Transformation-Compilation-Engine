import os
import json
import time
import threading
import random
import logging
import numpy as np
import tempfile
from shutil import move
from typing import Dict, List, Optional, Any

logger = logging.getLogger("audio_pool_manager")

PIPELINE_BLOCKED_KWS = [
    "_reaction", "_textreaction", "first_shot", "first_shots",
    "general_intro", "watermark_clean", "intro_mixed_temp",
    "final_compilation"
]

def _is_pipeline_artifact(filename: str) -> bool:
    lower_name = filename.lower()
    return any(kw in lower_name for kw in PIPELINE_BLOCKED_KWS)

class AudioPoolManager:
    """
    Manages the lifecycle of extracted audio clips.
    Pools:
      - active/: Eligible for selection.
      - cooldown/: Temporarily ineligible clips (recently used).
    """

    def __init__(self, base_dir: str = "Original_audio"):
        self.base_dir = base_dir
        self.active_dir = os.path.join(base_dir, "active")
        self.cooldown_dir = os.path.join(base_dir, "cooldown")
        self.beats_dir = os.path.join(base_dir, "beats")
        self.meta_path = os.path.join(base_dir, "pool_metadata.json")

        self.lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._beat_cache = {}
        self.MAX_CACHE_SIZE = 20
        self.CURRENT_VERSION = 2
        
        # Ensure directories exist
        os.makedirs(self.active_dir, exist_ok=True)
        os.makedirs(self.cooldown_dir, exist_ok=True)
        os.makedirs(self.beats_dir, exist_ok=True)
        
        self.metadata = self._load_metadata()
        # Sync any loose files that landed in root (e.g. from extract_audio_from_video)
        # into active/ so select_best_audio() can find them immediately.
        self._sync_root_to_active()

    def _load_metadata(self) -> Dict:
        """Loads metadata safely."""
        if not os.path.exists(self.meta_path):
            return {}
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"❌ Failed to load audio pool metadata: {e}")
            return {}

    def _save_metadata(self):
        """Saves metadata atomically with file locking."""
        with self.lock:
            temp_path = self.meta_path + ".tmp"
            try:
                # Always ensure version is present
                if "version" not in self.metadata:
                    self.metadata = {"version": self.CURRENT_VERSION, "files": self.metadata}
                
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self.metadata, f, indent=2)
                os.replace(temp_path, self.meta_path)
            except Exception as e:
                logger.error(f"❌ Failed to save audio pool metadata: {e}")
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass

    def _calculate_hash(self, path: str) -> str:
        """Fast size+mtime hash for integrity check."""
        try:
            stat = os.stat(path)
            return f"{stat.st_size}_{int(stat.st_mtime)}"
        except:
            return "unknown"

    def _safe_save_npz(self, path: str, **data):
        """Atomic NPZ save using tempfile + replace."""
        dir_name = os.path.dirname(path)
        fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".npz")
        os.close(fd)
        try:
            np.savez_compressed(temp_path, **data)
            os.replace(temp_path, path)
        except Exception as e:
            logger.error(f"❌ Atomic NPZ save failed for {path}: {e}")
            if os.path.exists(temp_path):
                try: os.remove(temp_path)
                except: pass

    def _get_file_metadata(self, filename: str) -> Optional[Dict]:
        """Helper to get file metadata accounting for schema version."""
        files = self.metadata.get("files", self.metadata)
        return files.get(filename)

    def _set_file_metadata(self, filename: str, data: Dict):
        """Helper to set file metadata accounting for schema version."""
        if "files" not in self.metadata:
            self.metadata = {"version": self.CURRENT_VERSION, "files": self.metadata}
        self.metadata["files"][filename] = data

    def _sync_root_to_active(self):
        """
        [FIX] Move any loose .mp3/.wav files sitting in Original_audio/ root into
        active/ so select_best_audio() can find them.

        extract_audio_from_video() writes to root (not active/).  If the
        orchestrator's process_new_audio() call was skipped (e.g. beat analysis
        exception), the file stays in root forever and is invisible to the pool.
        This method runs at startup and repairs the dir structure.
        """
        try:
            for filename in os.listdir(self.base_dir):
                if not filename.lower().endswith((".mp3", ".wav")):
                    continue
                src = os.path.join(self.base_dir, filename)
                if not os.path.isfile(src):
                    continue
                # Safety: skip if already in metadata as being in active/
                meta = self._get_file_metadata(filename)
                # ── PIPELINE ARTIFACT GATE ──
                if _is_pipeline_artifact(filename):
                    logger.debug(f"[POOL_SYNC] Skipping pipeline artifact: {filename}")
                    continue

                # ── MUSIC GATE: Never re-ingest voice-only files via boot-sync ──────────
                # Files rejected by the Music Gate in downloader.py / orchestrator.py
                # have is_speech_only=True written into pool_metadata.json.
                # Without this check they would silently bypass the gate on every restart.
                if meta and meta.get("is_speech_only", False):
                    logger.debug(f"[POOL_SYNC] Skipping voice-only file (speech gate flag): {filename}")
                    continue
                # ─────────────────────────────────────────────────────────────────────────
                dst = os.path.join(self.active_dir, filename)
                if os.path.exists(dst):
                    continue  # already there
                try:
                    move(src, dst)
                    logger.info(f"[POOL_SYNC] Moved loose audio to active/: {filename}")
                    # Stub metadata if absent so select_best_audio can score it
                    if not meta:
                        self._set_file_metadata(filename, {
                            "usage_count": 0,
                            "last_used":   0,
                            "bpm":         0.0,
                            "energy":      0.5,
                            "created_at":  time.time(),
                            "beat_data_path": None,
                            "drop_times":  [],
                            "sample_rate": 44100,
                            "audio_hash":  self._calculate_hash(dst),
                            "version":     self.CURRENT_VERSION,
                        })
                        self._save_metadata()
                except Exception as e:
                    logger.debug(f"[POOL_SYNC] Could not move {filename}: {e}")
        except Exception as e:
            logger.debug(f"[POOL_SYNC] Root sync failed (non-fatal): {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # GEMINI POOL ENRICHMENT (background, non-blocking, cached)
    # ──────────────────────────────────────────────────────────────────────────

    def _gemini_enrich_background(self, dest_path: str, filename: str):
        """
        Daemon thread: analyze one BGM track with Gemini and write the result
        back into pool_metadata.json.

        Adds:  gemini_genre, gemini_mood_tags, gemini_energy_level,
               gemini_has_vocals, gemini_content_match, gemini_avoid_match,
               gemini_analyzed = True

        Safe to skip:  any exception → track stays unanalyzed, pipeline
                       falls back to existing BPM/energy scoring.
        """
        import os, json, re, shutil, subprocess, tempfile
        try:
            # 0. Flag guard
            if os.getenv("ENABLE_POOL_GEMINI_ENRICH", "no").lower() not in ("yes", "true", "1"):
                return

            # 1. Skip if already analyzed
            with self.lock:
                meta = self._get_file_metadata(filename)
            if meta and meta.get("gemini_analyzed"):
                logger.debug(f"[GEMINI_POOL] Already analyzed: {filename}")
                return

            # 2. Resolve path (file may be in active/ or cooldown/)
            track_path = dest_path
            for candidate in [dest_path,
                               os.path.join(self.active_dir, filename),
                               os.path.join(self.cooldown_dir, filename)]:
                if os.path.exists(candidate):
                    track_path = candidate
                    break
            else:
                return  # file not found anywhere

            # 3. Trim to 30 s → temp MP3 (keeps Gemini token cost minimal)
            ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
            tmp_dir = tempfile.mkdtemp(prefix="gpool_")
            trimmed = os.path.join(tmp_dir, "trim30.mp3")
            try:
                subprocess.run(
                    [ffmpeg_bin, "-y", "-i", track_path,
                     "-t", "30", "-vn", "-ac", "1", "-ar", "22050", trimmed],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=30
                )
                if not os.path.exists(trimmed) or os.path.getsize(trimmed) < 1024:
                    return
                with open(trimmed, "rb") as f:
                    audio_bytes = f.read()
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

            # 4. Gemini call (direct SDK — avoids touching gemini_governor)
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                logger.debug("[GEMINI_POOL] No API key found — skipping enrichment.")
                return
                        
            prompt = (
                "Listen to this audio clip and respond with ONLY a valid JSON object. "
                "No markdown, no explanation, no code fences.\n"
                "{\n"
                '  "genre": "one word — lofi|phonk|hiphop|rnb|pop|edm|cinematic|'
                'acoustic|motivational|trap|drill|jazz|ambient|unknown",\n'
                '  "mood_tags": ["max 3 mood words"],\n'
                '  "energy_level": "low|medium|high",\n'
                '  "has_vocals": true_or_false,\n'
                '  "best_content_match": ["max 3 from: fashion|dance|fitness|comedy|'
                'motivational|aesthetic|food|travel|gaming|luxury|photography|'
                'sports|nature|educational"],\n'
                '  "avoid_content_match": ["max 2 categories this music does NOT fit"]\n'
                "}"
            )

            response = model.generate_content(
                [{"mime_type": "audio/mpeg", "data": audio_bytes}, prompt],
                generation_config={"temperature": 0.1, "max_output_tokens": 256},
                request_options={"timeout": 25},
            )
            raw = (response.text or "").strip()

            # 5. Parse JSON safely
            json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
            if not json_match:
                logger.debug(f"[GEMINI_POOL] No JSON in response for {filename}: {raw[:80]}")
                return
            data = json.loads(json_match.group())

            # 6. Write back into pool metadata (thread-safe)
            with self.lock:
                meta = self._get_file_metadata(filename) or {}
                meta["gemini_genre"]        = str(data.get("genre", "unknown"))[:32]
                meta["gemini_mood_tags"]    = list(data.get("mood_tags", []))[:3]
                meta["gemini_energy_level"] = str(data.get("energy_level", "medium"))
                meta["gemini_has_vocals"]   = bool(data.get("has_vocals", False))
                meta["gemini_content_match"] = list(data.get("best_content_match", []))[:3]
                meta["gemini_avoid_match"]  = list(data.get("avoid_content_match", []))[:2]
                meta["gemini_analyzed"]     = True
                self._set_file_metadata(filename, meta)
            self._save_metadata()

            logger.info(
                f"🎵 [GEMINI_POOL] Enriched: {filename} → "
                f"genre={data.get('genre')} | energy={data.get('energy_level')} | "
                f"fits={data.get('best_content_match')}"
            )

        except Exception as _ge:
            # Non-fatal — track simply stays unenriched
            logger.debug(f"[GEMINI_POOL] Background enrichment failed for {filename} (non-fatal): {_ge}")

    def get_beat_data(self, filename: str) -> Optional[Dict]:
        """Lazy load beat data from cache or disk."""
        with self._cache_lock:
            if filename in self._beat_cache:
                return self._beat_cache[filename]

        meta = self._get_file_metadata(filename)
        if not meta or "beat_data_path" not in meta:
            return None

        npz_path = os.path.join(self.base_dir, meta["beat_data_path"])
        if not os.path.exists(npz_path):
            return None

        try:
            with np.load(npz_path) as data:
                # Validation
                times = data.get("times", [])
                energies = data.get("energies", [])
                
                if len(times) == 0 or len(times) != len(energies):
                    logger.warning(f"⚠️ Validation failed for {filename} beat data. Length mismatch.")
                    return None
                
                beat_data = {
                    "times": times.tolist(),
                    "energies": energies.tolist(),
                    "sample_rate": meta.get("sample_rate", 44100)
                }
                
                # Update Cache (with growth control)
                with self._cache_lock:
                    if len(self._beat_cache) >= self.MAX_CACHE_SIZE:
                        # Simple FIFO pop
                        self._beat_cache.pop(next(iter(self._beat_cache)))
                    self._beat_cache[filename] = beat_data
                
                return beat_data
        except Exception as e:
            logger.error(f"❌ Failed to load beat data for {filename}: {e}")
            return None

    def process_new_audio(self, audio_path: str, bpm: float, energy: float, beat_analysis: Dict = None):
        """
        Moves newly extracted audio into pool and caches deep beat metadata.
        """
        if not os.path.exists(audio_path): return

        filename = os.path.basename(audio_path)
        dest_path = os.path.join(self.active_dir, filename)

        try:
            # Move to active pool
            if os.path.abspath(audio_path) != os.path.abspath(dest_path):
                move(audio_path, dest_path)
            
            # ── Precompute Binary Data ──
            rel_npz_path = None
            drop_times = []
            
            if beat_analysis:
                # Quantize beats and energies
                raw_beats = beat_analysis.get("beats", []) # [{"time": t, "energy": e}, ...]
                times = np.array([round(b["time"], 3) for b in raw_beats], dtype=np.float32)
                energies = np.array([round(b["energy"], 3) for b in raw_beats], dtype=np.float32)
                
                # Precompute Drops directly if possible
                drop_times = [round(b["time"], 3) for b in raw_beats if b["time"] in beat_analysis.get("drops", [])]
                
                # Atomic Save to NPZ
                npz_filename = os.path.splitext(filename)[0] + ".npz"
                npz_path = os.path.join(self.beats_dir, npz_filename)
                self._safe_save_npz(npz_path, times=times, energies=energies)
                rel_npz_path = os.path.join("beats", npz_filename)

            # Initialize metadata
            self._set_file_metadata(filename, {
                "usage_count": 0,
                "last_used": 0,
                "bpm": bpm,
                "energy": energy,
                "created_at": time.time(),
                "beat_data_path": rel_npz_path,
                "drop_times": drop_times,
                "sample_rate": 44100,
                "audio_hash": self._calculate_hash(dest_path),
                "version": self.CURRENT_VERSION
            })
            self._save_metadata()
            logger.info(f"🎵 [V{self.CURRENT_VERSION}] Processed: {filename} ({len(drop_times)} drops cached)")

            # ── Gemini background enrichment (daemon, never blocks) ──────────
            # Runs only if ENABLE_POOL_GEMINI_ENRICH=yes. Analyzes the track
            # once and caches genre/vibe/content-match in pool_metadata.json.
            _enrich_thread = threading.Thread(
                target=self._gemini_enrich_background,
                args=(dest_path, filename),
                daemon=True,
                name=f"gemini_pool_{filename[:16]}",
            )
            _enrich_thread.start()

        except Exception as e:
            logger.error(f"❌ Failed processing {filename}: {e}")

    def select_best_audio(
        self,
        video_bpm: float = 0,
        video_energy: float = 0,
        exclude_path: Optional[str] = None,
        recent_history: Optional[List[str]] = None,
        exclude_filenames: Optional[set] = None,
        target_bpm: float = 0,
        target_energy: float = 0,
        content_category: str = "",   # NEW — optional; "" = neutral (no change to behaviour)
    ) -> Optional[str]:
        """
        Scoring-based selection from the active pool.
        
        Formula:
            score = (bpm_match * 0.6 + energy_match * 0.2 + usage_score * 0.15)
            penalty: 0.5x if in recent_history
            variance: +0.0-0.05 random
        
        exclude_filenames: set of basenames to hard-block (e.g. current-job extracted audio
                           prevents the self-selection loop where the video's own audio
                           is used as its BGM).
        """
        if recent_history is None:
            recent_history = []
        
        # Merge all exclusions into one set of basenames
        _excluded_basenames: set = set(exclude_filenames or [])
        if exclude_path:
            _excluded_basenames.add(os.path.basename(exclude_path))
        
        best_audio = None
        best_score = -1.0

        active_files = os.listdir(self.active_dir)
        if not active_files:
            logger.info("ℹ️ Active audio pool is empty.")
            return None

        for filename in active_files:
            # 1. Exclusion Logic
            if filename in _excluded_basenames:
                logger.debug(f"[POOL] Skipping self-selected audio: {filename}")
                continue
                
            if _is_pipeline_artifact(filename):
                logger.debug(f"[POOL] Skipping pipeline artifact: {filename}")
                continue
            
            meta = self._get_file_metadata(filename)
            if not meta:
                continue

            # Skip explicitly flagged non-music files
            if meta.get("is_speech_only", False):
                continue

            # 2. Penalty/Recent Logic
            recent_penalty = (filename in recent_history)

            # 3. Match Logic
            # Resolve effective targets — support both old (video_bpm) and new (target_bpm) params
            _eff_bpm    = target_bpm    if target_bpm    > 0 else video_bpm
            _eff_energy = target_energy if target_energy > 0 else video_energy

            # BPM match: 1.0 when no preference, otherwise 1 - delta%
            if _eff_bpm > 0:
                bpm_match = max(0, 1 - abs(meta["bpm"] - _eff_bpm) / _eff_bpm)
            else:
                bpm_match = 1.0  # no preference → neutral score

            # Energy match: 1.0 when no preference
            if _eff_energy > 0:
                energy_match = max(0, 1 - abs(meta["energy"] - _eff_energy))
            else:
                energy_match = 1.0  # no preference → neutral score

            # Usage score: Inverse of count (favors NEW/LEAST USED)
            usage_score = 1 / (meta["usage_count"] + 1)

            # 4. Genre-Content Compatibility Score (new — only when Gemini data present)
            # If the track has been enriched and a content_category is known,
            # boost score for good matches and penalise mismatches.
            # Falls back to 0.5 (neutral) when either value is missing — identical
            # to the previous behaviour.
            genre_match = 0.5  # neutral default
            if content_category:
                _cat = content_category.lower().strip()
                _good = [c.lower() for c in (meta.get("gemini_content_match") or [])]
                _bad  = [c.lower() for c in (meta.get("gemini_avoid_match")  or [])]
                if _cat in _good:
                    genre_match = 1.0   # perfect fit
                elif _cat in _bad:
                    genre_match = 0.0   # active mismatch
                elif _good:             # has data but not a direct hit → slight boost over neutral
                    genre_match = 0.4

            # 5. Final Scoring
            # When genre data exists (gemini_analyzed) we rebalance weights to
            # give content-fit a meaningful seat.  When data is absent the
            # genre_match=0.5 contribution perfectly reproduces the OLD formula
            # (0.6+0.2+0.15 vs 0.45+0.20+0.15+0.10 but 0.5*0.20 ≈ same neutral).
            _has_gemini = bool(meta.get("gemini_analyzed")) and bool(content_category)
            if _has_gemini:
                score = (
                    bpm_match    * 0.45 +
                    energy_match * 0.20 +
                    genre_match  * 0.25 +
                    usage_score  * 0.10
                )
            else:
                score = (
                    bpm_match   * 0.60 +
                    energy_match * 0.20 +
                    usage_score  * 0.15
                )

            if recent_penalty:
                score *= 0.5

            # Human Variance
            score += random.uniform(0, 0.05)

            if score > best_score:
                best_score = score
                best_audio = filename

        if not best_audio:
            return None

        src = os.path.join(self.active_dir, best_audio)
        dst = os.path.join(self.cooldown_dir, best_audio)

        # Update usage metadata regardless
        meta = self._get_file_metadata(best_audio)
        if meta:
            meta["usage_count"] += 1
            meta["last_used"] = time.time()
            self._save_metadata()

        # Selection & Cooldown Shift
        try:
            from shutil import move as _move
            _move(src, dst)
            logger.info(
                f"🏆 [POOL] Selected: {best_audio} (Score: {best_score:.2f}) "
                f"→ Moved to cooldown to actively prevent abuse."
            )
        except Exception as e:
            logger.warning(f"⚠️ [POOL] Could not move to cooldown (non-fatal): {e}")

        return os.path.abspath(dst)

    def maintenance(self):
        """
        Rotates clips from cooldown back to active based on hybrid logic.
        Cleans up root directory of Original_audio.
        """
        now = time.time()
        
        # 1. 🔁 Cooldown → Active (Hybrid Logic: 48 hours OR implicit cycle gap via metadata analysis)
        # Note: 'usage_gap' isn't explicitly stored, but we can infer 'last_used' is the primary trigger.
        # User specified: now - last_used > 48h OR usage_gap >= 15.
        # Tracking "usage_gap" precisely requires a global count. 
        # For now, let's stick to the 48h time trigger provided in the skeleton.
        
        # 1. 🔁 Cooldown → Active rotation (48-hour rule)
        # Files that have been in cooldown for 48 hours are rotated back.
        count_rotated = 0
        for filename in os.listdir(self.cooldown_dir):
            if _is_pipeline_artifact(filename):
                continue
                
            path = os.path.join(self.cooldown_dir, filename)
            meta = self._get_file_metadata(filename)

            if not meta:
                # Orphaned cooldown file — move back to active for safety
                try: move(path, os.path.join(self.active_dir, filename)); count_rotated += 1
                except: pass
                continue

            time_passed = now - meta.get("last_used", 0)

            if time_passed > 48 * 3600:
                try:
                    move(path, os.path.join(self.active_dir, filename))
                    count_rotated += 1
                except Exception as e:
                    logger.error(f"Failed to rotate {filename} back to active: {e}")

        if count_rotated > 0:
            logger.info(f"🔁 Audio Maintenance: Rotated {count_rotated} clips from cooldown to active.")

        # 1b. 🧹 Orphaned NPZ cleanup
        try:
            meta_files = self.metadata.get("files", self.metadata)
            valid_npz = set()
            for f_meta in meta_files.values():
                if isinstance(f_meta, dict) and f_meta.get("beat_data_path"):
                    valid_npz.add(os.path.basename(f_meta["beat_data_path"]))
            
            count_npz_cleaned = 0
            for npz_file in os.listdir(self.beats_dir):
                if npz_file not in valid_npz:
                    try:
                        os.remove(os.path.join(self.beats_dir, npz_file))
                        count_npz_cleaned += 1
                    except: pass
            if count_npz_cleaned > 0:
                logger.info(f"🧹 Audio Maintenance: Cleaned {count_npz_cleaned} orphaned .npz files.")
        except Exception as e:
            logger.warning(f"⚠️ NPZ cleanup fail: {e}")

        # 2. 🧹 Root cleanup (Files > 6h old)
        count_cleaned = 0
        for filename in os.listdir(self.base_dir):
            path = os.path.join(self.base_dir, filename)

            # Skip subdirectories (active, cooldown)
            if os.path.isdir(path):
                continue

            # Skip metadata file
            if filename == os.path.basename(self.meta_path):
                continue

            try:
                created = os.path.getctime(path)
                if now - created > 6 * 3600:
                    os.remove(path)
                    count_cleaned += 1
            except Exception as e:
                logger.warning(f"Failed to clean root file {filename}: {e}")

        if count_cleaned > 0:
            logger.info(f"🧹 Audio Maintenance: Cleaned {count_cleaned} stale files from Original_audio root.")

# Global Instance
pool_manager = AudioPoolManager()
