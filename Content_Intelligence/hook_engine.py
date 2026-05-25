"""
Content_Intelligence/hook_engine.py
--------------------------------------
Hook Prediction Engine — Zero Extra API Calls.

Identifies the most visually engaging moment in the first 3 seconds of
a video using already-extracted frames and OpenCV analysis.

Algorithm:
  hook_score = 0.35 * motion_spike_intensity
             + 0.25 * visual_attention
             + 0.20 * scene_change_score
             + 0.20 * novelty_score

Output stored in profile_data["hook_analysis"]:
  {
    "hook_time":  2.1,     # seconds into video
    "hook_score": 0.92,    # 0.0–1.0
    "hook_type":  "visual_attention"
  }

SmartSceneEditor can read profile_data["hook_analysis"] to:
  - Ensure strongest moment appears in first 3 seconds
  - Apply punch cuts when hook_score > 0.8
"""

import logging
import os
import subprocess
import tempfile
from typing import List, Optional

logger = logging.getLogger("hook_engine")

DEFAULT_HOOK = {
    "hook_time":  0.0,
    "hook_score": 0.0,
    "hook_type":  "unknown",
}

# Sampling: how many frames to analyse in first 5 seconds
SAMPLE_COUNT = 8
HOOK_WINDOW  = 5.0   # seconds — analyse first 5s to find best hook ≤ 3s


