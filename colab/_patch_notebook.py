"""
Patches Influencer_Colab.ipynb:
  1. Cell 4  â€” Add Gemini/OpenCV outfit auto-detection from reference frame
  2. Cell 8  â€” Remove both animation cells + their markdown header
  3. Cell 9  â€” Remove video section, add Kling/Pika upload instructions
  4. Cell 10 â€” Change to download the final SYNCED IMAGE (not video)
  5. Header  â€” Update title table to reflect 3-stage pipeline
"""
import json, os, sys

NB_PATH = os.path.join(os.path.dirname(__file__), "Influencer_Colab.ipynb")

with open(NB_PATH, encoding="utf-8") as f:
    nb = json.load(f)

cells = nb["cells"]

# â”€â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def src(lines):
    """Convert list of plain strings into notebook source format."""
    out = []
    for i, ln in enumerate(lines):
        out.append(ln + "\n" if i < len(lines) - 1 else ln)
    return out

def code_cell(lines):
    return {"cell_type": "code", "execution_count": None,
            "metadata": {}, "outputs": [], "source": src(lines)}

def md_cell(lines):
    return {"cell_type": "markdown", "metadata": {},
            "source": src(lines)}

def find_cell(keyword):
    """Return index of first cell whose source contains keyword."""
    for i, c in enumerate(cells):
        text = "".join(c.get("source", []))
        if keyword in text:
            return i
    return -1

# â”€â”€â”€ 1. Update header markdown â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
hdr_idx = find_cell("| Stage | Model |")
if hdr_idx >= 0:
    cells[hdr_idx]["source"] = src([
        "# ðŸŽ¬ AI Influencer Engine",
        "**Self-Contained Colab | Apache 2.0 | Commercial Safe**",
        "",
        "Run cells 1â†’7 to produce your AI influencer image,",
        "then upload `stage3_synced.jpg` to **Kling AI / Pika** for animation.",
        "",
        "---",
        "",
        "## How It Works",
        "| Stage | Model | What Happens | Time on T4 |",
        "|-------|-------|-------------|------------|",
        "| **1** | Realistic Vision V5.1 + IP-Adapter | Generates photorealistic portrait matching your reference face | ~3 min |",
        "| **2** | ffmpeg | Extracts best pose frame from your reference reel | ~5 sec |",
        "| **3** | SD 1.5 ControlNet OpenPose + IP-Adapter | Places influencer face on reference body pose | ~3 min |",
        "",
        "**Total: ~6-8 minutes | Output: `stage3_synced.jpg` â€” upload to Kling AI for animation**",
        "",
        "---",
        "",
        "## License â€” All Commercial Safe",
        "| Model | License |",
        "|-------|---------|",
        "| Realistic Vision V5.1 | CreativeML OpenRAIL+M (commercial OK) |",
        "| IP-Adapter | Apache 2.0 |",
        "| ControlNet OpenPose | Apache 2.0 |",
        "",
        "No InsightFace. No ArcFace. No FaceID. No FLUX dev. No InstantID.",
        "",
        "---",
        "",
        "> **BEFORE YOU START:**",
        "> 1. Runtime â†’ Change runtime type â†’ **T4 GPU** â†’ Save",
        "> 2. (Optional) Add `GEMINI_API_KEY` in Colab Secrets for auto outfit detection",
        "> 3. Run cells top to bottom, one at a time",
    ])
    print(f"[âœ“] Header updated (cell {hdr_idx})")

