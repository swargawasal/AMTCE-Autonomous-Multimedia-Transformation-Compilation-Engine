"""
studio_status_tracker.py
────────────────────────
Publish real-time rendering progress to the GitHub Pages branch so the
AMTCE Content Studio can display a live "Visual Timeline" while the cloud
runner processes a video.

Usage (from any AMTCE module):
    from Core_Modules.studio_status_tracker import StudioStatusTracker
    tracker = StudioStatusTracker()
    tracker.start(job_id="abc123", actress="sakshitha", niche="Fashion",
                  source_url="https://youtube.com/watch?v=xxx")
    tracker.step("STEP_DOWNLOAD", 10)
    tracker.step("STEP_FACE_SWAP", 40)
    tracker.step("STEP_LIP_SYNC", 60)
    tracker.step("STEP_AUDIO_MIX", 75)
    tracker.step("STEP_SUBTITLES", 85)
    tracker.step("STEP_RENDER", 95)
    tracker.done()      # or tracker.failed("reason")
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from typing import Optional

log = logging.getLogger(__name__)

# ── Steps in order ────────────────────────────────────────────────────────────
STEPS = [
    "STEP_DOWNLOAD",
    "STEP_FACE_SWAP",
    "STEP_LIP_SYNC",
    "STEP_AUDIO_MIX",
    "STEP_SUBTITLES",
    "STEP_RENDER",
]

STEP_LABELS = {
    "STEP_DOWNLOAD":  "⬇️ Downloading",
    "STEP_FACE_SWAP": "🧬 Face Swapping",
    "STEP_LIP_SYNC":  "👄 Lip Syncing",
    "STEP_AUDIO_MIX": "🎵 Mixing Audio",
    "STEP_SUBTITLES": "🗒️ Subtitles",
    "STEP_RENDER":    "🖥️ Rendering",
}

# File on gh-pages branch that the studio polls
STATUS_FILE = "active_run_status.json"

# ─────────────────────────────────────────────────────────────────────────────

class StudioStatusTracker:
    """
    Writes JSON status updates to active_run_status.json on the gh-pages
    branch via direct git operations (uses the checked-out repo).

    Falls back silently if GITHUB_TOKEN or the repo are unavailable — 
    never crash the main pipeline due to UI tracking failures.
    """

    def __init__(self):
        self._token = os.environ.get("GITHUB_TOKEN", "")
        self._repo  = os.environ.get(
            "GITHUB_REPOSITORY",
            "swargawasal/AMTCE-Autonomous-Multimedia-Transformation-Compilation-Engine"
        )
        self._branch = "gh-pages"
        self._job_id: Optional[str] = None
        self._status: dict = {}
        self._enabled = bool(self._token)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(
        self,
        job_id: Optional[str] = None,
        actress: str = "",
        niche: str = "",
        source_url: str = "",
    ) -> None:
        self._job_id = job_id or str(uuid.uuid4())[:8]
        self._status = {
            "job_id":     self._job_id,
            "actress":    actress,
            "niche":      niche,
            "source_url": source_url,
            "state":      "RUNNING",
            "current_step": None,
            "progress":   0,
            "steps": {s: "pending" for s in STEPS},
            "started_at": int(time.time()),
            "updated_at": int(time.time()),
            "error":      None,
        }
        log.info(f"[Studio] Job {self._job_id} started — pushing initial status")
        self._push()

    def step(self, step: str, progress: int = 0) -> None:
        if not self._status:
            return
        # Mark previous steps done
        for s in STEPS:
            if s == step:
                break
            self._status["steps"][s] = "done"
        self._status["steps"][step]  = "active"
        self._status["current_step"] = step
        self._status["progress"]     = max(0, min(100, progress))
        self._status["updated_at"]   = int(time.time())
        log.info(f"[Studio] {STEP_LABELS.get(step, step)} — {progress}%")
        self._push()

    def done(self) -> None:
        if not self._status:
            return
        for s in STEPS:
            self._status["steps"][s] = "done"
        self._status["state"]        = "DONE"
        self._status["current_step"] = None
        self._status["progress"]     = 100
        self._status["updated_at"]   = int(time.time())
        log.info(f"[Studio] Job {self._job_id} completed")
        self._push()

    def failed(self, reason: str = "") -> None:
        if not self._status:
            return
        self._status["state"]      = "FAILED"
        self._status["error"]      = reason
        self._status["updated_at"] = int(time.time())
        log.warning(f"[Studio] Job {self._job_id} FAILED: {reason}")
        self._push()

    # ── Internal: push via GitHub API ─────────────────────────────────────────

    def _push(self) -> None:
        """Commit active_run_status.json to gh-pages using the GitHub Contents API."""
        if not self._enabled:
            return
        try:
            import urllib.request
            payload_bytes = json.dumps(self._status, indent=2).encode("utf-8")
            b64_content   = base64.b64encode(payload_bytes).decode("utf-8")

            api_url = (
                f"https://api.github.com/repos/{self._repo}"
                f"/contents/{STATUS_FILE}"
            )
            headers = {
                "Authorization": f"token {self._token}",
                "Content-Type":  "application/json",
                "Accept":        "application/vnd.github.v3+json",
            }

            # Try to GET current SHA (needed for update)
            sha: Optional[str] = None
            try:
                req = urllib.request.Request(
                    f"{api_url}?ref={self._branch}", headers=headers
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    meta = json.loads(resp.read())
                    sha = meta.get("sha")
            except Exception:
                pass  # File doesn't exist yet — will be created

            body: dict = {
                "message": f"studio: update run status [{self._status.get('current_step','done')}] [skip ci]",
                "content": b64_content,
                "branch":  self._branch,
            }
            if sha:
                body["sha"] = sha

            req = urllib.request.Request(
                api_url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 201):
                    log.warning(f"[Studio] Status push returned HTTP {resp.status}")
        except Exception as exc:
            log.warning(f"[Studio] Status push failed (non-fatal): {exc}")


# ── Singleton ─────────────────────────────────────────────────────────────────
_tracker: Optional[StudioStatusTracker] = None


def get_tracker() -> StudioStatusTracker:
    """Return the process-wide singleton tracker."""
    global _tracker
    if _tracker is None:
        _tracker = StudioStatusTracker()
    return _tracker
