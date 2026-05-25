"""
fix_background_person_bug.py
============================
Fixes the bug where Gemini picks a background person's garment
(e.g. purple saree in background) over the foreground subject.

Root cause: Dimension 6 gave +3 bonus for colorful/attractive garments
WITHOUT restricting it to the FOREGROUND SUBJECT only.

Fix:
  1. Add FOREGROUND SUBJECT LOCK rule — any garment worn by a person
     NOT in the foreground is automatically score-capped at 2 and
     placed in secondary_garments, never chosen as hero.
  2. Fix Dimension 5 & 6 — ONLY score garments on the FOREGROUND SUBJECT.
     Background people's garments are automatically disqualified.
  3. Strengthen WHAT TO REJECT section.
"""
import re
from pathlib import Path
import shutil

TARGET = Path(r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Monetization_Metrics\fashion_scout.py")
BACKUP = TARGET.with_suffix(".py.bak_bgfix")
shutil.copy2(TARGET, BACKUP)
print(f"[FIX] Backup: {BACKUP}")

src = TARGET.read_text(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────────────
# FIX 1 — Strengthen WHAT TO REJECT to explicitly exclude background people
# ─────────────────────────────────────────────────────────────────────────────
OLD_REJECT = (
    "  WHAT TO REJECT:\n"
    "  \u2717 A garment that appears in only 1 out of 4 frames (random, not featured)\n"
    "  \u2717 Background clothing on other people\n"
    "  \u2717 Garments that are partially hidden by hands, bags, or other objects\n"
    "  \u2717 Garments visible only in transition/movement frames (blurred, mid-spin)"
)
NEW_REJECT = (
    "  WHAT TO REJECT \u2014 ABSOLUTE EXCLUSIONS (no matter how colorful or attractive):\n"
    "  \u2717 Garments worn by BACKGROUND PEOPLE \u2014 anyone smaller in frame, further from camera,\n"
    "    or in soft focus is a background person. Their clothing is NEVER the hero garment.\n"
    "    Example: Foreground subject wears black jeans + top. Background has women in\n"
    "    bright purple sarees. The purple saree is EXCLUDED \u2014 it is worn by background people.\n"
    "  \u2717 Any garment that is NOT worn by the CLOSEST, LARGEST, SHARPEST-FOCUS person in frame\n"
    "  \u2717 A garment that appears in only 1 out of 4 frames (random, not featured)\n"
    "  \u2717 Garments that are partially hidden by hands, bags, or other objects\n"
    "  \u2717 Garments visible only in transition/movement frames (blurred, mid-spin)\n"
    "\n"
    "  FOREGROUND SUBJECT IDENTIFICATION \u2014 DO THIS FIRST:\n"
    "  Before scoring any garment, identify the PRIMARY SUBJECT:\n"
    "  \u2192 The PRIMARY SUBJECT is the person who is: CLOSEST to camera + LARGEST in frame\n"
    "    + in SHARPEST FOCUS. This is always the content creator / model being filmed.\n"
    "  \u2192 ONLY score garments worn by the PRIMARY SUBJECT.\n"
    "  \u2192 All other people in the frame are background \u2014 ignore their clothing entirely."
)

if "FOREGROUND SUBJECT IDENTIFICATION" not in src:
    if OLD_REJECT in src:
        src = src.replace(OLD_REJECT, NEW_REJECT, 1)
        print("[FIX 1] FOREGROUND SUBJECT LOCK added to WHAT TO REJECT")
    else:
        # Try with \r\n
        OLD_REJECT_CRLF = OLD_REJECT.replace("\n", "\r\n")
        NEW_REJECT_CRLF = NEW_REJECT.replace("\n", "\r\n")
        if OLD_REJECT_CRLF in src:
            src = src.replace(OLD_REJECT_CRLF, NEW_REJECT_CRLF, 1)
            print("[FIX 1] FOREGROUND SUBJECT LOCK added (CRLF path)")
        else:
            # Regex fallback
            pattern = r"(WHAT TO REJECT.*?)(PHASE 1)"
            def inject_fg(m):
                return NEW_REJECT + "\n \n\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\nPHASE 1"
            new_src, n = re.subn(pattern, inject_fg, src, flags=re.DOTALL)
            if n:
                src = new_src
                print(f"[FIX 1] Regex injection: {n} replacement(s)")
            else:
                print("[FIX 1] WARNING: Could not find WHAT TO REJECT anchor")
else:
    print("[FIX 1] Already fixed — skipping")

# ─────────────────────────────────────────────────────────────────────────────
# FIX 2 — Restrict Dimension 5 & 6 to foreground subject only
# ─────────────────────────────────────────────────────────────────────────────
OLD_DIM5 = (
    "  DIMENSION 5 \u2014 COLOUR SIGNAL (max 5 pts):\n"
    "     5 pts \u2192 Garment has the highest colour saturation in the scene\n"
    "     3 pts \u2192 Moderate colour presence\n"
    "     1 pt  \u2192 Low saturation, blends into background"
)
NEW_DIM5 = (
    "  DIMENSION 5 \u2014 COLOUR SIGNAL (max 5 pts) [FOREGROUND SUBJECT ONLY]:\n"
    "  NOTE: Only score the PRIMARY SUBJECT'S garment here. Background people's\n"
    "  colorful clothing (e.g. bright sarees in airport background) scores 0 \u2014 ignored.\n"
    "     5 pts \u2192 Garment has bold/saturated colour AND belongs to foreground subject\n"
    "     3 pts \u2192 Moderate colour presence on foreground subject's garment\n"
    "     1 pt  \u2192 Low saturation, muted, or neutral on foreground subject's garment"
)

if "[FOREGROUND SUBJECT ONLY]" not in src:
    if OLD_DIM5 in src:
        src = src.replace(OLD_DIM5, NEW_DIM5, 1)
        print("[FIX 2a] Dimension 5 foreground restriction added")
    else:
        # CRLF
        OLD_DIM5_CRLF = OLD_DIM5.replace("\n", "\r\n")
        NEW_DIM5_CRLF = NEW_DIM5.replace("\n", "\r\n")
        if OLD_DIM5_CRLF in src:
            src = src.replace(OLD_DIM5_CRLF, NEW_DIM5_CRLF, 1)
            print("[FIX 2a] Dimension 5 foreground restriction added (CRLF)")
        else:
            print("[FIX 2a] WARNING: Dim 5 anchor not found")
else:
    print("[FIX 2a] Already restricted — skipping")

# Fix Dimension 6 — restrict attractiveness bonus to foreground only
OLD_DIM6_BOOST = (
    "  ATTRACTIVENESS BOOST RULE:\n"
    "  A garment scoring 5 on Dimension 6 receives a +3 bonus to its raw total score.\n"
    "  This guarantees the most stunning / most highlighted garment wins even if another\n"
    "  garment appears in more frames (e.g. a plain jeans-top vs a sequin lehenga centrepiece)."
)
NEW_DIM6_BOOST = (
    "  ATTRACTIVENESS BOOST RULE:\n"
    "  A garment scoring 5 on Dimension 6 receives a +3 bonus to its raw total score.\n"
    "  CRITICAL RESTRICTION: This bonus ONLY applies to garments worn by the FOREGROUND\n"
    "  PRIMARY SUBJECT. Colorful background clothing (people in background wearing sarees,\n"
    "  suits, etc.) NEVER receives this bonus \u2014 they are excluded entirely.\n"
    "  Example: Foreground subject in black sparkle top + jeans vs background women in\n"
    "  purple sarees \u2192 The jeans+top is scored. The purple sarees are IGNORED."
)

if "CRITICAL RESTRICTION: This bonus ONLY" not in src:
    if OLD_DIM6_BOOST in src:
        src = src.replace(OLD_DIM6_BOOST, NEW_DIM6_BOOST, 1)
        print("[FIX 2b] Dimension 6 background exclusion added")
    else:
        OLD_DIM6_BOOST_CRLF = OLD_DIM6_BOOST.replace("\n", "\r\n")
        NEW_DIM6_BOOST_CRLF = NEW_DIM6_BOOST.replace("\n", "\r\n")
        if OLD_DIM6_BOOST_CRLF in src:
            src = src.replace(OLD_DIM6_BOOST_CRLF, NEW_DIM6_BOOST_CRLF, 1)
            print("[FIX 2b] Dimension 6 restriction added (CRLF)")
        else:
            print("[FIX 2b] WARNING: Dim 6 boost anchor not found")
else:
    print("[FIX 2b] Already restricted — skipping")

# ─────────────────────────────────────────────────────────────────────────────
# FIX 3 — Update Phase 1 header to emphasise foreground-first
# ─────────────────────────────────────────────────────────────────────────────
OLD_PHASE1_HDR = (
    "This is social media fashion content. One garment is being INTENTIONALLY featured.\n"
    "Your mission: identify the HERO GARMENT \u2014 what the creator wants the viewer to notice,\n"
    "remember, and search for after watching."
)
NEW_PHASE1_HDR = (
    "This is social media fashion content. One garment is being INTENTIONALLY featured.\n"
    "Your mission: identify the HERO GARMENT worn by the PRIMARY SUBJECT (the person\n"
    "closest to camera, largest in frame, sharpest focus). Background people and their\n"
    "clothing are INVISIBLE for scoring purposes \u2014 exclude them completely before\n"
    "you score a single dimension."
)

if "Background people and their" not in src:
    if OLD_PHASE1_HDR in src:
        src = src.replace(OLD_PHASE1_HDR, NEW_PHASE1_HDR, 1)
        print("[FIX 3] Phase 1 header updated with foreground-first mandate")
    else:
        OLD_PHASE1_CRLF = OLD_PHASE1_HDR.replace("\n", "\r\n")
        NEW_PHASE1_CRLF = NEW_PHASE1_HDR.replace("\n", "\r\n")
        if OLD_PHASE1_CRLF in src:
            src = src.replace(OLD_PHASE1_CRLF, NEW_PHASE1_CRLF, 1)
            print("[FIX 3] Phase 1 header updated (CRLF)")
        else:
            print("[FIX 3] WARNING: Phase 1 header anchor not found")
else:
    print("[FIX 3] Already updated — skipping")

# ─────────────────────────────────────────────────────────────────────────────
# Write & verify
# ─────────────────────────────────────────────────────────────────────────────
TARGET.write_text(src, encoding="utf-8")
print("\n[FIX] File written.")

checks = {
    "FOREGROUND SUBJECT IDENTIFICATION": "Foreground lock in WHAT TO REJECT",
    "FOREGROUND SUBJECT ONLY":           "Dim 5 restricted to foreground",
    "CRITICAL RESTRICTION: This bonus":  "Dim 6 bonus restricted to foreground",
    "Background people and their":       "Phase 1 header updated",
}
all_ok = True
for key, label in checks.items():
    ok = key in src
    print(f"  {'OK' if ok else 'FAIL'}  {label}")
    if not ok:
        all_ok = False

print("\n" + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED — review manually"))
