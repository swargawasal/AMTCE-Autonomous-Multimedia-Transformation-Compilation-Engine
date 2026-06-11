import os
import base64
from typing import Optional

def export_graph_to_base64(image_path: str) -> Optional[str]:
    """Reads a generated graph image and converts it to base64 for Gemini Vision."""
    if not os.path.exists(image_path):
        return None
        
    try:
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return encoded
    except Exception as e:
        print(f"Error encoding graph: {e}")
        return None
