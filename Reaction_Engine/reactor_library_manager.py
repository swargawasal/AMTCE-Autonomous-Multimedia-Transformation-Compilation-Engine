"""
Reaction_Engine/reactor_library_manager.py
------------------------------------------
Manages the reactor clip library.

Responsibilities:
  1. Load library_index.json from the reactor_library folder
  2. Map an emotion_type → a real clip path (non-placeholder only)
  3. Graceful fallback to 'neutral' when no clip is available for an emotion
  4. Random selection from multiple clips of the same emotion for variety

Usage:
    from Reaction_Engine.reactor_library_manager import ReactorLibraryManager
    mgr = ReactorLibraryManager()
    clip_path = mgr.get_clip("shocked")   # Returns path or None
"""

import json
import logging
import os
import random
from typing import Optional

logger = logging.getLogger("reactor_library_manager")

# Path to the reactor library, relative to AMTCE root
_LIBRARY_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "reactor_library"
)
_INDEX_PATH = os.path.join(_LIBRARY_DIR, "library_index.json")

# Fallback order: if an emotion has no valid clips, try these in sequence
_FALLBACK_ORDER = ["neutral", "impressed", "confused"]


class ReactorLibraryManager:
    """
    Manages and serves reactor clips from the structured library.

    The library folder layout is:
        reactor_library/
          library_index.json        ← maps emotion → [clip filenames]
          shocked/                  ← shocked_01.mp4, shocked_02.mp4, ...
          laughing/
          ...

    is_placeholder=true in the index means the clip hasn't been recorded yet —
    the manager skips those entries gracefully.
    """

    def __init__(self):
        self._index: dict = {}
        self._load_index()

    def _load_index(self) -> None:
        """Load library_index.json. Logs warning on missing file (non-fatal)."""
        try:
            if not os.path.exists(_INDEX_PATH):
                logger.warning(
                    f"[REACTOR_LIB] library_index.json not found at {_INDEX_PATH}. "
                    "All reactor clip lookups will return None."
                )
                return
            with open(_INDEX_PATH, "r", encoding="utf-8") as f:
                self._index = json.load(f)
            logger.info(
                f"[REACTOR_LIB] Loaded library index: {list(self._index.keys())}"
            )
        except Exception as e:
            logger.warning(f"[REACTOR_LIB] Failed to load index (non-fatal): {e}")

    def get_clip(self, emotion: str) -> Optional[str]:
        """
        Returns an absolute path to a real (non-placeholder) reactor clip
        matching the requested emotion.

        Falls back through neutral → impressed → confused if the primary
        emotion has no recorded clips. Returns None if nothing is available.

        Args:
            emotion: One of "shocked", "laughing", "impressed", "confused",
                     "hype", "neutral", "cringe"

        Returns:
            Absolute path to a clip file, or None.
        """
        # Try the requested emotion first, then fallbacks
        candidates = [emotion] + [e for e in _FALLBACK_ORDER if e != emotion]

        for emo in candidates:
            clip_path = self._resolve_clip(emo)
            if clip_path:
                if emo != emotion:
                    logger.info(
                        f"[REACTOR_LIB] '{emotion}' has no clips — "
                        f"falling back to '{emo}'"
                    )
                return clip_path

        logger.warning(
            f"[REACTOR_LIB] No real clips available for '{emotion}' "
            "or any fallback. Returning None."
        )
        return None

    def _resolve_clip(self, emotion: str) -> Optional[str]:
        """
        Look up an emotion in the index and return a random real clip path.
        Returns None if the emotion is not indexed or all clips are placeholders.
        """
        entry = self._index.get(emotion)
        if not entry or not isinstance(entry, dict):
            return None

        if entry.get("is_placeholder", False):
            return None  # Library not populated yet

        clips = entry.get("clips", [])
        if not clips:
            return None

        # Shuffle to pick randomly for variety
        random.shuffle(clips)

        for clip_name in clips:
            full_path = os.path.join(_LIBRARY_DIR, emotion, clip_name)
            if os.path.isfile(full_path):
                logger.debug(f"[REACTOR_LIB] Resolved: {full_path}")
                return full_path

        # Clips listed but files missing
        logger.warning(
            f"[REACTOR_LIB] '{emotion}' clips listed in index but files not found on disk."
        )
        return None

    def list_available_emotions(self) -> list:
        """Returns list of emotions that have at least one real clip on disk."""
        available = []
        for emotion, entry in self._index.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("is_placeholder", False):
                continue
            if self._resolve_clip(emotion):
                available.append(emotion)
        return available

    def is_library_ready(self) -> bool:
        """Returns True if at least the 'neutral' clip is available."""
        return bool(self._resolve_clip("neutral"))
