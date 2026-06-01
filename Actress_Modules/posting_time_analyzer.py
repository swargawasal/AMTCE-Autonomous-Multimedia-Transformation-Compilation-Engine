"""
posting_time_analyzer.py — Smart Harvest & Publish Time Optimizer
==================================================================
Mines Apify post timestamps from the actress ledger, splits by gender
(General_Fallback = women/mixed, Paparazzi = men), builds IST hourly
histograms, and recommends optimal harvest + publish time windows.

Usage:
  # Just print the report
  python -m Actress_Modules.posting_time_analyzer --report

  # Print report AND auto-patch Credentials/.env with new times
  python -m Actress_Modules.posting_time_analyzer --patch-env

  # Run as a module call from within AMTCE
  from Actress_Modules.posting_time_analyzer import get_recommendations
  recs = get_recommendations()
"""

import argparse
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# IST = UTC + 5:30
IST_OFFSET = timedelta(hours=5, minutes=30)

# Minimum data points needed per gender before we trust the histogram
MIN_SAMPLES = 20

# How many hours before OR after a post peak do we consider "active window"?
PEAK_WINDOW_HOURS = 1

# Channel names that map to "women" and "men" content
WOMEN_CHANNELS = {"General_Fallback"}    # will grow with General_Fallback_01, etc.
MEN_CHANNELS   = {"Paparazzi", "Paparazzi_Channel"}  # future paparazzi accounts


def _ist_hour(ts_utc: float) -> int:
    """Convert a UTC unix timestamp to IST hour (0-23)."""
    dt_ist = datetime.fromtimestamp(ts_utc, tz=timezone.utc) + IST_OFFSET
    return dt_ist.hour


def _classify_channel(channel: str) -> str:
    """Returns 'women', 'men', or 'mixed' for a given channel folder name."""
    ch_lower = channel.lower()
    # General_Fallback and General_Fallback_01, _02... → mixed (women post more)
    if ch_lower.startswith("general_fallback"):
        return "women"
    # Paparazzi, Paparazzi_01, Paparazzi_Channel → men
    if ch_lower.startswith("paparazzi"):
        return "men"
    # Fashion, Fashion_01... → women
    if ch_lower.startswith("fashion"):
        return "women"
    return "mixed"


def build_histograms(ledger_path: Optional[str] = None) -> Dict:
    """
    Reads the actress ledger and builds hourly posting histograms by gender.

    Returns:
        {
          "women": [count_h0, count_h1, ..., count_h23],
          "men":   [count_h0, count_h1, ..., count_h23],
          "total": {"women": N, "men": N}
        }
    """
    if ledger_path is None:
        ledger_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "actress_ledger.json"
        )

    histograms = {
        "women": [0] * 24,
        "men":   [0] * 24,
    }
    totals = {"women": 0, "men": 0}

    if not os.path.exists(ledger_path):
        logger.warning("⚠️ [ANALYZER] No ledger found at %s — no data yet.", ledger_path)
        return {"women": histograms["women"], "men": histograms["men"], "total": totals}

    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.error("❌ [ANALYZER] Could not read ledger: %s", exc)
        return {"women": histograms["women"], "men": histograms["men"], "total": totals}

    channel_map   = data.get("channel_map", {})
    timestamp_map = data.get("timestamp_map", {})

    for shortcode, ts_utc in timestamp_map.items():
        channel = channel_map.get(shortcode, "")
        if not channel or not ts_utc:
            continue
        gender = _classify_channel(channel)
        if gender in ("women", "mixed"):
            hour = _ist_hour(float(ts_utc))
            histograms["women"][hour] += 1
            totals["women"] += 1
        if gender == "men":
            hour = _ist_hour(float(ts_utc))
            histograms["men"][hour] += 1
            totals["men"] += 1

    return {"women": histograms["women"], "men": histograms["men"], "total": totals}


def top_hours(histogram: List[int], top_n: int = 3) -> List[int]:
    """Returns top_n hours sorted by posting frequency (highest first)."""
    indexed = [(count, hour) for hour, count in enumerate(histogram)]
    indexed.sort(reverse=True)
    return [hour for count, hour in indexed[:top_n] if count > 0]


def harvest_times_from_peaks(peak_hours: List[int], offset: int = 1) -> List[str]:
    """
    Converts peak posting hours to harvest times.
    Strategy: harvest (offset) hours AFTER the peak to catch freshest posts.
    Returns sorted HH:MM strings.
    """
    times = set()
    for h in peak_hours:
        harvest_h = (h + offset) % 24
        times.add(f"{harvest_h:02d}:00")
    return sorted(times)