# â”€â”€â”€ 2. Replace Cell 4 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
cfg_idx = find_cell("INFLUENCER_CONCEPT")
if cfg_idx >= 0:
    cells[cfg_idx]["source"] = src([
        "import os, subprocess",
        "",
        "# ================================================================",
        "# STEP 1: Scene / mood description (outfit is auto-detected below)",
        "#   Edit this to match the BACKGROUND and MOOD you want.",
        "# ================================================================",
        "STYLE_SCENE = (",
        "    'rooftop with city skyline at golden hour, confident and graceful'",
        ")",
        "",
        "# ================================================================",
        "# OUTPUT DIRECTORY",
        "# ================================================================",
        "OUTPUT_DIR = '/content/drive/MyDrive/AMTCE/Influencer_Output'",
        "# OUTPUT_DIR = '/content/Influencer_Output'  # use this if Drive fails",
        "",
        "# TECHNICAL SETTINGS",
        "IP_ADAPTER_SCALE = 0.65   # 0.5 = subtle identity, 0.8 = strong identity",
        "CONTROLNET_SCALE = 0.85   # 0.7 = loose pose, 1.0 = strict pose match",
        "SEED             = 42     # change for variation",
        "",
        "REFERENCE_FACE  = '/content/input_face.jpg'",
        "REFERENCE_VIDEO = '/content/input_video.mp4'",
        "FRAME_PATH      = '/content/stage2_ref_frame.jpg'",
        "",
        "# Mount Drive",
        "if OUTPUT_DIR.startswith('/content/drive'):",
        "    try:",
        "        from google.colab import drive",
        "        drive.mount('/content/drive', force_remount=False)",
        "        print('Google Drive mounted.')",
        "    except Exception as e:",
        "        print(f'Drive mount failed: {e}')",
        "        OUTPUT_DIR = '/content/Influencer_Output'",
        "os.makedirs(OUTPUT_DIR, exist_ok=True)",
        "",
        "# ================================================================",
        "# STEP 2: Extract reference frame EARLY â€” we need it for outfit detection",
        "# ================================================================",
        "if os.path.exists(REFERENCE_VIDEO):",
        "    print('Extracting reference frame for outfit detection...')",
        "    r = subprocess.run([",
        "        'ffmpeg', '-y', '-i', REFERENCE_VIDEO,",
        "        '-vf', 'thumbnail=300,scale=512:-1',",
        "        '-frames:v', '1', '-q:v', '2', FRAME_PATH",
        "    ], capture_output=True)",
        "    exists = os.path.exists(FRAME_PATH)",
        "    print(f'  Frame extracted: {FRAME_PATH}' if exists else '  Frame extraction failed')",
        "else:",
        "    print(f'No reference video at {REFERENCE_VIDEO} â€” run Cell 3 first.')",
        "",
        "# ================================================================",
        "# STEP 3: Auto-detect outfit colour + style from the frame",
        "#   Priority: Gemini Vision (accurate) â†’ OpenCV colour (fallback)",
        "# ================================================================",
        "outfit_desc = 'silk saree with traditional jewellery'  # default",
        "",
        "if os.path.exists(FRAME_PATH):",
        "    # Try Gemini Vision",
        "    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')",
        "    if not GEMINI_API_KEY:",
        "        try:",
        "            from google.colab import userdata",
        "            GEMINI_API_KEY = userdata.get('GEMINI_API_KEY') or ''",
        "        except: pass",
        "",
        "    if GEMINI_API_KEY:",
        "        try:",
        "            import google.generativeai as genai",
        "            from PIL import Image as _PIL",
        "            genai.configure(api_key=GEMINI_API_KEY)",
        "            model_g = genai.GenerativeModel('gemini-1.5-flash')",
        "            frame_pil = _PIL.open(FRAME_PATH).convert('RGB')",
        "            response = model_g.generate_content([",
        "                frame_pil,",
        "                'Describe the outfit worn by the person for a Stable Diffusion prompt. '",
        "                'Include: exact garment type (saree/lehenga/kurta/etc), precise colour, '",
        "                'fabric, pattern, embroidery, jewellery. Under 35 words. '",
        "                'Example: vibrant peacock blue Kanjeevaram silk saree with gold zari border, '",
        "                'temple jewellery, floral embroidery blouse'",
        "            ])",
        "            outfit_desc = response.text.strip().replace('\\n', ' ')",
        "            print(f'Gemini Vision outfit detected:')",
        "            print(f'  {outfit_desc}')",
        "        except Exception as e:",
        "            print(f'Gemini detection failed ({e}). Using OpenCV colour fallback...')",
        "    else:",
        "        print('No GEMINI_API_KEY â€” using OpenCV colour analysis (add key to Colab Secrets for better results)')",
        "",
        "    # OpenCV colour fallback (runs whenever Gemini is unavailable or fails)",
        "    if outfit_desc == 'silk saree with traditional jewellery':",
        "        try:",
        "            import cv2, numpy as np",
        "            frame_bgr = cv2.imread(FRAME_PATH)",
        "            # Sample the centre 50% of the image (where the outfit usually is)",
        "            h, w = frame_bgr.shape[:2]",
        "            roi = frame_bgr[h//4:3*h//4, w//4:3*w//4]",
        "            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)",
        "            h_mean = np.mean(hsv[:,:,0])",
        "            s_mean = np.mean(hsv[:,:,1])",
        "            if s_mean < 40:   colour = 'ivory white'",
        "            elif h_mean < 15 or h_mean > 165: colour = 'deep red'",
        "            elif h_mean < 35: colour = 'golden yellow'",
        "            elif h_mean < 75: colour = 'vibrant green'",
        "            elif h_mean < 130: colour = 'royal blue'",
        "            elif h_mean < 155: colour = 'royal purple'",
        "            else: colour = 'rose pink'",
        "            outfit_desc = f'{colour} traditional Indian saree with matching jewellery'",
        "            print(f'OpenCV colour-detected outfit: {outfit_desc}')",
        "        except Exception as cv_e:",
        "            print(f'OpenCV fallback also failed: {cv_e}. Using default.')",
        "else:",
        "    print('No reference frame â€” using default outfit. Run Cell 3 to upload video.')",
        "",
        "# ================================================================",
        "# STEP 4: Build final INFLUENCER_CONCEPT",
        "# ================================================================",
        "INFLUENCER_CONCEPT = (",
        "    f'beautiful indian fashion influencer wearing {outfit_desc}, '",
        "    f'{STYLE_SCENE}'",
        ")",
        "",
        "print()",
        "print('=' * 60)",
        "print('CONFIGURATION SUMMARY')",
        "print('=' * 60)",
        "preview = INFLUENCER_CONCEPT[:100] + ('...' if len(INFLUENCER_CONCEPT) > 100 else '')",
        "print(f'  Outfit (auto) : {outfit_desc}')",
        "print(f'  Full concept  : {preview}')",
        "print(f'  Output dir    : {OUTPUT_DIR}')",
        "print(f'  IP scale      : {IP_ADAPTER_SCALE}')",
        "print(f'  ControlNet    : {CONTROLNET_SCALE}')",
        "print(f'  Seed          : {SEED}')",
        "print('=' * 60)",
        "print()",
        "print('Configuration ready. Run Cell 5 (Stage 1 - Portrait).')",
    ])
    print(f"[âœ“] Cell 4 updated with outfit auto-detection (cell {cfg_idx})")

