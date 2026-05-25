"""
Reaction_Engine/reaction_engine.py
------------------------------------
Main orchestrator for the Automated Reaction Engine.

Pipeline position: AFTER the main pipeline produces its final output video,
BEFORE the main pipeline writes sidecar JSON.

This module is OPTIONAL and NON-DESTRUCTIVE:
  - It is activated ONLY when ENABLE_REACTION_ENGINE=yes in .env
  - It reads the final rendered video as INPUT and produces a NEW reaction video
  - The main pipeline output is NEVER modified — the reaction video is ADDITIONAL
  - If anything fails, it logs and returns without touching the main output

Integration (in orchestrator.py — just before final return):
    from Reaction_Engine.reaction_engine import run_reaction_engine
    run_reaction_engine(
        source_video=output_path,       # the main rendered video
        profile_data=profile_data,
        job_dir=job_dir,
    )

.env flags:
    ENABLE_REACTION_ENGINE=yes|no          (master switch, default: no)
    ENABLE_SOURCE_FACE_SWAP=yes|no         (face swap the main source clip, default: no)
    REACTION_OUTPUT_SUFFIX=_reaction       (suffix added to output filename)
    REACTION_LAYOUT=stacked|side_by_side   (default: stacked)
    REACTION_SOURCE_RATIO=0.65             (default: 0.65)
    ENABLE_LIP_SYNC=yes|no                 (default: no)
    WAV2LIP_DIR=path/to/Wav2Lip            (required if ENABLE_LIP_SYNC=yes)
    WAV2LIP_CHECKPOINT=path/to/weights.pth (required if ENABLE_LIP_SYNC=yes)
    REACTION_DUCK_SOURCE_AUDIO=yes|no      (default: yes)
    REACTION_DUCK_LEVEL=0.25               (0.0–1.0)
    REACTION_USE_TTS=yes|no                (auto-generate reactor voice via TTS, default: yes)
    REACTION_TTS_VOICE=en-US-JennyNeural   (Azure/Edge voice for reactor)
"""

import logging
import os
import shutil
import tempfile
from typing import Any, Dict, Optional

logger = logging.getLogger("reaction_engine")


# ── Master flag — checked once at module load ──────────────────────────────────
def _is_enabled() -> bool:
    return os.getenv("ENABLE_REACTION_ENGINE", "no").lower() in ("yes", "true", "1")


def run_reaction_engine(
    source_video: str,
    profile_data: Dict[str, Any],
    job_dir: str,
) -> Optional[str]:
    """
    Entry-point called by orchestrator.compile_video() after final audio mix.

    This is the ONLY function orchestrator.py needs to call.
    Everything else is handled internally.

    Args:
        source_video:  Path to the fully rendered output video (main pipeline output).
        profile_data:  Pipeline profile dict (contains fused_moments, etc.).
        job_dir:       Temporary job directory (for intermediate files).

    Returns:
        Path to the reaction video, or None if engine is disabled/failed.
    """
    # ── Guard: master kill-switch ────────────────────────────────────────────
    if not _is_enabled():
        logger.debug("[REACTION_ENGINE] Disabled (ENABLE_REACTION_ENGINE=no). Skipping.")
        return None

    logger.info("🎬 [REACTION_ENGINE] ─────────────────────────────────────────")
    logger.info("🎬 [REACTION_ENGINE] Starting Automated Reaction Engine...")

    if not source_video or not os.path.isfile(source_video):
        logger.warning(f"[REACTION_ENGINE] Source video not found: {source_video}. Skipping.")
        return None

    try:
        return _run_pipeline(source_video, profile_data, job_dir)
    except Exception as exc:
        logger.error(
            f"❌ [REACTION_ENGINE] Unexpected failure (non-fatal to main pipeline): {exc}",
            exc_info=True,
        )
        return None


