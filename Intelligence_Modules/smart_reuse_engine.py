"""
SMART REUSE ENGINE v3.0 — 4-Layer Video Fingerprint System

Layer 1: Source ID Match (reel_id from URL + duration verification)
Layer 2: Visual Perceptual Hash (12 frames at 0%,5%,10%,20%,30%,40%,50%,60%,70%,80%,90%,95%)
Layer 3: Frame Similarity (Hamming distance < 8 threshold)
Layer 4: AI CLIP Embedding Similarity (cosine similarity > 0.92)

Database: cache/video_fingerprint_db.json
"""

import os
import json
import logging
import cv2
import numpy as np
from typing import Optional, Dict, List, Tuple, Any
from scipy.fftpack import dct
from scipy.spatial.distance import cosine
import warnings

# Suppress CLIP warnings
warnings.filterwarnings("ignore", category=UserWarning)

logger = logging.getLogger("smart_reuse")

# Configuration
CACHE_DIR = "cache"
FINGERPRINT_DB = os.path.join(CACHE_DIR, "video_fingerprint_db.json")

# Thresholds
HAMMING_THRESHOLD = 8  # Bits difference for frame match
FRAME_SIMILARITY_THRESHOLD = 0.85  # 85% frame match required
AI_SIMILARITY_THRESHOLD = 0.92  # 92% CLIP similarity required
DURATION_TOLERANCE = 0.5  # seconds

# Frame sampling positions (percentages)
PHASH_SAMPLE_POINTS = [0, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95]
CLIP_SAMPLE_POINTS = [0.10, 0.30, 0.50, 0.70, 0.90]

# Ensure cache directory exists
os.makedirs(CACHE_DIR, exist_ok=True)


class CLIPModelCache:
    """Singleton to cache CLIP model in memory (load once at startup)."""
    
    _instance = None
    _model = None
    _preprocess = None
    _device = None
    _available = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def _load_model(self):
        """Load CLIP model if not already loaded."""
        if self._model is not None:
            return True
        
        try:
            import torch
            import open_clip
            
            # [CRASH_PROTECTION] Force CPU for stability in background workers
            self._device = "cpu"
            
            # Use CLIP-ViT-B-32 for balance of speed and accuracy
            self._model, _, self._preprocess = open_clip.create_model_and_transforms(
                'ViT-B-32', pretrained='openai', device=self._device
            )
            self._model.eval()
            
            self._available = True
            logger.info(f"[SMART_REUSE] CLIP model loaded on {self._device}")
            return True
            
        except ImportError:
            logger.warning("[SMART_REUSE] open_clip not available. Install: pip install open-clip-torch")
            self._available = False
            return False
        except Exception as e:
            logger.warning(f"[SMART_REUSE] CLIP model load failed: {e}")
            self._available = False
            return False
    
    def get_model(self):
        """Get cached CLIP model, loading if necessary."""
        if self._model is None:
            self._load_model()
        return self._model, self._preprocess, self._device, self._available
    
    def encode_images(self, images: List[np.ndarray]) -> Optional[np.ndarray]:
        """Encode list of images to CLIP embeddings."""
        model, preprocess, device, available = self.get_model()
        
        if not available or model is None:
            return None
        
        try:
            import torch
            from PIL import Image as PILImage
            
            # Convert numpy arrays to PIL and preprocess
            processed = []
            for img in images:
                if len(img.shape) == 2:  # Grayscale
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
                elif img.shape[2] == 3:  # BGR to RGB
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                
                pil_img = PILImage.fromarray(img)
                processed.append(preprocess(pil_img).unsqueeze(0))
            
            # Batch process
            batch = torch.cat(processed).to(device)
            
            with torch.no_grad():
                embeddings = model.encode_image(batch)
                embeddings = embeddings.cpu().numpy()
            
            # Normalize embeddings
            embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
            
            # Return mean embedding (single vector representing video)
            return np.mean(embeddings, axis=0)
            
        except Exception as e:
            logger.warning(f"[SMART_REUSE] CLIP encoding failed: {e}")
            return None


# Global CLIP cache instance
_clip_cache = CLIPModelCache()

# Frame cache for sharing between Smart Reuse and Gemini
# Structure: {video_path: {"frames": {...}, "timestamp": float}}
_frame_cache = {}
_FRAME_CACHE_MAX_AGE = 300  # 5 minutes


