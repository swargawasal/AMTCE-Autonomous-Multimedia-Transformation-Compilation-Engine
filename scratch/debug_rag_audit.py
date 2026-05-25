import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    print("Testing imports for RAG...")
    from analyzer.hybrid_analyzer import HybridAnalyzer
    print("✅ HybridAnalyzer imported")
    
    from decision.decision_engine import generate_with_rag
    print("✅ generate_with_rag imported")
    
    from rag.chroma_client import get_collection
    from rag.rag_bootstrap import ensure_collection_ready
    from rag.retriever import get_top_patterns
    print("✅ RAG components imported")
    
    print("\nTesting Chroma initialization...")
    collection = get_collection("test_audit")
    print(f"✅ Collection created: {collection.name}")
    
    print("\nTesting Bootstrap...")
    ready = ensure_collection_ready(collection)
    print(f"✅ Bootstrap ready: {ready}")
    
    if ready:
        count = collection.count()
        print(f"✅ Collection count: {count}")
    
except Exception as e:
    print(f"\n❌ FAILED: {e}")
    import traceback
    traceback.print_exc()
