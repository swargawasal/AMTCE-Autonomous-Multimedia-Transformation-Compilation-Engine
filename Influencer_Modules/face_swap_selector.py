"""
Influencer_Modules/face_swap_selector.py
-----------------------------------------
Scores a video against four face-swap eligibility conditions.

Conditions checked (composite score 0.0 – 1.0):
    1. Front-face ratio  (weight 0.35)  – % of sampled frames with a detectable face
    2. Motion score      (weight 0.30)  – optical-flow magnitude (low = still camera)
    3. Lighting stability(weight 0.20)  – brightness standard deviation across frames
    4. Extreme expression(weight 0.15)  – mouth-open ratio (proxy for OTT expressions)

Called by the Telegram "🎭 Face Swap & Post" button handler via asyncio.to_thread.
No side effects. Pure function – returns (score, details, eligible).

.env:
    INFLUENCER_REFINEMENT_MIN_SCORE=0.60   (eligibility threshold, default 0.60)
"""

import logging
import os
from typing import Tuple, Dict, Any

logger = logging.getLogger("influencer.face_swap_selector")


def score_video(
    video_path: str,
) -> Tuple[float, Dict[str, Any], bool]:
    """
    Score a video for face-swap eligibility.

    Returns
    -------
    score    : float  – composite 0.0 – 1.0
    details  : dict   – per-dimension breakdown
    eligible : bool   – score >= threshold
    """
    threshold = float(os.getenv("INFLUENCER_REFINEMENT_MIN_SCORE", "0.60"))

    details: Dict[str, Any] = {
        "threshold": threshold,
        "front_face":  {"value": 0.0, "pass": False, "label": "0% frames"},
        "motion":      {"value": 0.0, "pass": False, "label": "0.0 avg flow"},
        "lighting":    {"value": 0.0, "pass": False, "label": "var=0"},
        "expression":  {"value": 0.0, "pass": False, "label": "0% extreme"},
    }

    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("[SELECTOR] OpenCV not installed — cannot score video.")
        details["error"] = "OpenCV not installed"
        return 0.0, details, False

    if not os.path.isfile(video_path):
        details["error"] = f"File not found: {video_path}"
        return 0.0, details, False

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        details["error"] = "Cannot open video"
        return 0.0, details, False

    fps      = cap.get(cv2.CAP_PROP_FPS) or 24.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = n_frames / fps

    if duration < 3.0:
        cap.release()
        details["error"] = f"Video too short ({duration:.1f}s < 3s minimum)"
        return 0.0, details, False

    # Sample every 4th frame for speed
    frames, frame_idx = [], 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % 4 == 0:
            frames.append(frame)
        frame_idx += 1
    cap.release()

    if not frames:
        details["error"] = "No frames could be read"
        return 0.0, details, False

    # ── Dimension 1: Front-face ratio ─────────────────────────────────────
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    face_count = 0
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=3, minSize=(40, 40))
        if len(faces) > 0:
            face_count += 1
    face_ratio = face_count / len(frames)

    if face_ratio >= 0.60:
        ff_score = 0.35
        ff_pass  = True
    elif face_ratio >= 0.30:
        ff_score = 0.15
        ff_pass  = False
    else:
        ff_score = 0.0
        ff_pass  = False

    details["front_face"] = {
        "value": face_ratio,
        "pass":  ff_pass,
        "label": f"{face_ratio*100:.0f}% of sampled frames",
    }

    # ── Dimension 2: Motion score (optical flow) ───────────────────────────
    motion_values = []
    prev_gray = None
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            flow = cv2.calcOpticalFlowFarneback(
                prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
            )
            motion_values.append(float(np.mean(np.abs(flow))))
        prev_gray = gray

    motion_avg = float(np.mean(motion_values)) if motion_values else 0.0

    if motion_avg <= 4.0:
        mot_score = 0.30
        mot_pass  = True
    elif motion_avg <= 8.0:
        mot_score = 0.15
        mot_pass  = False
    else:
        mot_score = 0.0
        mot_pass  = False

    details["motion"] = {
        "value": motion_avg,
        "pass":  mot_pass,
        "label": f"{motion_avg:.1f} avg optical flow",
    }

    # ── Dimension 3: Lighting stability ────────────────────────────────────
    brightness_values = []
    for f in frames:
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        brightness_values.append(float(np.mean(hsv[:, :, 2])))

    lighting_std = float(np.std(brightness_values)) if brightness_values else 0.0

    if lighting_std <= 50.0:
        lit_score = 0.20
        lit_pass  = True
    elif lighting_std <= 80.0:
        lit_score = 0.10
        lit_pass  = False
    else:
        lit_score = 0.0
        lit_pass  = False

    details["lighting"] = {
        "value": lighting_std,
        "pass":  lit_pass,
        "label": f"brightness std={lighting_std:.0f}",
    }

    # ── Dimension 4: Extreme expressions (mouth-open proxy) ────────────────
    # Detected by comparing aspect ratio of the full face box vs upper half.
    # If the lower face takes > 60% of the face box height, mouth is likely open wide.
    mouth_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_smile.xml"
    )
    extreme_count = 0
    face_frames   = 0
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.05, 3, minSize=(40, 40))
        if len(faces) == 0:
            continue
        face_frames += 1
        x, y, fw, fh = faces[0]
        roi = gray[y : y + fh, x : x + fw]
        # Detect smile/open mouth in lower 50% of face region
        lower_roi = roi[fh // 2 :, :]
        smiles = mouth_cascade.detectMultiScale(lower_roi, 1.7, 11)
        if len(smiles) > 0:
            extreme_count += 1

    if face_frames > 0:
        extreme_ratio = extreme_count / face_frames
    else:
        extreme_ratio = 0.0   # no faces → can't measure, don't penalise

    if extreme_ratio < 0.30:
        expr_score = 0.15
        expr_pass  = True
    elif extreme_ratio < 0.50:
        expr_score = 0.08
        expr_pass  = False
    else:
        expr_score = 0.0
        expr_pass  = False

    details["expression"] = {
        "value": extreme_ratio,
        "pass":  expr_pass,
        "label": f"{extreme_ratio*100:.0f}% frames with open mouth",
    }

    # ── Composite score ────────────────────────────────────────────────────
    score    = ff_score + mot_score + lit_score + expr_score
    eligible = score >= threshold
    details["score"]    = score
    details["eligible"] = eligible

    logger.info(
        f"[SELECTOR] {os.path.basename(video_path)} | "
        f"score={score:.2f}/{threshold:.2f} eligible={eligible} | "
        f"face={face_ratio:.0%} motion={motion_avg:.1f} "
        f"lighting_std={lighting_std:.0f} expression={extreme_ratio:.0%}"
    )

    return score, details, eligible
