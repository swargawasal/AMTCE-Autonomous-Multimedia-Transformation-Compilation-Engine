"""
Visual_Refinement_Modules/motion_detector.py
--------------------------------------------
Robust Motion Detection Engine for AMTCE.

Computes frame-to-frame motion energy (pixel-wise intensity change).
Provides 'motion_scores' used by MomentMiner, RetentionEngine, etc.
Supports multi-clip tagging with clip_id.
"""

import cv2
import logging
import numpy as np
import os
from typing import Dict, List, Optional

logger = logging.getLogger("motion_detector")

class MotionDetector:
    """
    Analyzes video motion intensity using pixel-wise absolute difference.
    Normalization: 0.0 (static) to 1.0 (extreme movement).
    """

    def __init__(self, sampling_rate: int = 2):
        """
        Args:
            sampling_rate: Analyze every Nth frame for performance.
        """
        self.sampling_rate = sampling_rate

    def detect_motion(self, video_path: str, clip_id: int = 0) -> List[Dict]:
        """
        Calculates per-frame motion energy.
        
        Returns:
            List of {"time": float, "score": float, "clip_id": int}
        """
        if not os.path.exists(video_path):
            logger.error(f"❌ [MOTION] Video not found: {video_path}")
            return []

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"❌ [MOTION] Could not open video: {video_path}")
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0
            
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps
        
        scores = []
        prev_gray = None
        frame_idx = 0
        
        logger.info(f"🎬 [MOTION] Analyzing {os.path.basename(video_path)}... (dur={duration:.1f}s)")

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            # Sub-sample frames for performance
            if frame_idx % self.sampling_rate == 0:
                time_sec = round(frame_idx / fps, 3)
                
                # Pre-processing: Grayscale and Resize (speed + noise reduction)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, (128, 128))
                gray = cv2.GaussianBlur(gray, (5, 5), 0)
                
                if prev_gray is not None:
                    # Calculate absolute difference
                    diff = cv2.absdiff(gray, prev_gray)
                    
                    # Compute mean movement intensity
                    # We normalize roughly: mean pixel diff of 30+ is heavy motion
                    mean_val = np.mean(diff)
                    norm_score = min(1.0, mean_val / 35.0)
                    
                    scores.append({
                        "time": time_sec,
                        "score": round(float(norm_score), 4),
                        "clip_id": clip_id
                    })
                
                prev_gray = gray
                
            frame_idx += 1
            
        cap.release()
        
        # Smoothing: apply a simple moving average to filter noise
        if len(scores) > 2:
            scores = self._smooth_scores(scores)
            
        logger.info(f"✅ [MOTION] Analysis complete: {len(scores)} data points.")
        return scores

    def _smooth_scores(self, scores: List[Dict], window: int = 3) -> List[Dict]:
        """Simple moving average smoothing."""
        if len(scores) <= window:
            return scores
            
        smoothed = []
        for i in range(len(scores)):
            start = max(0, i - window // 2)
            end = min(len(scores), i + window // 2 + 1)
            avg_score = sum(s["score"] for s in scores[start:end]) / (end - start)
            
            entry = scores[i].copy()
            entry["score"] = round(avg_score, 4)
            smoothed.append(entry)
            
        return smoothed

def get_motion_detector(sampling_rate: int = 2) -> MotionDetector:
    return MotionDetector(sampling_rate=sampling_rate)
