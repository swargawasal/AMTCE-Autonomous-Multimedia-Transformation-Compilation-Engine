"""
organic_sales_copy.py
----------------------
2026 High-Conversion Instagram Caption Engine

Layout (enforced by template):
  Line 1:   #AD + scroll-stop hook              ← ASCI/FTC compliance
  Line 2:   Title / product pick line
  Line 3-5: 3 bullet-point benefits (fabric, fit, occasion)
  Line 6:   Price anchoring + scarcity
  Line 7:   CTA — "Comment KEYWORD" → DM automation
  Line 8:   Amazon Associate disclaimer
  Last:     Niche hashtags (max 5)

Key rules:
  - NO celebrity / real public figure names (CCPA false-endorsement risk)
  - NO outbound links in captions (Instagram reach penalty)
  - #AD MUST be the first two characters of every caption
  - Template rotates across 3 variants to avoid repetition spam flags

Usage:
    from Text_Modules.organic_sales_copy import build_organic_caption
    caption = build_organic_caption(product_data)
"""

import random
import logging
import os
from typing import Dict, Any, Optional, List

logger = logging.getLogger("organic_sales_copy")

# ─────────────────────────────────────────────────────────────────────────────
# 3 TEMPLATE VARIANTS — rotated to avoid Instagram repetition detection
# ─────────────────────────────────────────────────────────────────────────────
# Available placeholders:
#   {hook}            — scroll-stop opening line
#   {title}           — product name / persona pick line
#   {benefit_1/2/3}  — scannable bullet points
#   {price_original}  — MRP
#   {price_sale}      — discounted price
#   {discount}        — % off
#   {scarcity}        — low-stock / orders-in-24h line (can be empty)
#   {keyword}         — comment trigger word (e.g. DRESS, LINK)
#   {amazon_line}     — Amazon Associate disclaimer
#   {hashtags}        — niche hashtag string

_TEMPLATES = [
    # ── Template A ──────────────────────────────────────────────────────────
    """\
#AD {hook}

{title}
✦ {benefit_1}
✦ {benefit_2}
✦ {benefit_3}

MRP ₹{price_original} → ₹{price_sale} today ({discount}% off)
{scarcity}

Comment "{keyword}" and I'll DM the link instantly 👇

{amazon_line}
{hashtags}""",

    # ── Template B ──────────────────────────────────────────────────────────
    """\
#AD — {hook}

{title}

What actually makes this worth it:
— {benefit_1}
— {benefit_2}
— {benefit_3}

Was ₹{price_original}, now ₹{price_sale} ({discount}% off).
{scarcity}

Drop "{keyword}" in the comments for the link 👇

{amazon_line}
{hashtags}""",

    # ── Template C ──────────────────────────────────────────────────────────
    """\
#AD | Real talk — {hook}

{title}

Here's why it stayed in my cart:
{benefit_1}. {benefit_2}. {benefit_3}.

₹{price_original} marked down to ₹{price_sale} — that's {discount}% off.
{scarcity}

Comment "{keyword}" and I'll send the link 👇

{amazon_line}
{hashtags}""",
]

# Sequential rotation index (module-level state) — more predictable than pure random
_template_index = 0


def _next_template() -> str:
    """Returns templates in round-robin order for auditable rotation."""
    global _template_index
    rotation = os.getenv("CAPTION_TEMPLATE_ROTATION", "sequential").lower()
    if rotation == "random":
        return random.choice(_TEMPLATES)
    tmpl = _TEMPLATES[_template_index % len(_TEMPLATES)]
    _template_index += 1
    return tmpl


# ─────────────────────────────────────────────────────────────────────────────
# VIBE → HOOK FALLBACKS
# ─────────────────────────────────────────────────────────────────────────────
_VIBE_HOOKS = {
    "LUXURY": [
        "Old-money look, no old-money price 😏",
        "Looks ₹20,000. Isn't ₹20,000 😱",
        "The room noticed before anyone spoke 👀",
    ],
    "STREETWEAR": [
        "This dropped and sold out in 6 hours 🔥",
        "Underground drip that hit the algorithm 🔥",
        "One restock. Right now 👀",
    ],
    "ETHNIC": [
        "Festive season sorted — and it ships in 2 days 📦",
        "The most-saved ethnic look this season ✨",
        "Handcrafted detail you won't find on the high street 🌸",
    ],
    "CASUAL": [
        "The lazy-girl outfit that never misses ✨",
        "Office to dinner without changing — that's the point",
        "Comfortable, versatile, photographs beautifully 📸",
    ],
    "ATHLEISURE": [
        "Gymshark energy. Indian price 💪",
        "From workout to outing without changing ⚡",
        "Best-seller in the fitness category. Already rated 4.8★",
    ],
    "FORMAL": [
        "Power dressing without the designer price tag 💼",
        "7 seconds is all it takes. This outfit wins those 7 😏",
        "Boardroom-ready at a fraction of designer prices",
    ],
    "DEFAULT": [
        "This almost didn't make it to the feed — too good 😏",
        "3 compliments in 10 minutes 🤯",
        "Everyone asks where this is from 👇",
    ],
}

