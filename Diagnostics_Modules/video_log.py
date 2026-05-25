"""
video_log.py
Provenance store — records the edit plan decisions made for each uploaded video.

Written by the orchestrator immediately after upload.
Read by the pattern_extractor when analytics arrive.

Schema (video_log_entry):
{
  "video_id":          str,
  "upload_ts":         ISO-8601 str,
  "video_duration_s":  float,
  "arc_type":          str,
  "persona":           str,
  "avg_energy":        float,
  "confidence":        float,
  "segments": [
    {
      "start":      float,
      "end":        float,
      "role":       str,
      "transition": str,
    },
    ...
  ],
  "effects": [
    {"time": float, "type": str, "strength": float},
    ...
  ],
  "hook_time_s":       float,    # start time of the hook segment
  "top_moment_time_s": float,    # time of the highest-interest meaning moment
  "niche":             str,      # detected content niche (optional)
  "analytics_fetched": bool,     # flag: has the learning pass run yet?
}

Storage: one JSON file per video in ./video_logs/{video_id}.json
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = Path("video_logs")


class VideoLog:
    """
    Simple JSON-file-per-video provenance store.

    Args:
        log_dir: directory where video log files are stored
    """

    def __init__(self, log_dir: str | Path = _DEFAULT_LOG_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, video_id: str) -> Path:
        return self.log_dir / f"{video_id}.json"

    def write(self, entry: Dict) -> None:
        """
        Persist a video log entry.
        Called by the orchestrator immediately after upload succeeds.

        Required keys in entry: video_id, arc_type, persona, segments.
        """
        video_id = entry.get("video_id", "")
        if not video_id:
            logger.warning("VideoLog.write: entry has no video_id — skipping")
            return
        entry.setdefault("upload_ts", datetime.now(timezone.utc).isoformat())
        entry.setdefault("analytics_fetched", False)
        path = self._path(video_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2)
            logger.info("VideoLog: wrote %s", path)
        except OSError as e:
            logger.error("VideoLog: could not write %s: %s", path, e)

    def read(self, video_id: str) -> Optional[Dict]:
        """Return the log entry for video_id, or None if not found."""
        path = self._path(video_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("VideoLog: could not read %s: %s", path, e)
            return None

    def mark_analytics_fetched(self, video_id: str) -> None:
        """Mark this entry as having had its analytics processed."""
        entry = self.read(video_id)
        if entry:
            entry["analytics_fetched"] = True
            self.write(entry)

    def pending_analytics(self) -> List[str]:
        """
        Return list of video_ids that have been uploaded but not yet
        had their analytics processed.
        """
        pending = []
        for path in self.log_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                if not entry.get("analytics_fetched", False):
                    pending.append(entry["video_id"])
            except Exception:
                pass
        return pending

    @staticmethod
    def from_editor_brain_output(
        video_id: str,
        brain_result: Dict,
        video_duration_s: float = 30.0,
        niche: str = "",
    ) -> Dict:
        """
        Convenience constructor: builds a VideoLog entry from EditorBrainV3 output.

        Args:
            video_id:         YouTube video ID after upload
            brain_result:     dict returned by EditorBrainV3.process()
            video_duration_s: actual rendered video length
            niche:            content niche string (optional)
        """
        segments = brain_result.get("segments", [])
        effects = brain_result.get("effects", [])

        # Find hook segment start time
        hook_seg = next((s for s in segments if s.get("role") == "hook"), None)
        hook_time_s = hook_seg["start"] if hook_seg else 0.0

        # Find top moment time (highest strength effect)
        top_effect = max(effects, key=lambda e: e.get("strength", 0), default=None)
        top_moment_time_s = top_effect["time"] if top_effect else 0.0

        return {
            "video_id":          video_id,
            "video_duration_s":  video_duration_s,
            "arc_type":          brain_result.get("arc_type", ""),
            "persona":           brain_result.get("persona", ""),
            "avg_energy":        brain_result.get("avg_energy", 0.5),
            "confidence":        brain_result.get("confidence", 0.5),
            "coherence_score":   brain_result.get("validation", {}).get("coherence", {}).get("coherence_score", 1.0),
            "segments":          segments,
            "effects":           effects,
            "hook_time_s":       hook_time_s,
            "top_moment_time_s": top_moment_time_s,
            "niche":             niche,
            "analytics_fetched": False,
        }
