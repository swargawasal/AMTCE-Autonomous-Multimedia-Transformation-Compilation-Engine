"""
Exhaustive test of every image/video model on the account.
Tests actual generation, not just listing.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv("Credentials/.env")

from google import genai

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

PROMPT = "A full-body model wearing a red silk saree. White studio background. No text."

print("=" * 60)
print("TESTING: Imagen Models (generate_images)")
print("=" * 60)

IMAGEN_MODELS = [
    "imagen-4.0-ultra-generate-001",
    "imagen-4.0-generate-001",
    "imagen-4.0-fast-generate-001",
]
for m in IMAGEN_MODELS:
    try:
        r = client.models.generate_images(
            model=m, prompt=PROMPT,
            config=genai.types.GenerateImagesConfig(number_of_images=1, aspect_ratio="3:4")
        )
        print(f"  [OK] {m}: {len(r.generated_images)} image(s) generated!")
    except Exception as e:
        print(f"  [FAIL] {m}: {str(e)[:120]}")

print()
print("=" * 60)
print("TESTING: Gemini Flash Image Models (generate_content)")
print("=" * 60)

FLASH_IMAGE_MODELS = [
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
]
for m in FLASH_IMAGE_MODELS:
    try:
        r = client.models.generate_content(
            model=m,
            contents=[PROMPT],
            config=genai.types.GenerateContentConfig(response_modalities=["IMAGE"])
        )
        parts = r.candidates[0].content.parts if r.candidates else []
        has_image = any(hasattr(p, "inline_data") and p.inline_data for p in parts)
        print(f"  [OK] {m}: image={'YES' if has_image else 'NO (text only)'}")
    except Exception as e:
        print(f"  [FAIL] {m}: {str(e)[:120]}")

print()
print("=" * 60)
print("TESTING: Veo Video Models (generate_videos)")
print("=" * 60)

VEO_MODELS = [
    "veo-3.1-generate-preview",
    "veo-3.0-generate-001",
    "veo-2.0-generate-001",
]
for m in VEO_MODELS:
    try:
        op = client.models.generate_videos(
            model=m, prompt=PROMPT,
            config=genai.types.GenerateVideosConfig(aspect_ratio="9:16")
        )
        print(f"  [OK] {m}: Operation started! (name={op.name[:40]})")
    except Exception as e:
        print(f"  [FAIL] {m}: {str(e)[:120]}")

print()
print("Done.")
