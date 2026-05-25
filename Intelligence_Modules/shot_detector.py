import logging
import os
import re
from typing import List, Dict, Any

try:
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector, AdaptiveDetector
    SCENEDETECT_AVAILABLE = True
    ADAPTIVE_DETECTOR_AVAILABLE = True
except ImportError:
    SCENEDETECT_AVAILABLE = False
    ADAPTIVE_DETECTOR_AVAILABLE = False
    open_video = None
    SceneManager = None
    ContentDetector = None
    AdaptiveDetector = None

logger = logging.getLogger("shot_detector")

MIN_SHOT_DURATION = 0.8
MAX_SHOT_LENGTH = 6.0

def detect_shots(video_path: str) -> List[Dict]:
    """
    PHASE 2 — Shot Boundary Detection.
    Priority: scenedetect (fast, accurate) → OpenCV (reliable fallback).
    Gemini is NOT used here — shot detection is a deterministic problem, not an intelligence problem.
    """
    if not os.path.exists(video_path):
        logger.error(f"❌ Video not found: {video_path}")
        return []

    # 1. Try scenedetect (zero API cost, accurate for hard cuts)
    if SCENEDETECT_AVAILABLE:
        try:
            shots = _detect_with_scenedetect(video_path)
            if shots:
                return shots
            logger.warning("⚠️ scenedetect returned no shots, trying OpenCV fallback...")
        except Exception as e:
            logger.warning(f"⚠️ scenedetect failed: {e}, trying OpenCV fallback...")

    # 2. Fallback to OpenCV frame difference detection
    return _detect_with_opencv(video_path)


def _detect_with_gemini(video_path: str) -> List[Dict]:
    """Internal: Detect true semantic scene boundaries using Gemini Vision."""
    try:
        import cv2
        from PIL import Image
        import json
        from Intelligence_Modules.gemini_governor import gemini_router
        if not gemini_router:
            return []
    except ImportError:
        return []

    logger.info("🎬 [SCENE_DETECT] Requesting Gemini Vision semantic analysis...")

    # 1. Uniform Frame Extraction with chronological timestamps
    num_frames = 12
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = total_frames / fps

    if duration <= 0 or total_frames < num_frames:
        cap.release()
        return []

    indices = [int(i * total_frames / num_frames) for i in range(num_frames)]
    
    frames = []
    payload = []

    prompt_text = f"""SYSTEM ROLE:
You are a professional film editor specializing in shot detection and scene boundaries.

You do NOT detect pixel changes.
You detect MEANINGFUL visual changes like a human editor.

---

INPUT:
You are given a sequence of frames from a video in chronological order.
Each frame has a timestamp.
Total video duration: {duration:.2f}s

---

YOUR TASK:
Identify timestamps where a NEW SHOT begins.

A scene change occurs when:

1. CAMERA CHANGE
* angle shift (front → side)
* zoom change
* reframing or crop change

2. SUBJECT CHANGE
* new person appears
* subject position changes significantly

3. CONTEXT CHANGE
* background changes
* location or environment shifts

4. MOTION BREAK
* continuous motion stops → new action begins

---

DO NOT DETECT:
* minor motion
* small hand movement
* micro expression changes
* lighting flicker

---

STRICT RULES:
* Return ONLY strong scene boundaries
* Minimum gap between cuts: 0.8 seconds
* Maximum cuts: 8
* Avoid over-segmentation

---

OUTPUT FORMAT (STRICT JSON):
{{
"scene_changes": [
{{
"timestamp": 3.2,
"confidence": 0.85,
"reason": "camera angle shifts from front to side view"
}}
]
}}

---

CRITICAL:
Think like a professional editor.

If cutting here improves storytelling or visual clarity → include it
If not → ignore it
"""
    payload.append(prompt_text)

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)

        time_val = (idx / total_frames) * duration
        
        # Give context to Gemini before each frame
        payload.append(f"Frame at {time_val:.2f}s:")
        payload.append(pil_img)
        frames.append(pil_img)

    cap.release()

    if not frames:
        return []

    # 2. Gemini Vision Call
    res_text = gemini_router.generate(
        task_type="vision",
        prompt=payload,
        module_name="shot_detector",
        metadata={"num_frames": len(frames), "duration": duration}
    )

    if not res_text:
        return []

    # 3. Parse and Convert to Segment dictionaries
    try:
        import re
        match = re.search(r"\{.*\}", res_text, re.DOTALL)
        data = json.loads(match.group(0) if match else res_text)
        
        scene_changes = data.get("scene_changes", [])
        if not scene_changes:
            return []
            
        # Dedupe and sort timestamps
        cuts = []
        for c in scene_changes:
            t = float(c.get("timestamp", 0.0))
            if t > 0.5 and t < duration - 0.5:  # avoid cuts at very start/end
                cuts.append(t)
        
        cuts.sort()
        
        segments = []
        prev = 0.0

        for t in cuts:
            # Enforce 0.8s minimum gap dynamically
            if t - prev >= 0.8:
                segments.append({
                    "start": prev,
                    "end": t,
                    "duration": t - prev,
                    "type": "scene_segment",
                    "source": "gemini_semantic"
                })
                prev = t

        # Append final segment
        if duration - prev >= 0.5:
            segments.append({
                "start": prev,
                "end": duration,
                "duration": duration - prev,
                "type": "scene_segment",
                "source": "gemini_semantic"
            })
            
        return segments
    except Exception as e:
        logger.warning(f"⚠️ Failed to parse Gemini semantic scene boundaries: {e}")
        return []


