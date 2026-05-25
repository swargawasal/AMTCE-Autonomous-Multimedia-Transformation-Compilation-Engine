import logging
import os
import random
import subprocess
from typing import Optional

logger = logging.getLogger("audio_pipeline")

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")


def mix_audio(
    video_path: str,
    output_path: str,
    voiceover_path: Optional[str] = None,
    music_path: Optional[str] = None,
    music_vol: float = 0.2,
    vo_vol: float = 1.5,
    duration: Optional[float] = None,
    music_offset: Optional[float] = 0.0,
) -> bool:
    """
    Mixes Voiceover + Background Music + Original Audio
    """
    inputs = []
    filter_complex = ""

    # 0. Video (Stream 0)
    inputs.extend(["-i", video_path])

    # 1. Voiceover (Stream 1)
    has_vo = False
    vo_delay = "750|750" # Default TTS delay

    if voiceover_path and os.path.exists(voiceover_path):
        inputs.extend(["-i", voiceover_path])
        has_vo = True

    # 2. Music (Stream 2)
    has_music = False
    if music_path and os.path.exists(music_path):
        try:
            from Audio_Modules.audio_processing import has_audio_stream
            if has_audio_stream(music_path):
                inputs.extend(["-i", music_path])
                has_music = True
            else:
                logger.warning(f"⚠️ Music track has no audio stream, ignoring: {music_path}")
        except Exception:
            inputs.extend(["-i", music_path])
            has_music = True

    # Map Logic
    # [0:a] volume=1.0 [orig]
    # [1:a] volume=vo_vol [vo]
    # [2:a] volume=music_vol, aloop=loop=-1:size=2e+09 [mus] (Loop music)

    if not has_vo and not has_music:
        # Just copy
        return False  # No mixing needed

    steps = []

    # Define intermediate streams
    # [0:a] Original video audio (Lowered to background level)
    # [1:a] Voiceover (Narrator)
    # [2:a] Music (Background)

    # --- [mkpv-fix] REMIX BGM FOR FINGERPRINT BYPASS ---
    remixed_music_path = None
    if has_music and music_path:
        # Fix: Bypass remix for Original_audio since it alters tempo and destroys beat sync
        if "Original_audio" not in music_path.replace("\\", "/"):
            try:
                from Audio_Modules.audio_processing import heavy_remix

                remixed_music_path = os.path.join(
                    os.path.dirname(output_path),
                    f"remixed_bgm_{random.randint(1000, 9999)}.mp3",
                )
                # remix with lower volume to act as BGM
                if heavy_remix(music_path, remixed_music_path, original_volume=1.0):
                    music_path = remixed_music_path
                    logger.info(
                        f"🎛️ BGM Fingerprint Bypass: Heavy Remix Applied to {os.path.basename(music_path)}"
                    )
            except Exception as re:
                logger.warning(f"⚠️ Heavy Remix failed for BGM: {re}")
        else:
            logger.info("🎵 BGM is from Original_audio — bypassing heavy remix to preserve exact beat sync.")

    # 1. Prepare Original Audio
    # [a_orig_raw] might be missing if video has no audio. We use anullsrc if needed.
    try:
        from Audio_Modules.audio_processing import has_audio_stream
        has_orig_audio = has_audio_stream(video_path)
    except Exception:
        has_orig_audio = True  # Assume true if check fails

    if has_orig_audio:
        # [USER REQUEST] Mute the original video audio completely.
        # The AI reshuffles shots, making the native audio glitchy/disordered.
        # The clean extracted Original_audio track is used as BGM instead.
        steps.append("[0:a]volume=0.0[a_orig]")
    else:
        # Generate silent background track to replace missing [0:a]
        logger.info(f"🔇 Video has no audio stream. Generating silent [a_orig] track.")
        steps.append("anullsrc=channel_layout=stereo:sample_rate=44100[a_orig]")


    if has_vo and has_music:
        # 2. Sidechain Compression (Ducking)
        steps.append(
            f"[1:a]volume={vo_vol},adelay={vo_delay},apad,asplit=2[a_vo_trig][a_vo_mix]"
        )
        
        mus_filters = []
        if music_offset and music_offset > 0:
            mus_filters.append(f"atrim=start={music_offset}")
            mus_filters.append("asetpts=PTS-STARTPTS")
        mus_filters.append(f"volume={music_vol}")
        
        steps.append(f"[2:a]{','.join(mus_filters)}[a_mus_pre]")

        # threshold=0.1, ratio=4.0, release=700
        steps.append(
            "[a_mus_pre][a_vo_trig]sidechaincompress=threshold=0.1:ratio=4:attack=20:release=700[a_mus_duck]"
        )
        # 3. Final Mix (Order: [a_orig] first to control duration)
        # [mkpv-fix] duration=first is critical to prevent video freeze at the end
        steps.append(
            "[a_orig][a_vo_mix][a_mus_duck]amix=inputs=3:duration=first:dropout_transition=0[a_mixed]"
        )
    elif has_vo:
        steps.append(f"[1:a]volume={vo_vol},adelay={vo_delay}[a_vo]")
        # [mkpv-fix] duration=first is critical to prevent video freeze at the end
        steps.append(
            "[a_orig][a_vo]amix=inputs=2:duration=first:dropout_transition=0[a_mixed]"
        )
    elif has_music:
        # Logic fix: If no VO, music is input [1:a]
        mus_filters = []
        if music_offset and music_offset > 0:
            mus_filters.append(f"atrim=start={music_offset}")
            mus_filters.append("asetpts=PTS-STARTPTS")
        mus_filters.append(f"volume={music_vol}")
        
        steps.append(f"[1:a]{','.join(mus_filters)}[a_mus]")
        # [mkpv-fix] duration=first is critical to prevent video freeze at the end
        steps.append(
            "[a_orig][a_mus]amix=inputs=2:duration=first:dropout_transition=0[a_mixed]"
        )
    else:
        return False

    # 4. Professional Loudness Normalization (EBU R128)
    steps.append("[a_mixed]loudnorm=I=-16:TP=-1.5:LRA=11[outa]")

    filter_complex = ";".join(steps)

    cmd = [
        FFMPEG_BIN,
        "-y",
    ]

    # Construct Inputs
    cmd.extend(["-i", video_path])
    if has_vo and voiceover_path:
        cmd.extend(["-i", voiceover_path])
    if has_music and music_path:
        cmd.extend(["-stream_loop", "-1", "-i", music_path])

    if duration:
        cmd.extend(["-t", str(duration)])

    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[outa]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            output_path,
        ]
    )

    try:
        subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        # Cleanup remixed BGM if used
        if remixed_music_path and os.path.exists(remixed_music_path):
            try:
                os.remove(remixed_music_path)
            except:
                pass
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Audio Mix Failed: {e.stderr.decode()}")
        # Cleanup remixed BGM if used
        if remixed_music_path and os.path.exists(remixed_music_path):
            try:
                os.remove(remixed_music_path)
            except:
                pass
        return False
