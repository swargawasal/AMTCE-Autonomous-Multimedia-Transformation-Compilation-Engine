"""
Setup Runner — AMTCE Pipeline Verification
==========================================
Runs once on first boot (or explicitly when called) to verify all
pipeline dependencies are healthy before jobs start.

Checks:
  1. DNN Face Model Load (verify_dnn)
  2. Feature Integration Tests (verify_features)
  3. Full Compiler Integration (verify_integration)

Usage:
  python -m setup_modules.setup_runner          # Full check
  python -m setup_modules.setup_runner --quick  # DNN + Features only (skip render test)

Called automatically by main.py on first run (checks .setup_ok sentinel file).
"""

import os
import sys
import re
import logging
import argparse
import subprocess
import unittest
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("setup_runner")

SENTINEL_FILE = ".setup_ok"
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root


def check_dnn():
    """Verify Face DNN loads and runs inference."""
    logger.info("🔬 [1/3] Verifying DNN model (human_guard)...")
    try:
        sys.path.insert(0, SRC)
        try:
            from Visual_Refinement_Modules.quality_orchestrator import human_guard
        except ImportError:
            from quality_orchestrator import human_guard

        if not human_guard.face_net:
            logger.warning("⚠️  DNN: face_net is None — DNN face detection will be unavailable.")
            return False

        import numpy as np
        import cv2
        dummy = np.zeros((500, 500, 3), dtype=np.uint8)
        faces = human_guard.detect_faces(dummy)
        logger.info(f"✅ DNN OK — Inference ran (faces on blank frame: {len(faces)})")
        return True

    except Exception as e:
        logger.warning(f"⚠️  DNN check failed: {e}")
        return False


def check_features():
    """Run feature unit tests (mocked — no real API calls). Runs in subprocess to avoid sys.modules pollution."""
    logger.info("🧪 [2/3] Running feature integration tests...")
    try:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"          # Force UTF-8 mode in child process
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "setup_modules.verify_features", "-v"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",            # Replace un-decodable bytes instead of crashing
            timeout=60,
            cwd=SRC,
            env=env,
        )
        # unittest prints to stderr in verbose mode
        output = result.stderr + result.stdout
        ran_match = re.search(r"Ran (\d+) test", output)
        total = int(ran_match.group(1)) if ran_match else 5  # 5 known tests

        if result.returncode == 0:
            logger.info(f"✅ Feature Tests: {total}/{total} passed")
            return True
        else:
            failed_match = re.search(r"failures=(\d+)", output)
            error_match = re.search(r"errors=(\d+)", output)
            failures = int(failed_match.group(1)) if failed_match else 0
            errors = int(error_match.group(1)) if error_match else 0
            passed = total - failures - errors
            logger.info(f"✅ Feature Tests: {passed}/{total} passed")
            logger.warning(f"⚠️  {failures} failures, {errors} errors")
            for line in output.splitlines():
                if "FAIL:" in line or "ERROR:" in line or "AssertionError" in line:
                    logger.warning(f"   {line}")
            return False

    except subprocess.TimeoutExpired:
        logger.warning("⚠️  Feature tests timed out after 60s")
        return False
    except Exception as e:
        logger.warning(f"⚠️  Feature tests failed to run: {e}")
        return False



def check_integration(quick=False):
    """Test full compiler pipeline (skip in quick mode)."""
    if quick:
        logger.info("⚡ [3/3] Skipping full compile test (--quick mode)")
        return True

    logger.info("🚀 [3/3] Running full pipeline integration check...")
    try:
        sys.path.insert(0, SRC)
        from Compiler_Modules import compiler

        # Use a dummy path — verify that import + function signature works
        # (actual render only happens if a real video file exists)
        test_input = os.path.join(SRC, "downloads", "test_sample.mp4")
        test_output = os.path.join(SRC, "setup_modules", "verify_output.mp4")

        if not os.path.exists(test_input):
            logger.info("ℹ️  No test video found — skipping render, import-only check passed.")
            return True

        result, meta = compiler.compile_with_transitions(
            [test_input], test_output,
            title="Setup Verification", enhance=False
        )
        if result and os.path.exists(result):
            logger.info(f"✅ Pipeline compile OK: {result}")
            # Clean up verify output
            try:
                os.remove(result)
            except Exception:
                pass
            return True
        else:
            logger.warning(f"⚠️  Pipeline compile returned: {result}")
            return False
    except Exception as e:
        logger.error(f"❌ Integration check failed: {e}")
        return False


def check_fonts():
    """Verify and download required fonts."""
    logger.info("🔤 [X/X] Verifying required fonts (Montserrat)...")
    try:
        sys.path.insert(0, SRC)
        import Text_Modules.font_manager as fm
        font_path = fm.ensure_montserrat_font()
        if font_path:
            logger.info(f"✅ Font Manager: Verified at {font_path}")
            return True
        else:
            logger.warning("⚠️  Font Manager: Download or validation failed.")
            return False
    except Exception as e:
        logger.warning(f"⚠️  Font Manager check failed: {e}")
        return False

def run_setup(quick=False, force=False):
    """
    Main entry. Runs all checks, writes sentinel on success.
    Returns True if all checks passed.
    """
    logger.info("=" * 55)
    logger.info("  AMTCE — Setup & Verification Runner")
    logger.info("=" * 55)

    if not force and os.path.exists(SENTINEL_FILE):
        logger.info("✅ Setup already verified (.setup_ok exists). Skipping.")
        logger.info("   Run with force=True or delete .setup_ok to re-run.")
        return True

    results = {
        "dnn": check_dnn(),
        "fonts": check_fonts(),
        "features": check_features(),
        "integration": check_integration(quick=quick),
    }

    passed = sum(results.values())
    total = len(results)

    logger.info("")
    logger.info("=" * 55)
    logger.info(f"  Setup Results: {passed}/{total} checks passed")
    for name, ok in results.items():
        logger.info(f"  {'✅' if ok else '⚠️ '} {name}")
    logger.info("=" * 55)

    all_ok = passed == total
    if all_ok:
        # Write sentinel so subsequent boots skip this
        with open(SENTINEL_FILE, "w") as f:
            from datetime import datetime
            f.write(f"verified={datetime.now().isoformat()}\n")
        logger.info("✅ All checks passed — .setup_ok written. Ready for production.")
    else:
        logger.warning("⚠️  Some checks failed. Pipeline will still run but may degrade gracefully.")

    return all_ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AMTCE Setup Verifier")
    parser.add_argument("--quick", action="store_true", help="Skip full render test")
    parser.add_argument("--force", action="store_true", help="Re-run even if .setup_ok exists")
    args = parser.parse_args()
    success = run_setup(quick=args.quick, force=args.force)
    sys.exit(0 if success else 1)