_SCARCITY_LINES = [
    "M & L sizes low stock — {orders}+ orders in 24h",
    "Selling fast — {orders}+ ordered in the last 24h",
    "Low stock alert — only a few left at this price",
    "{orders}+ orders in 24h. Stock dropping fast.",
]


def _extract_vibe_key(vibe: str) -> str:
    v = (vibe or "DEFAULT").upper().strip()
    for key in _VIBE_HOOKS:
        if key in v:
            return key
    if any(k in v for k in ("FORMAL", "OFFICE", "PROFESSIONAL")):
        return "FORMAL"
    if any(k in v for k in ("ETHNIC", "FESTIVE", "BOHEMIAN")):
        return "ETHNIC"
    return "DEFAULT"


def _build_benefits(fashion_data: Dict[str, Any], product_name: str) -> List[str]:
    """
    Extracts 3 scannable benefit bullet points from fashion_scout data.
    Falls back to generic fabric / fit / occasion lines if data is thin.
    """
    attrs = fashion_data.get("attributes", {}) or {}
    fab = (attrs.get("fabric", {}) or {}).get("primary_material", "")
    fit = (attrs.get("fit", {}) or {}).get("fit_type", "")
    cls = (attrs.get("classification", {}) or {})
    occasion = cls.get("occasion", "")
    sub_cat = cls.get("sub_category", "")

    benefits = []
    if fab:
        benefits.append(f"{fab} fabric — breathable and premium feel")
    if fit:
        benefits.append(f"{fit} fit — true to size")
    if occasion:
        benefits.append(f"perfect for {occasion}")

    # Pad to 3 with generic lines
    fallbacks = [
        f"pairs with everything in your wardrobe",
        f"lightweight and comfortable all day",
        f"style that photographs beautifully",
        f"versatile enough for office and evenings",
    ]
    while len(benefits) < 3:
        benefits.append(fallbacks.pop(0))

    return benefits[:3]


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def build_organic_caption(
    fashion_data: Dict[str, Any],
    product_name: Optional[str] = None,
    price: Optional[float] = None,
    offer_price: Optional[float] = None,
    dm_keyword: str = "LINK",
    hashtags: str = "",
    scarcity_orders: int = 200,
) -> str:
    """
    Builds a 2026 high-conversion Instagram caption.

    Args:
        fashion_data:      Dict from FashionScout.scout_outfit() — full result.
        product_name:      Optional product name override.
        price:             MRP (original price). If None, price block is skipped.
        offer_price:       Discounted price. Used only if price is also set.
        dm_keyword:        Comment trigger word (default: "LINK").
        hashtags:          Pre-built hashtag string (max 5 tags enforced here).
        scarcity_orders:   Number to show in scarcity line (e.g. "200+ orders in 24h").

    Returns:
        Ready-to-post caption string. #AD is always the first two characters.

    Raises:
        ValueError: If a celebrity name is found in fashion_data (CCPA/ASCI risk).
    """
    # ── Celebrity name guard (CCPA false-endorsement protection) ──────────────
    _celeb_fields = [
        fashion_data.get("celebrity_name", ""),
        fashion_data.get("celebrity", ""),
        str(fashion_data.get("attributes", {}).get("celebrity", "")),
    ]
    if any(f.strip() for f in _celeb_fields):
        raise ValueError(
            "[ORGANIC_SALES] Celebrity name detected in fashion_data — "
            "this is a CCPA/ASCI false-endorsement risk. "
            "Use only your own name or a fictional persona."
        )

    # ── Extract fields ─────────────────────────────────────────────────────────
    attrs          = fashion_data.get("attributes", {}) or {}
    classification = attrs.get("classification", {}) or {}
    vibe           = fashion_data.get("vibe", "DEFAULT").strip()
    sub_category   = classification.get("sub_category", "").strip()
    outfit_description = fashion_data.get("outfit_description", "").strip()
    primary_category   = classification.get("primary_category", "").strip()

    if not product_name:
        if sub_category:
            product_name = sub_category.title()
        elif outfit_description:
            product_name = " ".join(outfit_description.split()[:4]).title()
        else:
            product_name = primary_category.title() or "this look"

    vibe_key = _extract_vibe_key(vibe)

    # ── Hook ───────────────────────────────────────────────────────────────────
    hook = random.choice(_VIBE_HOOKS.get(vibe_key, _VIBE_HOOKS["DEFAULT"]))

    # ── Title line (persona name only — no real celebrity) ───────────────────
    persona = os.getenv("IG_OWNER_NAME", "").strip() or "My"
    title = f"{persona} pick: {product_name.lower()}"

    # ── Benefits ───────────────────────────────────────────────────────────────
    benefits = _build_benefits(fashion_data, product_name)

    # ── Price block ────────────────────────────────────────────────────────────
    price_original_str = "—"
    price_sale_str     = "—"
    discount_str       = "—"

    if price and price > 0:
        price_original_str = f"{int(price):,}"
        if offer_price and offer_price > 0 and offer_price < price:
            price_sale_str = f"{int(offer_price):,}"
            discount_str   = str(int(((price - offer_price) / price) * 100))
        else:
            price_sale_str = price_original_str
            discount_str   = "0"

    # ── Scarcity ───────────────────────────────────────────────────────────────
    scarcity = random.choice(_SCARCITY_LINES).format(orders=scarcity_orders)

    # ── Hashtags: cap at 5, strip internal tracking tags ─────────────────────
    _raw_tags = hashtags.strip().split() if hashtags else []
    _clean_tags = [t for t in _raw_tags if not t.lower().startswith("#vid_")][:5]
    hashtag_str = " ".join(_clean_tags)

    # ── Amazon disclaimer ──────────────────────────────────────────────────────
    amazon_line = "As an Amazon Associate I earn from qualifying purchases."

    # ── Assemble caption from rotating template ────────────────────────────────
    template = _next_template()
    caption = template.format(
        hook           = hook,
        title          = title,
        benefit_1      = benefits[0],
        benefit_2      = benefits[1],
        benefit_3      = benefits[2],
        price_original = price_original_str,
        price_sale     = price_sale_str,
        discount       = discount_str,
        scarcity       = scarcity,
        keyword        = dm_keyword.upper(),
        amazon_line    = amazon_line,
        hashtags       = hashtag_str,
    )

    # ── Instagram hard limit: 2200 chars ──────────────────────────────────────
    if len(caption) > 2200:
        caption = caption[:2197] + "..."

    logger.info(
        f"[ORGANIC_SALES] Caption built | vibe={vibe_key} | "
        f"product='{product_name}' | template={(_template_index - 1) % len(_TEMPLATES)} "
        f"| chars={len(caption)}"
    )

    return caption


def build_youtube_description(
    fashion_data: Dict[str, Any],
    product_name: Optional[str] = None,
    price: Optional[float] = None,
    offer_price: Optional[float] = None,
    affiliate_link: Optional[str] = None,
    dm_keyword: str = "LINK",
    hashtags: str = "",
) -> str:
    """
    YouTube description variant. Links ARE clickable on YouTube,
    so the affiliate link is injected directly instead of via DM.
    """
    base = build_organic_caption(
        fashion_data=fashion_data,
        product_name=product_name,
        price=price,
        offer_price=offer_price,
        dm_keyword=dm_keyword,
        hashtags=hashtags,
    )

    if affiliate_link:
        youtube_cta = (
            f"🛒 Shop Now: {affiliate_link}\n\n"
            f'Or comment "{dm_keyword}" on Instagram for the DM link!'
        )
        base = base.replace(
            f'Comment "{dm_keyword.upper()}" and I\'ll DM the link instantly 👇',
            youtube_cta,
        ).replace(
            f'Drop "{dm_keyword.upper()}" in the comments for the link 👇',
            youtube_cta,
        ).replace(
            f'Comment "{dm_keyword.upper()}" and I\'ll send the link 👇',
            youtube_cta,
        )

    return base
