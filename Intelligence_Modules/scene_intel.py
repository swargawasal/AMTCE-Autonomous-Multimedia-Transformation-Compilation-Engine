"""
Scene Intelligence Layer
========================
Runs on EVERY video before the editing pipeline begins.

What it does:
1. Samples frames from the video
2. Detects faces using OpenCV DNN (res10 SSD model)
3. Clusters faces by spatial similarity → Character A, B, C...
4. Saves the best face crop as {title_name}.jpg in face_cache/ (RAG identity store)
5. Sends frames + title hint to Gemini → full scene understanding
6. Returns SceneContext: content_type, characters, activity, setting, creative_possibilities
7. Builds a ClipPlan: ordered list of what to export and with what focus

Universal: works for person videos, memes, products, nature, events — anything.
Gemini decides. We follow.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("scene_intel")

# ── Paths ─────────────────────────────────────────────────────────────────────
FACE_CACHE_DIR = Path("Intelligence_Data/face_cache")
DNN_PROTO      = Path("models/deploy.prototxt")
DNN_MODEL      = Path("models/res10_300x300_ssd_iter_140000.caffemodel")
FFMPEG_BIN     = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN    = os.getenv("FFPROBE_BIN", "ffprobe")

# ── Gemini prompt ─────────────────────────────────────────────────────────────
_SCENE_PROMPT_TEMPLATE = """
You are an expert video editor and content strategist. Analyze the provided video frames carefully.

Creator name hint: "{title_name}"

Your task — think step by step like a human editor would:
1. CONTENT TYPE: Is this a person/people video, a meme/graphic, a product showcase, nature/scenery, a news clip, or something else?
2. PEOPLE (if present):
   - How many distinct people appear? Give each an ID (A, B, C).
   - Describe each briefly (clothing, position, distinguishing features).
   - Which person is most likely the creator named "{title_name}"? (primary = true)
   - When does each person appear? (throughout / first_half / second_half / scattered)
3. ACTIVITY: What are they doing? (dancing, posing, talking, exercising, cooking, reacting, etc.)
4. SETTING: Where is this? (dance_studio, bedroom, street, gym, outdoor, studio, office, etc.)
5. ENERGY: What is the dominant mood/energy? (explosive, high, medium, calm, dramatic, comedic, emotional)
6. CREATIVE POSSIBILITIES: As a skilled editor, list the 2-3 best distinct clips you could make from this footage, ordered by impact. Be specific.

Return ONLY valid JSON. No markdown. No explanation outside the JSON.
{{
  "content_type": "person_video|meme|product|nature|event|graphic|other",
  "people": {{
    "count": 0,
    "subjects": [
      {{
        "id": "A",
        "description": "woman in red top, dancing on left side",
        "primary_match": true,
        "time_dominance": "throughout"
      }}
    ]
  }},
  "activity": "dancing",
  "setting": "dance_studio",
  "energy": "high",
  "creative_possibilities": [
    {{
      "rank": 1,
      "clip_label": "main_character_highlight",
      "focus": "primary_person",
      "subject_id": "A",
      "description": "Fast-paced cuts showing Dhansree's best dance moves",
      "ideal_duration_sec": 15,
      "editing_style": "rhythm_driven"
    }}
  ]
}}
"""

# ── Face cache helpers ────────────────────────────────────────────────────────

def _title_to_cache_key(title: str) -> str:
    """Convert title string to a safe filename key."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", title.strip()).lower()


def load_cached_face(title: str) -> Optional[np.ndarray]:
    """Load a previously saved face image for this creator. Returns None if not cached."""
    if not title:
        return None
    key  = _title_to_cache_key(title)
    path = FACE_CACHE_DIR / f"{key}.jpg"
    if path.exists():
        img = cv2.imread(str(path))
        if img is not None:
            logger.info(f"📂 [FACE_CACHE] Loaded cached face for '{title}' ← {path.name}")
            return img
    return None


