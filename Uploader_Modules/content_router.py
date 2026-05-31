"""
content_router.py — Gemini Vision Content Router
=================================================
Classifies a video clip into one of three routing tiers:
  • "fashion"  — clothing coverage >= 40% → General_Fallback / Fashion_Style accounts
  • "nsfw"     — clothing coverage <  40% → NSFW account
  • "general"  — no human detected       → General_Fallback only

Results are cached in a .route.json sidecar next to the video file so
Gemini is only called once per clip.
"""

import json
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
FASHION_THRESHOLD = int(os.getenv("CONTENT_ROUTER_FASHION_THRESHOLD", "40"))  # %
ROUTE_CACHE_EXT   = ".route.json"

# Route → ordered list of target niche folders (first = primary)
ROUTE_TARGETS = {
    "fashion": ["General_Fallback", "Fashion_Style"],
    "nsfw":    ["NSFW", "General_Fallback"],
    "general": ["General_Fallback"],
}


def _extract_frame(video_path: str) -> str | None:
    """Extract the middle frame from a video as a temp JPEG. Returns path or None."""
    try:
        # Get duration
        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", video_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        info     = json.loads(probe.stdout)
        duration = float(info.get("format", {}).get("duration", 4))
        seek     = max(0.5, duration / 2)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = tmp.name

        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(seek), "-i", video_path,
                "-vframes", "1", "-q:v", "2", frame_path,
            ],
            check=True, capture_output=True, timeout=30,
        )
        return frame_path
    except Exception as exc:
        logger.warning("⚠️ [CONTENT_ROUTER] Frame extraction failed: %s", exc)
        return None


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
            logger.warning("⚠️ [CONTENT_ROUTER] No Gemini API key found — defaulting to 'general'")
            return {"coverage_pct": 50, "has_human": False, "category": "general"}

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

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        # Sanitise
        result["coverage_pct"] = int(result.get("coverage_pct", 50))
        result["has_human"]    = bool(result.get("has_human", True))
        result["category"]     = result.get("category", "general").lower()
        if result["category"] not in ("fashion", "nsfw", "general"):
            result["category"] = "fashion" if result["coverage_pct"] >= FASHION_THRESHOLD else "nsfw"

        logger.info(
            "🎨 [CONTENT_ROUTER] Gemini result — coverage: %d%%, human: %s, category: %s",
            result["coverage_pct"], result["has_human"], result["category"],
        )
        return result

    except Exception as exc:
        logger.warning("⚠️ [CONTENT_ROUTER] Gemini classification failed: %s — defaulting to 'fashion'", exc)
        return {"coverage_pct": 50, "has_human": True, "category": "fashion"}


def classify_content(video_path: str, force: bool = False) -> dict:
    """
    Main entry point. Classifies a video clip.

    Returns:
        {
            "category":     "fashion" | "nsfw" | "general",
            "coverage_pct": int,
            "has_human":    bool,
            "targets":      ["General_Fallback", ...],   # ordered target niches
            "cached":       bool,
        }

    Result is cached in a .route.json sidecar next to the video.
    Pass force=True to re-classify even if cache exists.
    """
    if not video_path or not os.path.exists(video_path):
        logger.warning("⚠️ [CONTENT_ROUTER] video_path not found: %s", video_path)
        return {
            "category": "general",
            "coverage_pct": 50,
            "has_human": False,
            "targets": ROUTE_TARGETS["general"],
            "cached": False,
        }

    base        = os.path.splitext(video_path)[0]
    cache_path  = base + ROUTE_CACHE_EXT

    # ── Return cached result ──────────────────────────────────────────────────
    if not force and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached["cached"]  = True
            cached["targets"] = ROUTE_TARGETS.get(cached.get("category", "general"), ROUTE_TARGETS["general"])
            logger.info("📦 [CONTENT_ROUTER] Using cached route: %s → %s", video_path, cached["category"])
            return cached
        except Exception:
            pass  # Re-classify if cache is corrupted

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

    result["targets"] = ROUTE_TARGETS.get(result["category"], ROUTE_TARGETS["general"])
    result["cached"]  = False

    # ── Write sidecar cache ───────────────────────────────────────────────────
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in result.items() if k != "cached"}, f, indent=2)
    except Exception as exc:
        logger.warning("⚠️ [CONTENT_ROUTER] Failed to write route cache: %s", exc)

    return result


def get_route_targets(video_path: str) -> list[str]:
    """
    Convenience wrapper — returns ordered list of target niche folder names.
    Example: ["General_Fallback", "Fashion_Style"]
    """
    return classify_content(video_path)["targets"]
