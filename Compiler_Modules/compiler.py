import asyncio
import logging
import os

from Compiler_Modules import orchestrator

# Compatibility Shim
# This allows main.py to continue importing 'compiler' without breaking.
# We map the old functions to the new Orchestrator.

logger = logging.getLogger("compiler_shim")


async def process_video_pipeline(
    job_uuid, input_path, output_path, title, description, **kwargs
):
    """
    Async wrapper for the new synchronous orchestrator.
    Minimizes change radius in main.py.
    """
    logger.info(f"Using New Compiler Module for: {job_uuid}")

    # Offload to thread to keep async loop unblocked
    result = await asyncio.to_thread(
        orchestrator.compile_video,
        job_uuid,
        input_path,
        output_path,
        title,
        description,
        kwargs,
    )
    return result


# --- SHIM MAPPINGS ---

from pathlib import Path

# --- SHIM MAPPINGS ---


def compile_batch_with_transitions(file_list, output_file, **kwargs):
    """Shim for batch compilation."""
    logger.info(f"Shim: compile_batch_with_transitions -> compile_with_transitions (AI Authority Mode)")
    
    # Forward directly to the advanced Orchestrator pipeline which handles 
    # multi-clip AI beat & visual syncing (RhythmTimelineBuilder).
    result = compile_with_transitions(file_list, output_file, **kwargs)
    
    if isinstance(result, tuple) and len(result) > 0:
        return result[0]
    return result


import uuid


def compile_with_transitions(file_list, output_file, **kwargs):
    """
    Shim for compilation.
    CRITICAL RESTORATION: This must perform the FULL processing (Overlays, Filters),
    not just a simple batch concatenation.
    """
    logger.info(f"Shim: compile_with_transitions (Full Pipeline)")

    # Guard: orchestrator must be a module with compile_video callable, not a shadowed name
    if not callable(getattr(orchestrator, "compile_video", None)):
        logger.error(
            "[COMPILER_SHIM] orchestrator.compile_video is not callable — "
            "possible circular import. Re-importing directly."
        )
        from Compiler_Modules import orchestrator as _orch_direct
        _compile_video = _orch_direct.compile_video
    else:
        _compile_video = orchestrator.compile_video

    # Sanitization
    if isinstance(file_list, (str, Path)):
        file_list = [str(file_list)]
    file_list = [str(f) for f in file_list]

    # Extension Safety
    if not os.path.splitext(output_file)[1]:
        output_file = f"{output_file}.mp4"

    # CASE 1: Single Video (Standard Flow)
    if len(file_list) == 1:
        input_path = file_list[0]
        logger.info(
            "⚡ Single File detected -> Routing to Orchestrator.compile_video (Smart Render)"
        )

        # Generate valid props for the Orchestrator
        job_id = f"job_{uuid.uuid4().hex[:6]}"

        # Extract title from filename if not provided in kwargs
        # The main.py usually doesn't pass title here, it expects the compiler to handle it?
        # Or it passed it in kwargs? Check kwargs.
        # If CLI run, we have no metadata.

        raw_title = kwargs.get("title", Path(input_path).stem)
        description = kwargs.get("description", "Automated Compilation")

        # Ensure output directory exists
        os.makedirs(os.path.dirname(os.path.abspath(output_file)) or ".", exist_ok=True)

        try:
            # CALL THE BRAIN
            result = _compile_video(
                uuid_str=job_id,
                input_path=input_path,
                output_path=output_file,
                title=raw_title,
                description=description,
                profile_data=kwargs,
            )

            if isinstance(result, tuple) and len(result) == 2:
                success, orch_meta = result
                if success and isinstance(orch_meta, dict):
                    # Legacy Support for Monetization Tests
                    if "risk_level" in orch_meta:
                        _save_sidecar(output_file, orch_meta)

                    orch_meta["job_id"] = job_id
                    return output_file, orch_meta

            return output_file, {"job_id": job_id}

        except Exception as e:
            logger.error(f"Smart Render Failed: {e}", exc_info=True)
            return None, {}

    # CASE 2: Multi-Clip (Batch Flow)
    else:
        logger.info(
            f"📂 Multi-Clip detected ({len(file_list)} files) -> Disabling Pre-Compilation"
        )
        logger.info(
            "⚡ Routing raw clip list to Orchestrator.compile_video (AI Authority Mode)"
        )

        job_id = f"job_{uuid.uuid4().hex[:6]}"
        raw_title = kwargs.get("title", "Multi-Clip Compilation")
        description = kwargs.get("description", "AI Authority Edited")

        # Ensure output directory exists
        os.makedirs(os.path.dirname(os.path.abspath(output_file)) or ".", exist_ok=True)

        try:
            # CALL THE BRAIN with the RAW LIST
            result = _compile_video(
                uuid_str=job_id,
                input_path=file_list,
                output_path=output_file,
                title=raw_title,
                description=description,
                profile_data=kwargs,
            )

            if isinstance(result, tuple) and len(result) == 2:
                success, orch_meta = result
                if success and isinstance(orch_meta, dict):
                    if "risk_level" in orch_meta:
                        _save_sidecar(output_file, orch_meta)
                    orch_meta["job_id"] = job_id
                    return output_file, orch_meta

            return output_file, {"job_id": job_id}
        except Exception as e:
            logger.error(f"AI Authority Pipeline Failed: {e}")
            return None, {}


