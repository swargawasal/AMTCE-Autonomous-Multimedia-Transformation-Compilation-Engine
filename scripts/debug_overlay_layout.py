"""
UTILITY: Video Overlay Layout Visualizer (debug_overlay_layout.py)
--------------------------------------------------------------
Purpose:
- Generates a 'Skeleton' image (overlay_layout_proof.png) of a 1080x1920 video.
- Shows exactly where Captions, Branding, and Analysis text will be rendered.
- Helps developers verify 'Lane Collision' logic without processing a real video.

When to run:
- Run this manually when you want to change the positions of text in the video.
- Helps ensure your branding doesn't cover up the AI-generated captions.
"""

from PIL import Image, ImageDraw, ImageFont
import os
from dotenv import load_dotenv
load_dotenv("Credentials/.env")
_BRAND = os.getenv("BRAND_NAME", "YOUR_BRAND")

def create_layout_viz():
    W, H = 1080, 1920
    img = Image.new('RGB', (W, H), color=(50, 50, 50))
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("assets/fonts/Inter-Bold.ttf", 60)
        font_small = ImageFont.truetype("assets/fonts/Inter-Bold.ttf", 40)
    except:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Define Lanes
    lanes = [
        ("Captions Area (Ends Here)", 0.78, (100, 200, 255)),  # Blue
        (f"Branding: '{_BRAND}'", 0.85, (255, 215, 0)),      # Gold
        ("Style Analysis: 'Text...'", 0.92, (255, 100, 100))   # Red
    ]

    # Draw Lanes
    for label, pct, color in lanes:
        y = int(H * pct)
        
        # Line
        draw.line([(0, y), (W, y)], fill=color, width=5)
        
        # Text
        text = f"{label} (Y={pct*100}%)"
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        
        # Draw text centered above the line (except for captions which is below? No, label is description)
        draw.text(((W-w)//2, y - h - 10), text, font=font, fill=color)

    # Simulation Text
    # 1. Captions (Growing Up from 0.78)
    draw.text((100, int(H*0.78)-150), "Caption Line 3\nCaption Line 4 (Bottom)", font=font, fill=(200, 200, 200))
    
    # 2. Branding (At 0.85)
    draw.text((300, int(H*0.85)), _BRAND.upper(), font=font, fill=(255, 255, 255))
    
    # 3. Analysis (At 0.92)
    draw.text((200, int(H*0.92)), "Style Analysis Text Here", font=font_small, fill=(255, 255, 255))

    output_path = "overlay_layout_proof.png"
    img.save(output_path)
    print(f"Generated: {os.path.abspath(output_path)}")

if __name__ == "__main__":
    create_layout_viz()
