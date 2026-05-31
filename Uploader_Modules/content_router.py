"""
content_router.py — Gemini Vision Content Router
=================================================
Classifies a video clip into one of three routing tiers:
  • "fashion"  — clothing coverage >= 40% → Fashion_01, Fashion_02, ... accounts (randomly picked)
  • "nsfw"     — clothing coverage <  40% → NSFW_01, NSFW_02, ... accounts (randomly picked)
  • "general"  — no human detected        → General_Fallback only

Account pools are auto-discovered from Credentials/social_media/ at runtime.
Adding a new account is as simple as creating the folder:
  Fashion_01, Fashion_02, Fashion_03 ...
  NSFW_01,    NSFW_02,    NSFW_03    ...

Results are cached in a .route.json sidecar next to the video file so
Gemini is only called once per clip.
"""

import json
import logging
import os
import random
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
FASHION_THRESHOLD  = int(os.getenv("CONTENT_ROUTER_FASHION_THRESHOLD", "40"))  # %
ROUTE_CACHE_EXT    = ".route.json"
CREDS_BASE         = os.path.join("Credentials", "social_media")

# Fallback static targets used when no numbered folders exist yet
_STATIC_FALLBACK = {
    "fashion": ["General_Fallback"],
    "nsfw":    ["NSFW", "General_Fallback"],
    "general": ["General_Fallback"],
}


# ── Dynamic account pool discovery ───────────────────────────────────────────

def _discover_pool(prefix: str) -> list[str]:
    """
    Returns all credential folders for a given prefix, including:
      - Base folder (no number) = index 0  e.g. "Fashion", "NSFW"
      - Numbered folders        = _01, _02  e.g. "Fashion_01", "NSFW_02"

    Only includes folders that contain at least one credential file.
    Result is returned in natural order (base first, then 01, 02...).
    Caller shuffles before picking.
    """
    if not os.path.isdir(CREDS_BASE):
        return []

    found = []

    # ── Base folder (index 0, no number suffix) ───────────────────────────────
    base_folder = os.path.join(CREDS_BASE, prefix)
    if os.path.isdir(base_folder):
        files = [f for f in os.listdir(base_folder) if os.path.isfile(os.path.join(base_folder, f))]
        if files:
            found.append(prefix)  # e.g. "Fashion"

    # ── Numbered folders (_01, _02, ...) ─────────────────────────────────────
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$", re.IGNORECASE)
    numbered = []
    for name in os.listdir(CREDS_BASE):
        if pattern.match(name):
            folder = os.path.join(CREDS_BASE, name)
            files  = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
            if files:
                numbered.append(name)

    numbered.sort(key=lambda n: int(pattern.match(n).group(1)))
    found.extend(numbered)  # e.g. ["Fashion", "Fashion_01", "Fashion_02"]

    return found


def get_route_pool(category: str) -> list[str]:
    """
    Returns the full shuffled pool of eligible niche folders for a category.
    Always randomised so no single account is always first.

    fashion → all Fashion_XX folders (shuffled) + General_Fallback as final fallback
    nsfw    → all NSFW_XX folders (shuffled) + General_Fallback as final fallback
    general → [General_Fallback]
    """
    if category == "fashion":
        numbered = _discover_pool("Fashion")
        if not numbered:
            logger.info("📂 [CONTENT_ROUTER] No Fashion_XX folders found — using General_Fallback")
            return ["General_Fallback"]
        random.shuffle(numbered)
        # General_Fallback as ultimate fallback if all Fashion accounts are at daily limit
        return numbered + ["General_Fallback"]

    elif category == "nsfw":
        numbered = _discover_pool("NSFW")
        if not numbered:
            logger.info("📂 [CONTENT_ROUTER] No NSFW_XX folders found — falling back to NSFW then General_Fallback")
            return ["NSFW", "General_Fallback"]
        random.shuffle(numbered)
        return numbered + ["General_Fallback"]

    else:  # general
        return ["General_Fallback"]


# ── Frame extraction ──────────────────────────────────────────────────────────

