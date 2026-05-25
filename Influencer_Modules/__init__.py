"""
Influencer_Modules
==================
Open-source, 100% commercial-safe AI Influencer Engine for AMTCE.

Pipeline:
  Stage 1 : Identity Generator  — SDXL + IP-Adapter Plus Face (CLIP, Apache 2.0)
  Stage 2 : Frame Extractor     — ffmpeg scene detection (already in AMTCE)
  Stage 3 : Pose Syncer         — ControlNet OpenPose + IP-Adapter (Apache 2.0)
  Stage 4 : Motion Animator     — Wan2.1-1.3B I2V (Apache 2.0)

License:  All components are Apache 2.0 or CreativeML RAIL+M (commercial OK).
GPU Gate: Requires >6 GB VRAM — mirrors Upscale_Modules compute gate.

NO InsightFace / NO ArcFace / NO FaceID weights — zero non-commercial risk.
"""

__version__ = "1.0.0"
__all__ = [
    "compute_gate",
    "frame_extractor",
    "identity_generator",
    "pose_syncer",
    "motion_animator",
    "influencer_pipeline",
]
