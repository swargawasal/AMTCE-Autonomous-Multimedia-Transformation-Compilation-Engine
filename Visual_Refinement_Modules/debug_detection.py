
import os
import cv2
import json
import logging
from dotenv import load_dotenv
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from . import gemini_enhance_for_watermark as gemini_enhance
except ImportError:
    import gemini_enhance_for_watermark as gemini_enhance

load_dotenv("watermark_env.env", override=True)

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_detection")

# Setup Gemini
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    # Try getting from args or hardcode if debug
    pass

if not api_key:
    print("❌ No API Key found.")
    exit()

# gemini_enhance.init_gemini(api_key) # Deprecated - handled automatically via env

# Load Image
import sys
image_path = sys.argv[1] if len(sys.argv) > 1 else "test.png"
if not os.path.exists(image_path):
    print(f"❌ Image not found: {image_path}")
    exit()

img = cv2.imread(image_path)
if img is None:
    print("❌ Failed to load image.")
    exit()

print(f"📸 Loaded image: {img.shape}")

# Run Detection
print("🚀 Running Gemini Detection...")
results, detected_niche = gemini_enhance.detect_watermark([img], keywords="MIRRORED TEXT")

print("\n--- RESULTS ---")
print(json.dumps(results, indent=2))

# Face Safety Check
try:
    from .opencv_watermark import FaceProtector
except ImportError:
    from opencv_watermark import FaceProtector
print("\n🛡️ Testing Face Safety...")
for item in results:
    box = {'x': item['x'], 'y': item['y'], 'w': item['w'], 'h': item['h']}
    is_safe, reason = FaceProtector.is_safe_region(img, box)
    print(f"Item {item.get('semantic_hint', 'Unknown')}: Safe={is_safe}, Reason={reason}")

# SIMULATION: Test a hypothetical 'FILMYGYAN' box on the left
# Based on the failure image, it's roughly at x=20, y=730, w=100, h=40
sim_box = {'x': 20, 'y': 730, 'w': 100, 'h': 40}
is_safe, reason = FaceProtector.is_safe_region(img, sim_box)
print(f"🔬 SIMULATION (Filmygyan Left): Safe={is_safe}, Reason={reason}")
