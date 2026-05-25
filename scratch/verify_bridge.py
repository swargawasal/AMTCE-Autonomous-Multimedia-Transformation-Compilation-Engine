import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    # Import from the LEGACY path
    from Intelligence_Modules import fashion_scout
    
    # Test if we can access the production functions through the bridge
    fallback = fashion_scout.get_fallback_payload()
    print(f"SUCCESS: Bridge established.")
    print(f"Fallback Vibe: {fallback.get('vibe')}")
    print(f"Legacy Key Present: {'fashion' in fallback}")
    
    # Check if 'scout' is the production singleton
    from Monetization_Metrics.fashion_scout import scout as prod_scout
    print(f"Singleton Identity Match: {fashion_scout.scout is prod_scout}")

except Exception as e:
    print(f"FAILURE: {e}")
