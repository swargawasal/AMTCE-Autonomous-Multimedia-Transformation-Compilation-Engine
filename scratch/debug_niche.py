
main_item_name = "black lace bodysuit"
main_caption = "" # We don't know the caption, let's assume empty for now

mappings = {
    "Fashion & Style": ["bra", "outfit", "dress", "style", "wear", "clothing", "fashion", "look"],
    "AI Tech & Futuristic Content": ["ai", "tech", "robot", "future", "gadget"],
    "Comedy & Relatable Meme": ["joke", "funny", "meme", "comedy", "laugh"],
    "Food & Cooking": ["food", "cooking", "recipe", "chef", "eat"],
    "Fitness & Body Transformation": ["fitness", "gym", "workout", "body", "muscle"]
}

def test_inference(item_name, caption):
    item_name = item_name.lower()
    caption = caption.lower()
    for niche_name, keywords in mappings.items():
        if any(kw in item_name or kw in caption for kw in keywords):
            return niche_name, [kw for kw in keywords if kw in item_name or kw in caption]
    return "General_Fallback", []

print(f"Test 1 (item only): {test_inference(main_item_name, '')}")
print(f"Test 2 (with 'ai' in caption): {test_inference(main_item_name, 'Sabrina wearing a bodysuit #ai')}")
print(f"Test 3 (with 'future' in caption): {test_inference(main_item_name, 'The future of pop')}")
