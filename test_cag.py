import os
import time
from cag_engine import TieredCAGEngine, CacheDomain, cag_intercept

# Manually load .env from the Credentials folder
env_path = os.path.join("Credentials", ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.strip().split("=", 1)
                if key.strip() == "GEMINI_API_KEY":
                    os.environ["GEMINI_API_KEY"] = val.strip().strip("'").strip('"')

# 1. Initialize the engine
print("Initializing CAG Engine...")
engine = TieredCAGEngine(base_path=".")

# 2. Create a mock RAG function with the decorator
print("Creating mock RAG function...")
@cag_intercept(CacheDomain.METADATA, engine)
def mock_generate_metadata(garment_desc: str):
    print(f"   [!] MOCK LLM CALL EXECUTING for: '{garment_desc}'")
    time.sleep(2) # Simulate 2 seconds of LLM generation latency
    return {
        "identified_item": garment_desc,
        "style_category": "Luxury" if "sequin" in garment_desc.lower() else "Casual",
        "suggested_price": "$199.99"
    }

print("\n--- RUNNING TESTS ---")

# Test A: First Run (Cache Miss expected)
print("\nTest A: Exact Query 1 (Should take 2 seconds, Source: LLM_FRESH)")
start = time.time()
res1 = mock_generate_metadata("Pink Sequin Skirt")
print(f"Result: {res1}")
print(f"Time taken: {time.time() - start:.3f}s")

# Test B: Exact Same Run (L1 Hash Hit expected)
print("\nTest B: Exact Query 1 Again (Should be instant, Source: CAG_HIT)")
start = time.time()
res2 = mock_generate_metadata("Pink Sequin Skirt")
print(f"Result: {res2}")
print(f"Time taken: {time.time() - start:.3f}s")

# Test C: Semantically Similar Run (L2 Vector Hit expected if similarity > threshold)
print("\nTest C: Semantically Similar Query (Should hit L2 Vector Cache, Source: CAG_HIT)")
# Threshold for METADATA is 0.90
start = time.time()
res3 = mock_generate_metadata("A Pink Sequin Skirt")
print(f"Result: {res3}")
print(f"Time taken: {time.time() - start:.3f}s")

# Test D: Completely Different Query (Cache Miss expected)
print("\nTest D: Completely Different Query (Should take 2 seconds, Source: LLM_FRESH)")
start = time.time()
res4 = mock_generate_metadata("Black Leather Jacket")
print(f"Result: {res4}")
print(f"Time taken: {time.time() - start:.3f}s")

print("\n--- TEST COMPLETE ---")