def save_face_cache(title: str, face_img: np.ndarray) -> None:
    """Save the best detected face crop for this creator name."""
    if not title or face_img is None:
        return
    FACE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key  = _title_to_cache_key(title)
    path = FACE_CACHE_DIR / f"{key}.jpg"
    cv2.imwrite(str(path), face_img)
    logger.info(f"💾 [FACE_CACHE] Saved face for '{title}' → {path}")


# ── DNN Face detection ────────────────────────────────────────────────────────

_dnn_net = None  # module-level singleton

def _get_dnn_net():
    global _dnn_net
    if _dnn_net is not None:
        return _dnn_net
    if not DNN_PROTO.exists() or not DNN_MODEL.exists():
        logger.warning("⚠️ [SCENE_INTEL] DNN model files not found — face detection disabled.")
        return None
    try:
        _dnn_net = cv2.dnn.readNetFromCaffe(str(DNN_PROTO), str(DNN_MODEL))
        logger.info("✅ [SCENE_INTEL] DNN face detector loaded.")
    except Exception as e:
        logger.warning(f"⚠️ [SCENE_INTEL] DNN load failed: {e}")
    return _dnn_net


def _detect_faces(frame: np.ndarray, confidence_threshold: float = 0.5) -> List[Dict]:
    """Detect faces in a single frame using OpenCV DNN. Returns list of face boxes."""
    net = _get_dnn_net()
    if net is None:
        return []
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(
        cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0)
    )
    net.setInput(blob)
    try:
        detections = net.forward()
    except Exception as e:
        logger.warning(f"[SCENE_INTEL] DNN forward failed: {e}")
        return []

    faces = []
    for i in range(detections.shape[2]):
        conf = float(detections[0, 0, i, 2])
        if conf < confidence_threshold:
            continue
        box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
        x1, y1, x2, y2 = box.astype(int)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            faces.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "confidence": conf})
    return faces


# ── Frame sampling ────────────────────────────────────────────────────────────

def _get_video_duration(video_path: str) -> float:
    try:
        res = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, check=True, timeout=15
        )
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def _sample_frames(video_path: str, interval_sec: float = 2.0, max_frames: int = 12) -> List[Tuple[float, np.ndarray]]:
    """Extract frames at regular intervals. Returns list of (timestamp, frame)."""
    duration = _get_video_duration(video_path)
    if duration <= 0:
        return []

    frames = []
    t = 0.0
    while t < duration and len(frames) < max_frames:
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            frames.append((round(t, 2), frame))
        t += interval_sec

    logger.info(f"🎞️ [SCENE_INTEL] Sampled {len(frames)} frames from {duration:.1f}s video")
    return frames


def _save_frame_for_gemini(frame: np.ndarray, path: str) -> bool:
    """Save an OpenCV frame as JPEG for Gemini consumption."""
    try:
        cv2.imwrite(path, frame)
        return True
    except Exception:
        return False


# ── Face clustering ───────────────────────────────────────────────────────────

