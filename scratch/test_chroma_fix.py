import sys
import os
from pathlib import Path

# Add root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import logging
logging.basicConfig(level=logging.INFO)

from rag.chroma_client import get_collection

def test_fix():
    print("--- TESTING CHROMA FIX ---")
    
    # 1. Test normal load (might timeout if ONNX is broken, which is what we want to test)
    print("\nAttempting collection load...")
    # Reduce timeout for test
    os.environ["CHROMA_EF_TIMEOUT"] = "5" 
    
    try:
        collection = get_collection("test_collection")
        print(f"OK - Collection created/retrieved: {collection.name}")
        
        # Verify it can embed
        docs = ["hello world"]
        # Accessing private attribute just for test verification
        ef_type = type(collection._embedding_function).__name__
        print(f"Using Embedding Function: {ef_type}")
        
        embeddings = collection._embedding_function(docs)
        print(f"OK - Embedding successful. Dim: {len(embeddings[0])}")
        
    except Exception as e:
        print(f"ERROR - Test failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_fix()
