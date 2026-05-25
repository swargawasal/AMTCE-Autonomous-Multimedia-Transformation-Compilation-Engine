"""
Reaction_Engine/lip_sync_engine.py
------------------------------------
Optional lip-sync applicator for reactor clips.

The TTS narration audio is the MASTER signal. The reactor's lips move
to match the narration — like a news anchor. Original reactor clip audio
is ALWAYS stripped (visual only input).

Two modes:
  - sync()         → single-pass, for short clips (≤20s)
  - sync_chunked() → chunked pass, for long assembled reels (>20s)

.env flags:
    ENABLE_LIP_SYNC=yes|no
    WAV2LIP_DIR=path/to/Wav2Lip
    WAV2LIP_CHECKPOINT=path/to/wav2lip_gan.pth
    LIP_SYNC_CHUNK_SECONDS=20     (max seconds per chunk, default 20)
"""

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger("lip_sync_engine")

FFMPEG_BIN  = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")

_CHUNK_SECONDS = float(os.getenv("LIP_SYNC_CHUNK_SECONDS", "20.0"))


def _enabled() -> bool:
    return os.getenv("ENABLE_LIP_SYNC", "no").lower() in ("yes", "true", "1")


def _wav2lip_dir() -> Optional[str]:
    d = os.getenv("WAV2LIP_DIR", "")
    return d if d and os.path.isdir(d) else None


def _wav2lip_checkpoint() -> Optional[str]:
    cp = os.getenv("WAV2LIP_CHECKPOINT", "")
    if cp and os.path.isfile(cp):
        return cp
    wav2lip_d = _wav2lip_dir()
    if wav2lip_d:
        default = os.path.join(wav2lip_d, "checkpoints", "wav2lip_gan.pth")
        if os.path.isfile(default):
            return default
    return None


def _get_duration(path: str) -> float:
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


