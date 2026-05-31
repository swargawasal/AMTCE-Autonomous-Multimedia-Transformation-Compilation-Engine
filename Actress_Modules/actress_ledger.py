"""
actress_ledger.py — Military-Grade Deduplication Ledger
========================================================
Prevents re-downloading the same reel across ALL sessions.

Layer 1 — Shortcode lock  : Instagram post ID extracted from URL.
           Zero cost. Checked BEFORE any download call.

Layer 2 — Content hash lock: MD5 of the downloaded file bytes.
           Catches cases where the same video is served from a
           different URL (re-uploads, CDN mirrors, etc.)
           Checked AFTER download; file is deleted on hit.

Layer 3 — Cross-channel dedup: Tracks which channel used each clip.
           A clip used by General_Fallback will NOT be reused by
           Paparazzi_Channel, and vice versa.

Ledger file : Actress_Modules/actress_ledger.json
              Human-readable, safe to delete to reset history.
"""

import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

_LEDGER_PATH = os.path.join(os.path.dirname(__file__), "actress_ledger.json")
_LOCK        = threading.Lock()          # thread-safe write protection


# ─────────────────────────────────────────────────────────────────────────────
# Shortcode extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_shortcode(url: str) -> Optional[str]:
    """
    Extracts the Instagram shortcode from any reel/post URL.
    https://www.instagram.com/reel/ABC123xyz/ → 'ABC123xyz'
    Returns None if no shortcode found.
    """
    if not url:
        return None
    match = re.search(
        r"instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_\-]+)", url
    )
    return match.group(1) if match else None


# ─────────────────────────────────────────────────────────────────────────────
# Content hashing
# ─────────────────────────────────────────────────────────────────────────────