def _detect_with_scenedetect(video_path: str) -> List[Dict]:
    """Internal: Detect shots using pyscenedetect with adaptive escalation."""
    video = open_video(video_path)
    
    # 1. Primary Pass: ContentDetector (Fast, Standard)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=27))
    scene_manager.detect_scenes(video=video)
    scenes = scene_manager.get_scene_list()
    
    # 2. Adaptive Pass: Retry with AdaptiveDetector if nothing found
    if not scenes and ADAPTIVE_DETECTOR_AVAILABLE:
        logger.info("⚡ [SHOT_DETECT] ContentDetector returned 0 scenes, retrying with AdaptiveDetector...")
        scene_manager = SceneManager()
        scene_manager.add_detector(AdaptiveDetector())
        # Reset video to start for re-detection
        video.seek(0)
        scene_manager.detect_scenes(video=video)
        scenes = scene_manager.get_scene_list()
        
    # 3. Sensitive Pass: Retry ContentDetector with lower threshold
    if not scenes:
        logger.info("⚡ [SHOT_DETECT] Still 0 scenes, retrying with sensitive ContentDetector (threshold=18)...")
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=18))
        video.seek(0)
        scene_manager.detect_scenes(video=video)
        scenes = scene_manager.get_scene_list()

    # 4. Safety Net: If still nothing, treat entire video as one shot if valid
    if not scenes:
        try:
            duration = video.duration.get_seconds()
            if duration > 1.0:
                logger.info(f"🛡️ [SHOT_DETECT] No cuts found after escalation. Treating whole video ({duration:.1f}s) as one shot.")
                # We need to create dummy scenes using Timecode if available, or just use seconds
                # But get_scene_list items are expected to have get_seconds()
                # So we simulate a scene object
                class DummyTime:
                    def __init__(self, s): self.s = s
                    def get_seconds(self): return self.s
                
                scenes = [(DummyTime(0.0), DummyTime(duration))]
        except Exception as e:
            logger.warning(f"⚠️ [SHOT_DETECT] Safety net failed: {e}")

    raw_shots = []
    for scene in scenes:
        start, end = scene[0].get_seconds(), scene[1].get_seconds()
        raw_shots.append({"start": start, "end": end, "duration": end - start})

    # Split long shots (force pacing)
    final_shots = []
    for shot in raw_shots:
        if shot["duration"] > MAX_SHOT_LENGTH:
            final_shots.extend(_split_long_shot(shot))
        else:
            final_shots.append(shot)

    # Filter micro-shots
    valid_shots = [s for s in final_shots if s["duration"] >= MIN_SHOT_DURATION]
    
    logger.info(f"🎬 [SHOT_DETECTION] shots_found={len(valid_shots)} (scenedetect escalation)")
    if len(final_shots) > len(valid_shots):
        logger.info(f"✂️ [SHOT_FILTER] removed_short_shots={len(final_shots) - len(valid_shots)}")
        
    return valid_shots


