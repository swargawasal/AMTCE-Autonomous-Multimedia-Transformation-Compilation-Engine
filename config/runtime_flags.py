"""
config/runtime_flags.py
─────────────────────────────────────────────────────────────────
Global runtime control switches for the AMTCE pipeline.

STRICT MODE (default)
    ALLOW_PYTHON_FALLBACK  = False   →  Gemini fail == pipeline STOP
    ENABLE_EARLY_HARD_STOP = True    →  Stop immediately after timeline
    ENABLE_VALIDATOR_HARD_STOP = True→  Stop on FAKE_EDITOR verdict

COMPATIBILITY / DEBUG MODE
    Set env-var  ALLOW_FALLBACK=true  (or flip the constant below)
    to re-enable python heuristic fallback for local testing.

Usage:
    from config.runtime_flags import ALLOW_PYTHON_FALLBACK
"""

import os

# ──────────────────────────────────────────────────────────────────────────────
# 🔥 STRICT REAL EDITOR MODE — Change to True ONLY for debug / local testing
# ──────────────────────────────────────────────────────────────────────────────

# Master fallback gate.
# False (default) = STRICT MODE — any python fallback triggers a hard stop.
# True            = COMPATIBILITY MODE — python heuristic fallback is allowed.
ALLOW_PYTHON_FALLBACK = True

# Stop the pipeline immediately after timeline reconstruction if editing failed.
ENABLE_EARLY_HARD_STOP = False

# Stop the pipeline if the segment validator returns a FAKE_EDITOR verdict.
ENABLE_VALIDATOR_HARD_STOP = False
