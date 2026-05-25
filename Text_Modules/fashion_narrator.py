"""
fashion_narrator.py
-------------------
Utility to extract short outfit captions from FashionScout data.
NOTE: Script generation has been DELETED. The system now uses FashionScout's
native editorial_script to avoid wasting Gemini API calls and losing data.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("fashion_narrator")


def extract_outfit_caption(fashion_data: Dict[str, Any], max_words: int = 5) -> str:
    """
    Derive a short overlay caption from the outfit description.
    E.g. "Ruched Satin Mini Dress" or "Raw Denim x Oversized Blazer"

    Args:
        fashion_data: Dict from fashion_scout.scan() or scout.scout_outfit()
        max_words: Maximum number of words to keep (default 5)

    Returns:
        Short outfit name string, title-cased.
    """
    # Handle flat or nested schemas
    flat_data = fashion_data.get("fashion", fashion_data) if isinstance(fashion_data, dict) else {}

    # Priority 1: Use the exact outfit_type identified by FashionScout (e.g. "Western formal gown")
    outfit_type = flat_data.get("outfit_type", "").strip()
    if outfit_type:
        return outfit_type.title()

    # Priority 2: Use first_shot wear_name if it exists (highly descriptive)
    first_shot = flat_data.get("first_shot", {})
    if isinstance(first_shot, dict):
        wear_name = first_shot.get("wear_name", "").strip()
        if wear_name:
            return " ".join(wear_name.split()[:max_words]).title()

    # Priority 3: Fallback to deriving from outfit_description
    outfit_description = flat_data.get("outfit_description", "").strip()

    if not outfit_description:
        return ""

    # Title-case the first N words as the caption
    words = outfit_description.split()
    return " ".join(words[:max_words]).title()
