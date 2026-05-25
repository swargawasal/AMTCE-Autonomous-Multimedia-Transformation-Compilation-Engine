import sys
import os

# Mock the NICHE_LIST
NICHE_LIST = [
    "Adult Content & NSFW Content",
    "AI Tech & Futuristic Content",
    "Comedy & Relatable Meme",
    "Educational & Quick Learning",
    "Fashion & Style",
    "Fitness & Body Transformation",
    "Food & Cooking",
    "General_Fallback",
    "Money & Online Income",
    "Motivation & Self-Improvement",
    "Relationships & Dating Psychology",
    "Travel & Lifestyle",
]

NICHE_REDIRECTS = {
    "fashion": "Fashion & Style",
    "style": "Fashion & Style",
    "comedy": "Comedy & Relatable Meme",
    "meme": "Comedy & Relatable Meme",
    "adult": "Adult Content & NSFW Content",
    "nsfw": "Adult Content & NSFW Content"
}

def mock_classify(raw_niche):
    detected_niche = None
    if raw_niche in NICHE_LIST:
        detected_niche = raw_niche
    elif raw_niche:
        raw_lower = raw_niche.lower()
        if raw_lower in NICHE_REDIRECTS:
            detected_niche = NICHE_REDIRECTS[raw_lower]
        else:
            for n in NICHE_LIST:
                if n.lower() == raw_lower:
                    detected_niche = n
                    break
    
    if not detected_niche:
        detected_niche = "General_Fallback"
    return detected_niche

# Tests
test_cases = [
    "Fashion",
    "Fashion & Style",
    "fashion",
    "Comedy",
    "NSFW",
    "UnknownNiche"
]

print("--- Niche Mapping Verification ---")
for tc in test_cases:
    print(f"Input: '{tc}' -> Resolved: '{mock_classify(tc)}'")
