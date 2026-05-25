"""
creator_memory.py
─────────────────────────────────────────────────────────────────────────────
PER-CREATOR EDITING FINGERPRINT MEMORY

Stores and retrieves editing patterns keyed by creator identity.
Creator ID is extracted from the input filename (e.g. "Corn_Entertainments").

On each successful render, stores:
  - Hook type and time
  - Avg cut duration
  - Transitions used
  - Pacing style
  - Niche

On next video from the same creator, retrieves these patterns and
biases the creative_brain + Gemini prompts toward what worked before.

Storage: JSON file per creator in rag/creator_fingerprints/
─────────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("creator_memory")

_FINGERPRINT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "rag", "creator_fingerprints"
)


def _extract_creator_id(input_paths: List[str]) -> Optional[str]:
    """
    Extract creator ID from input filenames.
    Examples:
      "downloads/Corn_Entertainments_5.mp4"   → "Corn_Entertainments"
      "downloads/clean_FitnessBro_12.mp4"     → "FitnessBro"
      "downloads/clean_Corn_Entertainmeents_5.mp4" → "Corn_Entertainmeents"
    Strategy: strip common prefixes (clean_, Watermark_Buffer/), then strip
    trailing _<number> and extension.
    """
    if not input_paths:
        return None
    path = input_paths[0]
    name = os.path.splitext(os.path.basename(path))[0]
    # Strip watermark-buffer prefix
    name = re.sub(r"^clean_", "", name)
    name = re.sub(r"^Watermark_Buffer[/\\]", "", name)
    name = re.sub(r"^clean_", "", name)  # second clean_ if any
    # Strip trailing _<digits>
    name = re.sub(r"_\d+$", "", name)
    name = name.strip("_").strip()
    if len(name) < 2:
        return None
    return name


class CreatorMemory:
    """Lightweight JSON-backed per-creator fingerprint store."""

    def __init__(self, store_dir: str = _FINGERPRINT_DIR):
        self._dir = store_dir
        os.makedirs(self._dir, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def get_fingerprint(self, input_paths: List[str]) -> Optional[Dict]:
        """Return stored fingerprint for the creator of these clips, or None."""
        creator_id = _extract_creator_id(input_paths)
        if not creator_id:
            return None
        fp_path = self._fp_path(creator_id)
        if not os.path.exists(fp_path):
            logger.info(f"[CreatorMemory] No fingerprint for '{creator_id}' — first video")
            return None
        try:
            with open(fp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(
                f"[CreatorMemory] 🧠 Loaded fingerprint for '{creator_id}' "
                f"| renders={data.get('render_count', 0)} "
                f"| hook_type={data.get('hook_type', '?')} "
                f"| avg_cut={data.get('avg_cut_duration', '?')}s"
            )
            data["creator_id"] = creator_id
            return data
        except Exception as e:
            logger.warning(f"[CreatorMemory] Failed to read fingerprint for '{creator_id}': {e}")
            return None

    def store_edit(
        self,
        input_paths: List[str],
        edit_metadata: Dict[str, Any],
    ) -> None:
        """
        Called after a successful render to update the creator fingerprint.
        edit_metadata keys (all optional, skip missing ones):
          hook_time, climax_time, hook_type, pacing_style, segments,
          niche, transitions, creative_intent
        """
        creator_id = _extract_creator_id(input_paths)
        if not creator_id:
            return
        fp_path = self._fp_path(creator_id)

        # Load existing or start fresh
        existing: Dict = {}
        if os.path.exists(fp_path):
            try:
                with open(fp_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}

        # Merge new signals
        render_count = existing.get("render_count", 0) + 1

        # Rolling average of cut durations
        segments = edit_metadata.get("segments", [])
        if isinstance(segments, list) and segments:
            new_avg = sum(
                float(s.get("end", 0)) - float(s.get("start", 0))
                for s in segments if isinstance(s, dict)
            ) / len(segments)
            prev_avg = existing.get("avg_cut_duration", new_avg)
            # EMA decay: old=70%, new=30%
            avg_cut = round(0.7 * prev_avg + 0.3 * new_avg, 2)
        else:
            avg_cut = existing.get("avg_cut_duration", 2.0)

        # Track used transitions (frequency count)
        transition_counts: Dict[str, int] = existing.get("transition_counts", {})
        for seg in (segments if isinstance(segments, list) else []):
            t = str(seg.get("transition", seg.get("style", ""))).strip()
            if t:
                transition_counts[t] = transition_counts.get(t, 0) + 1

        # Best N transitions
        top_transitions = sorted(transition_counts, key=transition_counts.get, reverse=True)[:4]

        updated = {
            "creator_id":        creator_id,
            "render_count":      render_count,
            "last_updated":      time.strftime("%Y-%m-%d %Human:%M:%S"),
            "avg_cut_duration":  avg_cut,
            "hook_type":         edit_metadata.get("hook_type") or existing.get("hook_type", "motion_peak"),
            "pacing_style":      edit_metadata.get("pacing_style") or existing.get("pacing_style", "rhythm_driven"),
            "niche":             edit_metadata.get("niche") or existing.get("niche", "generic"),
            "transitions":       top_transitions,
            "transition_counts": transition_counts,
            "style_note":        edit_metadata.get("style_note") or existing.get("style_note", ""),
            "best_hook_times":   self._rolling_list(
                existing.get("best_hook_times", []),
                edit_metadata.get("hook_time"),
                max_len=5
            ),
            "best_climax_times": self._rolling_list(
                existing.get("best_climax_times", []),
                edit_metadata.get("climax_time"),
                max_len=5
            ),
        }

        # Derive style_note from patterns if empty
        if not updated["style_note"] and render_count >= 2:
            updated["style_note"] = (
                f"{updated['pacing_style']} cuts averaging {updated['avg_cut_duration']}s, "
                f"hook via {updated['hook_type']}, "
                f"prefers {updated['transitions'][:2]}"
            )

        try:
            with open(fp_path, "w", encoding="utf-8") as f:
                json.dump(updated, f, indent=2)
            logger.info(
                f"[CreatorMemory] 💾 Updated fingerprint for '{creator_id}' "
                f"| render #{render_count} | avg_cut={avg_cut:.2f}s"
            )
        except Exception as e:
            logger.warning(f"[CreatorMemory] Failed to write fingerprint for '{creator_id}': {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _fp_path(self, creator_id: str) -> str:
        safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", creator_id)
        return os.path.join(self._dir, f"{safe_id}.json")

    @staticmethod
    def _rolling_list(existing: List, new_val: Any, max_len: int = 5) -> List:
        if new_val is None:
            return existing
        result = existing + [round(float(new_val), 2)]
        return result[-max_len:]
