
import os
import time
import json
from Intelligence_Modules.gemini_governor import gemini_router
import google.generativeai as genai
from dotenv import load_dotenv

# Load credentials
load_dotenv("Credentials/.env")
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

def test_cache():
    print("\n--- Testing Cache ---")
    prompt = "What is 2+2? Answer only with the number."
    # First call - should hit API
    res1 = gemini_router.generate(task_type="test", prompt=prompt, module_name="verify_test")
    print(f"Call 1: {res1}")
    
    # Second call - should hit CACHE
    res2 = gemini_router.generate(task_type="test", prompt=prompt, module_name="verify_test")
    print(f"Call 2: {res2}")
    
    if gemini_router.stats["cache_hits"] > 0:
        print("✅ Cache verification successful!")
    else:
        print("❌ Cache verification failed.")

def test_model_ban():
    print("\n--- Testing Model Ban Logic ---")
    model_name = "gemini-1.5-flash-lite"
    
    print(f"Initial status {model_name}: {gemini_router.is_model_available(model_name)}")
    
    # Manually mark as banned (simulating 429)
    gemini_router.mark_model_banned(model_name)
    
    print(f"Status after ban: {gemini_router.is_model_available(model_name)}")
    
    if not gemini_router.is_model_available(model_name):
        print("✅ Model ban logic successful!")
    else:
        print("❌ Model ban logic failed.")
        
    # Check if router picks a different model
    # preferred for watermark is flash-lite, since it's banned it should pick next available
    next_model = gemini_router.get_available_model("watermark")
    print(f"Next available model for 'watermark': {next_model}")
    if next_model != model_name:
        print("✅ Router redirection successful!")
    else:
        print("❌ Router redirection failed.")

def test_embeddings():
    print("\n--- Testing Embeddings ---")
    text = "Fashion trends 2027"
    emb = gemini_router.embed(text, module_name="verify_test")
    print(f"Embedding length: {len(emb) if emb else 0}")
    if emb:
        print("✅ Embedding call successful!")
    else:
        print("❌ Embedding call failed.")

if __name__ == "__main__":
    test_cache()
    test_model_ban()
    test_embeddings()
    gemini_router.print_usage_report()