class LipSyncEngine:

    def __init__(self):
        self._enabled      = _enabled()
        self._wav2lip_dir  = _wav2lip_dir()
        self._checkpoint   = _wav2lip_checkpoint()

        if self._enabled and self._wav2lip_dir and self._checkpoint:
            logger.info(
                f"[LIP_SYNC] Wav2Lip available. "
                f"Dir={self._wav2lip_dir} | Checkpoint={os.path.basename(self._checkpoint)}"
            )
        elif self._enabled:
            logger.warning(
                "[LIP_SYNC] ENABLE_LIP_SYNC=yes but Wav2Lip not configured. "
                "Set WAV2LIP_DIR and WAV2LIP_CHECKPOINT in .env. Falling back."
            )
        else:
            logger.info("[LIP_SYNC] Disabled via ENABLE_LIP_SYNC=no.")

    def is_available(self) -> bool:
        return bool(self._enabled and self._wav2lip_dir and self._checkpoint)

    # ── Single-pass sync ───────────────────────────────────────────────────────

    def sync(
        self,
        reactor_clip_path: str,
        audio_path: str,
        output_dir: str,
        tag: str = "synced",
    ) -> str:
        """
        Lip-sync a short clip (≤20s). TTS audio is the master.
        Falls back gracefully to the original clip on any failure.
        """
        if not self.is_available():
            return reactor_clip_path
        if not os.path.isfile(reactor_clip_path):
            logger.warning(f"[LIP_SYNC] reactor_clip not found: {reactor_clip_path}")
            return reactor_clip_path
        if not os.path.isfile(audio_path):
            logger.warning(f"[LIP_SYNC] audio_path not found: {audio_path}")
            return reactor_clip_path

        os.makedirs(output_dir, exist_ok=True)
        base    = os.path.splitext(os.path.basename(reactor_clip_path))[0]
        outfile = os.path.join(output_dir, f"{base}_{tag}_lipsync.mp4")
        return self._run_wav2lip(reactor_clip_path, audio_path, outfile, 600, reactor_clip_path)

    # ── Chunked sync for long reels ────────────────────────────────────────────

    def sync_chunked(
        self,
        reactor_reel_path: str,
        narration_audio_path: str,
        output_dir: str,
        tag: str = "reel_synced",
    ) -> str:
        """
        Lip-sync a long assembled reactor reel using chunked processing.

        The narration_audio_path is the MASTER — its audio drives all lip
        movements (news-anchor style). The reel is split into 20s chunks,
        each chunk is synced to its corresponding audio segment, then
        all chunks are concatenated back into one reel.

        Falls back gracefully to reactor_reel_path on any failure.
        """
        if not self.is_available():
            return reactor_reel_path
        if not os.path.isfile(reactor_reel_path):
            logger.warning(f"[LIP_SYNC] Reel not found: {reactor_reel_path}")
            return reactor_reel_path
        if not os.path.isfile(narration_audio_path):
            logger.warning(f"[LIP_SYNC] Narration audio not found: {narration_audio_path}")
            return reactor_reel_path

        reel_dur  = _get_duration(reactor_reel_path)
        audio_dur = _get_duration(narration_audio_path)
        use_dur   = min(reel_dur, audio_dur) if audio_dur > 0 else reel_dur

        if use_dur <= 0:
            logger.warning("[LIP_SYNC] Could not determine reel duration.")
            return reactor_reel_path

        # Short enough for single pass?
        if use_dur <= _CHUNK_SECONDS:
            logger.info(f"[LIP_SYNC] Reel is short ({use_dur:.1f}s) — single pass.")
            return self.sync(reactor_reel_path, narration_audio_path, output_dir, tag)

        logger.info(
            f"[LIP_SYNC] Chunked sync: reel={reel_dur:.1f}s | "
            f"audio={audio_dur:.1f}s | chunk={_CHUNK_SECONDS}s"
        )

        chunks_dir    = os.path.join(output_dir, "lipsync_chunks")
        os.makedirs(chunks_dir, exist_ok=True)
        synced_chunks = []
        cursor        = 0.0
        chunk_idx     = 0

        while cursor < use_dur:
            chunk_dur = min(_CHUNK_SECONDS, use_dur - cursor)
            if chunk_dur < 0.5:
                break

            vchunk = os.path.join(chunks_dir, f"vchunk_{chunk_idx:03d}.mp4")
            achunk = os.path.join(chunks_dir, f"achunk_{chunk_idx:03d}.mp3")
            ochunk = os.path.join(chunks_dir, f"synced_{chunk_idx:03d}.mp4")

            v_ok = self._cut_video(reactor_reel_path, cursor, chunk_dur, vchunk)
            a_ok = self._cut_audio(narration_audio_path, cursor, chunk_dur, achunk)

            if v_ok and a_ok:
                result = self._run_wav2lip(vchunk, achunk, ochunk, 300, vchunk)
                synced_chunks.append(result)
                logger.info(
                    f"[LIP_SYNC] Chunk {chunk_idx}: "
                    f"{cursor:.1f}s–{cursor+chunk_dur:.1f}s → {os.path.basename(result)}"
                )
            else:
                logger.warning(f"[LIP_SYNC] Chunk {chunk_idx} extraction failed — skipping.")

            cursor    += chunk_dur
            chunk_idx += 1

        if not synced_chunks:
            logger.error("[LIP_SYNC] All chunks failed. Returning original reel.")
            return reactor_reel_path

        out_reel  = os.path.join(output_dir, f"assembled_{tag}_lipsync.mp4")
        concat_ok = self._concat(synced_chunks, out_reel)

        if concat_ok and os.path.isfile(out_reel):
            size_mb = os.path.getsize(out_reel) / 1024 / 1024
            logger.info(
                f"✅ [LIP_SYNC] Chunked sync complete: "
                f"{os.path.basename(out_reel)} ({size_mb:.1f}MB, {chunk_idx} chunks)"
            )
            return out_reel

        logger.error("[LIP_SYNC] Chunk concat failed. Returning original reel.")
        return reactor_reel_path

    # ── Private helpers ────────────────────────────────────────────────────────

    def _run_wav2lip(self, video: str, audio: str, outfile: str, timeout: int, fallback: str) -> str:
        import sys
        inference_script = os.path.join(self._wav2lip_dir, "inference.py")
        if not os.path.isfile(inference_script):
            logger.warning(f"[LIP_SYNC] inference.py not found.")
            return fallback

        cmd = [
            sys.executable, inference_script,
            "--checkpoint_path", os.path.abspath(self._checkpoint),
            "--face",    os.path.abspath(video),
            "--audio",   os.path.abspath(audio),
            "--outfile", os.path.abspath(outfile),
            "--resize_factor", "1",
            "--nosmooth",
            "--wav2lip_batch_size", os.getenv("WAV2LIP_BATCH_SIZE", "16"),
            "--face_det_batch_size", os.getenv("FACE_DET_BATCH_SIZE", "2")
        ]
        logger.info(f"[LIP_SYNC] Running Wav2Lip on {os.path.basename(video)} + {os.path.basename(audio)}")
        try:
            r = subprocess.run(cmd, cwd=self._wav2lip_dir,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=timeout)
            if r.returncode == 0 and os.path.isfile(outfile):
                size_mb = os.path.getsize(outfile) / 1024 / 1024
                logger.info(f"✅ [LIP_SYNC] Synced: {os.path.basename(outfile)} ({size_mb:.1f}MB)")
                return outfile
            logger.warning(f"[LIP_SYNC] Wav2Lip failed (rc={r.returncode}). stderr={r.stderr[-300:]}")
            return fallback
        except subprocess.TimeoutExpired:
            logger.warning(f"[LIP_SYNC] Wav2Lip timed out (>{timeout}s).")
            return fallback
        except Exception as e:
            logger.warning(f"[LIP_SYNC] Error: {e}")
            return fallback

    def _cut_video(self, src: str, start: float, dur: float, out: str) -> bool:
        cmd = [FFMPEG_BIN, "-y", "-ss", str(start), "-t", str(dur), "-i", src,
               "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", out]
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, timeout=30)
            return r.returncode == 0 and os.path.isfile(out)
        except Exception:
            return False

    def _cut_audio(self, src: str, start: float, dur: float, out: str) -> bool:
        cmd = [FFMPEG_BIN, "-y", "-ss", str(start), "-t", str(dur), "-i", src,
               "-c:a", "libmp3lame", "-q:a", "2", out]
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, timeout=30)
            return r.returncode == 0 and os.path.isfile(out)
        except Exception:
            return False

    def _concat(self, chunks: list, out: str) -> bool:
        list_path = out.replace(".mp4", "_concat.txt")
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for c in chunks:
                    abs_c = os.path.abspath(c).replace(chr(92), '/')
                    f.write(f"file '{abs_c}'\n")
        except Exception as e:
            logger.error(f"[LIP_SYNC] Failed to write concat list: {e}")
            return False
        cmd = [FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
               "-c:a", "aac", "-b:a", "192k", out]
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, timeout=300)
            return r.returncode == 0 and os.path.isfile(out)
        except Exception as e:
            logger.error(f"[LIP_SYNC] Concat exception: {e}")
            return False