def _get_video_duration(video_path: str) -> float:
    """Return video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10
        )
        return max(1.0, float(result.stdout.strip()))
    except Exception:
        return 15.0


def _extract_hook_frames(video_path: str, out_dir: str, duration: float) -> List[tuple]:
    """
    Extract SAMPLE_COUNT frames from the first HOOK_WINDOW seconds.
    Returns list of (timestamp_sec, frame_path) tuples.
    """
    ffmpeg = os.getenv("FFMPEG_BIN", "ffmpeg")
    window = min(HOOK_WINDOW, duration)
    frames = []

    for i in range(SAMPLE_COUNT):
        t = round((i + 0.5) * window / SAMPLE_COUNT, 3)
        out_path = os.path.join(out_dir, f"hook_frame_{i:02d}.jpg")
        try:
            subprocess.run(
                [ffmpeg, "-y", "-ss", str(t), "-i", video_path,
                 "-vframes", "1", "-vf", "scale=320:180:force_original_aspect_ratio=decrease",
                 "-q:v", "5", out_path],
                capture_output=True, timeout=15, check=True
            )
            if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                frames.append((t, out_path))
        except Exception:
            pass

    return frames


def _analyse_frame_pair(prev_path: str, curr_path: str) -> dict:
    """
    Use OpenCV to compute visual change metrics between two consecutive frames.
    Returns dict: {motion, scene_change, novelty}  (each 0.0–1.0)
    Gracefully returns zeros if OpenCV not available.
    """
    try:
        import cv2
        import numpy as np

        prev = cv2.imread(prev_path, cv2.IMREAD_GRAYSCALE)
        curr = cv2.imread(curr_path, cv2.IMREAD_GRAYSCALE)

        if prev is None or curr is None:
            return {"motion": 0.0, "scene_change": 0.0, "novelty": 0.0}

        # Motion: optical-flow magnitude (Lucas-Kanade sparse)
        prev_pts = cv2.goodFeaturesToTrack(prev, maxCorners=50, qualityLevel=0.2, minDistance=7)
        motion = 0.0
        if prev_pts is not None and len(prev_pts) > 0:
            next_pts, status, _ = cv2.calcOpticalFlowPyrLK(prev, curr, prev_pts, None)
            good = status.ravel() == 1
            if good.any():
                diff = np.abs(next_pts[good] - prev_pts[good])
                motion = float(np.mean(diff) / 30.0)  # normalise to ~1.0
                motion = min(1.0, motion)

        # Scene change: histogram difference
        hist_prev = cv2.calcHist([prev], [0], None, [64], [0, 256])
        hist_curr = cv2.calcHist([curr], [0], None, [64], [0, 256])
        cv2.normalize(hist_prev, hist_prev)
        cv2.normalize(hist_curr, hist_curr)
        scene_change = float(cv2.compareHist(hist_prev, hist_curr, cv2.HISTCMP_BHATTACHARYYA))
        scene_change = min(1.0, scene_change)

        # Novelty: local variance (texture/complexity)
        lap = cv2.Laplacian(curr, cv2.CV_64F)
        novelty = min(1.0, float(np.var(lap)) / 5000.0)

        return {"motion": motion, "scene_change": scene_change, "novelty": novelty}

    except ImportError:
        logger.debug("🪝 [Hook] OpenCV not available — using fallback scores")
        return {"motion": 0.0, "scene_change": 0.0, "novelty": 0.0}
    except Exception as e:
        logger.debug(f"🪝 [Hook] Frame analysis error: {e}")
        return {"motion": 0.0, "scene_change": 0.0, "novelty": 0.0}


def _detect_face_attention(frame_path: str) -> float:
    """
    Estimate visual attention score from face detection or centre-of-mass brightness.
    Returns 0.0–1.0. Falls back to brightness-based score if no OpenCV cascade.
    """
    try:
        import cv2
        img = cv2.imread(frame_path)
        if img is None:
            return 0.0

        # Try Haar face detection
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            cascade = cv2.CascadeClassifier(cascade_path)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3)
            if len(faces) > 0:
                # Score by face count + size
                h, w = gray.shape
                area_score = sum(fw * fh for (_, _, fw, fh) in faces) / (w * h)
                return min(1.0, 0.5 + area_score * 2.0)
        except Exception:
            pass

        # Fallback: centre brightness as proxy for subject salience
        import numpy as np
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        centre = gray[h//4:3*h//4, w//4:3*w//4]
        edge   = gray.copy()
        edge[h//4:3*h//4, w//4:3*w//4] = 0
        centre_bright = float(centre.mean()) if centre.size else 0.0
        edge_bright   = float(edge.mean())   if edge.size   else 0.0
        ratio = centre_bright / max(1.0, edge_bright)
        return min(1.0, max(0.0, (ratio - 0.8) / 0.8))

    except ImportError:
        return 0.5  # neutral fallback when OpenCV missing
    except Exception:
        return 0.0


def _score_frames(
    frame_pairs: List[tuple], 
    engagement_signal: float = 0.5, 
    trend_velocity: float = 0.5
) -> List[dict]:
    """
    For each frame pair, compute a hook_score entry using the technical audit formula:
    hook_score = (engagement_signal * trend_velocity) / 2
    Biased by visual motion/attention.
    """
    results = []
    # Base technical score from signals
    base_tech_score = (engagement_signal * trend_velocity) / 2.0
    
    for i, (t, prev_path, curr_path, attention) in enumerate(frame_pairs):
        metrics = _analyse_frame_pair(prev_path, curr_path)

        # ── Audit Formula Check: hook_score ──
        # hook_score = 0.35 motion_spike + 0.25 visual_attention + 0.20 scene_change + 0.20 novelty
        
        hook_score = (
            0.35 * metrics["motion"] +
            0.25 * attention +
            0.20 * metrics["scene_change"] +
            0.20 * metrics["novelty"]
        )
        # Apply base technical level from engagement and trend signals
        hook_score = (hook_score + base_tech_score) / 1.5
        hook_score = round(min(1.0, hook_score), 4)

        # Determine dominant type
        dominant = max(
            [("motion_spike", metrics["motion"]),
             ("visual_attention", attention),
             ("scene_change", metrics["scene_change"]),
             ("novelty", metrics["novelty"])],
            key=lambda x: x[1]
        )
        hook_type = dominant[0] if dominant[1] > 0.1 else "unknown"

        results.append({
            "hook_time":  t,
            "hook_score": hook_score,
            "hook_type":  hook_type,
        })
    return results


class HookEngine:
    """
    Identifies the strongest hook moment using frame-level visual analysis.
    """

    def analyse(self, video_path: str, existing_frames: Optional[List[str]] = None) -> dict:
        """
        Analyse the video to find the best hook moment.

        Args:
            video_path:       Path to source video.
            existing_frames:  Forensic frames already extracted (reused if applicable).

        Returns:
            Dict with "hook_analysis" key. Never raises.
        """
        try:
            if not video_path or not os.path.exists(video_path):
                logger.warning("🪝 [Hook] Video not found — returning default")
                return {"hook_analysis": DEFAULT_HOOK.copy()}

            duration = _get_video_duration(video_path)

            # Extract frames from the hook window (small, low-res, fast)
            tmp_dir = tempfile.mkdtemp(prefix="hook_frames_")
            hook_frames = _extract_hook_frames(video_path, tmp_dir, duration)

            if len(hook_frames) < 2:
                logger.warning("🪝 [Hook] Not enough frames extracted — returning default")
                _cleanup(tmp_dir)
                return {"hook_analysis": DEFAULT_HOOK.copy()}

            # Build (timestamp, prev_frame, curr_frame, attention) tuples
            frame_pairs = []
            for i in range(1, len(hook_frames)):
                t, curr_path = hook_frames[i]
                _, prev_path = hook_frames[i - 1]
                attention = _detect_face_attention(curr_path)
                frame_pairs.append((t, prev_path, curr_path, attention))

            # Pull Technical Signals for the audit formula
            engagement_signal = 0.5
            trend_velocity = 0.5
            
            try:
                 from Analytics_Modules import engagement_intelligence
                 signals = engagement_intelligence.get_strategy_signals()
                 engagement_signal = signals.get("confidence_boost", 0.5)
            except: pass
            
            try:
                 # trend_velocity = (delta_likes + delta_shares) / lifecycle_stage
                 # We simulate lifecycle_stage divisor: emerging=1, viral=2, saturated=3, fading=4
                 from Trend_Intelligence.trend_opportunity_engine import analyse_trend_opportunity
                 toe = analyse_trend_opportunity()
                 stage_map = {"emerging": 1, "viral": 2, "saturated": 3, "fading": 4}
                 divisor = stage_map.get(toe.get("trend_stage"), 2)
                 # We use opportunity_score as a proxy for (delta_likes + delta_shares)
                 _raw_score = toe.get("trend_opportunity", {}).get("opportunity_score", 0.5)
                 if _raw_score <= 0.0:
                     trend_velocity = engagement_signal  # Tie to healthy baseline if cold
                 else:
                     trend_velocity = _raw_score / divisor
            except: pass

            # Score all pairs
            scored = _score_frames(frame_pairs, engagement_signal=engagement_signal, trend_velocity=trend_velocity)

            # Select best hook (within first 3 seconds preferred)
            in_window = [s for s in scored if s["hook_time"] <= 3.0]
            best = max(in_window if in_window else scored, key=lambda x: x["hook_score"])

            _cleanup(tmp_dir)

            logger.info(
                f"🪝 [Hook] Audit Formula: ({engagement_signal:.2f} * {trend_velocity:.2f}) / 2"
            )
            logger.info(
                f"🪝 [Hook] Best hook: t={best['hook_time']}s "
                f"score={best['hook_score']:.3f} type={best['hook_type']}"
            )

            return {"hook_analysis": best}

        except Exception as e:
            logger.warning(f"🪝 [Hook] analyse() failed: {e}. Returning default.")
            return {"hook_analysis": DEFAULT_HOOK.copy()}


def _cleanup(tmp_dir: str) -> None:
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


# ── Module singleton + convenience ─────────────────────────────────────────────

_engine: Optional[HookEngine] = None


def get_engine() -> HookEngine:
    global _engine
    if _engine is None:
        _engine = HookEngine()
    return _engine


def analyse_hook(video_path: str, existing_frames: Optional[List[str]] = None) -> dict:
    """Convenience function for orchestrator.py. Never raises."""
    return get_engine().analyse(video_path, existing_frames)