def _cleanup_frame_cache():
    """Remove old entries from frame cache."""
    import time
    now = time.time()
    stale = [k for k, v in _frame_cache.items() if now - v.get("timestamp", 0) > _FRAME_CACHE_MAX_AGE]
    for k in stale:
        del _frame_cache[k]


def cache_video_frames(video_path: str, frames: Dict[str, np.ndarray]):
    """
    Cache extracted frames for later use by Gemini.
    
    Args:
        video_path: Path to video file
        frames: Dict mapping position labels to frame arrays
    """
    import time
    _cleanup_frame_cache()
    _frame_cache[video_path] = {
        "frames": frames,
        "timestamp": time.time()
    }


def get_cached_frame(video_path: str, position: str = None) -> Optional[np.ndarray]:
    """
    Retrieve cached frame(s) for a video.
    
    Args:
        video_path: Path to video file
        position: Specific position label (e.g., '10%', 'mid') or None for all frames
    
    Returns:
        Frame array if position specified, dict of all frames if not, None if not cached
    """
    import time
    if video_path not in _frame_cache:
        return None
    
    entry = _frame_cache[video_path]
    # Check age
    if time.time() - entry.get("timestamp", 0) > _FRAME_CACHE_MAX_AGE:
        del _frame_cache[video_path]
        return None
    
    frames = entry.get("frames", {})
    
    if position:
        return frames.get(position)
    return frames


def get_frame_for_gemini(video_path: str, timestamp: str = "00:00:01") -> Optional[str]:
    """
    Get a frame for Gemini analysis, using cache if available.
    Saves frame to temp file and returns path.
    
    Args:
        video_path: Path to video file
        timestamp: Timestamp string (HH:MM:SS or seconds) for Gemini analysis
    
    Returns:
        Path to saved frame image, or None
    """
    import cv2
    import tempfile
    import os
    
    # Parse timestamp to approximate percentage
    try:
        if ":" in timestamp:
            parts = timestamp.split(":")
            if len(parts) == 3:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                seconds = int(parts[0]) * 60 + float(parts[1])
            else:
                seconds = float(parts[0])
        else:
            seconds = float(timestamp)
    except:
        seconds = 1.0
    
    # Try to get video duration
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        cap.release()
        return None
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    duration = total_frames / fps if fps > 0 else 0
    cap.release()
    
    if duration > 0:
        # Map timestamp to nearest cached position
        percent = min(seconds / duration, 0.95)
        # Find nearest cached frame
        nearest_pos = None
        min_diff = float('inf')
        
        cached = get_cached_frame(video_path)
        if cached:
            for pos_label, frame in cached.items():
                if pos_label.endswith('%'):
                    pos_pct = float(pos_label.rstrip('%')) / 100
                    diff = abs(pos_pct - percent)
                    if diff < min_diff:
                        min_diff = diff
                        nearest_pos = pos_label
            
            # Use cached frame if within 5% tolerance
            if nearest_pos and min_diff < 0.05:
                frame = cached[nearest_pos]
                # Save to temp file
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    frame_path = tmp.name
                    # Convert grayscale to RGB if needed
                    if len(frame.shape) == 2:
                        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    else:
                        frame_bgr = frame
                    cv2.imwrite(frame_path, frame_bgr)
                    return frame_path
    
    return None


