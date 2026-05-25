import json
import os
import logging
import hashlib
from gemini_governor import gemini_router
from Intelligence_Modules.gemini_governor import gemini_router
from collections import deque
from typing import List, Dict, Any

try:
        HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

logger = logging.getLogger("caption_memory")

MEMORY_PATH = "The_json/caption_memory.json"
MAX_ITEMS = 100
EMBED_MODEL = "text-embedding-004"


def stable_hash(text: str) -> int:
    """Deterministic hash across runs."""
    return int(hashlib.md5(text.encode()).hexdigest(), 16)


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class CaptionMemory:
    def __init__(self, path: str = MEMORY_PATH, max_items: int = MAX_ITEMS):
        self.path = path
        self.max_items = max_items
        self.items = deque(maxlen=max_items)
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data[-self.max_items :]:
                    if "text" in item:
                        self.items.append(item)
            logger.info(f"[CAPTION_MEMORY] loaded {len(self.items)} items.")
        except Exception as e:
            logger.warning(f"[CAPTION_MEMORY] load failed: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(list(self.items), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[CAPTION_MEMORY] save failed: {e}")

    def _get_embedding(self, text: str) -> List[float]:
        return gemini_router.embed(text, module_name="caption_memory")
    def is_too_similar(self, text: str, threshold: float = 0.85) -> bool:
        """Lazy similarity: only compute embedding if needed."""
        if not text:
            return False
        # quick exact/substring checks first
        lower = text.lower()
        for item in self.items:
            if lower == item.get("text", "").lower():
                return True
        if not self.items:
            return False
        emb = self._get_embedding(text)
        if not emb:
            return False
        for item in self.items:
            vec = item.get("embedding") or []
            if vec:
                if cosine(emb, vec) > threshold:
                    return True
        return False

    def add(self, text: str):
        if not text:
            return
        emb = self._get_embedding(text)
        self.items.append({"text": text, "embedding": emb})
        self._save()


memory = CaptionMemory()
