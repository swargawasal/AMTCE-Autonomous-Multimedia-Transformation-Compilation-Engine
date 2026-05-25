import os
import sys
import argparse
import logging
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Thumb_Modules.ai_media_generator import generate_ad_images, generate_ad_video

# Setup logging to console
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("ad_toolkit")

def main():
    parser = argparse.ArgumentParser(description="AMTCE Ad Toolkit — Standalone Generator")
    parser.add_argument("--mode", choices=["image", "video", "full"], default="full", help="What to generate")
    parser.add_argument("--title", type=str, required=True, help="Title/Product name (used for AI prompt)")
    parser.add_argument("--image", type=str, help="Path to an existing image (required for 'video' mode)")
    parser.add_argument("--outdir", type=str, default="sample_thumbs/manual_tests", help="Output directory")
    
    args = parser.parse_args()
    
    # Load .env
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Credentials", ".env")
    load_dotenv(env_path)
    
    # Check API key
    if not os.getenv("GEMINI_API_KEY"):
        print("❌ Error: GEMINI_API_KEY not found in Credentials/.env")
        return

    os.makedirs(args.outdir, exist_ok=True)
    base_name = args.title.lower().replace(" ", "_")[:20]

    images_generated = {}

    # --- Mode: Image ---
    if args.mode in ["image", "full"]:
        print(f"🎨 Generating AI Ad Images for: '{args.title}'...")
        images_generated = generate_ad_images(args.title, args.outdir, base_name)
        if images_generated:
            print(f"✅ Images saved to: {args.outdir}")
            for format, paths in images_generated.items():
                print(f"   ↳ {format}: {len(paths)} variants")
        else:
            print("❌ Image generation failed (check logs/quota).")

    # --- Mode: Video ---
    if args.mode in ["video", "full"]:
        source_image = args.image
        
        # If no image provided but we just generated some, use the first one
        if not source_image and images_generated.get("instagram_feed"):
            source_image = images_generated["instagram_feed"][0]
        
        if source_image and os.path.exists(source_image):
            print(f"🎬 Generating AI Video Ad (Veo) from image: {os.path.basename(source_image)}...")
            # Momentarily force enable video creation for this script if it was off in .env
            os.environ["ENABLE_AI_VIDEO_CREATION"] = "true"
            video_path = generate_ad_video(args.title, source_image, args.outdir, base_name)
            if video_path:
                print(f"✅ Video Ad saved: {video_path}")
            else:
                print("❌ Video generation failed (likely no Veo access or quota).")
        else:
            if args.mode == "video":
                print("❌ Error: You must provide --image [path] or run in 'full' mode to generate video.")

if __name__ == "__main__":
    main()
