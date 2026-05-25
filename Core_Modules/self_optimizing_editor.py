"""
self_optimizing_editor.py
Master controller for the Self-Optimizing Editor subsystem.

Runs the full learning loop:
  1. Find videos pending analytics (from VideoLog)
  2. Fetch YouTube Analytics for each
  3. Analyze retention curves
  4. Extract editing patterns
  5. Update editor memory
  6. Rebuild optimization signals

Also provides get_optimization_hints() — called by the orchestrator at edit time
to inject learned strategy preferences into CreativeDirector and RewardScorer.

Usage in orchestrator.py:
    from Intelligence_Modules.self_optimizing_editor import SelfOptimizingEditor
    _soe = SelfOptimizingEditor()           # instantiate once at module load

    # After upload:
    _soe.record_upload(video_id, brain_result, duration_s)

    # Periodically (e.g. daily cron or after N uploads):
    _soe.run_learning_pass()

    # At edit time (before EditorBrainV3.process):
    hints = _soe.get_optimization_hints()
    # Pass hints into CreativeDirector and RewardScorer (see apply_hints_to_orchestrator)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from Core_Modules.analytics_engine import AnalyticsEngine
from Core_Modules.editor_memory import EditorMemory
from Core_Modules.memory_updater import MemoryUpdater
from Core_Modules.pattern_extractor import PatternExtractor
from Core_Modules.retention_analyzer import RetentionAnalyzer
from Core_Modules.strategy_optimizer import StrategyOptimizer
from Core_Modules.video_log import VideoLog

logger = logging.getLogger(__name__)

# Minimum hours after upload before fetching analytics (retention data matures)
_MIN_AGE_HOURS = 24.0
# How long to wait between API calls to avoid quota spikes
_API_CALL_DELAY_S = 1.5


class SelfOptimizingEditor:
    """
    Self-optimizing learning loop for AMTCE.

    Args:
        memory_path:      path to editor_memory.json
        log_dir:          path to video_logs/ directory
        credentials:      YouTube OAuth2 credentials (None = mock mode)
        mock_analytics:   use synthetic analytics instead of real API
    """

    def __init__(
        self,
        memory_path: str | Path = "editor_memory.json",
        log_dir: str | Path = "video_logs",
        credentials=None,
        mock_analytics: bool = False,
    ):
        self.memory = EditorMemory(path=memory_path)
        self.video_log = VideoLog(log_dir=log_dir)
        self.analytics = AnalyticsEngine(
            credentials=credentials,
            _mock_mode=mock_analytics,
        )
        self.analyzer = RetentionAnalyzer()
        self.extractor = PatternExtractor()
        self.updater = MemoryUpdater(self.memory)
        self.optimizer = StrategyOptimizer(self.memory)

        logger.info(
            "SelfOptimizingEditor ready — %d patterns in memory, %d videos learned",
            self.memory.total_patterns,
            self.memory.total_videos_learned,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def record_upload(
        self,
        video_id: str,
        brain_result: Dict,
        video_duration_s: float = 30.0,
        niche: str = "",
    ) -> None:
        """
        Record a video upload for future learning.
        Call this immediately after the YouTube uploader returns a video_id.
        """
        entry = VideoLog.from_editor_brain_output(
            video_id=video_id,
            brain_result=brain_result,
            video_duration_s=video_duration_s,
            niche=niche,
        )
        self.video_log.write(entry)
        logger.info("SelfOptimizingEditor: recorded upload %s", video_id)

    def run_learning_pass(self, max_videos: int = 20) -> Dict:
        """
        Run the full learning loop for all pending videos.

        Args:
            max_videos: safety cap — process at most this many per call

        Returns:
            summary dict with counts for each stage
        """
        pending = self.video_log.pending_analytics()
        if not pending:
            logger.info("SelfOptimizingEditor: no videos pending analytics")
            return {"pending": 0, "processed": 0, "skipped": 0, "patterns_added": 0}

        to_process = pending[:max_videos]
        logger.info(
            "SelfOptimizingEditor: %d videos pending, processing %d",
            len(pending),
            len(to_process),
        )

        all_patterns: List[Dict] = []
        processed = 0
        skipped = 0

        for video_id in to_process:
            log_entry = self.video_log.read(video_id)
            if not log_entry:
                skipped += 1
                continue

            duration_s = log_entry.get("video_duration_s", 30.0)

            # Step 1: fetch analytics
            snapshot = self.analytics.fetch(video_id, video_duration_s=duration_s)
            if snapshot is None:
                logger.info(
                    "SelfOptimizingEditor: no data yet for %s — will retry later",
                    video_id,
                )
                skipped += 1
                continue

            # Step 2: analyze retention
            retention = self.analyzer.analyze(snapshot)
            if retention is None:
                logger.info(
                    "SelfOptimizingEditor: retention curve too short for %s", video_id
                )
                self.video_log.mark_analytics_fetched(video_id)
                skipped += 1
                continue

            # Step 3: extract patterns
            patterns = self.extractor.extract(retention, log_entry)
            all_patterns.extend(patterns)

            self.video_log.mark_analytics_fetched(video_id)
            processed += 1

            # Throttle API calls
            time.sleep(_API_CALL_DELAY_S)

        # Step 4 + 5: update memory and rebuild scores
        update_summary: Dict = {"patterns_updated": 0}
        if all_patterns:
            update_summary = self.updater.update(all_patterns, save=True)

        result = {
            "pending": len(pending),
            "processed": processed,
            "skipped": skipped,
            "patterns_added": update_summary.get("patterns_updated", 0),
            "new_patterns": update_summary.get("new_patterns", 0),
            "total_in_memory": self.memory.total_patterns,
            "total_videos": self.memory.total_videos_learned,
        }
        logger.info("SelfOptimizingEditor learning pass: %s", result)
        return result

    def get_optimization_hints(self) -> Dict:
        """
        Return the current optimization signals for use at edit time.
        Returns a cold signal if memory is insufficient.

        Callers: orchestrator.py, before EditorBrainV3.process()
        """
        return self.optimizer.build_signals()

    def get_arc_score(self, arc_type: str) -> float:
        """Return the learned EWMA score for an arc type [0,1]."""
        return self.memory.get_arc_score(arc_type)

    def get_persona_score(self, persona: str) -> float:
        """Return the learned EWMA score for a persona [0,1]."""
        return self.memory.get_persona_score(persona)

    def status(self) -> Dict:
        """Return a brief health summary of the learning subsystem."""
        return {
            "total_patterns": self.memory.total_patterns,
            "total_videos": self.memory.total_videos_learned,
            "memory_cold": self.memory.total_videos_learned < 5,
            "pending_analytics": len(self.video_log.pending_analytics()),
            "top_arc": (self._top_arc() or "none"),
            "top_persona": (self._top_persona() or "none"),
        }

    # ── Private helpers ────────────────────────────────────────────────────────

    def _top_arc(self) -> Optional[str]:
        scores = self.memory._data.get("arc_scores", {})
        if not scores:
            return None
        return max(scores, key=scores.get)

    def _top_persona(self) -> Optional[str]:
        scores = self.memory._data.get("persona_scores", {})
        if not scores:
            return None
        return max(scores, key=scores.get)
