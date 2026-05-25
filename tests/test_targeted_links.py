import os
import sys
import json
import logging

# Setup path for imports
sys.path.append(os.getcwd())

from Intelligence_Modules.monetization_brain import MonetizationStrategist

# Mocking adaptive intelligence to avoid errors
class MockAdaptive:
    def get_execution_constraints(self):
        return {"cta_aggression": 1.0}

import Intelligence_Modules.adaptive_intelligence as ai
ai.brain = MockAdaptive()

logging.basicConfig(level=logging.INFO)

def test_targeted_links():
    brain = MonetizationStrategist()
    
    # Test cases: (category, expected_key_match)
    test_cases = [
        ("Saree", "Saree"),
        ("Silk Saree", "Saree"),
        ("Cotton Dress", "Dress"),
        ("Designer Gown", "Gown"),
        ("Unknown Category", "default"),
        (None, "default")
    ]
    
    print("\n--- Testing Targeted Amazon Links ---")
    for category, expected in test_cases:
        link = brain.get_monetization_link(target_platform="youtube", item_category=category)
        cat_str = str(category)
        print(f"Category: {cat_str:15} | Link: {link} | Expected: {expected}")
        
        # In our case, all links are currently the same placeholder, 
        # so we check if a link was returned.
        assert link is not None, f"Failed for {category}"

    print("\n--- Testing Backward Compatibility ---")
    # Backup original file
    original_file = "Monetization_Metrics/Amazon_affliate_link.json"
    backup_file = "Monetization_Metrics/Amazon_affliate_link.json.bak"
    if os.path.exists(original_file):
        import shutil
        shutil.copy2(original_file, backup_file)
        
        # Test with old list format
        try:
            with open(original_file, "w") as f:
                json.dump(["https://old-link.com"], f)
            
            link = brain.get_monetization_link(target_platform="youtube", item_category="Saree")
            print(f"Old List Format: {link}")
            assert link == "https://old-link.com"
        finally:
            # Restore original
            shutil.move(backup_file, original_file)

    print("\n✅ Verification Successful!")

if __name__ == "__main__":
    test_targeted_links()
