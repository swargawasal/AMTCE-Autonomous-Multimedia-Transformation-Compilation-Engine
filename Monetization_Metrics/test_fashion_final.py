import os
import sys
import logging
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

# Load environment
load_dotenv("Credentials/.env")

# Enable logging to stdout
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

from Monetization_Metrics.fashion_scout import scout

def test_final_direct():
    print("\n--- FINAL DIRECT FASHION SCOUT TEST ---")
    test_image = "assets/snapped_thumbs/test_frame.jpg"
    
    if not os.path.exists(test_image):
        print("❌ Test image missing. Creating dummy...")
        from PIL import Image
        img = Image.new('RGB', (200, 200), color = 'red')
        if not os.path.exists("assets/snapped_thumbs"): os.makedirs("assets/snapped_thumbs")
        img.save(test_image)

    res = scout.scout_outfit([test_image])
    if res:
        print("\n✅ SUCCESS!")
        print(f"Vibe: {res.get('vibe')}")
        print(f"Hinglish CTA: {res.get('imaginative_ctas', {}).get('hinglish')}")
    else:
        print("\n❌ FAILED. Possible reasons: API Key limits, head-less environment issues, or model naming policy.")

if __name__ == "__main__":
    test_final_direct()
