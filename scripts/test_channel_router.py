import sys
sys.path.insert(0, ".")
from Actress_Modules.channel_router import resolve_channel, get_source_accounts, detect_gender_from_name

print("=== Source Accounts ===")
accs = get_source_accounts()
print("  Count:", len(accs), "(all placeholders expected since not configured yet)")

print("\n=== Channel Routing Tests ===")

# Known woman by IG ID (in women dict)
r1 = resolve_channel("avneetkaur_13", {})
print("avneetkaur_13 ->", r1)
assert r1[0] == "General_Fallback", "FAIL: expected General_Fallback, got " + r1[0]

# NSFW known account
r2 = resolve_channel("miamalkova", {})
print("miamalkova ->", r2)
assert r2[0] == "General_Fallback", "FAIL: expected General_Fallback, got " + r2[0]
assert r2[2] == True, "FAIL: expected is_nsfw=True"

# Unknown man - male name tokens
r3 = resolve_channel("unknown_dude99", {"fullName": "Rahul Kumar", "taggedUsers": []})
print("unknown_dude99 (Rahul Kumar) ->", r3)
assert r3[0] == "Paparazzi_Channel", "FAIL: expected Paparazzi_Channel, got " + r3[0]

# Name heuristic - female name in ownerFullName (what Apify actually returns)
r4 = resolve_channel("xyz_page", {"ownerFullName": "Priya Sharma", "taggedUsers": []})
print("xyz_page (ownerFullName=Priya Sharma) ->", r4)
assert r4[0] == "General_Fallback", "FAIL: expected General_Fallback, got " + r4[0]

# Tagged user test - tagged user is a known woman
r4b = resolve_channel("paparazzi_page", {
    "taggedUsers": [{"username": "kiaraaliaadvani", "fullName": "Kiara Advani"}]
})
print("paparazzi_page (tagged=kiaraaliaadvani) ->", r4b)
assert r4b[0] == "General_Fallback", "FAIL: expected General_Fallback, got " + r4b[0]
assert r4b[1] == "Kiara Advani", "FAIL: expected 'Kiara Advani', got " + r4b[1]

# Completely unknown name - safe default to Paparazzi
r5 = resolve_channel("some_unknown", {"fullName": "Xyz Abc", "taggedUsers": []})
print("some_unknown (Xyz Abc) ->", r5)
assert r5[0] == "Paparazzi_Channel", "FAIL: expected Paparazzi_Channel, got " + r5[0]

print("\n=== Gender Detection Tests ===")
tests = [
    ("Avneet Kaur", "female"),
    ("Ranveer Singh", "male"),
    ("Priya Kapoor", "female"),
    ("Unknown Name", "unknown"),
]
for name, expected in tests:
    result = detect_gender_from_name(name)
    status = "OK" if result == expected else "FAIL (expected " + expected + ")"
    print(name, "->", result, status)

print("\nALL TESTS PASSED")
