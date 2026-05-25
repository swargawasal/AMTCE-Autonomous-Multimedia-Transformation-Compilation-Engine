# Influencer_Modules 🎬

> **100% Open-Source | 100% Commercial-Safe | Zero Platform Limits**

Replaces Luvart AI + Kie.ai Kling 2.6 with a fully local, Apache-2.0-only pipeline for creating AI influencer videos.

---

## Pipeline Overview

```
[Reference Face Image]          ← Pinterest download (yt-dlp, already in AMTCE)
        │
        ▼ Stage 1 (SDXL + IP-Adapter Plus Face)
[Generated Influencer Portrait]  ← 896×1152 px, 3:4 Instagram ratio

[Reference Reel / Video]         ← Pinterest download (yt-dlp, already in AMTCE)
        │
        ▼ Stage 2 (ffmpeg scene detection, already in AMTCE)
[Best Reference Frame]           ← Body pose + outfit to replicate

        ▼ Stage 3 (ControlNet OpenPose + IP-Adapter)
[Synced Image]                   ← Influencer face on reference body/pose/outfit
        │
        ▼ Stage 4 (Wan2.1-1.3B I2V)
[🎬 Final Animated MP4]          ← Instagram Reels ready (480×832, 9:16)
```

---

## License Safety

| Component | License | Commercial? |
|---|---|---|
| SDXL Base 1.0 | CreativeML RAIL+M | ✅ Yes |
| IP-Adapter Plus Face (`vit-h`) | Apache 2.0 | ✅ Yes |
| ControlNet OpenPose SDXL | Apache 2.0 | ✅ Yes |
| controlnet-aux / DWPose | Apache 2.0 | ✅ Yes |
| MediaPipe | Apache 2.0 | ✅ Yes |
| Wan2.1-1.3B I2V | Apache 2.0 | ✅ Yes |

**Explicitly NOT used (non-commercial):**
- ❌ InsightFace / ArcFace
- ❌ ip-adapter-faceid* weights
- ❌ FLUX.1 [dev]
- ❌ InstantID weights
- ❌ FaceFusion

---

## GPU Requirements

| Colab Tier | GPU | VRAM | Models Used |
|---|---|---|---|
| **Free** | T4 | 16 GB | Wan2.1-1.3B + SDXL FP16 offload ✅ |
| **Pro** | L4 | 24 GB | Wan2.1-1.3B + SDXL full ✅ |
| **Pro+** | A100 | 40 GB | Wan2.1-14B + SDXL full quality ✅✅ |

Minimum: **6 GB VRAM** (enforced by AMTCE `ComputeCaps` gate).

---

## Quick Start (Colab)

1. Upload AMTCE folder to Google Drive.
2. Open **`colab/Influencer_Colab.ipynb`** in Colab.
3. Runtime → Change runtime type → **T4 GPU**.
4. Run all cells, upload your face image + reference video when prompted.
5. Download the final MP4.

---

## Programmatic Usage

```python
from Influencer_Modules.influencer_pipeline import run

result = run(
    reference_face_path  = "downloads/face_ref.jpg",
    reference_video_path = "downloads/pinterest_reel.mp4",
    prompt = "realistic indian instagram influencer, ethnic silk saree, outdoor",
    output_dir = "Influencer_Output",
)

print(result["output_video"])   # path to final animated MP4
```

---

## AMTCE Integration (main.py)

Add to `.env`:
```
ENABLE_INFLUENCER_ENGINE=yes
INFLUENCER_REFERENCE_FACE=downloads/face_ref.jpg
INFLUENCER_REFERENCE_VIDEO=downloads/pinterest_reel.mp4
INFLUENCER_PROMPT=realistic indian instagram influencer, ethnic wear
```

The pipeline will **not run** on CPU-only machines — the existing `ComputeCaps` gate enforces this automatically, keeping the main compilation pipeline unaffected.
