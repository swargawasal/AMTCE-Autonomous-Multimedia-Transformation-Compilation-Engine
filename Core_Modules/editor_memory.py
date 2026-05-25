"""
editor_memory.py
Persistent pattern memory store with EWMA (Exponentially Weighted Moving Average) scoring.

Key design decisions:
  - No database required — single JSON file, human-readable
  - EWMA prevents old poor decisions from being permanently penalised
    once the system has learned better approaches
  - Minimum sample guard prevents over-fitting on 1–2 observations
  - Every write is atomic (write-to-temp + rename)

Memory schema (editor_memory.json):
{
  "version": 2,
  "last_updated": ISO-8601 str,
  "total_videos_learned": int,
  "patterns": {
    "{pattern_key}": {
      "key":              str,     # e.g. "reveal_arc|HYPE|reveal|zoom_in"
      "arc_type":         str,
      "persona":          str,
      "segment_role":     str,
      "transition":       str,
      "ewma_score":       float,   # [0,1] — current EWMA engagement estimate
      "sample_count":     int,
      "positive_signals": int,
      "negative_signals": int,
      "last_seen":        ISO-8601 str,
      "confidence":       float,   # [0,1] — reliability of score (rises with samples)
    },
    ...
  },
  "arc_scores": {
    "reveal_arc": float,           # mean ewma_score across patterns for this arc
    ...
  },
  "persona_scores": {
    "HYPE": float,
    ...
  },
}
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_MEMORY_VERSION = 3
_DEFAULT_MEMORY_PATH = Path("editor_memory.json")

# EWMA alpha: 0.3 = new data has 30% weight, history has 70%
# Lower alpha = slower to change (more stable but slower adaptation)
# Higher alpha = faster to change (more reactive but noisier)
_DEFAULT_EWMA_ALPHA = 0.30

# How many samples before we trust the score enough to act on it
_MIN_SAMPLES_FOR_CONFIDENCE = 3


def _pattern_key(arc_type: str, persona: str, segment_role: str, transition: str) -> str:
    """Canonical key for a pattern observation."""
    return f"{arc_type}|{persona}|{segment_role}|{transition}"


def find_similar_pattern(signature: str) -> Optional[Dict]:
    """
    Finds a memory-backed editing preference for a given moment signature.
    Currently used as a bridge for CreativeDirector.
    """
    # For now, we return the top ranked arc/persona from global memory
    # Ignoring the signature for a generic 'best' pick until signature logic is recovered
    persona_scores = memory._data.get("persona_scores", {})
    arc_scores = memory._data.get("arc_scores", {})

    if not persona_scores and not arc_scores:
        return None

    best_persona = (
        max(persona_scores.items(), key=lambda kv: kv[1])[0] if persona_scores else None
    )
    best_arc = max(arc_scores.items(), key=lambda kv: kv[1])[0] if arc_scores else None

    return {"preferred_persona": best_persona, "preferred_arc": best_arc}


def _confidence_from_samples(n: int) -> float:
    """
    Sigmoid-like confidence from sample count.
    0 samples → 0.0, 3 samples → ~0.5, 10 samples → ~0.9, 30+ → ~0.99
    """
    if n <= 0:
        return 0.0
    return round(1.0 - (1.0 / (1.0 + n / 5.0)), 4)


class EditorMemory:
    """
    Reads and writes the persistent editing pattern memory.

    Args:
        path:       path to editor_memory.json
        ewma_alpha: learning rate for EWMA score update
    """

    def __init__(
        self,
        path: str | Path = _DEFAULT_MEMORY_PATH,
        ewma_alpha: float = _DEFAULT_EWMA_ALPHA,
    ):
        self.path = Path(path)
        self.alpha = ewma_alpha
        self._data: Dict = self._load()

    def _load(self) -> Dict:
        """Load memory from disk, or return a fresh empty memory."""
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version") == _MEMORY_VERSION:
                    return data
                logger.warning(
                    "EditorMemory: version mismatch in %s — resetting memory", self.path
                )
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("EditorMemory: could not read %s: %s", self.path, e)
        return self._empty_memory()

    def _empty_memory(self) -> Dict:
        return {
            "version": _MEMORY_VERSION,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_videos_learned": 0,
            "patterns": {},
            "arc_scores": {},
            "persona_scores": {},
        }

    def _save(self) -> None:
        """Atomic write to disk."""
        self._data["last_updated"] = datetime.now(timezone.utc).isoformat()
        tmp_path = self.path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self.path)
        except OSError as e:
            logger.error("EditorMemory: could not save %s: %s", self.path, e)

    def get_pattern(self, key: str) -> Optional[Dict]:
        return self._data["patterns"].get(key)

    def all_patterns(self) -> Dict[str, Dict]:
        return dict(self._data["patterns"])

    def get_arc_score(self, arc_type: str) -> float:
        return self._data["arc_scores"].get(arc_type, 0.5)

    def get_persona_score(self, persona: str) -> float:
        return self._data["persona_scores"].get(persona, 0.5)

    def upsert_pattern(self, key: str, new_score: float, signal: str, pattern_meta: Dict) -> Dict:
        """
        Insert or update a pattern entry using EWMA.

        Args:
            key:          pattern_key string
            new_score:    engagement_score from this observation [0,1]
            signal:       "positive" | "negative" | "neutral"
            pattern_meta: dict with arc_type, persona, segment_role, transition
        """
        patterns = self._data["patterns"]
        now = datetime.now(timezone.utc).isoformat()

        rw_weight = pattern_meta.get("rewatch_weight", 1.0) or 1.0
        new_score = max(0.0, min(1.0, new_score * rw_weight))

        coherence_score = pattern_meta.get("coherence_score", 1.0)
        cut_offset = pattern_meta.get("cut_offset")
        reaction_offset = pattern_meta.get("reaction_offset", cut_offset)
        segment_duration = pattern_meta.get("segment_duration")
        hook_time = pattern_meta.get("hook_time")

        if key not in patterns:
            patterns[key] = {
                "key":              key,
                "arc_type":         pattern_meta.get("arc_type", ""),
                "persona":          pattern_meta.get("persona", ""),
                "segment_role":     pattern_meta.get("segment_role", ""),
                "transition":       pattern_meta.get("transition", ""),
                "ewma_score":       new_score,
                "sample_count":     1,
                "positive_signals": 1 if signal == "positive" else 0,
                "negative_signals": 1 if signal == "negative" else 0,
                "last_seen":        now,
                "confidence":       _confidence_from_samples(1),
                "cut_offset_avg":   cut_offset if cut_offset is not None else 0.0,
                "reaction_offset_avg": reaction_offset if reaction_offset is not None else 0.0,
                "segment_duration_avg": segment_duration if segment_duration is not None else 0.0,
                "hook_time_avg":    hook_time if hook_time is not None else 0.0,
            }
        else:
            entry = patterns[key]
            # Ensure new timing fields exist for backward compatibility
            entry.setdefault("cut_offset_avg", 0.0)
            entry.setdefault("reaction_offset_avg", 0.0)
            entry.setdefault("segment_duration_avg", 0.0)
            entry.setdefault("hook_time_avg", 0.0)
            old_score = entry["ewma_score"]
            # EWMA update: new = alpha * observation + (1 - alpha) * old
            entry["ewma_score"] = round(
                self.alpha * new_score + (1 - self.alpha) * old_score, 4
            )
            entry["sample_count"] += 1
            entry["positive_signals"] += (1 if signal == "positive" else 0)
            entry["negative_signals"] += (1 if signal == "negative" else 0)
            entry["last_seen"] = now
            entry["confidence"] = _confidence_from_samples(entry["sample_count"])

            def _ewma_field(field_name: str, new_val):
                if new_val is None:
                    return
                old_val = entry.get(field_name, 0.0)
                entry[field_name] = round(
                    self.alpha * float(new_val) + (1 - self.alpha) * float(old_val), 4
                )

            _ewma_field("cut_offset_avg", cut_offset)
            _ewma_field("reaction_offset_avg", reaction_offset)
            _ewma_field("segment_duration_avg", segment_duration)
            _ewma_field("hook_time_avg", hook_time)
            _ewma_field("coherence_avg", coherence_score)

        return patterns[key]

    def rebuild_aggregate_scores(self) -> None:
        """
        Recompute arc_scores and persona_scores as weighted mean of pattern scores.
        Only patterns with confidence >= _MIN_SAMPLES confidence level are included.
        """
        arc_buckets: Dict[str, List[float]] = {}
        persona_buckets: Dict[str, List[float]] = {}

        for entry in self._data["patterns"].values():
            if entry["sample_count"] < _MIN_SAMPLES_FOR_CONFIDENCE:
                continue
            arc = entry["arc_type"]
            persona = entry["persona"]
            score = entry["ewma_score"]
            # Weight by confidence so high-sample patterns dominate
            w = entry["confidence"]
            arc_buckets.setdefault(arc, []).append((score, w))
            persona_buckets.setdefault(persona, []).append((score, w))

        def _weighted_mean(pairs):
            total_w = sum(w for _, w in pairs)
            if total_w == 0:
                return 0.5
            return round(sum(s * w for s, w in pairs) / total_w, 4)

        self._data["arc_scores"] = {
            arc: _weighted_mean(pairs) for arc, pairs in arc_buckets.items()
        }
        self._data["persona_scores"] = {
            persona: _weighted_mean(pairs) for persona, pairs in persona_buckets.items()
        }

    def increment_video_count(self) -> None:
        self._data["total_videos_learned"] = self._data.get("total_videos_learned", 0) + 1

    def save(self) -> None:
        """Public save — call after a batch of upserts."""
        self._save()

    @property
    def total_patterns(self) -> int:
        return len(self._data["patterns"])

    @property
    def total_videos_learned(self) -> int:
        return self._data.get("total_videos_learned", 0)

memory = EditorMemory()
