"""
Profit Scorer — The Machine Learns What Sells
==============================================
Every 24 hours the machine reviews its own output:
  - Which vibe made people buy?
  - Which pacing style drove the most saves?
  - Which price point had the highest click-through?

The winners get injected back as Creative DNA into the next batch.
This is how the machine evolves. Supply → Demand → Profit → Feedback → Better Supply.

The loop never stops. The machine never sleeps.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional
from collections import Counter

logger = logging.getLogger("profit_scorer")

_HERE         = os.path.dirname(os.path.abspath(__file__))
_ROOT         = os.path.dirname(_HERE)
FACTORY_LOG   = os.path.join(_ROOT, "Monetization_Metrics", "factory_log.json")
MARKET_MEMORY = os.path.join(_ROOT, "Monetization_Metrics", "market_memory.json")

# ── Scoring weights ────────────────────────────────────────────────────────────
# These weights reflect what actually drives affiliate revenue:
#   - Link clicks are the DIRECT money signal
#   - Saves signal "I want to buy this later" — high purchase intent
#   - Views/reach are vanity unless they convert
WEIGHT_LINK_CLICKS = 0.50
WEIGHT_SAVES       = 0.30
WEIGHT_REACH       = 0.10
WEIGHT_LIKES       = 0.10


def _load_factory_log() -> List[Dict]:
    try:
        if os.path.exists(FACTORY_LOG):
            with open(FACTORY_LOG, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[SCORER] Could not load factory log: {e}")
    return []


def _load_market_memory() -> Dict:
    try:
        if os.path.exists(MARKET_MEMORY):
            with open(MARKET_MEMORY, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_market_memory(data: Dict):
    try:
        os.makedirs(os.path.dirname(MARKET_MEMORY), exist_ok=True)
        with open(MARKET_MEMORY, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"[SCORER] Market memory updated: {data}")
    except Exception as e:
        logger.warning(f"[SCORER] Could not save market memory: {e}")


def _compute_profit_score(entry: Dict) -> float:
    """
    Compute a single profit score for one video from its platform analytics.
    Score = weighted sum of engagement signals that correlate with revenue.
    """
    platform_results = entry.get("platform_results", {})

    link_clicks = 0
    saves       = 0
    reach       = 0
    likes       = 0

    # Extract from Instagram result
    ig = platform_results.get("instagram", {})
    if isinstance(ig, dict):
        link_clicks += ig.get("link_clicks", 0) or ig.get("website_clicks", 0)
        saves       += ig.get("saved", 0) or ig.get("saves", 0)
        reach       += ig.get("reach", 0)
        likes       += ig.get("like_count", 0) or ig.get("likes", 0)

    # Extract from YouTube Shorts
    yt = platform_results.get("youtube", {})
    if isinstance(yt, dict):
        link_clicks += yt.get("clicks", 0)
        reach       += yt.get("views", 0)
        likes       += yt.get("likes", 0)

    score = (
        (link_clicks * WEIGHT_LINK_CLICKS) +
        (saves       * WEIGHT_SAVES)       +
        (reach       * WEIGHT_REACH / 100) +  # normalise reach (usually 100x bigger)
        (likes       * WEIGHT_LIKES)
    )
    return round(score, 3)


def run_profit_analysis(top_n: int = 7) -> Dict:
    """
    Score all completed jobs, find the top performers,
    extract their Creative DNA, and save to market_memory.json.

    Called daily (or on-demand) by the factory or a scheduler.
    """
    log = _load_factory_log()
    memory = _load_market_memory()
    last_scored_id = memory.get("last_scored_job_id")

    # Fast-forward past already-processed entries
    if last_scored_id:
        try:
            ids = [e.get("job_id") for e in log]
            cursor = ids.index(last_scored_id) + 1
            log = log[cursor:]
            logger.info("[SCORER] Cursor at job %s — processing %d new entries", last_scored_id, len(log))
        except ValueError:
            pass  # last_scored_id not found (log rotated) — process all

    successful = [e for e in log if e.get("status") == "success"]

    if not successful:
        logger.info("[SCORER] No successful jobs to score yet.")
        return {}

    # Score each job
    scored = []
    for entry in successful:
        score = _compute_profit_score(entry)
        scored.append({**entry, "_profit_score": score})

    scored.sort(key=lambda x: x["_profit_score"], reverse=True)

    # Extract winning DNA from top N
    top   = scored[:top_n]
    vibes   = [e.get("vibe")   for e in top if e.get("vibe")   and e["vibe"] != "unknown"]
    pacings = [e.get("pacing") for e in top if e.get("pacing") and e["pacing"] != "unknown"]

    def _most_common(lst):
        if not lst:
            return None
        return Counter(lst).most_common(1)[0][0]

    top_vibe    = _most_common(vibes)
    top_pacing  = _most_common(pacings)

    # Compute average profit score of top N
    avg_top_score = sum(e["_profit_score"] for e in top) / max(len(top), 1)

    memory = _load_market_memory()
    memory.update({
        "updated_at":            datetime.now(timezone.utc).isoformat(),
        "last_scored_job_id":    scored[-1].get("job_id") if scored else last_scored_id,
        "top_vibe":              top_vibe,
        "top_pacing_style":      top_pacing,
        "avg_top_score":         round(avg_top_score, 3),
        "total_videos_scored":   len(scored),
        "top_jobs":          [
            {
                "job_id":         e.get("job_id"),
                "source":         e.get("source"),
                "profit_score":   e.get("_profit_score"),
                "vibe":           e.get("vibe"),
                "pacing":         e.get("pacing"),
            }
            for e in top
        ],
    })

    _save_market_memory(memory)

    logger.info(
        f"[SCORER] ✅ Analysis complete: {len(scored)} videos scored | "
        f"top_vibe={top_vibe} top_pacing={top_pacing} "
        f"avg_top_score={avg_top_score:.1f}"
    )
    return memory
