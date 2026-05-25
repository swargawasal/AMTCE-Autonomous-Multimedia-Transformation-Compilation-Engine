import threading
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger("pipeline_context")

@dataclass
class PipelineContext:
    """
    Centralized State Management for the Autonomous Video Processing Pipeline.
    All modules must read/write data through this object.
    """
    video_path: str = ""
    clean_video_path: Optional[str] = None
    feature_flags: Dict[str, Any] = field(default_factory=dict)
    intelligence_data: Dict[str, Any] = field(default_factory=dict)
    trend_data: Dict[str, Any] = field(default_factory=dict)
    segments: List[Dict[str, Any]] = field(default_factory=list)
    audio_analysis: Dict[str, Any] = field(default_factory=dict)
    
    # Internal locks for safe mutation if multi-threading is used later
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def set_clean_source(self, path: str):
        with self._lock:
            self.clean_video_path = path

    def update_feature_flags(self, flags: Dict[str, Any]):
        with self._lock:
            self.feature_flags.update(flags)

    def update_intelligence_data(self, data: Dict[str, Any]):
        with self._lock:
            self.intelligence_data.update(data)

    def set_segments(self, segments: List[Dict[str, Any]]):
        with self._lock:
            self.segments = segments

    def update_audio_analysis(self, data: Dict[str, Any]):
        with self._lock:
            self.audio_analysis.update(data)
            
    def get_current_source(self) -> str:
        """Returns the cleanest available video source."""
        return self.clean_video_path if self.clean_video_path else self.video_path

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "video_path": self.video_path,
                "clean_video_path": self.clean_video_path,
                "feature_flags": self.feature_flags,
                "intelligence_data": self.intelligence_data,
                "segments_count": len(self.segments),
                "audio_analysis_keys": list(self.audio_analysis.keys())
            }
