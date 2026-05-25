import sys
import os

# Set standard output encoding to utf-8 to support emojis on Windows
sys.stdout.reconfigure(encoding='utf-8')

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Higgsfield_Modules.higgsfield_engine import get_engine

try:
    print("Initializing Higgsfield Engine for Disha Patani...")
    engine = get_engine("Disha Patani")
    print("\n--- Higgsfield Engine Status ---")
    print(engine.status_report())
    print("--------------------------------")
except Exception as e:
    print(f"Error occurred: {e}", file=sys.stderr)
