import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from Intelligence_Modules.editor_brain import (
        HYBRID_ANALYZER_AVAILABLE,
        RAG_AVAILABLE,
        DECISION_ENGINE_AVAILABLE,
        STYLE_VALIDATOR_AVAILABLE
    )
    
    print(f"HYBRID_ANALYZER_AVAILABLE: {HYBRID_ANALYZER_AVAILABLE}")
    print(f"RAG_AVAILABLE: {RAG_AVAILABLE}")
    print(f"DECISION_ENGINE_AVAILABLE: {DECISION_ENGINE_AVAILABLE}")
    print(f"STYLE_VALIDATOR_AVAILABLE: {STYLE_VALIDATOR_AVAILABLE}")
    
    if not RAG_AVAILABLE:
        print("\nChecking RAG imports manually...")
        try:
            import chromadb
            print("✅ chromadb imported")
        except ImportError as e:
            print(f"❌ chromadb import failed: {e}")
            
        try:
            from rag.chroma_client import get_collection
            print("✅ rag.chroma_client.get_collection imported")
        except Exception as e:
            print(f"❌ rag.chroma_client.get_collection failed: {e}")
            
except Exception as e:
    print(f"Error checking flags: {e}")