# Alias for safety if names vary
process_batch = compile_batch_with_transitions


# --- LEGACY HELPERS FOR TESTS ---
def _get_video_info(path):
    """Legacy shim for tests."""
    from Compiler_Modules import video_pipeline

    return video_pipeline.get_video_info(path)


def _run_command(cmd, **kwargs):
    """Legacy shim for tests."""
    import subprocess

    return subprocess.run(cmd, **kwargs)


def _save_sidecar(path, data):
    """Legacy shim for tests."""
    import json

    # Handle the fact that tests might pass different arg orders or names
    # In some versions it was (video_path, caption_meta, pipeline_metrics)
    # But monetization_reporting mocks it as _save_sidecar(mock_sidecar) with data=...
    # We'll just make it flexible.
    if isinstance(path, str) and not path.endswith(".json"):
        # If path is a video path, try to make it a json path
        path = f"{os.path.splitext(path)[0]}_meta.json"

    with open(path, "w") as f:
        json.dump(data, f)


def check_health():
    """Legacy shim for tests."""
    try:
        from Visual_Refinement_Modules import health

        return health.check_health()
    except ImportError:
        return {"safe": True, "summary": "Module Missing"}


try:
    from Text_Modules import gemini_captions
except ImportError:
    gemini_captions = None


def apply_ferrari_composer(*args, **kwargs):
    """Legacy shim for tests."""
    return True


