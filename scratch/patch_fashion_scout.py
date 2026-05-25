"""
patch_fashion_scout.py
======================
Surgical patcher for Monetization_Metrics/fashion_scout.py
Adds:
  1. DIMENSION 6 (Visual Attractiveness) to Phase 1 scoring
  2. INDUSTRY_NAME generation rules inside Phase 6
  3. industry_name field in JSON output schema
  4. industry_name enforcement in the Python pipeline
"""
import re, sys, shutil
from pathlib import Path

TARGET = Path(r"d:\Autonomous Multimedia Transformation & Compilation Engine (AMTCE)\Monetization_Metrics\fashion_scout.py")
BACKUP = TARGET.with_suffix(".py.bak_patch")

# ── Safety: back up first ─────────────────────────────────────────────────────
shutil.copy2(TARGET, BACKUP)
print(f"[PATCH] Backup created: {BACKUP}")

src = TARGET.read_text(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 1 — Add DIMENSION 6 after DIMENSION 5 block and fix dominance formula
# ─────────────────────────────────────────────────────────────────────────────
DIM5_ANCHOR = "DIMENSION 5"
DIM6_BLOCK = """
  DIMENSION 6 \u2014 VISUAL ATTRACTIVENESS & HIGHLIGHT DOMINANCE (max 5 pts + bonus):
  Ask: "What is the FIRST garment the viewer\u2019s eye snaps to?"
  The MOST eye-catching, most highlighted garment must always win.
     5 pts \u2192 Undisputed visual centrepiece: heavily embellished (sequins / mirror work /
             zari / gota / feathers / stones) OR bold saturated colour that pops against
             background OR dramatic silhouette (flared lehenga / ballgown / tiered ruffle
             maxi) \u2014 AND placed in sharp focus at frame centre.
     4 pts \u2192 Clearly more attractive than other garments in frame \u2014 one signal above present
     3 pts \u2192 Moderately decorative \u2014 some embellishment or colour pop but not flashiest
     2 pts \u2192 Plain garment, minimal visual interest
     1 pt  \u2192 Dull, muted, partially covered, or visually suppressed

  ATTRACTIVENESS BOOST RULE:
  A garment scoring 5 on Dimension 6 receives a +3 bonus to its raw total score.
  This guarantees the most stunning / most highlighted garment wins even if another
  garment appears in more frames (e.g. a plain jeans-top vs a sequin lehenga centrepiece).

"""

# Find the DIMENSION 5 section and insert DIM6 before the DOMINANCE RULE line
if "DIMENSION 6" not in src:
    # Find position just before "DOMINANCE RULE:" inside the prompt string
    pattern = r'(DOMINANCE RULE:\n  The garment with the HIGHEST total score across all 5 dimensions)'
    replacement = DIM6_BLOCK + r'DOMINANCE RULE:\n  The garment with the HIGHEST total score across all 6 dimensions'
    new_src, n = re.subn(pattern, replacement, src)
    if n:
        # Also fix the tiebreak rules and denominator
        new_src = new_src.replace(
            "If still tied: prefer the UPPER-BODY garment (dress / top / blouse > trousers).",
            "If still tied: prefer the garment with higher Visual Attractiveness (Dimension 6) score.\n"
            "  If still tied: prefer the UPPER-BODY garment (dress / top / blouse > trousers)."
        )
        new_src = new_src.replace(
            "dominance_score in the JSON = (total_score / 25) to produce a 0.0\u20131.0 value.",
            "dominance_score in the JSON = (total_score / 28) to produce a 0.0\u20131.0 value.\n"
            "  (Max raw score = 25 base + 3 attractiveness bonus = 28)"
        )
        src = new_src
        print(f"[PATCH 1] DIMENSION 6 added ({n} replacement)")
    else:
        print("[PATCH 1] WARNING: DOMINANCE RULE anchor not found \u2014 skipping Dim 6 insert")
else:
    print("[PATCH 1] DIMENSION 6 already present \u2014 skipping")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 2 — Add INDUSTRY_NAME block inside Phase 6 (after the Phase 6 header)
# ─────────────────────────────────────────────────────────────────────────────
INDUSTRY_BLOCK = """
INDUSTRY_NAME GENERATION [MANDATORY \u2014 new field]:
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
industry_name = the OFFICIAL TRADE/INDUSTRY CATEGORY used by:
  - Wholesale distributors (Meesho / Glowroad / IndiaMART)
  - Myntra / Nykaa Fashion internal category taxonomy
  - FDCI / Lakme Fashion Week buyers and export houses

RULES:
  \u2022 2\u20134 words maximum \u2014 these are category codes, not product titles
  \u2022 Must match the hero garment chosen by Phase 1 + Dimension 6
  \u2022 Never start with a colour word
  \u2022 Use ONLY real industry-recognised category names:

  WESTERN WEAR:
    "Bodycon Dress" | "Slip Dress" | "Shirt Dress" | "Wrap Dress"
    "Fit-and-Flare Dress" | "Maxi Dress" | "Midi Dress" | "Mini Dress"
    "Co-ord Set" | "Jumpsuit" | "Blazer Dress" | "Crop Top" | "Cami Top"
    "Tube Top" | "Bustier Top" | "High-Waist Jeans" | "Wide-Leg Trousers"
    "Cargo Pants" | "Biker Shorts" | "Sports Bra" | "Leggings"
    "Athleisure Set" | "Bomber Jacket" | "Trench Coat"

  INDIAN ETHNIC WEAR:
    "Saree" | "Lehenga Choli" | "Anarkali Kurta" | "Salwar Kameez"
    "Sharara Set" | "Gharara Set" | "Palazzo Set" | "Patiala Set"
    "Kurti" | "Kaftan" | "Indo-Western Gown" | "Concept Saree"
    "Dhoti Pants Set" | "Jacket Lehenga" | "Angrakha Kurta"

  OCCASION-CODED:
    "Bridal Lehenga" | "Cocktail Dress" | "Party Wear Gown"
    "Reception Saree" | "Office Wear Set" | "Resort Wear Dress"
    "Mehendi Outfit" | "Sangeet Co-ord" | "Fusion Bridesmaid Set"

  NEVER use: "Indian Outfit" / "Fashion Wear" / "Ethnic Dress" / colour-first names
  RULE: industry_name MUST match the garment Phase 1 identified as visually dominant.

"""

if "INDUSTRY_NAME GENERATION" not in src:
    # Insert right after "THIS IS WHERE wear_name AND outfit_description ARE GENERATED."
    anchor = "THIS IS WHERE wear_name AND outfit_description ARE GENERATED."
    if anchor in src:
        src = src.replace(anchor, anchor + "\n" + INDUSTRY_BLOCK, 1)
        print("[PATCH 2] INDUSTRY_NAME block inserted")
    else:
        # Fallback: insert after PHASE 6 header line
        anchor2 = "PHASE 6 \u2014 RETAIL PRODUCT NAMING ENGINE"
        if anchor2 in src:
            # find the next occurrence of WEAR_NAME GENERATION and insert before it
            src = src.replace("WEAR_NAME GENERATION:", INDUSTRY_BLOCK + "\nWEAR_NAME GENERATION:", 1)
            print("[PATCH 2] INDUSTRY_NAME block inserted via fallback anchor")
        else:
            print("[PATCH 2] WARNING: Phase 6 anchor not found \u2014 manual insertion needed")
else:
    print("[PATCH 2] INDUSTRY_NAME block already present \u2014 skipping")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 3 — Add industry_name to JSON output schema in the prompt
# ─────────────────────────────────────────────────────────────────────────────
OLD_SCHEMA_TAIL = '  "vibe": "",\n  "genre": "",\n  "wear_name": ""\n}'
NEW_SCHEMA_TAIL = '  "vibe": "",\n  "genre": "",\n  "wear_name": "",\n  "industry_name": ""\n}'

if '"industry_name"' not in src:
    if OLD_SCHEMA_TAIL in src:
        src = src.replace(OLD_SCHEMA_TAIL, NEW_SCHEMA_TAIL, 1)
        print("[PATCH 3] industry_name added to JSON schema")
    else:
        # Try relaxed match with \r\n
        OLD2 = '  "vibe": "",\r\n  "genre": "",\r\n  "wear_name": ""\r\n}'
        NEW2 = '  "vibe": "",\r\n  "genre": "",\r\n  "wear_name": "",\r\n  "industry_name": ""\r\n}'
        if OLD2 in src:
            src = src.replace(OLD2, NEW2, 1)
            print("[PATCH 3] industry_name added to JSON schema (CRLF path)")
        else:
            print("[PATCH 3] WARNING: JSON schema tail not found \u2014 trying regex")
            src, n = re.subn(
                r'("wear_name"\s*:\s*""\s*\n\s*\})',
                '"wear_name": "",\n  "industry_name": ""\n}',
                src
            )
            print(f"[PATCH 3] Regex replaced {n} occurrences")
else:
    print("[PATCH 3] industry_name already in schema \u2014 skipping")

# ─────────────────────────────────────────────────────────────────────────────
# PATCH 4 — Enforce industry_name in Python pipeline (after wear_name enforcement)
# ─────────────────────────────────────────────────────────────────────────────
ENFORCE_ANCHOR = 'logger.info(\n                        "\ud83d\udd27 [WEAR_NAME_ENFORCE] Upgraded weak name'
ENFORCE_ANCHOR2 = "[WEAR_NAME_ENFORCE] Upgraded weak name"

INDUSTRY_ENFORCE = """
            # \u2500\u2500 INDUSTRY_NAME ENFORCEMENT (Python layer) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
            # Gemini supplies industry_name in the JSON. If it's missing or blank,
            # infer it from primary_category so downstream always has a clean label.
            _ind = data.get("industry_name", "").strip()
            if not _ind:
                _cat = (
                    data.get("attributes", {})
                        .get("classification", {})
                        .get("primary_category", "")
                ).strip().title()
                _sub = (
                    data.get("attributes", {})
                        .get("classification", {})
                        .get("sub_category", "")
                ).strip()
                # Use sub_category first word(s) if it's short, else primary_category
                if _sub and len(_sub.split()) <= 3:
                    _ind = _sub.title()
                elif _cat:
                    _ind = _cat
                data["industry_name"] = _ind
                if _ind:
                    logger.info("[FASHION_SCOUT] industry_name inferred: '%s'", _ind)
            else:
                logger.info("[FASHION_SCOUT] industry_name from Gemini: '%s'", _ind)
"""

if "industry_name inferred" not in src:
    # Insert right before the brand enrichment pipeline block
    brand_anchor = "# \u2500\u2500 BRAND ENRICHMENT PIPELINE"
    if brand_anchor in src:
        src = src.replace(brand_anchor, INDUSTRY_ENFORCE + "\n            " + brand_anchor, 1)
        print("[PATCH 4] industry_name pipeline enforcement added")
    else:
        # Fallback: insert after the wear_name enforcement closing comment
        anchor4 = "# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n            # \u2500\u2500 BRAND ENRICHMENT"
        if anchor4 in src:
            src = src.replace(anchor4, INDUSTRY_ENFORCE + anchor4, 1)
            print("[PATCH 4] industry_name enforcement inserted via fallback")
        else:
            print("[PATCH 4] WARNING: Brand anchor not found \u2014 skipping pipeline enforcement")
else:
    print("[PATCH 4] Pipeline enforcement already present \u2014 skipping")

# ─────────────────────────────────────────────────────────────────────────────
# Write patched file
# ─────────────────────────────────────────────────────────────────────────────
TARGET.write_text(src, encoding="utf-8")
print("\n[PATCH] All done. File written: " + str(TARGET))
print("[PATCH] Backup at:             " + str(BACKUP))
print("\nVerification checks:")
chk_dim6  = "DIMENSION 6" in src
chk_ind   = '"industry_name"' in src
chk_block = "INDUSTRY_NAME GENERATION" in src
chk_pipe  = "industry_name inferred" in src
print(f"  DIMENSION 6 present:       {chk_dim6}")
print(f"  industry_name in schema:   {chk_ind}")
print(f"  INDUSTRY_NAME block:       {chk_block}")
print(f"  Pipeline enforcement:      {chk_pipe}")
