import hashlib
import chromadb
import json
import time
import threading
from functools import wraps
from enum import Enum
import os
import google.generativeai as genai

class CacheDomain(Enum):
    PRICE_TAG    = ("price_tag",    0.98, 1)   # threshold, ttl_days
    METADATA     = ("metadata",     0.90, 3)
    THUMBNAIL    = ("thumbnail",    0.95, 14)
    RHYTHM       = ("rhythm",       0.92, 7)
    NARRATIVE    = ("narrative",    0.85, 2)

class TieredCAGEngine:
    def __init__(self, base_path: str):
        self.base_path = base_path
        self.l1_cache = {}          # Exact hash cache
        self.l1_lock = threading.RLock()
        
        # Configure Gemini API to offload embedding math (Zero local hardware stress)
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        
        # L2 — ChromaDB (safer than FAISS on Windows)
        self.chroma = chromadb.PersistentClient(
            path=f"{base_path}/.cag_cache/chroma"
        )
        
        # Separate collection per domain prevents cross-domain false positives
        self.collections = {
            d.value[0]: self.chroma.get_or_create_collection(
                name=d.value[0],
                metadata={"hnsw:space": "cosine"}
            ) for d in CacheDomain
        }
        
        # Quality tracking — fixes race condition
        self.quality_ledger = {}
        self.ledger_lock = threading.RLock()

    def embed(self, text: str):
        # Offload embedding to GeminiGovernor Router to respect global keys and limits
        try:
            from Intelligence_Modules.gemini_governor import gemini_router
            emb = gemini_router.embed(text)
            if not emb or len(emb) < 768:
                return [0.0] * 768
            return emb
        except Exception as e:
            print(f"CAG Embedding Error (Governor): {e}")
            return [0.0] * 768

    def get(self, domain: CacheDomain, query: str, 
            embedding: list) -> dict | None:
        
        name, threshold, ttl = domain.value
        
        # L1: Exact match (deterministic, 0ms)
        key = hashlib.sha256(
            f"{name}:{query}".encode()
        ).hexdigest()
        
        with self.l1_lock:
            if key in self.l1_cache:
                entry = self.l1_cache[key]
                if not self._is_expired(entry, ttl):
                    if self._is_healthy(key):  # quality gate
                        return entry['value']
        
        # L2: Semantic vector match
        collection = self.collections[name]
        results = collection.query(
            query_embeddings=[embedding],
            n_results=1,
            include=['documents','metadatas','distances']
        )
        
        if results and results['ids'] and len(results['ids'][0]) > 0:
            distance = results['distances'][0][0]
            similarity = 1 - distance  # cosine → similarity
            
            if similarity >= threshold:
                meta = results['metadatas'][0][0]
                cache_key = results['ids'][0][0]
                
                # TTL check
                if not self._is_expired(meta, ttl):
                    # Quality check — fixes race condition
                    if self._is_healthy(cache_key):
                        loaded_doc = json.loads(results['documents'][0][0])
                        loaded_doc['_cag_confidence'] = round(similarity, 4)
                        return loaded_doc
        return None  # Cache miss → go to LLM

    def set(self, domain: CacheDomain, query: str,
            embedding: list, value: dict):
        
        name, threshold, ttl = domain.value
        key = hashlib.sha256(
            f"{name}:{query}".encode()
        ).hexdigest()
        
        # Ensure value is a dict before dumping (or handle strings differently)
        if not isinstance(value, dict):
            value = {"result": value}
            
        payload = json.dumps(value)
        
        # Store in both L1 and L2
        with self.l1_lock:
            self.l1_cache[key] = {
                'value': value,
                'timestamp': time.time(),
                'domain': name
            }
        
        self.collections[name].upsert(
            ids=[key],
            embeddings=[embedding],
            documents=[payload],
            metadatas=[{
                'timestamp': time.time(),
                'domain': name,
                'query_hash': key
            }]
        )

    def flag_unhealthy(self, cache_key: str, reason: str):
        """
        Safe replacement for delete. Marks bad — doesn't delete immediately.
        Fixes the race condition.
        """
        with self.ledger_lock:
            self.quality_ledger[cache_key] = {
                'healthy': False,
                'reason': reason,
                'flagged_at': time.time()
            }

    def _is_healthy(self, key: str) -> bool:
        with self.ledger_lock:
            entry = self.quality_ledger.get(key)
            return entry is None or entry.get('healthy', True)

    def _is_expired(self, entry: dict, ttl_days: int) -> bool:
        age = time.time() - entry.get('timestamp', 0)
        return age > (ttl_days * 86400)


# ── Decorator ──────────────────────────────────────────
def cag_intercept(domain: CacheDomain, engine: TieredCAGEngine):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Build query string from args (simple serialization)
            query = str(args) + str(kwargs)
            embedding = engine.embed(query) 
            
            # Try cache first
            cached = engine.get(domain, query, embedding)
            if cached is not None:
                cached['_source'] = 'CAG_HIT'
                return cached
            
            # Cache miss → run real function
            result = func(*args, **kwargs)
            
            # Store for next time
            if result:
                engine.set(domain, query, embedding, result)
                if isinstance(result, dict):
                    result['_source'] = 'LLM_FRESH'
            
            return result
        return wrapper
    return decorator
