import os
import sys
import logging
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Credentials", ".env")
load_dotenv(env_path)

try:
    import google.generativeai as genai
except ImportError:
    print("google-generativeai not found.")
    sys.exit(1)

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("GEMINI_API_KEY not found in .env")
    sys.exit(1)

genai.configure(api_key=api_key)

print("🔍 Inspecting Available Models on your API Key...\n")

image_models = []
video_models = []
other_models = []

count = 0
try:
    for model in genai.list_models():
        count += 1
        methods = model.supported_generation_methods
        is_media = False
        
        # Check for image generation
        if 'generateImage' in methods or 'generateImages' in methods or 'image' in model.name:
            image_models.append((model.name, methods))
            is_media = True
            
        # Check for video generation
        if 'generateVideo' in methods or 'generateVideos' in methods or 'veo' in model.name:
            video_models.append((model.name, methods))
            is_media = True
            
        if not is_media:
             # Just keep track of a few for debugging
             if len(other_models) < 5:
                  other_models.append(model.name)

except Exception as e:
    print(f"❌ Error fetching models: {e}")
    sys.exit(1)

print(f"Total models available: {count}")
print("\n--- 🖼️ Image Generation Models ---")
if not image_models:
    print("None found!")
else:
    for name, methods in image_models:
        print(f" - {name} (Methods: {methods})")

print("\n--- 🎬 Video Generation Models ---")
if not video_models:
    print("None found!")
else:
    for name, methods in video_models:
        print(f" - {name} (Methods: {methods})")
        
print("\n--- 📝 Other available models (Sample) ---")
for name in other_models:
     print(f" - {name}")

print("\nDone.")