# â”€â”€â”€ 3. Remove Cell 8 (markdown + both code versions) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Find the "Cell 8" markdown header and remove it + the two code cells after it
c8_md_idx = find_cell("Cell 8")
if c8_md_idx == -1:
    c8_md_idx = find_cell("Stage 4")
if c8_md_idx >= 0:
    # Check how many consecutive cells to remove: markdown + code cells until next markdown
    end_idx = c8_md_idx + 1
    while end_idx < len(cells) and cells[end_idx]["cell_type"] == "code":
        end_idx += 1
    removed = cells[c8_md_idx:end_idx]
    del cells[c8_md_idx:end_idx]
    print(f"[âœ“] Cell 8 removed ({end_idx - c8_md_idx} cells: 1 markdown + {end_idx - c8_md_idx - 1} code cells)")
else:
    print("[!] Cell 8 not found â€” may already be removed")

# â”€â”€â”€ 4. Replace Cell 9 (View Outputs) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
c9_idx = find_cell("ALL STAGE OUTPUTS")
if c9_idx == -1:
    c9_idx = find_cell("Cell 9")
if c9_idx >= 0 and cells[c9_idx]["cell_type"] == "code":
    cells[c9_idx]["source"] = src([
        "from IPython.display import display, Image as IPImage",
        "import os",
        "",
        "REFERENCE_FACE = '/content/input_face.jpg'",
        "PORTRAIT_PATH  = '/content/stage1_portrait.jpg'",
        "FRAME_PATH     = '/content/stage2_ref_frame.jpg'",
        "POSE_DBG_PATH  = '/content/stage3_pose_skeleton.jpg'",
        "SYNCED_PATH    = '/content/stage3_synced.jpg'",
        "",
        "print('=' * 60)",
        "print('ALL STAGE OUTPUTS')",
        "print('=' * 60)",
        "",
        "def show_img(path, title, width=280):",
        "    print(f'\\n{title}')",
        "    print('-' * len(title))",
        "    if os.path.exists(path):",
        "        kb = os.path.getsize(path) / 1024",
        "        print(f'  {path}  ({kb:.0f} KB)')",
        "        display(IPImage(path, width=width))",
        "    else:",
        "        print(f'  Not generated yet: {path}')",
        "",
        "show_img(REFERENCE_FACE,  'INPUT â€” Your Reference Face')",
        "show_img(PORTRAIT_PATH,   'STAGE 1 â€” AI Portrait (Realistic Vision + IP-Adapter)')",
        "show_img(FRAME_PATH,      'STAGE 2 â€” Reference Frame (ffmpeg extracted)')",
        "show_img(POSE_DBG_PATH,   'STAGE 3 â€” Pose Skeleton (OpenPose)')",
        "show_img(SYNCED_PATH,     'STAGE 3 â€” FINAL SYNCED IMAGE â† Upload this to Kling AI', width=360)",
        "",
        "print()",
        "print('=' * 60)",
        "print('NEXT STEP: ANIMATE WITH KLING AI (FREE)')",
        "print('=' * 60)",
        "print()",
        "print('  1. Download stage3_synced.jpg (run Cell 8 below)')",
        "print('  2. Go to: https://kling.kuaishou.com  (66 free credits/day)')",
        "print('     OR    https://pika.art             (free plan)')",
        "print('     OR    https://app.runwayml.com     (125 free credits)')",
        "print('  3. Upload stage3_synced.jpg as the IMAGE input')",
        "print('  4. Set motion prompt: walking gracefully, fabric flowing, confident')",
        "print('  5. Generate â†’ download MP4')",
        "print()",
        "print('  Face identity is preserved because the AI starts FROM your image.')",
        "print('  This gives 10x better quality than any local T4 animation model.')",
    ])
    print(f"[âœ“] Cell 9 updated (cell {c9_idx})")