class SmartReuseEngine:
    """
    4-Layer Smart Reuse System for video deduplication.
    
    Priority:
    1. Exact reel ID match (source platform ID) + duration verification
    2. Frame similarity > 85% (Hamming distance on 12 frames)
    3. AI embedding similarity > 92% (CLIP cosine similarity)
    
    Never uses: filename, file size, duration alone
    """
    
    def __init__(self):
        self.db = self._load_db()
        # Ensure CLIP model is loaded (cached)
        _clip_cache.get_model()
    
    def _load_db(self) -> Dict:
        """Load fingerprint database."""
        if os.path.exists(FINGERPRINT_DB):
            try:
                with open(FINGERPRINT_DB, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[SMART_REUSE] db_load_failed: {e}")
        return {}
    
    def _save_db(self):
        """Atomic save of fingerprint database."""
        temp = FINGERPRINT_DB + ".tmp"
        try:
            with open(temp, 'w', encoding='utf-8') as f:
                json.dump(self.db, f, indent=2)
            if os.path.exists(FINGERPRINT_DB):
                os.remove(FINGERPRINT_DB)
            os.rename(temp, FINGERPRINT_DB)
        except Exception as e:
            logger.error(f"[SMART_REUSE] db_save_failed: {e}")
            if os.path.exists(temp):
                os.remove(temp)
    
    def _extract_frames_at_positions(self, video_path: str, positions: List[float], cache_for_gemini: bool = True) -> List[np.ndarray]:
        """
        Extract frames at specified percentage positions.
        
        Args:
            video_path: Path to video file
            positions: List of positions as percentages (0.0 to 1.0)
            cache_for_gemini: If True, cache frames for Gemini reuse
        
        Returns:
            List of frames as numpy arrays (grayscale 64x64)
        """
        if isinstance(video_path, tuple):
            video_path = video_path[0]
        
        # [STEP 3] Skip visual fingerprinting if no valid path exists
        if not video_path or not os.path.exists(video_path):
            return []
            
        assert isinstance(video_path, str)
        
        frames = []
        cap = cv2.VideoCapture(video_path)
        
        if not cap.isOpened():
            logger.warning(f"[SMART_REUSE] cannot_open_video: {video_path}")
            return []
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        if total_frames == 0:
            cap.release()
            return []
        
        # For Gemini caching: store original frames before resize
        gemini_cache = {} if cache_for_gemini else None
        
        for i, pos in enumerate(positions):
            frame_idx = int(total_frames * pos)
            frame_idx = min(frame_idx, total_frames - 1)
            
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            
            if ret and frame is not None and frame.size > 0:
                # Store original for Gemini cache (at 224x224 for CLIP compatibility)
                if gemini_cache is not None:
                    label = f"{int(pos*100)}%"
                    # Store a higher-res version for Gemini (BGR format)
                    gemini_frame = cv2.resize(frame, (224, 224))
                    gemini_cache[label] = gemini_frame
                
                # Convert to grayscale and resize to 64x64 for consistent hashing
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                resized = cv2.resize(gray, (64, 64))
                frames.append(resized)
            else:
                # Return empty list if we can't get a frame - forces fallback
                cap.release()
                return []
        
        cap.release()
        
        # Cache frames for Gemini reuse
        if gemini_cache and frames:
            cache_video_frames(video_path, gemini_cache)
            logger.debug(f"[SMART_REUSE] cached {len(gemini_cache)} frames for Gemini reuse")
        
        return frames
    
    def _get_video_metadata(self, video_path: str) -> Tuple[Optional[float], Optional[str]]:
        """Get video duration and resolution."""
        if isinstance(video_path, tuple):
            video_path = video_path[0]
        
        # [STEP 3] Skip if path is invalid
        if not video_path or not os.path.exists(video_path):
            return None, None
            
        assert isinstance(video_path, str)
        
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return None, None
            
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            cap.release()
            
            if fps > 0 and frames > 0:
                duration = frames / fps
                resolution = f"{width}x{height}"
                return duration, resolution
            
            return None, None
        except Exception as e:
            logger.warning(f"[SMART_REUSE] metadata_extraction_failed: {e}")
            return None, None
    
    def _phash(self, image: np.ndarray) -> str:
        """
        Compute perceptual hash (pHash) using DCT.
        
        Returns 16-character hex hash (64 bits)
        """
        # Ensure image is 64x64 grayscale
        if image.shape != (64, 64):
            image = cv2.resize(image, (64, 64))
        
        # Convert to float32 for DCT
        img_float = np.float32(image)
        
        # Apply DCT
        dct_low = dct(dct(img_float, axis=0), axis=1)
        
        # Keep top-left 8x8 low frequencies
        dct_8x8 = dct_low[:8, :8]
        
        # Compute hash: compare each value to median (excluding DC at [0,0])
        avg = (dct_8x8[0, 1:].mean() + dct_8x8[1:, 0].mean()) / 2.0
        
        hash_bits = []
        for i in range(8):
            for j in range(8):
                if i == 0 and j == 0:
                    continue  # Skip DC component
                hash_bits.append(1 if dct_8x8[i, j] > avg else 0)
        
        # Pad to 64 bits for clean 16-char hex representation
        while len(hash_bits) < 64:
            hash_bits.append(0)
        
        hash_str = ""
        for i in range(0, 64, 4):
            nibble = 0
            for j in range(4):
                nibble = (nibble << 1) | hash_bits[i + j]
            hash_str += format(nibble, 'x')
        
        return hash_str
    
    def _compute_frame_hashes(self, video_path: str) -> Optional[List[str]]:
        """
        Compute pHash for 12 sample frames (0%,5%,10%,20%,30%,40%,50%,60%,70%,80%,90%,95%).
        
        Returns:
            List of 16-character hex hashes, or None on failure
        """
        frames = self._extract_frames_at_positions(video_path, PHASH_SAMPLE_POINTS)
        
        if len(frames) < len(PHASH_SAMPLE_POINTS):
            logger.warning(f"[SMART_REUSE] insufficient_frames: {len(frames)}/{len(PHASH_SAMPLE_POINTS)}")
            return None
        
        # Compute pHash for each frame
        hashes = [self._phash(frame) for frame in frames]
        
        return hashes
    
    def _compute_clip_embedding(self, video_path: str) -> Optional[List[float]]:
        """
        Compute CLIP embedding from 5 keyframes (10%,30%,50%,70%,90%).
        
        Returns:
            List of floats (embedding vector), or None on failure
        """
        # Extract frames at CLIP sample points
        frames = self._extract_frames_at_positions(video_path, CLIP_SAMPLE_POINTS)
        
        if len(frames) < len(CLIP_SAMPLE_POINTS):
            return None
        
        # Convert grayscale to RGB for CLIP
        rgb_frames = []
        for frame in frames:
            rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
            # Resize to 224x224 for CLIP
            rgb_resized = cv2.resize(rgb, (224, 224))
            rgb_frames.append(rgb_resized)
        
        # Get CLIP embedding
        embedding = _clip_cache.encode_images(rgb_frames)
        
        if embedding is not None:
            return embedding.tolist()
        
        return None
    
    def _compute_frame_similarity(self, new_hashes: List[str], stored_hashes: List[str]) -> float:
        """
        Compute frame similarity using Hamming distance.
        
        Algorithm:
        - Count matches where Hamming distance < HAMMING_THRESHOLD (8)
        - similarity = matches / total_frames
        
        Returns:
            Similarity as float 0.0-1.0
        """
        if not new_hashes or not stored_hashes:
            return 0.0
        
        matches = 0
        
        for new_hash in new_hashes:
            # Check if this frame matches any stored frame
            for old_hash in stored_hashes:
                if self._hamming_distance(new_hash, old_hash) < HAMMING_THRESHOLD:
                    matches += 1
                    break
        
        similarity = matches / len(new_hashes)
        return similarity
    
    def _compute_cosine_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Compute cosine similarity between two embeddings.
        
        Returns:
            Similarity as float 0.0-1.0
        """
        if not embedding1 or not embedding2:
            return 0.0
        
        try:
            # Convert to numpy arrays
            a = np.array(embedding1)
            b = np.array(embedding2)
            
            # Compute cosine similarity
            similarity = 1 - cosine(a, b)
            
            # Handle NaN
            if np.isnan(similarity):
                return 0.0
            
            return float(similarity)
        except Exception:
            return 0.0
    
    def check_reuse(self, video_path: str, reel_id: str = "", expected_duration: Optional[float] = None) -> Tuple[Optional[str], Dict]:
        """
        4-Layer reuse check.
        
        Args:
            video_path: Path to video file
            reel_id: Source platform ID (e.g., Instagram reel ID)
            expected_duration: Optional duration to verify against (useful for ID-only checks)
        
        Returns:
            Tuple of (existing_file_path or None, metadata_dict)
            metadata contains: id_match, frame_similarity, ai_similarity, final_decision
        """
        if isinstance(video_path, tuple):
            video_path = video_path[0]
        
        # [STEP 3] If no path exists, only Layer 1 (Source ID Match) can run
        path_exists = bool(video_path and os.path.exists(video_path))
        
        result = {
            "id_match": False,
            "frame_similarity": 0.0,
            "ai_similarity": 0.0,
            "final_decision": "new_video",
            "matched_file": None
        }
        
        # Get new video metadata only if we have a valid file path.
        new_duration = expected_duration # Default to expected if provided
        new_resolution = None
        if path_exists:
            new_duration, new_resolution = self._get_video_metadata(video_path)
            if new_duration is None and expected_duration is None:
                logger.error(f"[SMART_REUSE] metadata_extraction_failed: {video_path}")
                result["final_decision"] = "error"
                return None, result
        
        # === LAYER 1: Source ID Match ===
        if reel_id and reel_id in self.db:
            entry = self.db[reel_id]
            stored_file = entry.get("file", "")
            stored_duration = entry.get("duration", 0)
            
            if stored_file and os.path.exists(stored_file):
                # Always verify duration if we have a way to do so
                if new_duration is not None:
                    duration_diff = abs(new_duration - stored_duration)
                    if duration_diff < DURATION_TOLERANCE:
                        result["id_match"] = True
                        result["final_decision"] = "reuse"
                        result["matched_file"] = stored_file
                        
                        logger.info(f"[SMART_REUSE] id_match=True (duration_diff={duration_diff:.2f}s)")
                        logger.info(f"[SMART_REUSE] reuse=True")
                        
                        return stored_file, result
                    else:
                        logger.warning(f"[SMART_REUSE] id_matched_but_duration_diff={duration_diff:.2f}s (Stored: {stored_duration}s, New: {new_duration}s)")
                        # DO NOT delete if it might just be a different video with same ID
                elif not path_exists:
                    # Legacy fallback if no expected_duration passed
                    logger.warning(f"[SMART_REUSE] id_match_without_duration_verification: {reel_id}")
                    result["id_match"] = True
                    result["final_decision"] = "reuse"
                    result["matched_file"] = stored_file
                    return stored_file, result
            else:
                # Stale entry, remove it
                logger.warning(f"[SMART_REUSE] stale_entry_removed: {reel_id}")
                del self.db[reel_id]
                self._save_db()

        # if we don't have a valid file path to analyze, bail out now
        if not path_exists:
            # no video present => only id-match layer applies; metadata and
            # fingerprint layers skipped
            return None, result
        
        # Compute fingerprints
        new_frame_hashes = self._compute_frame_hashes(video_path)
        new_embedding = self._compute_clip_embedding(video_path)
        
        if new_frame_hashes is None:
            logger.error(f"[SMART_REUSE] fingerprint_generation_failed: {video_path}")
            result["final_decision"] = "error"
            return None, result
        
        # === LAYER 3: Frame Similarity ===
        # === LAYER 4: AI Embedding Similarity ===
        best_match = None
        best_frame_sim = 0.0
        best_ai_sim = 0.0
        
        for stored_id, entry in self.db.items():
            stored_file = entry.get("file", "")
            stored_duration = entry.get("duration", 0)
            stored_resolution = entry.get("resolution", "")
            stored_hashes = entry.get("frame_hashes", [])
            stored_embedding = entry.get("embedding", [])
            
            if not stored_file or not os.path.exists(stored_file):
                continue
            
            # Duration check
            duration_diff = abs(new_duration - stored_duration)
            if duration_diff > DURATION_TOLERANCE:
                continue
            
            # Resolution check (optional but helpful)
            if stored_resolution and new_resolution:
                if stored_resolution != new_resolution:
                    continue
            
            # Compute frame similarity
            if stored_hashes:
                frame_sim = self._compute_frame_similarity(new_frame_hashes, stored_hashes)
                
                if frame_sim > best_frame_sim:
                    best_frame_sim = frame_sim
                    best_match = stored_file
                
                # Early exit if we have a strong frame match
                if frame_sim > FRAME_SIMILARITY_THRESHOLD:
                    # Check AI similarity if available
                    ai_sim = 0.0
                    if new_embedding and stored_embedding:
                        ai_sim = self._compute_cosine_similarity(new_embedding, stored_embedding)
                        if ai_sim > best_ai_sim:
                            best_ai_sim = ai_sim
                    
                    if ai_sim > AI_SIMILARITY_THRESHOLD or not new_embedding:
                        result["frame_similarity"] = round(frame_sim, 2)
                        result["ai_similarity"] = round(ai_sim, 2) if ai_sim > 0 else 0.0
                        result["final_decision"] = "reuse"
                        result["matched_file"] = stored_file
                        
                        logger.info("[SMART_REUSE]")
                        logger.info(f"[SMART_REUSE] id_match=False")
                        logger.info(f"[SMART_REUSE] frame_similarity={frame_sim:.2f}")
                        logger.info(f"[SMART_REUSE] ai_similarity={ai_sim:.2f}")
                        logger.info(f"[SMART_REUSE] final_decision=reuse")
                        
                        return stored_file, result
        
        # No strong match found - check if we have moderate matches
        result["frame_similarity"] = round(best_frame_sim, 2)
        result["ai_similarity"] = round(best_ai_sim, 2)
        
        logger.info("[SMART_REUSE]")
        logger.info(f"[SMART_REUSE] id_match=False")
        logger.info(f"[SMART_REUSE] frame_similarity={best_frame_sim:.2f}")
        logger.info(f"[SMART_REUSE] ai_similarity={best_ai_sim:.2f}")
        logger.info(f"[SMART_REUSE] final_decision=new_video")
        
        return None, result
    
    def register(self, video_path: str, reel_id: str = "") -> bool:
        """
        Register a new video in the fingerprint database.
        
        Args:
            video_path: Path to video file
            reel_id: Source platform ID (optional but recommended)
        
        Returns:
            True if registered successfully
        """
        if isinstance(video_path, tuple):
            video_path = video_path[0]
            
        assert isinstance(video_path, str)
        
        if not os.path.exists(video_path):
            logger.error(f"[SMART_REUSE] file_not_found: {video_path}")
            return False
        
        # Get metadata
        duration, resolution = self._get_video_metadata(video_path)
        
        if duration is None:
            logger.error(f"[SMART_REUSE] metadata_extraction_failed: {video_path}")
            return False
        
        # Compute fingerprints
        frame_hashes = self._compute_frame_hashes(video_path)
        embedding = self._compute_clip_embedding(video_path)
        
        if frame_hashes is None:
            logger.error(f"[SMART_REUSE] fingerprint_generation_failed: {video_path}")
            return False
        
        # Use reel_id as key, otherwise use filename
        db_key = reel_id if reel_id else os.path.basename(video_path)
        
        # Create entry
        entry = {
            "id": reel_id,
            "file": video_path,
            "duration": duration,
            "resolution": resolution,
            "frame_hashes": frame_hashes,
            "embedding": embedding if embedding else []
        }
        
        # Register in database
        self.db[db_key] = entry
        self._save_db()
        
        logger.info(f"[SMART_REUSE] registered: {db_key}")
        logger.info(f"[SMART_REUSE] frames_hashed: {len(frame_hashes)}")
        if embedding:
            logger.info(f"[SMART_REUSE] embedding_generated: dim={len(embedding)}")
        
        return True
    
    def remove_stale_entries(self):
        """Remove database entries for files that no longer exist."""
        stale_keys = []
        
        for key, entry in self.db.items():
            file_path = entry.get("file", "")
            if not file_path or not os.path.exists(file_path):
                stale_keys.append(key)
        
        for key in stale_keys:
            del self.db[key]
            logger.info(f"[SMART_REUSE] removed_stale: {key}")
        
        if stale_keys:
            self._save_db()
        
        return len(stale_keys)


# Global instance for module-level access
_engine = None


def get_engine() -> SmartReuseEngine:
    """Get or create global SmartReuseEngine instance."""
    global _engine
    if _engine is None:
        _engine = SmartReuseEngine()
    return _engine


def check_smart_reuse(video_path: str, reel_id: str = "", expected_duration: Optional[float] = None) -> Tuple[Optional[str], Dict]:
    """
    Convenience function for 3-layer reuse check.
    
    Args:
        video_path: Path to video file
        reel_id: Source platform ID (e.g., Instagram reel ID)
        expected_duration: Optional duration to verify against
    
    Returns:
        (existing_file_path or None, metadata_dict)
    """
    engine = get_engine()
    return engine.check_reuse(video_path, reel_id, expected_duration)


def register_video(video_path: str, reel_id: str = "") -> bool:
    """
    Convenience function to register a video.
    
    Args:
        video_path: Path to video file
        reel_id: Source platform ID
    
    Returns:
        True if registered successfully
    """
    engine = get_engine()
    return engine.register(video_path, reel_id)


def cleanup_stale_entries() -> int:
    """Remove stale entries from database."""
    engine = get_engine()
    return engine.remove_stale_entries()


def preload_clip_model():
    """Preload CLIP model at startup (call this early)."""
    _clip_cache.get_model()


if __name__ == "__main__":
    # Test the system
    logging.basicConfig(level=logging.INFO)
    
    print("🧠 Smart Reuse Engine v3.0 (4-Layer) Test")
    print("=" * 50)
    
    engine = SmartReuseEngine()
    
    # Show current database
    print(f"\n📊 Database entries: {len(engine.db)}")
    for key, entry in engine.db.items():
        print(f"  - {key}: {entry.get('file', 'N/A')}")
    
    # Show CLIP availability
    model, preprocess, device, available = _clip_cache.get_model()
    print(f"\n🤖 CLIP Available: {available}")
    if available:
        print(f"   Device: {device}")
    
    print("\n✅ Smart Reuse Engine ready")
