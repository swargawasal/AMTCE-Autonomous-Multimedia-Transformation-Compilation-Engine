"""
Energy Scorer — AMTCE Per-Segment Audio Energy Analysis
========================================================

Scores each clip segment by its audio RMS energy (measured in dB) to
identify the loudest, most emotionally charged moments. Those segments
are flagged for "punch-in" dynamic zoom by build_transition_graph().

Design principles:
  - librosa is an optional heavy dependency. If unavailable, fall back to
    FFmpeg's `astats` filter which is always present.
  - Energy scores are normalised to [0.0, 1.0] so they integrate directly
    with the existing `importance` / `energy_score` fields on segment dicts.
  - The top 60% threshold (ZOOM_ENERGY_THRESHOLD = 0.4) means only segments
    in the upper 40th percentile receive a punch-in — matching professional
    human editing judgement that reserves zooms for real emphasis.
  - All errors are caught; the function degrades silently and returns the
    original segments unchanged rather than crashing the pipeline.

Integration:
  Call `score_segments(audio_path, segments)` from orchestrator.py BEFORE
  passing segments to build_transition_graph(). The returned list has each
  segment annotated with an `energy_score` (float 0–1) and an
  `importance` field that mirrors it (for legacy compat).
"""

import logging
import os
import subprocess
import math
from typing import List, Dict

logger = logging.getLogger("energy_scorer")