def file_md5(path: str, chunk: int = 1 << 20) -> str:
    """Fast MD5 of a file (1 MB chunks)."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Ledger class
# ─────────────────────────────────────────────────────────────────────────────

class ActressLedger:
    """
    Singleton ledger loaded once per process and persisted to disk after
    every successful commit.

    Usage
    -----
    ledger = ActressLedger()

    # Layer 1 — before download
    if ledger.shortcode_seen(sc):
        continue

    # Layer 2 — after download
    if ledger.hash_seen(video_path):
        os.remove(video_path)
        continue

    # Commit both to disk
    ledger.commit(shortcode, video_path)
    """

    def __init__(self, path: str = _LEDGER_PATH):
        self._path        = path
        self._shortcodes:    Set[str]        = set()
        self._hashes:        Dict[str, str]  = {}   # md5 → stored_path
        self._channel_map:   Dict[str, str]  = {}   # shortcode → channel_folder
        self._timestamp_map: Dict[str, float] = {}  # shortcode → Apify post unix timestamp
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(self._path):
            logger.info("📖 Actress Ledger: fresh start (no existing ledger).")
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._shortcodes    = set(data.get("shortcodes", []))
            self._hashes        = data.get("hashes", {})
            self._channel_map   = data.get("channel_map", {})
            self._timestamp_map = data.get("timestamp_map", {})
            logger.info(
                "📖 Actress Ledger loaded — %d shortcodes, %d hashes, %d channel-mapped, %d timestamped",
                len(self._shortcodes), len(self._hashes), len(self._channel_map), len(self._timestamp_map)
            )
        except Exception as exc:
            logger.warning("⚠️ Ledger load failed (starting fresh): %s", exc)

    def _save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version":       4,
                        "updated":       datetime.now().isoformat(),
                        "shortcodes":    sorted(self._shortcodes),
                        "hashes":        self._hashes,
                        "channel_map":   self._channel_map,
                        "timestamp_map": self._timestamp_map,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception as exc:
            logger.error("❌ Ledger save failed: %s", exc)

    # ── Layer 1: Shortcode check ──────────────────────────────────────────────

    def shortcode_seen(self, shortcode: str) -> bool:
        """Returns True if this Instagram post was already downloaded."""
        return shortcode in self._shortcodes

    # ── Layer 2: Hash check ───────────────────────────────────────────────────

    def hash_seen(self, video_path: str) -> bool:
        """
        Computes the file MD5 and checks if we've seen it before.
        Attaches the computed hash to self._last_hash for reuse in commit().
        Returns True if duplicate.
        """
        try:
            self._last_hash = file_md5(video_path)
            if self._last_hash in self._hashes:
                existing = self._hashes[self._last_hash]
                logger.warning(
                    "🚫 [LEDGER] Hash duplicate detected! "
                    "New: %s  ←→  Existing: %s  (MD5: %s)",
                    os.path.basename(video_path),
                    existing,
                    self._last_hash[:12],
                )
                return True
            return False
        except Exception as exc:
            logger.warning("⚠️ Hash check failed: %s", exc)
            self._last_hash = None
            return False

    # ── Commit ────────────────────────────────────────────────────────────────

    def commit(self, shortcode: Optional[str], video_path: str) -> None:
        """
        Adds a successfully downloaded reel to the ledger and persists to disk.
        Call this ONCE after both layer checks pass and the file is committed.
        """
        with _LOCK:
            if shortcode:
                self._shortcodes.add(shortcode)
            if hasattr(self, "_last_hash") and self._last_hash:
                self._hashes[self._last_hash] = os.path.basename(video_path)
            elif os.path.exists(video_path):
                # hash wasn't computed yet — compute now
                try:
                    h = file_md5(video_path)
                    self._hashes[h] = os.path.basename(video_path)
                except Exception:
                    pass
            self._save()
            logger.info(
                "🔒 [LEDGER] Committed — shortcode: %s | total seen: %d SC / %d hashes",
                shortcode or "N/A",
                len(self._shortcodes),
                len(self._hashes),
            )

    # ── Layer 3: Cross-channel dedup ──────────────────────────────────────────

    def channel_for_shortcode(self, shortcode: str) -> Optional[str]:
        """
        Returns the channel folder that already used this shortcode,
        or None if not yet used by any channel.
        Use this BEFORE downloading to enforce cross-channel dedup.
        """
        if not shortcode:
            return None
        return self._channel_map.get(shortcode)

    def commit_with_channel(
        self,
        shortcode: Optional[str],
        video_path: str,
        channel_folder: str,
        post_timestamp: Optional[float] = None,  # Unix epoch from Apify reel metadata
    ) -> None:
        """
        Extended commit that also records which channel used this clip
        and the original post timestamp from Apify (for posting time analysis).
        Prevents the same clip from being posted on multiple channels.

        Replaces the plain commit() call in the new paparazzi pipeline.
        commit() still works for legacy actress_accounts.json paths.
        post_timestamp: reel.get("timestamp") from Apify — Unix epoch float.
        """
        with _LOCK:
            if shortcode:
                self._shortcodes.add(shortcode)
                self._channel_map[shortcode] = channel_folder
                if post_timestamp is not None:
                    try:
                        self._timestamp_map[shortcode] = float(post_timestamp)
                    except ValueError:
                        try:
                            from datetime import datetime
                            ts_str = str(post_timestamp).replace("Z", "+00:00")
                            self._timestamp_map[shortcode] = datetime.fromisoformat(ts_str).timestamp()
                        except Exception:
                            pass
            if hasattr(self, "_last_hash") and self._last_hash:
                self._hashes[self._last_hash] = os.path.basename(video_path)
            elif os.path.exists(video_path):
                try:
                    h = file_md5(video_path)
                    self._hashes[h] = os.path.basename(video_path)
                except Exception:
                    pass
            self._save()
            logger.info(
                "🔒 [LEDGER+CH] Committed — shortcode: %s | channel: %s | ts: %s | "
                "total: %d SC / %d hashes / %d channel-mapped",
                shortcode or "N/A",
                channel_folder,
                datetime.fromtimestamp(self._timestamp_map[shortcode]).strftime("%H:%M") if (shortcode and shortcode in self._timestamp_map) else "N/A",
                len(self._shortcodes),
                len(self._hashes),
                len(self._channel_map),
            )

    def get_timestamp_map(self) -> Dict:
        """
        Returns a dict of {shortcode: {channel, timestamp_utc}} for the posting time analyzer.
        Only includes entries that have both a channel and a timestamp.
        """
        result = {}
        for sc, ts in self._timestamp_map.items():
            ch = self._channel_map.get(sc)
            if ch and ts:
                result[sc] = {"channel": ch, "timestamp_utc": ts}
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton (one instance per process)
# ─────────────────────────────────────────────────────────────────────────────

_ledger: Optional[ActressLedger] = None

def get_ledger() -> ActressLedger:
    """Returns the process-level singleton ledger, initialising it if needed."""
    global _ledger
    if _ledger is None:
        _ledger = ActressLedger()
    return _ledger