def _extract_frame(video_path: str) -> str | None:
    """Extract the middle frame from a video as a temp JPEG. Returns path or None."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
            capture_output=True, text=True, timeout=15,
        )
        info     = json.loads(probe.stdout)
        duration = float(info.get("format", {}).get("duration", 4))
        seek     = max(0.5, duration / 2)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = tmp.name

        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", video_path, "-vframes", "1", "-q:v", "2", frame_path],
            check=True, capture_output=True, timeout=30,
        )
        return frame_path
    except Exception as exc:
        logger.warning("⚠️ [CONTENT_ROUTER] Frame extraction failed: %s", exc)
        return None


# ── Gemini Vision classification ──────────────────────────────────────────────

def _classify_with_gemini(frame_path: str) -> dict:
    """
    Send frame to Gemini Vision and get clothing coverage classification.
    Returns dict with keys: coverage_pct (int), has_human (bool), category (str).
    """
    try:
        import google.generativeai as genai
        from PIL import Image

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("⚠️ [CONTENT_ROUTER] No Gemini API key — defaulting to 'fashion'")
            return {"coverage_pct": 50, "has_human": True, "category": "fashion"}

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")

        img    = Image.open(frame_path)
        prompt = (
            "Look at this image. If there is a human body visible:\n"
            "1. Estimate what percentage of the visible body is covered by clothing/outfit (0-100).\n"
            "   0 = completely unclothed, 100 = fully covered.\n"
            "2. Classify the content:\n"
            f"   - coverage >= {FASHION_THRESHOLD}%  → category = 'fashion'\n"
            f"   - coverage <  {FASHION_THRESHOLD}%  → category = 'nsfw'\n"
            "   - no human visible               → category = 'general'\n\n"
            "Respond ONLY with valid JSON, no markdown, no explanation:\n"
            '{"coverage_pct": <int>, "has_human": <bool>, "category": "<fashion|nsfw|general>"}'
        )

        response = model.generate_content([prompt, img])
        raw      = response.text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        result["coverage_pct"] = int(result.get("coverage_pct", 50))
        result["has_human"]    = bool(result.get("has_human", True))
        result["category"]     = result.get("category", "fashion").lower()
        if result["category"] not in ("fashion", "nsfw", "general"):
            result["category"] = "fashion" if result["coverage_pct"] >= FASHION_THRESHOLD else "nsfw"

        logger.info(
            "🎨 [CONTENT_ROUTER] Gemini — coverage: %d%%, human: %s, category: %s",
            result["coverage_pct"], result["has_human"], result["category"],
        )
        return result

    except Exception as exc:
        logger.warning("⚠️ [CONTENT_ROUTER] Gemini failed: %s — defaulting to 'fashion'", exc)
        return {"coverage_pct": 50, "has_human": True, "category": "fashion"}


# ── Main entry point ──────────────────────────────────────────────────────────

def classify_content(video_path: str, force: bool = False) -> dict:
    """
    Classifies a video clip and returns routing info.

    Returns:
        {
            "category":     "fashion" | "nsfw" | "general",
            "coverage_pct": int,
            "has_human":    bool,
            "targets":      ["Fashion_02", "Fashion_01", "General_Fallback"],  # shuffled pool
            "cached":       bool,
        }

    The targets list is a shuffled pool — account_limiter picks the first one
    that hasn't hit its daily cap.
    """
    if not video_path or not os.path.exists(video_path):
        logger.warning("⚠️ [CONTENT_ROUTER] video_path not found: %s", video_path)
        return {
            "category": "general", "coverage_pct": 50,
            "has_human": False, "targets": ["General_Fallback"], "cached": False,
        }

    base       = os.path.splitext(video_path)[0]
    cache_path = base + ROUTE_CACHE_EXT

    # ── Return cached classification (but always re-discover pool so new accounts are picked up) ──
    cached_category = None
    if not force and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached_category = cached.get("category", "fashion")
            # Re-run pool discovery every time (accounts may have been added)
            cached["targets"] = get_route_pool(cached_category)
            cached["cached"]  = True
            logger.info("📦 [CONTENT_ROUTER] Cached category=%s pool=%s", cached_category, cached["targets"])
            return cached
        except Exception:
            pass

    # ── Extract frame & classify ──────────────────────────────────────────────
    frame_path = _extract_frame(video_path)
    if frame_path:
        try:
            result = _classify_with_gemini(frame_path)
        finally:
            try:
                os.remove(frame_path)
            except Exception:
                pass
    else:
        result = {"coverage_pct": 50, "has_human": True, "category": "fashion"}

    result["targets"] = get_route_pool(result["category"])
    result["cached"]  = False

    # ── Cache classification result (not the pool — pool is re-discovered each time) ──
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            cache_data = {k: v for k, v in result.items() if k not in ("cached", "targets")}
            json.dump(cache_data, f, indent=2)
    except Exception as exc:
        logger.warning("⚠️ [CONTENT_ROUTER] Failed to write route cache: %s", exc)

    logger.info("🗺️  [CONTENT_ROUTER] category=%s pool=%s", result["category"], result["targets"])
    return result


def get_route_targets(video_path: str) -> list[str]:
    """Convenience wrapper — returns shuffled pool of target niche folder names."""
    return classify_content(video_path)["targets"]