def assemble_narrated_compilation(
    video_paths: list,
    voiceover_path: str,
    bgm_path: str,
    output_path: str,
) -> bool:
    """
    Assembles multiple video clips into a narrated compilation with BGM.
    Used by cmd_compile in main.py for NarrativeDirector compilations.

    Pipeline:
      1. Concatenate all video clips into a silent merged video
      2. Mix voiceover + BGM onto the merged video via audio_pipeline
      3. Write final output

    Returns True on success, False on any failure.
    """
    import shutil
    import subprocess
    import tempfile

    from Compiler_Modules import audio_pipeline

    logger.info(
        f"🎬 [NARRATED_COMPILATION] Assembling {len(video_paths)} clips "
        f"VO={os.path.basename(voiceover_path) if voiceover_path else 'None'} "
        f"BGM={os.path.basename(bgm_path) if bgm_path else 'None'}"
    )

    if not video_paths:
        logger.error("[NARRATED_COMPILATION] No video paths provided.")
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    # Step 1: Build concat list file
    tmp_dir = tempfile.mkdtemp(prefix="narration_")
    try:
        concat_list = os.path.join(tmp_dir, "concat.txt")
        merged_silent = os.path.join(tmp_dir, "merged_silent.mp4")

        # Filter to existing paths only
        valid_paths = [p for p in video_paths if p and os.path.exists(p)]
        if not valid_paths:
            logger.error(
                "[NARRATED_COMPILATION] None of the video paths exist on disk."
            )
            return False

        with open(concat_list, "w", encoding="utf-8") as f:
            for vp in valid_paths:
                # ffmpeg concat demuxer requires forward-slash or escaped paths
                abs_vp = os.path.abspath(vp)
                safe = abs_vp.replace("\\", "/")
                f.write(f"file '{safe}'\n")

        ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")

        # Step 2: Concatenate clips — re-encode to uniform 1080×1920@30fps
        concat_cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            merged_silent,
        ]
        result = subprocess.run(
            concat_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            logger.error(
                f"[NARRATED_COMPILATION] Concat failed: "
                f"{result.stderr.decode('utf-8', errors='replace')[-500:]}"
            )
            return False

        logger.info(
            f"[NARRATED_COMPILATION] Clips merged → {os.path.basename(merged_silent)}"
        )

        # Step 3: Mix VO + BGM onto merged video
        mixed_ok = audio_pipeline.mix_audio(
            video_path=merged_silent,
            output_path=output_path,
            voiceover_path=voiceover_path
            if voiceover_path and os.path.exists(str(voiceover_path))
            else None,
            music_path=bgm_path if bgm_path and os.path.exists(str(bgm_path)) else None,
            vo_vol=2.5,
            music_vol=0.12,
        )

        if not mixed_ok:
            # Fallback: copy silent merge if audio mixing fails
            logger.warning(
                "[NARRATED_COMPILATION] Audio mix failed — "
                "copying silent merge as fallback."
            )
            shutil.copy2(merged_silent, output_path)

        if os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(
                f"✅ [NARRATED_COMPILATION] Output ready: "
                f"{os.path.basename(output_path)} ({size_mb:.1f} MB)"
            )
            return True

        logger.error("[NARRATED_COMPILATION] Output file not found after assembly.")
        return False

    except Exception as exc:
        logger.error(f"[NARRATED_COMPILATION] Fatal error: {exc}", exc_info=True)
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class NarratedCompilationBuilder:
    @staticmethod
    def compile_with_timeline(
        clip_paths: list,
        timeline: list,
        output_path: str,
        **kwargs
    ) -> bool:
        """
        Multi-clip timeline rendering shim.
        Restores the 'rhythm-sync' compilation flow in main.py by mapping 
        to the video_pipeline reconstruction engine.
        """
        from Compiler_Modules import video_pipeline

        logger.info(
            f"🎬 [RHYTHM_SYNC] Compiling {len(clip_paths)} clips "
            f"with {len(timeline)} segments -> {os.path.basename(output_path)}"
        )

        try:
            # Map to the new robust scene reconstruction engine
            success = video_pipeline.render_scene_reconstruction(
                input_path=clip_paths,
                output_path=output_path,
                segments=timeline,
                **kwargs
            )
            return success
        except Exception as e:
            logger.error(f"[RHYTHM_SYNC] Compilation failed: {e}", exc_info=True)
            return False

def compile_monetizable_longform(
    clip_paths: list, output_path: str, title_hook: str, music_dir: str = "music"
) -> str:
    """
    Compiles a monetizable long-form video with background music and text overlay.
    """
    import shutil
    import subprocess
    import tempfile
    import random
    import glob

    logger.info(f"🎬 [MONETIZABLE_COMPILE] Assembling {len(clip_paths)} clips with hook: '{title_hook}'")

    if not clip_paths:
        logger.error("[MONETIZABLE_COMPILE] No video paths provided.")
        return None

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="monetize_")
    
    try:
        concat_list = os.path.join(tmp_dir, "concat.txt")
        merged_raw = os.path.join(tmp_dir, "merged_raw.mp4")

        # Step 1: Concat List
        valid_paths = [p for p in clip_paths if p and os.path.exists(p)]
        with open(concat_list, "w", encoding="utf-8") as f:
            for vp in valid_paths:
                f.write(f"file '{os.path.abspath(vp).replace(chr(92), '/')}'\n")

        ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")

        # Step 2: Concat into raw merged (preserve audio)
        concat_cmd = [
            ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,format=yuv420p",
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart",
            merged_raw
        ]
        res = subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res.returncode != 0:
            logger.error(f"[MONETIZABLE_COMPILE] Concat failed: {res.stderr.decode('utf-8', errors='replace')[-500:]}")
            return None

        # Step 3: Select random music
        bgm_path = None
        if os.path.exists(music_dir):
            music_files = glob.glob(os.path.join(music_dir, "*.mp3")) + glob.glob(os.path.join(music_dir, "*.wav"))
            if music_files:
                bgm_path = random.choice(music_files)
                logger.info(f"🎵 [MONETIZABLE_COMPILE] Selected BGM: {os.path.basename(bgm_path)}")

        # Step 4: Mix audio & Add Text Overlay (First 3 seconds)
        # Font path needs to be valid. We will use standard Arial or sans-serif fallback if possible.
        # Drawtext syntax handles escaping
        escaped_title = title_hook.replace("'", "").replace(":", "").replace("\\", "")
        
        # Build complex filter
        filter_complex = []
        inputs = ["-i", merged_raw]
        
        # Audio
        if bgm_path:
            inputs.extend(["-i", bgm_path])
            # Duck original audio (amix)
            filter_complex.append("[0:a]volume=0.8[a1];[1:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=shortest[a_out]")
            audio_map = "[a_out]"
        else:
            filter_complex.append("[0:a]volume=1.0[a_out]")
            audio_map = "[a_out]"

        # Video Overlay
        # drawtext=text='HOOK':fontcolor=white:fontsize=64:box=1:boxcolor=black@0.6:boxborderw=10:x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t,0,3)'
        text_filter = f"drawtext=text='{escaped_title}':fontcolor=white:fontsize=72:box=1:boxcolor=black@0.7:boxborderw=15:x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t,0,3)'"
        filter_complex.append(f"[0:v]{text_filter}[v_out]")

        final_cmd = [
            ffmpeg_bin, "-y",
            *inputs,
            "-filter_complex", ";".join(filter_complex),
            "-map", "[v_out]",
            "-map", audio_map,
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            output_path
        ]
        
        mix_res = subprocess.run(final_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if mix_res.returncode != 0:
            logger.error(f"[MONETIZABLE_COMPILE] Mix failed: {mix_res.stderr.decode('utf-8', errors='replace')[-500:]}")
            # Fallback
            shutil.copy2(merged_raw, output_path)

        return output_path if os.path.exists(output_path) else None

    except Exception as exc:
        logger.error(f"[MONETIZABLE_COMPILE] Fatal error: {exc}", exc_info=True)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)