def get_recommendations(ledger_path: Optional[str] = None) -> Dict:
    """
    Main API — returns harvest and publish time recommendations based on
    analyzed ledger histograms if MIN_SAMPLES is met, falling back to research-backed
    defaults otherwise.
    """
    hists = build_histograms(ledger_path)
    result = {}

    for gender in ("women", "men"):
        sample_count = hists["total"][gender]
        if sample_count >= MIN_SAMPLES:
            peaks = top_hours(hists[gender], top_n=3)
            confidence = "high"
        else:
            # Research-backed Indian audience defaults
            peaks = [9, 13, 20] if gender == "women" else [10, 14, 21]
            confidence = f"low (only {sample_count} samples, need {MIN_SAMPLES})"

        # Fallback if top_hours returns empty list
        if not peaks:
            peaks = [9, 13, 20] if gender == "women" else [10, 14, 21]

        publish_times = [f"{h:02d}:00" for h in sorted(peaks)]
        harvest_times = harvest_times_from_peaks(peaks, offset=1)

        result[gender] = {
            "peak_hours_ist": sorted(peaks),
            "publish_times":  publish_times,
            "harvest_times":  harvest_times,
            "sample_count":   sample_count,
            "confidence":     confidence,
        }

    # Combined: union of both, max 5 entries to avoid spamming
    all_harvest = sorted(set(
        result["women"]["harvest_times"] + result["men"]["harvest_times"]
    ))[:5]
    all_publish = sorted(set(
        result["women"]["publish_times"] + result["men"]["publish_times"]
    ))[:5]

    result["combined"] = {
        "harvest_times": all_harvest,
        "publish_times": all_publish,
    }

    return result


def print_report(recs: Optional[Dict] = None) -> None:
    """Prints a human-readable report of the posting time analysis."""
    if recs is None:
        recs = get_recommendations()

    print("\n" + "=" * 60)
    print("  AMTCE — Posting Time Analysis Report (IST)")
    print("=" * 60)

    for gender in ("women", "men"):
        g   = recs[gender]
        lbl = "👩 WOMEN (General_Fallback)" if gender == "women" else "👨 MEN (Paparazzi)"
        print(f"\n{lbl}")
        print(f"  Samples     : {g['sample_count']}  [{g['confidence'].upper()} confidence]")
        if g["sample_count"] < MIN_SAMPLES:
            print("  ⚠️  Not enough data yet — using defaults. Run more harvest cycles.")
        print(f"  Peak hours  : {', '.join(f'{h:02d}:00' for h in g['peak_hours_ist'])}")
        print(f"  Publish at  : {', '.join(g['publish_times'])}")
        print(f"  Harvest at  : {', '.join(g['harvest_times'])}")

    comb = recs["combined"]
    print(f"\n{'─' * 60}")
    print(f"  Combined harvest  → ACTRESS_SCHEDULE_TIMES={','.join(comb['harvest_times'])}")
    print(f"  Combined publish  → ACTRESS_STATIC_PUBLISH_TIMES={','.join(comb['publish_times'])}")
    print("=" * 60 + "\n")


def patch_env(recs: Optional[Dict] = None, env_path: str = "Credentials/.env") -> None:
    """
    Auto-patches ACTRESS_SCHEDULE_TIMES and ACTRESS_STATIC_PUBLISH_TIMES
    in Credentials/.env based on recommendations.
    """
    if recs is None:
        recs = get_recommendations()

    if not os.path.exists(env_path):
        logger.error("❌ [ANALYZER] .env not found at %s — cannot patch.", env_path)
        return

    comb = recs["combined"]
    new_harvest = ",".join(comb["harvest_times"])
    new_publish = ",".join(comb["publish_times"])

    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace existing values
    content = re.sub(
        r"^ACTRESS_SCHEDULE_TIMES=.*$",
        f"ACTRESS_SCHEDULE_TIMES={new_harvest}",
        content,
        flags=re.MULTILINE,
    )
    content = re.sub(
        r"^ACTRESS_STATIC_PUBLISH_TIMES=.*$",
        f"ACTRESS_STATIC_PUBLISH_TIMES={new_publish}",
        content,
        flags=re.MULTILINE,
    )

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info("✅ [ANALYZER] Patched %s:", env_path)
    logger.info("   ACTRESS_SCHEDULE_TIMES      = %s", new_harvest)
    logger.info("Patched %s with smart schedule.", env_path)


# ── CLI entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="AMTCE Posting Time Analyzer — smart harvest & publish scheduler"
    )
    parser.add_argument(
        "--report", action="store_true", help="Print posting time histogram report"
    )
    parser.add_argument(
        "--patch-env", action="store_true",
        help="Auto-update ACTRESS_SCHEDULE_TIMES and ACTRESS_STATIC_PUBLISH_TIMES in Credentials/.env"
    )
    parser.add_argument(
        "--ledger", default=None, help="Path to actress_ledger.json (default: auto-detect)"
    )
    args = parser.parse_args()

    recs = get_recommendations(ledger_path=args.ledger)

    if args.report or not args.patch_env:
        print_report(recs)

    if args.patch_env:
        patch_env(recs)