# Segments whose normalised energy score is above this threshold get flagged
# as candidates for dynamic punch-in zoom.
# 0.4 = top 60% (only the loudest, most emphatic moments).
ZOOM_ENERGY_THRESHOLD = float(os.getenv("ZOOM_ENERGY_THRESHOLD", "0.4"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_segments(audio_path: str, segments: List[Dict]) -> List[Dict]:
    """
    Annotates each segment dict with:
        segment["energy_score"]   float 0.0–1.0   (normalised RMS energy)
        segment["importance"]     float 0.0–1.0   (mirrors energy_score for compat)

    Args:
        audio_path: Path to the source audio/video file to measure.
        segments:   List of segment dicts with at least "start" and "end" keys.

    Returns:
        The same list of dicts, mutated in-place with new energy fields.
        On any failure, the original list is returned with energy_score = 0.5.
    """
    if not segments:
        return segments

    if not audio_path or not os.path.exists(audio_path):
        logger.warning(f"⚠️ [EnergyScorer] Audio path not found: {audio_path}. Skipping energy scoring.")
        return _apply_default_scores(segments)

    try:
        raw_scores = _measure_rms_per_segment(audio_path, segments)
        return _normalise_and_annotate(segments, raw_scores)
    except Exception as e:
        logger.error(f"❌ [EnergyScorer] Unexpected failure: {e}")
        return _apply_default_scores(segments)


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def _measure_rms_per_segment(audio_path: str, segments: List[Dict]) -> List[float]:
    """
    Attempts librosa first (GPU-optional), then falls back to FFmpeg astats.
    Returns a list of raw RMS values (in linear amplitude, not dB) per segment.
    """
    try:
        return _measure_librosa(audio_path, segments)
    except Exception as e:
        logger.debug(f"[EnergyScorer] librosa unavailable ({e}), falling back to FFmpeg astats")
        return _measure_ffmpeg_astats(audio_path, segments)


def _measure_librosa(audio_path: str, segments: List[Dict]) -> List[float]:
    """
    Uses librosa.load + librosa.feature.rms to compute mean RMS per segment.
    Only loads the audio once (full file) and slices by sample index — fast.
    """
    import librosa
    import numpy as np

    logger.info(f"🎵 [EnergyScorer] Loading audio with librosa: {os.path.basename(audio_path)}")

    # sr=None preserves native sample rate; mono=True reduces memory
    y, sr = librosa.load(audio_path, sr=None, mono=True)

    scores = []
    for seg in segments:
        start_s = float(seg.get("start", 0.0))
        end_s   = float(seg.get("end",   start_s + 1.0))

        # Convert timestamps to sample indices
        start_sample = int(start_s * sr)
        end_sample   = int(end_s   * sr)

        # Guard against out-of-range slices
        start_sample = max(0, min(start_sample, len(y) - 1))
        end_sample   = max(start_sample + 1, min(end_sample, len(y)))

        segment_audio = y[start_sample:end_sample]

        if len(segment_audio) == 0:
            scores.append(0.0)
            continue

        # librosa.feature.rms returns shape (1, n_frames); take mean
        rms_frames = librosa.feature.rms(y=segment_audio)
        mean_rms = float(np.mean(rms_frames))
        scores.append(mean_rms)

    logger.info(f"✅ [EnergyScorer] librosa: scored {len(scores)} segments.")
    return scores


def _measure_ffmpeg_astats(audio_path: str, segments: List[Dict]) -> List[float]:
    """
    FFmpeg fallback: uses `astats` filter with `atrim` to measure RMS per segment.
    Slower than librosa (subprocess per segment) but zero Python dependencies.
    """
    logger.info(f"🔧 [EnergyScorer] Using FFmpeg astats for {len(segments)} segments.")
    scores = []

    for seg in segments:
        start_s = float(seg.get("start", 0.0))
        end_s   = float(seg.get("end",   start_s + 1.0))
        duration = max(0.1, end_s - start_s)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_s),
            "-t",  str(duration),
            "-i",  audio_path,
            "-vn",                          # no video processing
            "-filter:a", "astats=metadata=1:reset=1",
            "-f", "null", "-"
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            stderr_text = result.stderr.decode(errors="ignore")

            # Parse "RMS level dB" from astats output
            # Format: "lavfi.astats.Overall.RMS_level=-18.3"
            rms_db = None
            for line in stderr_text.splitlines():
                if "RMS_level" in line and "Overall" in line:
                    try:
                        rms_db = float(line.split("=")[-1].strip())
                        break
                    except ValueError:
                        pass

            if rms_db is not None and math.isfinite(rms_db):
                # Convert dB to linear amplitude: amplitude = 10^(dB/20)
                # Clamp to reasonable range (-60dB to 0dB)
                rms_db_clamped = max(-60.0, min(0.0, rms_db))
                linear = 10.0 ** (rms_db_clamped / 20.0)
                scores.append(linear)
            else:
                scores.append(0.0)

        except subprocess.TimeoutExpired:
            logger.warning(f"⚠️ [EnergyScorer] FFmpeg astats timed out for segment {start_s:.1f}s-{end_s:.1f}s")
            scores.append(0.0)
        except Exception as e:
            logger.debug(f"[EnergyScorer] astats failed for segment {start_s:.1f}-{end_s:.1f}: {e}")
            scores.append(0.0)

    logger.info(f"✅ [EnergyScorer] FFmpeg astats: scored {len(scores)} segments.")
    return scores


# ---------------------------------------------------------------------------
# Normalisation & Annotation
# ---------------------------------------------------------------------------

def _normalise_and_annotate(segments: List[Dict], raw_scores: List[float]) -> List[Dict]:
    """
    Min-max normalises raw RMS values to [0.0, 1.0] and writes them to each
    segment dict. Also sets `importance` for backward compatibility with
    build_transition_graph() which already reads seg.get("importance", 0).
    """
    if not raw_scores:
        return _apply_default_scores(segments)

    min_score = min(raw_scores)
    max_score = max(raw_scores)
    score_range = max_score - min_score

    for i, seg in enumerate(segments):
        raw = raw_scores[i] if i < len(raw_scores) else 0.0

        if score_range > 1e-9:
            normalised = (raw - min_score) / score_range
        else:
            # All segments have identical energy (silence or constant tone)
            normalised = 0.5

        normalised = round(max(0.0, min(1.0, normalised)), 4)
        seg["energy_score"] = normalised
        # `importance` mirrors energy_score so build_transition_graph()'s
        # existing `if not zoom_applied and importance > 0.6` gate fires correctly
        seg["importance"] = normalised

    high_energy = sum(1 for s in raw_scores if s > 0)
    logger.info(
        f"📊 [EnergyScorer] Annotated {len(segments)} segments. "
        f"Zoom-eligible (energy_score>{ZOOM_ENERGY_THRESHOLD:.1f}): "
        f"{sum(1 for seg in segments if seg.get('energy_score', 0) > ZOOM_ENERGY_THRESHOLD)}"
    )

    return segments


def _apply_default_scores(segments: List[Dict]) -> List[Dict]:
    """
    Fallback: assign a neutral score so the pipeline keeps running.
    0.5 means no segment will be zoom-flagged (threshold is 0.4 above 0.6).
    """
    for seg in segments:
        seg.setdefault("energy_score", 0.5)
        seg.setdefault("importance",   0.5)
    return segments
