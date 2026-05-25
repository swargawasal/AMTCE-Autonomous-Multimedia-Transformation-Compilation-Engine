
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv("Credentials/.env")
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

print("Available Models:")
try:
    for m in genai.list_models():
        print(f"- {m.name} ({m.supported_generation_methods})")
except Exception as e:
    print(f"Error listing models: {e}")
