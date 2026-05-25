import sys
import os
import logging

# Add project root to path
sys.path.append(os.getcwd())

from Intelligence_Modules.monetization_brain import MonetizationStrategist

logging.basicConfig(level=logging.INFO)

def test_truncation():
    brain = MonetizationStrategist()
    
    # Test case 1: Short script (should remain same)
    script1 = "This is a short script. It is very nice. Find this look linked in the description."
    duration = 15.0 # 15s allows ~35 words at 1.45 density
    result1 = brain._adaptive_truncate(script1, duration)
    print(f"\n--- TEST 1: Short Script ---")
    print(f"Original: {script1}")
    print(f"Result:   {result1}")
    print(f"STATUS: {'PASS' if result1 == script1 else 'FAIL'}")

    # Test case 2: Long script (should truncate)
    script2 = "Tamannaah Bhatia is absolutely stunning in this blue outfit. It fits her perfectly. The color is amazing. You can see the detail. It is a masterpiece. This dress is made of silk. It is very elegant. Everyone is looking. She is the star of the show. We love this look. Find this look linked in the description."
    duration = 10.0 # 10s allows 23 words at 1.20 density
    result2 = brain._adaptive_truncate(script2, duration)
    print(f"\n--- TEST 2: Long Truncation ---")
    print(f"Original: {len(script2.split())} words")
    print(f"Result:   {len(result2.split())} words")
    print(f"Script:   {result2}")
    print(f"STATUS: {'PASS' if len(result2.split()) < len(script2.split()) and result2.endswith('.') else 'FAIL'}")
    
    # Test case 3: Single sentence over 1.20 but under 1.45
    script3 = "Tamannaah Bhatia commands attention in this striking, shimmering sapphire blue jumpsuit and she looks absolutely magnetic and statuesque in every single frame of this clip."
    duration3 = 8.0 # 8s
    result3 = brain._adaptive_truncate(script3, duration3)
    print(f"\n--- TEST 3: Single Sentence High Density ---")
    print(f"Original Density: ~1.33")
    print(f"Result: {result3}")
    print(f"STATUS: {'PASS' if result3 == script3 else 'FAIL'}")

def test_smart_truncation():
    brain = MonetizationStrategist()
    
    print(f"\n--- TEST 4: Smart Truncation (Word Boundary) ---")
    # This text is 120+ chars and will be cut if hard-truncated
    # "The stunning Aishwarya Rai Bachchan is seen here in a breathtaking red gown that perfectly captures her timeless elegance..."
    # 120 chars: "The stunning Aishwarya Rai Bachchan is seen here in a breathtaking red gown that perfectly captures her timeless elegance"
    # If cut at 120, it's "elegance" (maybe lucky), but let's try a different one.
    
    script = "Deepika Padukone looks absolutely regal in this gold saree, showcasing why she is the undisputed queen of Bollywood fashion and grace."
    # 120 chars: "Deepika Padukone looks absolutely regal in this gold saree, showcasing why she is the undisputed queen of Bollywood fash"
    # Truncated: "...Bollywood fash" <- HALF CUT TAIL
    
    result = brain._smart_truncate_caption(script, max_chars=120)
    print(f"Original: {script}")
    print(f"Result:   {result}")
    
    assert "fash" not in result.split()[-1], "Tail is cut!"
    assert result.endswith("..."), "Ellipsis missing"
    print(f"STATUS: PASS")

    print(f"\n--- TEST 5: Smart Truncation (Line Limit) ---")
    # Each word is 10 chars, so 3 words per line (wrap_width=32)
    # 12 words = 4 lines. 13 words = 5 lines.
    script_long = "Wordlongone Wordlongtwo Wordlongthr Wordlongfou Wordlongfiv Wordlongsix Wordlongsev Wordlongeig Wordlongnin Wordlongten Wordlongele Wordlongtwe Wordlongthr"
    result_lines = brain._smart_truncate_caption(script_long, max_lines=4, wrap_width=32)
    import textwrap
    lines = textwrap.wrap(result_lines, width=32)
    print(f"Result lines: {len(lines)}")
    print(f"Result: {result_lines}")
    assert len(lines) <= 4, "Line limit exceeded!"
    print(f"STATUS: PASS")

if __name__ == "__main__":
    test_truncation()
    test_smart_truncation()
