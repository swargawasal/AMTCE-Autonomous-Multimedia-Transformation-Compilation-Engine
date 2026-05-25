"""
salesman_state.py — AMTCE Intelligent Salesman State Engine
============================================================
Tracks harvest and publish activity across process restarts so that
missed schedule slots are detected and recovered automatically.

Think of it like a sales rep with a daily quota:
  - Knows exactly which harvest runs fired and which didn't
  - Knows which publish slots were served and which were missed
  - Plans catch-up work intelligently (spread out, not all-at-once)
  - Persists Apify quota to disk so restarts don't reset the budget

State file: Intelligence_Data/salesman_state.json
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

logger = logging.getLogger(__name__)

# ── State file location ──────────────────────────────────────────────────────
_STATE_DIR  = "Intelligence_Data"
_STATE_FILE = os.path.join(_STATE_DIR, "salesman_state.json")
_LOCK       = threading.Lock()

os.makedirs(_STATE_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Raw I/O helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_state() -> Dict:
    """Load state from disk. Returns a safe default if missing or corrupt."""
    if not os.path.exists(_STATE_FILE):
        return _default_state()
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure all keys exist (forward-compat for new fields)
        default = _default_state()
        for section in ("harvest", "publisher", "apify"):
            if section not in data:
                data[section] = default[section]
            else:
                for k, v in default[section].items():
                    data[section].setdefault(k, v)
        return data
    except Exception as exc:
        logger.warning("⚠️ [SALESMAN] State file corrupt, resetting: %s", exc)
        return _default_state()


def _save_state(state: Dict) -> None:
    """Atomically save state to disk."""
    try:
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, _STATE_FILE)
    except Exception as exc:
        logger.error("❌ [SALESMAN] Failed to save state: %s", exc)


def _default_state() -> Dict:
    today = _today_str()
    return {
        "harvest": {
            "last_run_date":            today,
            "slots_completed_today":    [],   # ["01:45", "03:00"]
            "catchup_fired_today":      False,
            "total_runs_all_time":      0,
        },
        "publisher": {
            "last_check_date":          today,
            "slots_published_today":    [],   # ["07:30", "19:30"]
            "slots_missed_today":       [],   # ["04:02"]
            "catchup_slots_today":      [],   # catch-up times planned
            "deficit_videos":           0,
        },
        "apify": {
            "quota_date":               today,
            "quota_used":               0,
            "quota_limit":              int(os.getenv("APIFY_DAILY_QUOTA", "50")),
            "total_calls_all_time":     0,
        },
    }


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _parse_hhmm(t: str) -> Tuple[int, int]:
    h, _, m = t.strip().partition(":")
    return int(h), int(m or "0")


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Harvest
# ─────────────────────────────────────────────────────────────────────────────

class HarvestState:
    """
    Manages harvest slot tracking and catch-up logic.

    Usage:
        state = HarvestState()
        if state.should_catchup():
            run_daily_cycle()
        ...
        state.mark_slot_complete("01:45")
    """

    def __init__(self):
        with _LOCK:
            self._state = _load_state()
            self._roll_date_if_new_day()

    def _roll_date_if_new_day(self):
        """Reset daily counters when a new day starts."""
        today = _today_str()
        h = self._state["harvest"]
        if h["last_run_date"] != today:
            logger.info("🌅 [HARVEST SALESMAN] New day detected — rolling counters")
            h["last_run_date"]         = today
            h["slots_completed_today"] = []
            h["catchup_fired_today"]   = False
            _save_state(self._state)

    def slots_completed_today(self) -> List[str]:
        with _LOCK:
            self._state = _load_state()
            self._roll_date_if_new_day()
            return list(self._state["harvest"]["slots_completed_today"])

    def mark_slot_complete(self, slot_hhmm: str) -> None:
        """Call this immediately after a successful harvest run."""
        with _LOCK:
            state = _load_state()
            h = state["harvest"]
            today = _today_str()
            if h["last_run_date"] != today:
                h["last_run_date"]         = today
                h["slots_completed_today"] = []
                h["catchup_fired_today"]   = False
            if slot_hhmm not in h["slots_completed_today"]:
                h["slots_completed_today"].append(slot_hhmm)
            h["total_runs_all_time"] = h.get("total_runs_all_time", 0) + 1
            _save_state(state)
            logger.info("✅ [HARVEST SALESMAN] Slot marked complete: %s", slot_hhmm)

    def mark_catchup_fired(self) -> None:
        with _LOCK:
            state = _load_state()
            state["harvest"]["catchup_fired_today"] = True
            _save_state(state)

    def should_catchup(self, configured_slots: List[str]) -> bool:
        """
        Returns True if:
          - At least one configured harvest slot passed today
          - AND none of those passed slots are in slots_completed_today
          - AND no catch-up has fired yet today
          - AND the program is running (obviously)

        Max 1 catch-up per day — we don't cascade multi-day deficits
        into multiple back-to-back Apify calls.
        """
        with _LOCK:
            self._state = _load_state()
            self._roll_date_if_new_day()
            h = self._state["harvest"]

        if h["catchup_fired_today"]:
            return False

        now = datetime.now()
        missed_any = False
        for slot_str in configured_slots:
            try:
                hh, mm = _parse_hhmm(slot_str)
            except Exception:
                continue
            slot_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if slot_dt < now and slot_str not in h["slots_completed_today"]:
                missed_any = True
                break

        if missed_any:
            logger.warning(
                "🚨 [HARVEST SALESMAN] Missed harvest slot(s) detected. "
                "Completed today: %s | Configured: %s",
                h["slots_completed_today"], configured_slots
            )
        return missed_any

    def get_summary(self) -> str:
        with _LOCK:
            h = _load_state()["harvest"]
        return (
            f"Harvest State — {h['last_run_date']} | "
            f"Done: {h['slots_completed_today']} | "
            f"Catchup fired: {h['catchup_fired_today']} | "
            f"Total runs: {h['total_runs_all_time']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Publisher
# ─────────────────────────────────────────────────────────────────────────────

class PublisherState:
    """
    Manages publish slot tracking and intelligent catch-up scheduling.

    The publisher calls:
        state = PublisherState()
        catchup_slots = state.get_catchup_slots(configured_slots)
        # → returns extra publish times to fire today to make up for misses
        state.mark_slot_published("07:30")
        state.mark_slot_missed("04:02")
    """

    # Max number of catch-up videos to schedule in one day
    MAX_CATCHUP_PER_DAY: int = int(os.getenv("PUBLISHER_MAX_CATCHUP_PER_DAY", "2"))

    def __init__(self):
        with _LOCK:
            self._state = _load_state()
            self._roll_date_if_new_day()

    def _roll_date_if_new_day(self):
        today = _today_str()
        p = self._state["publisher"]
        if p["last_check_date"] != today:
            logger.info("🌅 [PUBLISHER SALESMAN] New day detected — rolling counters")
            # Carry over deficit from yesterday (capped at MAX_CATCHUP_PER_DAY)
            yesterday_deficit = min(
                len(p.get("slots_missed_today", [])),
                self.MAX_CATCHUP_PER_DAY
            )
            p["last_check_date"]       = today
            p["slots_published_today"] = []
            p["slots_missed_today"]    = []
            p["catchup_slots_today"]   = []
            p["deficit_videos"]        = yesterday_deficit
            if yesterday_deficit:
                logger.warning(
                    "📊 [PUBLISHER SALESMAN] Carrying %d deficit video(s) from yesterday",
                    yesterday_deficit
                )
            _save_state(self._state)

    def mark_slot_published(self, slot_hhmm: str) -> None:
        """Call this when a publish slot successfully fires."""
        with _LOCK:
            state = _load_state()
            p = state["publisher"]
            if slot_hhmm not in p["slots_published_today"]:
                p["slots_published_today"].append(slot_hhmm)
            # Clear from missed if it was wrongly tracked
            if slot_hhmm in p["slots_missed_today"]:
                p["slots_missed_today"].remove(slot_hhmm)
            # Reduce deficit
            if p["deficit_videos"] > 0:
                p["deficit_videos"] -= 1
            _save_state(state)
            logger.info("✅ [PUBLISHER SALESMAN] Slot published: %s", slot_hhmm)

    def mark_slot_missed(self, slot_hhmm: str) -> None:
        """Call this when a slot is detected as missed (program was off)."""
        with _LOCK:
            state = _load_state()
            p = state["publisher"]
            if slot_hhmm not in p["slots_missed_today"] and slot_hhmm not in p["slots_published_today"]:
                p["slots_missed_today"].append(slot_hhmm)
                p["deficit_videos"] = p.get("deficit_videos", 0) + 1
                logger.warning("⚠️ [PUBLISHER SALESMAN] Missed slot recorded: %s", slot_hhmm)
            _save_state(state)

    def get_missed_slots(self, configured_slots: List[str]) -> List[str]:
        """
        Compare configured slots against published-today to find what was missed.
        Only considers slots that have already passed.
        """
        now = datetime.now()
        with _LOCK:
            state = _load_state()
        p = state["publisher"]
        today = _today_str()

        if p["last_check_date"] != today:
            return []

        missed = []
        for slot_str in configured_slots:
            try:
                hh, mm = _parse_hhmm(slot_str)
            except Exception:
                continue
            slot_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            # Slot must have passed (at least PROCESS_LEAD_TIME_MINUTES ago)
            lead = int(os.getenv("PROCESS_LEAD_TIME_MINUTES", "6"))
            fire_dt = slot_dt - timedelta(minutes=lead)
            if fire_dt < now and slot_str not in p["slots_published_today"]:
                missed.append(slot_str)
        return missed

    def plan_catchup_slots(
        self,
        configured_slots: List[str],
        active_start: str = "07:00",
        active_end: str = "23:00",
    ) -> List[str]:
        """
        Intelligently plans catch-up publish times for missed slots.

        Strategy:
          - Find gaps between remaining static slots and now
          - Insert catch-up times at the MIDPOINT of each gap
          - Respect active hours (no overnight catch-up)
          - Cap at MAX_CATCHUP_PER_DAY total

        Returns a list of HH:MM strings (new virtual slots to fire today).
        """
        with _LOCK:
            state = _load_state()
        p = state["publisher"]

        # Don't re-plan if already planned today
        if p.get("catchup_slots_today"):
            return list(p["catchup_slots_today"])

        missed = self.get_missed_slots(configured_slots)
        if not missed:
            return []

        now = datetime.now()
        try:
            sh, sm = _parse_hhmm(active_start)
            eh, em = _parse_hhmm(active_end)
        except Exception:
            sh, sm = 7, 0
            eh, em = 23, 0

        active_start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        active_end_dt   = now.replace(hour=eh, minute=em, second=0, microsecond=0)

        # Find future static slot times
        future_slots = []
        for slot_str in configured_slots:
            try:
                hh, mm = _parse_hhmm(slot_str)
            except Exception:
                continue
            slot_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if slot_dt > now:
                future_slots.append(slot_dt)
        future_slots.sort()

        catchup_times = []
        anchor = max(now + timedelta(minutes=15), active_start_dt)  # at least 15min from now

        for i, _ in enumerate(missed):
            if len(catchup_times) >= self.MAX_CATCHUP_PER_DAY:
                break

            if future_slots:
                # Midpoint between now/anchor and next static slot
                next_static = future_slots[0]
                gap_mid = anchor + (next_static - anchor) / 2
                # Round to nearest 5 minutes
                gap_mid = gap_mid.replace(
                    minute=(gap_mid.minute // 5) * 5, second=0, microsecond=0
                )
            else:
                # No future static slot — space catch-ups 60 min apart within active hours
                gap_mid = anchor + timedelta(hours=1)

            # Only add if within active hours
            if active_start_dt <= gap_mid <= active_end_dt and gap_mid > now:
                slot_str = gap_mid.strftime("%H:%M")
                if slot_str not in catchup_times:
                    catchup_times.append(slot_str)
                    logger.info(
                        "📅 [PUBLISHER SALESMAN] Catch-up slot planned: %s (missed: %s)",
                        slot_str, missed[i]
                    )
                anchor = gap_mid + timedelta(minutes=30)  # min spacing
            else:
                logger.info(
                    "⏭️ [PUBLISHER SALESMAN] Catch-up slot %s outside active hours — skipped",
                    gap_mid.strftime("%H:%M")
                )

        # Persist the plan
        with _LOCK:
            state = _load_state()
            state["publisher"]["catchup_slots_today"] = catchup_times
            _save_state(state)

        if catchup_times:
            logger.info(
                "📊 [PUBLISHER SALESMAN] Catch-up plan: %d missed → publishing at %s",
                len(missed), catchup_times
            )
        return catchup_times

    def get_deficit(self) -> int:
        with _LOCK:
            return _load_state()["publisher"].get("deficit_videos", 0)

    def get_summary(self) -> str:
        with _LOCK:
            p = _load_state()["publisher"]
        return (
            f"Publisher State — {p['last_check_date']} | "
            f"Published: {p['slots_published_today']} | "
            f"Missed: {p['slots_missed_today']} | "
            f"Catch-up: {p['catchup_slots_today']} | "
            f"Deficit: {p['deficit_videos']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Apify Persistent Quota
# ─────────────────────────────────────────────────────────────────────────────

class ApifyQuotaState:
    """
    Disk-persisted Apify quota tracker.
    Survives process restarts — critical for a $5/month budget.

    Replaces the in-memory _quota_used / _quota_date in apify_downloader.py.
    """

    def __init__(self):
        with _LOCK:
            state = _load_state()
            a = state["apify"]
            today = _today_str()
            if a["quota_date"] != today:
                logger.info("📅 [APIFY SALESMAN] New day — resetting Apify quota")
                a["quota_date"]  = today
                a["quota_used"]  = 0
                a["quota_limit"] = int(os.getenv("APIFY_DAILY_QUOTA", "50"))
                _save_state(state)

    def check(self, needed: int = 1) -> bool:
        """Returns True if quota allows `needed` more calls."""
        with _LOCK:
            state = _load_state()
            a = state["apify"]
            today = _today_str()
            if a["quota_date"] != today:
                a["quota_date"] = today
                a["quota_used"] = 0
                _save_state(state)
            used  = a.get("quota_used", 0)
            limit = a.get("quota_limit", int(os.getenv("APIFY_DAILY_QUOTA", "50")))

        if used + needed > limit:
            logger.warning(
                "🛑 [APIFY SALESMAN] Disk quota exhausted (%d/%d). "
                "Protecting the $5 budget. 💰",
                used, limit
            )
            return False
        return True

    def consume(self, amount: int = 1) -> None:
        """Record that `amount` Apify calls were made."""
        with _LOCK:
            state = _load_state()
            a = state["apify"]
            today = _today_str()
            if a["quota_date"] != today:
                a["quota_date"] = today
                a["quota_used"] = 0
            a["quota_used"]           = a.get("quota_used", 0) + amount
            a["total_calls_all_time"] = a.get("total_calls_all_time", 0) + amount
            _save_state(state)
            logger.info(
                "💰 [APIFY SALESMAN] Quota used: %d/%d today | %d all-time",
                a["quota_used"], a.get("quota_limit", 50), a["total_calls_all_time"]
            )

    def remaining(self) -> int:
        with _LOCK:
            a = _load_state()["apify"]
            today = _today_str()
            if a["quota_date"] != today:
                return int(os.getenv("APIFY_DAILY_QUOTA", "50"))
            return max(0, a.get("quota_limit", 50) - a.get("quota_used", 0))

    def get_summary(self) -> str:
        with _LOCK:
            a = _load_state()["apify"]
        return (
            f"Apify Quota — {a['quota_date']} | "
            f"Used: {a.get('quota_used', 0)}/{a.get('quota_limit', 50)} | "
            f"Remaining: {self.remaining()} | "
            f"Total all-time: {a.get('total_calls_all_time', 0)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience singletons (lazy init on first import)
# ─────────────────────────────────────────────────────────────────────────────

_harvest_state:   Optional[HarvestState]   = None
_publisher_state: Optional[PublisherState] = None
_apify_quota:     Optional[ApifyQuotaState] = None
_singleton_lock   = threading.Lock()


def get_harvest_state() -> HarvestState:
    global _harvest_state
    with _singleton_lock:
        if _harvest_state is None:
            _harvest_state = HarvestState()
    return _harvest_state


def get_publisher_state() -> PublisherState:
    global _publisher_state
    with _singleton_lock:
        if _publisher_state is None:
            _publisher_state = PublisherState()
    return _publisher_state


def get_apify_quota() -> ApifyQuotaState:
    global _apify_quota
    with _singleton_lock:
        if _apify_quota is None:
            _apify_quota = ApifyQuotaState()
    return _apify_quota


def log_full_status() -> None:
    """Dump a full salesman dashboard to the logger."""
    logger.info("=" * 60)
    logger.info("🧠 SALESMAN STATE DASHBOARD")
    logger.info("  %s", get_harvest_state().get_summary())
    logger.info("  %s", get_publisher_state().get_summary())
    logger.info("  %s", get_apify_quota().get_summary())
    logger.info("=" * 60)
