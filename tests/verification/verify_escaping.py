
import sys
import os

# Add the project root to sys.path
sys.path.append(os.getcwd())

from Text_Modules.text_overlay import overlay_engine

def test_escaping():
    test_cases = [
        ("Hello World", "Hello World"),
        ("Value: 100", "Value\\: 100"),
        ("It's a test", "It\\'s a test"),
        ("Comma, separated", "Comma\\, separated"),
        ("Semicolon; test", "Semicolon\\; test"),
        ("100%", "100\\%"),
        ("[Bracket] test", "\\[Bracket\\] test"),
        ("(Parenthesis) test", "\\(Parenthesis\\) test"),
        ("Multiple\nLines", "Multiple Lines"),
        ("Complex: [one] (two) 'three' %four%", "Complex\\: \\[one\\] \\(two\\) \\'three\\' \\%four\\%")
    ]

    print("--- Testing Escaping ---")
    all_passed = True
    for original, expected in test_cases:
        escaped = overlay_engine._escape_drawtext(original)
        if escaped == expected:
            print(f"PASS: '{original}' -> '{escaped}'")
        else:
            print(f"FAIL: '{original}' -> '{escaped}' (Expected: '{expected}')")
            all_passed = False
    return all_passed

if __name__ == "__main__":
    if test_escaping():
        print("\nAll escaping tests passed!")
    else:
        print("\nSome escaping tests failed.")
        sys.exit(1)
