"""
Reaction_Engine/face_swap_engine.py
------------------------------------
Automated face swapping for reactor clips using FaceFusion.

When ENABLE_FACE_SWAP=yes and FaceFusion is configured, this module takes:
  - A silent raw reactor clip (.mp4)
  - A source face image     (.jpg / .png)
  
And returns a face-swapped version.
To save time, it caches swapped results inside `reactor_library/swapped/<emotion>/`.

Fallback:
  If FaceFusion is unavailable or crashes, this module returns the original 
  unswapped clip so the pipeline continues.

.env flags:
    ENABLE_FACE_SWAP=yes|no              (default: no)
    FACEFUSION_DIR=path/to/facefusion
    FACEFUSION_PROVIDER=cpu              (cpu, cuda, tensorrt, etc.)
    FACEFUSION_SOURCE_IMAGE=path/to/my_avatar.jpg
    FACEFUSION_SOURCE_CLIP_IMAGE=path/to/source_avatar.jpg (optional,. for source clip)
"""

import logging
import os
import sys
import subprocess
from typing import Optional

logger = logging.getLogger("face_swap_engine")

def _enabled() -> bool:
    return os.getenv("ENABLE_FACE_SWAP", "no").lower() in ("yes", "true", "1")

def _facefusion_dir() -> Optional[str]:
    d = os.getenv("FACEFUSION_DIR", "")
    return d if d and os.path.isdir(d) else None

def _source_image(is_source_clip: bool = False) -> Optional[str]:
    env_var = "FACEFUSION_SOURCE_CLIP_IMAGE" if is_source_clip else "FACEFUSION_SOURCE_IMAGE"
    p = os.getenv(env_var, os.getenv("FACEFUSION_SOURCE_IMAGE", ""))
    if p and os.path.isfile(p):
        return os.path.abspath(p)
    return None

def _provider() -> str:
    provider = os.getenv("FACEFUSION_PROVIDER", "auto").lower()
    if provider == "auto":
        try:
            import torch
            if torch.cuda.is_available():
                logger.info("[FACE_SWAP] Auto-detected GPU. Using 'cuda' provider.")
                return "cuda"
        except ImportError:
            pass
        logger.info("[FACE_SWAP] No GPU detected or torch missing. Using 'cpu' provider.")
        return "cpu"
    return provider


class FaceSwapEngine:
    """
    Wraps FaceFusion CLI to apply a target face onto a reactor clip.
    """

    def __init__(self, is_source_clip: bool = False):
        self._enabled = _enabled()
        self._dir = _facefusion_dir()
        self._source_img = _source_image(is_source_clip)
        self._provider = _provider()

        if self._enabled and self._dir and self._source_img:
            logger.info(
                f"[FACE_SWAP] FaceFusion ready | Provider={self._provider} | Source={os.path.basename(self._source_img)}"
            )
        elif self._enabled:
            logger.warning(
                "[FACE_SWAP] ENABLE_FACE_SWAP=yes but directory or source image missing in .env. Falling back."
            )
        else:
            logger.info("[FACE_SWAP] Disabled via ENABLE_FACE_SWAP=no")

    def is_available(self) -> bool:
        """Returns True if FaceFusion is fully configured."""
        return bool(self._enabled and self._dir and self._source_img)

    def get_source_image_name(self) -> str:
        """Returns the stem of the source image to use as a cache key."""
        if not self._source_img:
            return "unknown_face"
        return os.path.splitext(os.path.basename(self._source_img))[0]

    def swap_face(self, target_video_path: str, emotion_category: str) -> str:
        """
        Swaps the face on the target_video_path.
        Caches the result in `reactor_library/swapped/<emotion_category>/`.
        
        Args:
            target_video_path: The raw recorded reactor clip
            emotion_category: "shocked", "laughing", etc. Used for caching directory.
            
        Returns:
            Path to the face-swapped video, or the original on failure.
        """
        if not self.is_available():
            return target_video_path

        if not os.path.isfile(target_video_path):
            logger.warning(f"[FACE_SWAP] target video missing: {target_video_path}")
            return target_video_path

        # Determine cache path
        engine_root = os.path.dirname(os.path.abspath(__file__))
        face_name = self.get_source_image_name()
        
        # Create nested directory for the specific face name to separate cached swaps
        cache_dir = os.path.join(engine_root, "reactor_library", "swapped", emotion_category, face_name)
        os.makedirs(cache_dir, exist_ok=True)
        
        vid_stem = os.path.splitext(os.path.basename(target_video_path))[0]
        
        # Name convention: originalclip_swapped.mp4 inside the face directory
        output_filename = f"{vid_stem}_swapped.mp4"
        output_path = os.path.join(cache_dir, output_filename)
        
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            logger.info(f"[FACE_SWAP] Found cached swapped clip: {os.path.basename(output_path)} for {face_name}")
            return output_path
            
        run_script = os.path.join(self._dir, "facefusion.py")
        if not os.path.isfile(run_script):
            logger.warning(f"[FACE_SWAP] FaceFusion entry point missing at {run_script}")
            return target_video_path
            
        logger.info(f"⏳ [FACE_SWAP] Swapping {os.path.basename(target_video_path)} -> {face_name}...")
        
        # Build FaceFusion headless command
        cmd = [
            sys.executable, run_script, "headless-run",
            "--execution-providers", self._provider,
            "-s", self._source_img,
            "-t", target_video_path,
            "-o", output_path,
        ]
        
        # Apply strict throttling for CPU vs GPU
        if self._provider == "cpu":
            # On a CPU, the enhancer causes fatal freezing
            # Limit thread to 1 to prevent locking the OS
            cmd.extend([
                "--processors", "face_swapper",
                "--execution-thread-count", "1",
                "--video-memory-strategy", "strict"
            ])
        else:
            # On GPU, we can afford the luxury of the enhancer and parallel processing
            cmd.extend([
                "--processors", "face_swapper", "face_enhancer"
            ])
        
        try:
            result = subprocess.run(
                cmd,
                cwd=self._dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600, # 10 mins max
            )
            
            if result.returncode == 0 and os.path.isfile(output_path):
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                logger.info(f"✅ [FACE_SWAP] Complete: {os.path.basename(output_path)} ({size_mb:.1f}MB)")
                return output_path
            else:
                logger.warning(
                    f"[FACE_SWAP] Process failed (rc={result.returncode}). "
                    f"stderr={result.stderr[-300:]}"
                )
                return target_video_path
                
        except subprocess.TimeoutExpired:
            logger.warning("[FACE_SWAP] Process timed out (>600s). Using original.")
            return target_video_path
        except Exception as e:
            logger.warning(f"[FACE_SWAP] Fatal exception: {e}")
            return target_video_path

