import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("GEMINI_API_KEY_1") or os.getenv("GEMINI_API_KEY_2")
if not key:
    print("No API key found")
    exit(1)

genai.configure(api_key=key)

print("Checking Imagen Models...")
try:
    imagen_models = []
    for m in genai.list_models():
        if 'image' in m.name.lower() or 'vision' in m.name.lower() or 'imagen' in m.name.lower() or 'veo' in m.name.lower():
            imagen_models.append(m.name)
            print(f"- {m.name} (Methods: {m.supported_generation_methods})")
    
    if not imagen_models:
        print("No image/video generation models found in list_models().")
        
except Exception as e:
    print("Error listing models:", e)

try:
    print("Checking if we can generate an image with 'gemini-1.5-pro'...")
    model = genai.GenerativeModel('gemini-1.5-pro')
    # This usually throws an error if trying to generate images, but let's check
    # if there is a generate_images method on genai or similar
except Exception as e:
    print("Error:", e)