def _detect_with_opencv(video_path: str) -> List[Dict]:
    """
    Fallback shot detection using OpenCV frame differencing.
    Used when scenedetect fails or is unavailable.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.error("❌ OpenCV not available for shot detection fallback")
        return []
    
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"❌ Cannot open video: {video_path}")
            return []
        
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        if total_frames <= 0:
            logger.error("❌ Video has no frames")
            return []
        
        # Parameters for shot detection
        hist_threshold = 0.7  # Histogram correlation threshold
        min_shot_frames = int(MIN_SHOT_DURATION * fps)
        
        shots = []
        shot_start_frame = 0
        prev_hist = None
        
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            # Convert to HSV and calculate histogram
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
            hist = cv2.normalize(hist, hist).flatten()
            
            if prev_hist is not None:
                # Calculate correlation between consecutive frames
                correlation = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                
                # Shot boundary detected
                if correlation < hist_threshold and (frame_idx - shot_start_frame) >= min_shot_frames:
                    shot_end_frame = frame_idx
                    shots.append({
                        "start": shot_start_frame / fps,
                        "end": shot_end_frame / fps,
                        "duration": (shot_end_frame - shot_start_frame) / fps
                    })
                    shot_start_frame = frame_idx
            
            prev_hist = hist
            frame_idx += 1
            
            # Process every Nth frame for speed (adaptive sampling)
            if frame_idx % 3 != 0:
                continue
        
        # Add final shot
        if frame_idx > shot_start_frame:
            shots.append({
                "start": shot_start_frame / fps,
                "end": frame_idx / fps,
                "duration": (frame_idx - shot_start_frame) / fps
            })
        
        cap.release()
        
        # Split long shots
        final_shots = []
        for shot in shots:
            if shot["duration"] > MAX_SHOT_LENGTH:
                final_shots.extend(_split_long_shot(shot))
            else:
                final_shots.append(shot)
        
        # Filter micro-shots
        valid_shots = [s for s in final_shots if s["duration"] >= MIN_SHOT_DURATION]
        
        logger.info(f"🎬 [SHOT_DETECTION] shots_found={len(valid_shots)} (OpenCV fallback)")
        return valid_shots
        
    except Exception as e:
        logger.warning(f"[SHOT_DETECTION_FAILED] fallback=motion_editor | Reason: {e}")
        return []


def _split_long_shot(shot: Dict) -> List[Dict]:
    """
    Helper to split shots > 6s at midpoint or motion spikes.
    For now, uses midpoint as fallback; future could pass motion_data if available early.
    """
    logger.info(f"📐 [SHOT_DETECT_SPLIT] shot_too_long={round(shot['duration'], 1)}s")
    mid = shot["start"] + (shot["duration"] / 2.0)
    return [
        {"start": shot["start"], "end": mid, "duration": mid - shot["start"]},
        {"start": mid, "end": shot["end"], "duration": shot["end"] - mid}
    ]

def score_shots(shots: List[Dict], motion_data: List[Dict], face_data: List[Dict], gemini_data: Dict) -> List[Dict]:
    """
    PHASE 3 — Multi-modal scoring with normalization.
    Weights: 0.35 Motion, 0.30 Face, 0.35 Gemini
    """
    for shot in shots:
        start, end = shot["start"], shot["end"]

        # 1. Motion Score (Normalized 0.0-1.0)
        m_score = _get_motion_score(motion_data, start, end)
        
        # 2. Face Score (Normalized 0.0-1.0)
        f_score = _get_face_score(face_data, start, end)
        
        # 3. Gemini Importance (Normalized 0.0-1.0)
        g_score = _get_gemini_score(gemini_data, start, end)

        # 4. Normalized Weighted Score
        shot["score"] = (0.35 * m_score) + (0.30 * f_score) + (0.35 * g_score)
        
        # 5. Gemini Priority Override (Force Include if semantic importance > 0.8)
        if g_score > 0.8:
            shot["score"] = max(shot["score"], 0.95)
            logger.info(f"💎 [GEMINI_PRIORITY] Overriding score for shot {round(start, 1)}s due to high importance.")

        shot["debug"] = {"m": round(m_score, 2), "f": round(f_score, 2), "g": round(g_score, 2)}

    # Sort by score descending for selection
    shots.sort(key=lambda x: x["score"], reverse=True)
    if shots:
        logger.info(f"🏆 [SHOT_SCORING] highest_score={round(shots[0]['score'], 2)}")
    
    return shots

def select_best_shots(shots: List[Dict], target_duration: float = 12.0) -> List[Dict]:
    """
    PHASE 4 — Select shots with exact duration budget guard.
    Includes Cold Open (highest scoring shot first).
    Remaining shots are sorted chronologically.
    """
    if not shots: return []

    selected = []
    current_duration = 0.0
    
    # 1. Hook/Cold Open (The absolute best shot)
    best_shot = shots[0]
    best_shot_dur = min(best_shot["duration"], target_duration)
    
    # Trim best shot if it exceeds target on its own
    if best_shot["duration"] > target_duration:
         best_shot["end"] = best_shot["start"] + target_duration
         best_shot["duration"] = target_duration

    selected.append(best_shot)
    current_duration += best_shot["duration"]
    
    # 2. Context & Highlights (Next best shots by score)
    # Remaining candidates already sorted by score
    remaining_candidates = [s for s in shots[1:]] 
    
    for shot in remaining_candidates:
        if current_duration >= target_duration:
            break
            
        remaining_budget = target_duration - current_duration
        
        if shot["duration"] > remaining_budget:
            # Trim the last shot to fit exactly the budget
            shot["end"] = shot["start"] + remaining_budget
            shot["duration"] = remaining_budget
            logger.info(f"✂️ [BUDGET_GUARD] Trimming final shot to {round(remaining_budget, 1)}s")
            
        selected.append(shot)
        current_duration += shot["duration"]

    # 3. Final Ordering (Cold Open first, others chronological)
    cold_open = selected[0]
    others = sorted(selected[1:], key=lambda x: x["start"])
    final_sequence = [cold_open] + others

    logger.info(f"✨ [SHOT_SELECTION] selected_shots={len(final_sequence)} | final_duration={round(current_duration, 1)}s")
    return final_sequence

def snap_to_beats(shots: List[Dict], beats: List[float], tolerance: float = 0.35) -> List[Dict]:
    """
    PHASE 5 — Snap shot boundaries to beats if within tolerance.
    """
    if not beats: return shots

    for shot in shots:
        for key in ["start", "end"]:
            orig = shot[key]
            # Find nearest beat
            nearest = min(beats, key=lambda b: abs(b - orig))
            if abs(nearest - orig) < tolerance:
                shot[key] = nearest
                if key == "start":
                    logger.info(f"[SHOT_BEAT_SYNC] shot_start={orig:.2f} → snapped={nearest:.2f}")

    return shots

def _get_motion_score(motion_data: List[Dict], start: float, end: float) -> float:
    if not motion_data: return 0.5
    
    strength_map = {"large": 1.0, "medium": 0.6, "small": 0.3}
    relevant = [m for m in motion_data if start <= m.get("time", -1) <= end]
    
    if not relevant: return 0.2
    
    # Return max strength found in the shot
    return max(strength_map.get(m.get("strength"), 0.3) for m in relevant)

def _get_face_score(face_data: List[Dict], start: float, end: float) -> float:
    if not face_data: return 0.0
    
    relevant = [f for f in face_data if start <= f.get("time", -1) <= end]
    if not relevant: return 0.0
    
    # Return 1.0 if any face, maybe more if specific types?
    return 1.0

def _get_gemini_score(gemini_data: Dict, start: float, end: float) -> float:
    """
    Check overlap with Gemini editing_plan segments.
    """
    if not gemini_data: return 0.5
    
    segments = gemini_data.get("editing_plan", {}).get("segments", [])
    if not segments: return 0.5
    
    max_overlap = 0.0
    for seg in segments:
        s_start, s_end = seg.get("start", 0), seg.get("end", 0)
        # Calculate intersection
        overlap_start = max(start, s_start)
        overlap_end = min(end, s_end)
        overlap_dur = max(0.0, overlap_end - overlap_start)
        
        if overlap_dur > 0:
            # Overlap ratio relative to Gemini segment
            seg_dur = max(0.1, s_end - s_start)
            max_overlap = max(max_overlap, overlap_dur / seg_dur)
            
    return max(0.3, max_overlap) # Base 0.3 if no overlap but Gemini ran
