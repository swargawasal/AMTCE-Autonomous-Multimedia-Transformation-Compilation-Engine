import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("test_wear")

from PIL import Image

sys.path.append(r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)")
from Monetization_Metrics.fashion_scout import FashionScout
from Text_Modules.gemini_captions import GeminiCaptionGenerator

def test_wear_finder():
    input_image = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\sample_thumbs\Reem_01_thumb.jpg"
    cropped_image_path = r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\scratch\Reem_01_cropped.jpg"
    
    logger.info("Cropping image...")
    with Image.open(input_image) as img:
        w, h = img.size
        top = int(h * 0.2)
        bottom = int(h * 0.8)
        cropped_img = img.crop((0, top, w, bottom))
        cropped_img.save(cropped_image_path)
        logger.info(f"Saved cropped image: {cropped_image_path}")

    logger.info("Initializing FashionScout...")
    scout = FashionScout()
    logger.info("Running FashionScout.scout_outfit...")
    scout_result = scout.scout_outfit([cropped_image_path], {"brand": "unknown", "text_in_video": []})
    
    print("\n" + "="*40)
    print("--- Fashion Scout Output ---")
    if scout_result:
        print(f"Wear Name: {scout_result.get('first_shot', {}).get('wear_name', 'N/A')}")
        print(f"Description: {scout_result.get('outfit_description', 'N/A')}")
        print(f"Query: {scout_result.get('search_query', 'N/A')}")
    else:
        print("Fashion Scout failed.")

    logger.info("Initializing GeminiCaptionGenerator...")
    cap_gen = GeminiCaptionGenerator()
    logger.info("Running GeminiCaptionGenerator.generate_caption...")
    caption_result = cap_gen.generate_caption(cropped_image_path, style="editorial")
    
    print("\n--- Gemini Captions Output ---")
    print(f"Generated Caption: {caption_result}")
    print("="*40 + "\n")

if __name__ == "__main__":
    test_wear_finder()