def _run_pipeline(
    source_video: str,
    profile_data: Dict[str, Any],
    job_dir: str,
) -> Optional[str]:
    """
    Internal pipeline execution — v2 emotion-based reel.

    Steps:
        1. Generate reaction script (emotion moments from source analysis)
        2. Load reactor library
        3. Build assembled reactor reel (emotion clips + neutral gaps = source duration)
        4. Render full narration TTS (covers all reaction moments)
        5. Apply chunked lip sync (TTS audio is master → news-anchor style)
        6. Optional face swap on the synced reel
        7. Composite: PiP overlay on full-screen source
    """
    from Reaction_Engine.reaction_script_generator import generate_reaction_script
    from Reaction_Engine.reactor_library_manager import ReactorLibraryManager
    from Reaction_Engine.reactor_reel_builder import build_reactor_reel
    from Reaction_Engine.lip_sync_engine import LipSyncEngine
    from Reaction_Engine.reaction_compositor import composite_reaction_video
    from Reaction_Engine.reaction_compositor import _get_video_duration
    from Reaction_Engine.audio_interleave_scheduler import (
        is_interleave_enabled, build_interleaved_schedule
    )
    from Reaction_Engine.face_swap_engine import FaceSwapEngine

    # ── Step 0: Optional face swap on the SOURCE video ────────────────────────
    face_swap_engine = FaceSwapEngine(is_source_clip=True)
    if os.getenv("ENABLE_SOURCE_FACE_SWAP", "no").lower() in ("yes", "true", "1") and face_swap_engine.is_available():
        logger.info("[REACTION_ENGINE] Step 0: Applying face swap to SOURCE video...")
        swapped_source = face_swap_engine.swap_face(
            target_video_path=source_video,
            emotion_category="source_clip",
        )
        if swapped_source and os.path.isfile(swapped_source):
            logger.info("[REACTION_ENGINE] Source video successfully face-swapped.")
            source_video = swapped_source
        else:
            logger.warning("[REACTION_ENGINE] Source face swap failed, falling back to original source.")

    # ── Step 1: Generate reaction script ─────────────────────────────────────
    logger.info("[REACTION_ENGINE] Step 1: Generating reaction script...")
    reaction_lines = generate_reaction_script(profile_data)

    if not reaction_lines:
        logger.info("[REACTION_ENGINE] No reaction moments found. Skipping.")
        return None
    logger.info(f"[REACTION_ENGINE] {len(reaction_lines)} reaction lines generated.")

    # ── Step 2: Load reactor library ──────────────────────────────────────────
    logger.info("[REACTION_ENGINE] Step 2: Loading reactor library...")
    library = ReactorLibraryManager()
    if not library.is_library_ready():
        logger.warning(
            "[REACTION_ENGINE] Reactor library has no clips. "
            "Add clips to Reaction_Engine/reactor_library/{emotion}/. Skipping."
        )
        return None

    # ── Step 3: Build assembled reactor reel (emotion clips + neutral gaps) ───
    logger.info("[REACTION_ENGINE] Step 3: Building emotion-based reactor reel...")
    source_duration = _get_video_duration(source_video)
    if source_duration <= 0:
        logger.warning(f"[REACTION_ENGINE] Could not read source duration. Skipping.")
        return None

    reactor_reel = build_reactor_reel(
        reaction_lines=reaction_lines,
        library=library,
        source_duration=source_duration,
        output_dir=job_dir,
    )
    if not reactor_reel:
        logger.error("[REACTION_ENGINE] Reel builder failed. Skipping.")
        return None
    logger.info(f"[REACTION_ENGINE] Reactor reel: {os.path.basename(reactor_reel)}")

    # ── Step 4: Render full narration TTS ─────────────────────────────────────
    use_tts = os.getenv("REACTION_USE_TTS", "yes").lower() in ("yes", "true", "1")
    narrator_audio = None

    if use_tts:
        logger.info("[REACTION_ENGINE] Step 4: Rendering narration TTS...")
        # Concatenate all reaction line texts into one narration script
        full_narration = " ... ".join(
            line.get("text", "") for line in reaction_lines if line.get("text", "").strip()
        )
        narrator_audio = _render_reactor_tts(
            text=full_narration,
            output_dir=job_dir,
        )
        if narrator_audio:
            logger.info(f"[REACTION_ENGINE] Narration TTS: {os.path.basename(narrator_audio)}")
        else:
            logger.info("[REACTION_ENGINE] TTS unavailable — reactor will be silent.")
    else:
        logger.info("[REACTION_ENGINE] REACTION_USE_TTS=no — no narration.")

    # ── Step 5: Chunked lip sync (narration audio = master, news-anchor style) ─
    lip_sync_engine = LipSyncEngine()
    if lip_sync_engine.is_available() and narrator_audio:
        logger.info("[REACTION_ENGINE] Step 5: Applying chunked lip sync (narration master)...")
        reactor_reel = lip_sync_engine.sync_chunked(
            reactor_reel_path=reactor_reel,
            narration_audio_path=narrator_audio,
            output_dir=job_dir,
            tag="narration_sync",
        )
    else:
        logger.info("[REACTION_ENGINE] Step 5: Lip sync skipped (disabled or no audio).")

    # ── Step 6: Optional face swap on the assembled reel ──────────────────────
    from Reaction_Engine.face_swap_engine import FaceSwapEngine
    face_swap_engine = FaceSwapEngine()
    if face_swap_engine.is_available():
        logger.info("[REACTION_ENGINE] Step 6: Applying face swap on assembled reel...")
        # Use dominant emotion for cache folder
        dominant_emotion = max(reaction_lines, key=lambda l: l.get("score", 0.0)).get("emotion", "neutral")
        reactor_reel = face_swap_engine.swap_face(
            target_video_path=reactor_reel,
            emotion_category=dominant_emotion,
        )
    else:
        logger.info("[REACTION_ENGINE] Step 6: Face swap skipped.")

    # ── Step 7a: Build interleaved audio schedule ─────────────────────────────
    audio_schedule = None
    if is_interleave_enabled() and narrator_audio:
        logger.info("[REACTION_ENGINE] Step 7a: Building interleaved audio schedule...")
        audio_schedule = build_interleaved_schedule(
            reaction_lines=reaction_lines,
            narration_audio=narrator_audio,
            video_duration=source_duration,
            job_dir=job_dir,
        )
        n_nr = sum(1 for s in audio_schedule if s["track"] == "narration")
        n_rx = sum(1 for s in audio_schedule if s["track"] == "reactor")
        logger.info(f"[REACTION_ENGINE] Interleave: {n_nr} narration + {n_rx} reactor slots.")

    # ── Step 7: PiP composite ──────────────────────────────────────────────────
    logger.info("[REACTION_ENGINE] Step 7: PiP composite overlay...")

    suffix       = os.getenv("REACTION_OUTPUT_SUFFIX", "_reaction")
    source_dir   = os.path.dirname(os.path.abspath(source_video))
    source_stem  = os.path.splitext(os.path.basename(source_video))[0]
    reaction_out = os.path.join(source_dir, f"{source_stem}{suffix}.mp4")

    success = composite_reaction_video(
        source_video=source_video,
        reactor_video=reactor_reel,
        reactor_audio=narrator_audio,
        output_path=reaction_out,
        audio_schedule=audio_schedule,
    )


    if success:
        size_mb = os.path.getsize(reaction_out) / (1024 * 1024)
        logger.info(
            f"✅ [REACTION_ENGINE] Reaction video ready: "
            f"{os.path.basename(reaction_out)} ({size_mb:.1f}MB)"
        )
        profile_data["reaction_video_path"] = reaction_out
        return reaction_out
    else:
        logger.error("[REACTION_ENGINE] Compositor failed. No reaction video produced.")
        return None


def _render_reactor_tts(text: str, output_dir: str) -> Optional[str]:
    """
    Render a short TTS audio clip for the reactor's spoken line.

    Uses the existing VoiceoverGenerator from Audio_Modules.
    Reads REACTION_TTS_VOICE from .env (defaults to a neural voice).

    Returns:
        Path to .wav file, or None if TTS is unavailable.
    """
    if not text.strip():
        return None

    try:
        from Audio_Modules.voiceover import VoiceoverGenerator

        # VoiceoverGenerator uses gTTS/EdgeTTS and produces .mp3 output
        generator = VoiceoverGenerator()

        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, "reactor_tts.mp3")

        result = generator.generate_voiceover(
            script_text=text,
            output_file=out_path,
        )

        if result and os.path.isfile(out_path):
            logger.info(f"[REACTION_ENGINE] TTS audio ready: {os.path.basename(out_path)}")
            return out_path
        else:
            logger.warning("[REACTION_ENGINE] TTS ran but output file missing or empty.")

    except ImportError:
        logger.warning("[REACTION_ENGINE] VoiceoverGenerator not available for TTS.")
    except Exception as e:
        logger.warning(f"[REACTION_ENGINE] TTS render failed (non-fatal): {e}")

    return None