# â”€â”€â”€ 5. Replace Cell 10 (Download) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
c10_idx = find_cell("Download")
if c10_idx >= 0 and cells[c10_idx]["cell_type"] == "code":
    cells[c10_idx]["source"] = src([
        "from google.colab import files",
        "import os",
        "",
        "print('=' * 60)",
        "print('DOWNLOAD FINAL INFLUENCER IMAGE')",
        "print('=' * 60)",
        "print()",
        "",
        "SYNCED_PATH   = '/content/stage3_synced.jpg'",
        "PORTRAIT_PATH = '/content/stage1_portrait.jpg'",
        "",
        "for label, path in [",
        "    ('Final synced image (Stage 3 â€” upload to Kling)', SYNCED_PATH),",
        "    ('Portrait (Stage 1)', PORTRAIT_PATH),",
        "]:",
        "    if os.path.exists(path):",
        "        kb = os.path.getsize(path) / 1024",
        "        print(f'  Downloading {label}')",
        "        print(f'    {path}  ({kb:.0f} KB)')",
        "        files.download(path)",
        "    else:",
        "        print(f'  Not found: {path}  (run Cells 5-7 first)')",
        "    print()",
        "",
        "print('Upload the synced image to Kling AI for high-quality animation.')",
        "print('https://kling.kuaishou.com  (66 free credits/day)')",
    ])
    print(f"[âœ“] Cell 10 updated (download image, cell {c10_idx})")

# â”€â”€â”€ 6. Also update Cell 10 MARKDOWN if it mentions Video â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
c10_md_idx = c10_idx - 1 if c10_idx > 0 and cells[c10_idx - 1]["cell_type"] == "markdown" else -1
if c10_md_idx >= 0:
    cells[c10_md_idx]["source"] = src([
        "---",
        "## ðŸ“¥ Cell 8 â€” Download Your Influencer Image",
        "Downloads `stage3_synced.jpg` (the final output) to your PC.",
        "Upload it to **Kling AI**, **Pika**, or **Runway** for professional animation.",
    ])
    print(f"[âœ“] Cell 10 markdown updated (cell {c10_md_idx})")

# â”€â”€â”€ Save â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
with open(NB_PATH, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)

print()
print(f"[âœ“] Notebook saved: {NB_PATH}")
print(f"    Total cells: {len(nb['cells'])}")