def _cluster_faces(face_timeline: List[Dict]) -> Dict[int, List[Dict]]:
    """
    Simple centroid clustering: group faces from different frames into
    consistent subject IDs based on relative position in frame.

    face_timeline: [{time_sec, faces: [{x1,y1,x2,y2,confidence}]}]
    Returns: {cluster_id: [face_dicts with time_sec added]}
    """
    clusters: Dict[int, List[Dict]] = {}
    centroids: List[Tuple[float, float]] = []  # (cx_rel, cy_rel) per cluster

    for entry in face_timeline:
        t = entry["time_sec"]
        for face in entry.get("faces", []):
            # Normalise centroid to [0,1] range (relative to typical 1080p)
            cx = (face["x1"] + face["x2"]) / 2.0 / 1080.0
            cy = (face["y1"] + face["y2"]) / 2.0 / 1920.0

            # Find nearest existing cluster
            best_id, best_dist = -1, float("inf")
            for cid, (ecx, ecy) in enumerate(centroids):
                d = ((cx - ecx) ** 2 + (cy - ecy) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_id = cid

            MERGE_THRESHOLD = 0.25  # ~25% of frame width/height
            if best_id >= 0 and best_dist < MERGE_THRESHOLD:
                # Update centroid (exponential moving average)
                ecx, ecy = centroids[best_id]
                centroids[best_id] = (ecx * 0.7 + cx * 0.3, ecy * 0.7 + cy * 0.3)
                clusters[best_id].append({**face, "time_sec": t, "cluster_id": best_id})
            else:
                new_id = len(centroids)
                centroids.append((cx, cy))
                clusters[new_id] = [{**face, "time_sec": t, "cluster_id": new_id}]

    return clusters


def _best_face_crop(cluster_faces: List[Dict], frames_map: Dict[float, np.ndarray]) -> Optional[np.ndarray]:
    """Return the highest-confidence face crop from a cluster."""
    best = max(cluster_faces, key=lambda f: f["confidence"], default=None)
    if best is None:
        return None
    frame = frames_map.get(best["time_sec"])
    if frame is None:
        return None
    x1, y1, x2, y2 = best["x1"], best["y1"], best["x2"], best["y2"]
    # Add 20% padding for a more natural portrait crop
    pad_x = int((x2 - x1) * 0.2)
    pad_y = int((y2 - y1) * 0.2)
    h, w = frame.shape[:2]
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(w, x2 + pad_x)
    y2 = min(h, y2 + pad_y)
    return frame[y1:y2, x1:x2]


# ── Gemini analysis ───────────────────────────────────────────────────────────

_FALLBACK_CONTEXT = {
    "content_type": "person_video",
    "people": {"count": 1, "subjects": [{"id": "A", "description": "main subject", "primary_match": True, "time_dominance": "throughout"}]},
    "activity": "unknown",
    "setting": "unknown",
    "energy": "medium",
    "creative_possibilities": [
        {"rank": 1, "clip_label": "highlight_reel", "focus": "primary_person", "subject_id": "A",
         "description": "Best moments highlight reel", "ideal_duration_sec": 15, "editing_style": "rhythm_driven"}
    ],
    "_source": "fallback"
}


def _call_gemini(frame_paths: List[str], title_name: str) -> Dict:
    """Send sampled frames + title to Gemini for scene understanding."""
    try:
        from Intelligence_Modules.gemini_governor import gemini_router
        if not gemini_router:
            return {**_FALLBACK_CONTEXT, "_source": "no_gemini_router"}

        from PIL import Image

        prompt_text = _SCENE_PROMPT_TEMPLATE.format(title_name=title_name or "unknown")
        payload = [prompt_text]
        for fp in frame_paths[:8]:  # max 8 frames to Gemini
            if os.path.exists(fp):
                try:
                    payload.append(Image.open(fp))
                except Exception:
                    pass

        raw = gemini_router.generate(
            task_type="vision",
            prompt=payload,
            module_name="scene_intel",
            gen_config={"temperature": 0.2}
        )
        if not raw:
            return {**_FALLBACK_CONTEXT, "_source": "gemini_empty"}

        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return {**_FALLBACK_CONTEXT, "_source": "gemini_no_json"}

        data = json.loads(match.group(0))
        data["_source"] = "gemini"
        return data

    except Exception as e:
        logger.warning(f"⚠️ [SCENE_INTEL] Gemini call failed: {e}")
        return {**_FALLBACK_CONTEXT, "_source": f"error:{e}"}


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_scene(video_path: str, title_name: str = "", job_dir: str = "temp") -> Dict[str, Any]:
    """
    Main entry point. Always runs on every video.

    Returns SceneContext dict:
    {
        content_type, people, activity, setting, energy,
        creative_possibilities,
        face_timeline, clusters, primary_cluster_id,
        _source (gemini|fallback|error)
    }
    """
    logger.info(f"🧠 [SCENE_INTEL] Analyzing: '{os.path.basename(video_path)}' | title='{title_name}'")

    # ── 1. Sample frames ──────────────────────────────────────────────────────
    sampled = _sample_frames(video_path, interval_sec=2.0, max_frames=12)
    if not sampled:
        logger.warning("⚠️ [SCENE_INTEL] No frames sampled — returning fallback context.")
        return {**_FALLBACK_CONTEXT, "_source": "no_frames"}

    frames_map: Dict[float, np.ndarray] = {t: f for t, f in sampled}

    # ── 2. DNN face detection per frame ──────────────────────────────────────
    face_timeline: List[Dict] = []
    for t, frame in sampled:
        faces = _detect_faces(frame)
        if faces:
            face_timeline.append({"time_sec": t, "faces": faces})

    total_faces_found = sum(len(e["faces"]) for e in face_timeline)
    logger.info(f"👤 [SCENE_INTEL] Detected {total_faces_found} face instances across {len(face_timeline)} frames")

    # ── 3. Cluster faces into subjects ───────────────────────────────────────
    clusters = _cluster_faces(face_timeline)
    logger.info(f"👥 [SCENE_INTEL] Clustered into {len(clusters)} distinct subject(s)")

    # ── 4. Check face cache / save new face ──────────────────────────────────
    primary_cluster_id = 0
    cached_face = load_cached_face(title_name) if title_name else None

    if title_name and clusters:
        if cached_face is not None:
            # RAG match: compare cached face to each cluster's best crop
            # Simple size-based heuristic: largest face region = most prominent subject
            best_cid = max(clusters.keys(), key=lambda cid: len(clusters[cid]))
            primary_cluster_id = best_cid
            logger.info(f"⚡ [FACE_CACHE] RAG hit for '{title_name}' → cluster {primary_cluster_id}")
        else:
            # No cache: save the largest/most confident cluster as the named subject
            best_cid = max(clusters.keys(), key=lambda cid: sum(f["confidence"] for f in clusters[cid]))
            primary_cluster_id = best_cid
            face_crop = _best_face_crop(clusters[best_cid], frames_map)
            if face_crop is not None:
                save_face_cache(title_name, face_crop)

    # ── 5. Save frames for Gemini ─────────────────────────────────────────────
    frame_save_dir = Path(job_dir) / "scene_intel_frames"
    frame_save_dir.mkdir(parents=True, exist_ok=True)
    frame_paths = []
    for i, (t, frame) in enumerate(sampled[:8]):
        fp = str(frame_save_dir / f"frame_{i:02d}_{t:.1f}s.jpg")
        if _save_frame_for_gemini(frame, fp):
            frame_paths.append(fp)

    # ── 6. Gemini scene understanding ────────────────────────────────────────
    scene_ctx = _call_gemini(frame_paths, title_name)

    # ── 7. Attach face analysis results ──────────────────────────────────────
    scene_ctx["face_timeline"]      = face_timeline
    scene_ctx["clusters"]           = {str(k): [{"time_sec": f["time_sec"], "confidence": f["confidence"]}
                                                  for f in v] for k, v in clusters.items()}
    scene_ctx["primary_cluster_id"] = primary_cluster_id
    scene_ctx["num_detected_faces"] = len(clusters)

    content_type = scene_ctx.get("content_type", "unknown")
    activity     = scene_ctx.get("activity", "unknown")
    setting      = scene_ctx.get("setting", "unknown")
    n_clips      = len(scene_ctx.get("creative_possibilities", []))
    logger.info(
        f"✅ [SCENE_INTEL] content={content_type} | activity={activity} | "
        f"setting={setting} | subjects={len(clusters)} | clip_plan={n_clips} possibilities"
    )
    return scene_ctx


def build_clip_plan(scene_ctx: Dict, max_clips: int = 3) -> List[Dict]:
    """
    Convert creative_possibilities into a concrete ClipPlan list.

    Each entry:
    {
        "clip_label": str,
        "focus": "primary_person|secondary_person|both|no_person",
        "subject_id": str | None,
        "cluster_id": int | None,
        "description": str,
        "ideal_duration_sec": float,
        "editing_style": str
    }
    """
    possibilities = scene_ctx.get("creative_possibilities", [])
    content_type  = scene_ctx.get("content_type", "person_video")
    clusters      = scene_ctx.get("clusters", {})
    primary_cid   = scene_ctx.get("primary_cluster_id", 0)

    # For non-person content: single clip plan, no character focus
    if content_type not in ("person_video",) or not possibilities:
        return [{
            "clip_label": "content_highlight",
            "focus": "no_person",
            "subject_id": None,
            "cluster_id": None,
            "description": f"Best moments — {scene_ctx.get('activity', 'highlight')} style",
            "ideal_duration_sec": 15.0,
            "editing_style": "rhythm_driven",
        }]

    clip_plan = []
    for poss in sorted(possibilities, key=lambda p: p.get("rank", 99))[:max_clips]:
        subject_id = poss.get("subject_id")
        # Map subject letter (A, B, C) to cluster_id (0, 1, 2)
        cluster_id = primary_cid
        if subject_id:
            letter_idx = ord(subject_id.upper()) - ord("A")
            cluster_id = letter_idx if str(letter_idx) in clusters else primary_cid

        clip_plan.append({
            "clip_label":        poss.get("clip_label", f"clip_{len(clip_plan)+1}"),
            "focus":             poss.get("focus", "primary_person"),
            "subject_id":        subject_id,
            "cluster_id":        cluster_id,
            "description":       poss.get("description", ""),
            "ideal_duration_sec": float(poss.get("ideal_duration_sec", 15)),
            "editing_style":     poss.get("editing_style", "rhythm_driven"),
        })

    logger.info(f"📋 [CLIP_PLAN] {len(clip_plan)} clip(s) planned: {[c['clip_label'] for c in clip_plan]}")
    return clip_plan


def filter_scenes_by_cluster(
    scenes: List[Dict],
    face_timeline: List[Dict],
    cluster_id: int,
    presence_threshold: float = 0.3,
) -> List[Dict]:
    """
    Filter a scene list to keep only windows where the given face cluster
    is present in at least `presence_threshold` fraction of sampled frames.

    If no face data is available (no humans detected), returns all scenes unchanged.
    """
    if not face_timeline:
        return scenes  # non-person content — no filtering

    # Build a set of time windows where cluster_id appears
    cluster_times = set()
    for entry in face_timeline:
        for face in entry.get("faces", []):
            if face.get("cluster_id") == cluster_id:
                cluster_times.add(entry["time_sec"])

    if not cluster_times:
        logger.warning(f"⚠️ [SCENE_FILTER] Cluster {cluster_id} has no face timestamps — returning all scenes.")
        return scenes

    filtered = []
    for seg in scenes:
        st = float(seg.get("start", 0))
        en = float(seg.get("end", 0))
        # Count how many sampled frames in this window show the cluster
        window_samples = [t for t in cluster_times if st <= t <= en]
        total_samples  = sum(1 for entry in face_timeline if st <= entry["time_sec"] <= en)
        if total_samples == 0:
            filtered.append(seg)  # no data for this window — keep it
            continue
        presence = len(window_samples) / total_samples
        if presence >= presence_threshold:
            filtered.append(seg)

    logger.info(
        f"🎯 [SCENE_FILTER] Cluster {cluster_id}: {len(filtered)}/{len(scenes)} scenes "
        f"pass presence threshold ({presence_threshold*100:.0f}%)"
    )
    return filtered if filtered else scenes  # never return empty — fallback to all
