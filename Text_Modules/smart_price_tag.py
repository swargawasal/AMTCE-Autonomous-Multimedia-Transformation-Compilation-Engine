import os
import time
import uuid
import logging
import random
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from anchor_helper import compute_anchor

logger = logging.getLogger("smart_price_tag")

# --- PREMIUM PRICE PSYCHOLOGY ENGINE ---
# Philosophy: At ₹7k+, the brain stops thinking "cheap clone" and starts thinking
# "investment." Multiples of 7 feel auspicious in Indian culture, AND they feel
# "calculated" — like a brand set this price intentionally. That subconscious signal
# = trust = click.
#
# Pattern-break rule: Every 7th invocation, output a "human" number (e.g., ₹8,500)
# to prevent the viewer's brain from detecting the pattern and losing trust.
#
# Category → (brand_mrp_min, brand_mrp_max, clone_min, clone_max)
# ALL clone prices are now 7,000 INR minimum — below this is mismatch territory.
CATEGORY_PRICE_TIERS = {
    "Saree":        (14999,  34999,   7999,  15999),
    "Lehenga":      (21999,  55999,   9999,  21999),
    "Kurti":        (7999,   18999,   7000,   9999),
    "Anarkali":     (11999,  27999,   7499,  13999),
    "Salwar":       (9999,   21999,   7000,  10999),
    "Dress":        (12999,  29999,   7999,  14999),
    "Gown":         (21999,  55999,   9999,  21999),
    "Western":      (10999,  24999,   7499,  13999),
    "Jumpsuit":     (9999,   21999,   7000,  11999),
    "Coord_Set":    (11999,  27999,   7499,  13999),
    "Blazer":       (13999,  34999,   7999,  15999),
    "Jewellery":    (9999,   27999,   7000,  13999),
    "Bag":          (14999,  49999,   8999,  21999),
    "Heels":        (9999,   24999,   7000,  12999),
    "Sunglasses":   (7999,   19999,   7000,   9999),
    "Perfume":      (8999,   21999,   7000,  10999),
    "Watches":      (18999,  59999,   9999,  24999),
    "Makeup":       (7999,   16999,   7000,   9499),
    "Skincare":     (7999,   18999,   7000,   9999),
    "default":      (12999,  29999,   7999,  14999),
}

# ── Multiples-of-7 Premium Price Engine ───────────────────────────────────────
# Global counter — tracks how many premium prices have been generated this session.
# Every 7th price breaks the pattern with a "human" number.
_premium_price_counter = 0

def _nearest_multiple_of_7(value: int, base: int = 7000) -> int:
    """Round value to the nearest multiple of 7 above the base floor."""
    # Work in units of 7 above the 7000 floor
    above_base = max(0, value - base)
    rounded = round(above_base / 7) * 7
    return base + rounded

def _premium_price(lo: int, hi: int) -> int:
    """
    Generate a premium-psychology price in [lo, hi].
    Every 7th call breaks the pattern with a human-looking number.
    """
    global _premium_price_counter
    _premium_price_counter += 1

    # Every 7th generation — pattern break for authenticity
    if _premium_price_counter % 7 == 0:
        # Use a "human" number: round hundreds with a psychological 99/49/00 tail
        raw = random.randint(lo, hi)
        tails = [0, 99, 49, 500]
        tail = random.choice(tails)
        human_price = (raw // 1000) * 1000 + tail
        human_price = max(lo, min(hi, human_price))
        logger.debug(f"[PREMIUM_PRICE] Pattern break #{_premium_price_counter}: ₹{human_price:,}")
        return human_price

    # Normal path: multiple of 7
    raw = random.randint(lo, hi)
    price = _nearest_multiple_of_7(raw, base=max(7000, lo))
    price = max(lo, min(hi, price))
    logger.debug(f"[PREMIUM_PRICE] Multiple-of-7 #{_premium_price_counter}: ₹{price:,}")
    return price


# Desire hooks — urgency + price-drop psychology (no "alternative/clone" language)
URGENCY_LINES = [
    "✨ Same look. Amazon has the original for way less 👇",
    "💛 Original market price is above ↑ Amazon price is below 🔥",
    "👀 This is what influencers actually paid vs what you can pay 👇",
    "🔥 Original retail was ₹{celeb_price}. Not anymore — check link 🔓",
    "💫 Original price ₹{celeb_price}. Amazon has it at ₹{clone_price} right now 😱",
    "🛍️ Grab it at Amazon before the price goes back up ⏳",
    "✨ Original Price struck above. Amazon deals don't last — link below 👇",
    "💥 Original ₹{celeb_price}. You pay ₹{clone_price}. That's the deal 👇",
    "👑 Market value: ₹{celeb_price}. Amazon offering: ₹{clone_price}. No contest.",
    "🎯 400+ buyers paid original price. You don't have to. 👇",
]

# ── Pipeline Hook Cache ───────────────────────────────────────────────
# Populated by monetization_brain after its master Gemini call.
# Downstream modules (smart_price_tag, community_promoter) read from
# this cache to avoid making redundant individual Gemini API calls.
import threading

# ── Pipeline Hook Cache ───────────────────────────────────────────────
# Populated by monetization_brain after its master Gemini call.
# Downstream modules (smart_price_tag, community_promoter) read from
# this cache to avoid making redundant individual Gemini API calls.
# Uses threading.local() so concurrent video processing threads don't mix hooks.
_pipeline_state = threading.local()


def set_pipeline_hooks(hooks: dict) -> None:
    """Called by monetization_brain after its master Gemini call completes."""
    if not hasattr(_pipeline_state, "hook_cache"):
        _pipeline_state.hook_cache = {}
    _pipeline_state.hook_cache.clear()
    _pipeline_state.hook_cache.update({k: v for k, v in hooks.items() if v})
    logger.info(f"[HOOK_CACHE] Populated with keys: {list(_pipeline_state.hook_cache.keys())}")


def _read_from_pipeline_cache(key: str) -> str | None:
    """Returns a cached hook string or None if not available."""
    if not hasattr(_pipeline_state, "hook_cache"):
        return None
    return _pipeline_state.hook_cache.get(key)


def generate_telegram_hook(
    editorial_script: str = "",
    outfit_description: str = "",
    vibe: str = "",
    item_category: str = "default",
) -> str | None:
    """
    Generate a business-strategic, craving-inducing Telegram hook.
    PRIORITY: reads from the shared pipeline cache populated by monetization_brain
    (no extra Gemini call). Falls back to its own Gemini call only on cache miss.
    """
    # ── Tier 0: Read from pipeline cache (set by monetization_brain master call) ─
    _cached = _read_from_pipeline_cache("telegram_hook")
    if _cached:
        logger.info("[HOOK_GEN] Using cached master telegram hook — no extra Gemini call.")
        return _cached

    # ── Tier 1: Own Gemini call as fallback (cache miss / brain didn't run) ───
    _context = (outfit_description or editorial_script or "").strip()
    if not _context:
        return None

    try:
        from Intelligence_Modules.gemini_governor import gemini_router
        if not gemini_router:
            return None

        prompt = (
            f"You are a viral fashion commerce copywriter for Indian Instagram Reels and Telegram.\n"
            f"Content: {_context[:400]}\n"
            f"Category: {item_category}\n"
            f"Vibe: {vibe or 'TRENDY'}\n\n"
            f"Write ONE punchy Telegram caption hook (max 20 words) that:\n"
            f"  - Creates DESIRE and FOMO — make the reader CRAVE clicking the Amazon link\n"
            f"  - Is specific to THIS outfit/content (mention fabric, color, or style)\n"
            f"  - Uses strong action words or sensory language\n"
            f"  - Includes 1-2 perfectly placed emojis\n"
            f"  - Sounds like a top influencer/brand copywriter, NOT a bot\n"
            f"  - MUST NOT say 'witness greatness', 'stop scrolling', or be generic\n\n"
            f"Reply with ONLY the hook text. No quotes, no extra text.\n"
            f"Example: This velvet co-ord is what dreams are made of — and it's ₹999 on Amazon 🔥"
        )

        result = gemini_router.generate(
            task_type="master",
            prompt=prompt,
            module_name="telegram_hook",
            gen_config={"temperature": 0.85, "max_output_tokens": 60},
        )

        if result:
            hook = str(result).strip().strip('"').strip("'")
            _generic = ["stop scrolling", "witness", "greatness", "ruk jao", "scroll"]
            if hook and len(hook.split()) >= 4 and not any(g in hook.lower() for g in _generic):
                logger.info(f"[HOOK_GEN] Fallback Gemini hook: {hook}")
                return hook

    except Exception as _e:
        logger.warning(f"[HOOK_GEN] Fallback Gemini hook generation failed: {_e}")

    return None

def estimate_price_with_gemini(
    outfit_description: str,
    vibe: str,
    item_category: str = "default",
) -> dict:
    """
    [MODIFIED] Gemini pricing call destroyed per user request to save API calls.
    Returns realistic prices based on category-tier logic (the legacy fallback).
    """
    tiers = CATEGORY_PRICE_TIERS.get(item_category, CATEGORY_PRICE_TIERS["default"])
    cel_min, cel_max, clone_min, clone_max = tiers

    # ── Fallback: premium category-tier (now primary) ───────────────────────
    clone_rand  = _premium_price(clone_min, clone_max)
    # Brand MRP: 1.5x–2.5x clone (believable premium range at 7k+ price points)
    cel_mult    = random.uniform(1.5, 2.5)
    cel_rand    = _premium_price(
        max(cel_min, int(clone_rand * 1.5)),
        max(cel_max, int(clone_rand * 2.5)),
    )
    cel_rand = max(cel_min, min(cel_max, cel_rand))
    logger.info(
        f"[PRICE_ESTIMATOR] Premium tier generated: brand_mrp=₹{cel_rand:,} clone=₹{clone_rand:,} "
        f"ratio={cel_rand/clone_rand:.1f}x counter={_premium_price_counter}"
    )
    return {"celebrity_price": cel_rand, "clone_price": clone_rand, "source": "premium_tier"}


def get_price_display(
    item_category: str,
    gemini_celebrity_price: int = None,
    *,
    precomputed: dict = None,
) -> dict:
    """
    Returns price psychology data for overlay + Telegram message.

    Priority:
      1. precomputed dict (already estimated earlier in the pipeline) → use it directly.
      2. gemini_celebrity_price hint → plug into tier clone range.
      3. Category-tier random (legacy fallback, ratio-capped at 4x).

    Pass `precomputed` from profile_data['price_data'] to guarantee the
    video overlay and the Telegram message always show identical prices.
    """
    tiers = CATEGORY_PRICE_TIERS.get(item_category, CATEGORY_PRICE_TIERS["default"])
    cel_min, cel_max, clone_min, clone_max = tiers

    if precomputed:
        celebrity_price = precomputed["celebrity_price"]
        clone_price     = precomputed["clone_price"]
    elif gemini_celebrity_price and cel_min * 0.5 <= gemini_celebrity_price <= cel_max * 2:
        # Snap the Gemini hint to nearest multiple-of-7 as well
        celebrity_price = _nearest_multiple_of_7(gemini_celebrity_price, base=max(7000, cel_min))
        celebrity_price = max(cel_min, min(cel_max, celebrity_price))
        clone_price = _premium_price(clone_min, clone_max)
    else:
        clone_price     = _premium_price(clone_min, clone_max)
        cel_raw         = int(clone_price * random.uniform(1.5, 2.5))
        celebrity_price = _premium_price(
            max(cel_min, cel_raw - 2000),
            max(cel_min + 1000, cel_raw + 2000),
        )
        celebrity_price = max(cel_min, min(cel_max, celebrity_price))

    savings = celebrity_price - clone_price
    # Cap at 75% — within 2x-4x range this is naturally 60-75%, all believable
    raw_pct = int((savings / celebrity_price) * 100)
    savings_pct = min(raw_pct, 75)
    urgency = random.choice(URGENCY_LINES)
    # Resolve any price placeholders in the hook text
    urgency = urgency.replace("{clone_price}", f"₹{clone_price:,}")
    urgency = urgency.replace("{celeb_price}", f"₹{celebrity_price:,}")

    # Format Indian number style (1,29,999 style)
    def _fmt(n):
        s = str(n)
        if len(s) <= 3:
            return s
        # Indian: last 3 then pairs
        result = s[-3:]
        s = s[:-3]
        while s:
            result = s[-2:] + "," + result
            s = s[:-2]
        return result.lstrip(",")

    display_text = (
        f"Original Price ₹{_fmt(celebrity_price)}+\n"
        f"Amazon Price → ₹{_fmt(clone_price)}\n"
        f"Save up to {savings_pct}%\n"
        f"{urgency}"
    )

    return {
        "celebrity_price": celebrity_price,
        "clone_price": clone_price,
        "savings": savings,
        "savings_pct": savings_pct,
        "urgency": urgency,
        "display_text": display_text,
        "clone_price_formatted": f"₹{_fmt(clone_price)}",
        "celebrity_price_formatted": f"₹{_fmt(celebrity_price)}+",
        "celebrity_price_label": f"Original Price ₹{_fmt(celebrity_price)}+",
    }



class SmartPriceTag:
    def __init__(self):
        self.font_path = os.path.abspath("assets/fonts/Inter-Bold.ttf")
        if not os.path.exists(self.font_path):
            self.font_path = "arial.ttf"

    def _draw_glass_box(self, draw, x, y, w, h, radius=15):
        """Draws a premium glass-morphism background."""
        draw.rounded_rectangle([x, y, x+w, y+h], radius=radius, fill=(20, 20, 20, 220))
        draw.rounded_rectangle([x, y, x+w, y+h], radius=radius, outline=(255, 255, 255, 40), width=1)

    def _draw_strikethrough(self, draw, x, y, w, color=(180, 180, 180, 200)):
        """Draws a strikethrough line over the celebrity price text."""
        mid_y = y + 8
        draw.line([(x, mid_y), (x + w, mid_y)], fill=color, width=2)

    def compute_anchor(self, human_box, face_box=None):
        """
        Adaptive Anchor System: Calculates a safe y-coordinate for the tag.
        Targets the garment chest/mid-torso — not the hair/head/face.

        Math target:
          - If face_box is given: anchor below chin + generous padding (PRIMARY)
          - Otherwise: skip top 38% of body box (covers head+neck for close-ups)
          - Add 10% offset into the safe zone → lands on upper chest / bodice ✓
        
        FIX: Increased head-skip from 25% → 38% so close-up shots (where the
        person fills 80-90% of the frame) don't land the dot in the hair.
        face_box chin is now given a 40px buffer (was 20px) to clear the jaw.
        """
        hx, hy, hw, hh = human_box

        if face_box:
            # face_box = [x, y, w, h] — use chin bottom + generous buffer
            chin_y = face_box[1] + face_box[3]
            # 80px padding below chin so the dot is clearly on the garment neckline/chest
            safe_top = chin_y + 80
            # Never let safe_top exceed 55% of body box
            safe_top = min(safe_top, hy + hh * 0.55)
            safe_top = max(safe_top, chin_y + 50)
        else:
            # No face box: skip top 52% (safe for close-up shots)
            safe_top = hy + (0.52 * hh)

        # 12% of body height below safe_top → upper chest / dress bodice
        anchor_y = safe_top + (0.12 * hh)

        # Clamp: must stay inside the visible body box
        return min(anchor_y, hy + hh - 20)

    def verify_anchor_pixel(self, frame_bgr, x, y, location, human_box, scan_range=80):
        if frame_bgr is None:
            return y
        import cv2
        import numpy as np
        h_img, w_img = frame_bgr.shape[:2]
        x = max(0, min(x, w_img - 1))
        y = max(0, min(y, h_img - 1))

        def _is_skin_or_hair(py):
            py = max(0, min(py, h_img - 1))
            pixel = frame_bgr[py, x]
            hsv = cv2.cvtColor(np.uint8([[pixel]]), cv2.COLOR_BGR2HSV)[0][0]
            h, s, v = hsv
            # Skin tone
            if (0 <= h <= 25) and (15 <= s <= 75) and (v >= 100):
                return True
            # Hair tone (darker/brown)
            if (5 <= h <= 20) and (20 <= s <= 60) and (v < 100):
                return True
            return False

        if location in ["glasses", "eyes", "head", "hat"]:
            return y # don't pixel verify eyewear/headwear
        
        direction = -1 if location in ["feet", "shoes"] else 1
        
        if _is_skin_or_hair(y):
            for offset in range(1, scan_range):
                test_y = y + (offset * direction)
                if 0 <= test_y < h_img and not _is_skin_or_hair(test_y):
                    return test_y
            
            # If we didn't find fabric, clamp to a safe zone
            hx, hy, hw, hh = human_box
            if direction == 1:
                return min(y + 40, hy + hh - 20)
            else:
                return max(y - 40, hy + 20)
        
        return y

    def generate(
        self,
        width: int,
        height: int,
        human_box: list,
        item_name: str,
        price_text: str,           # legacy
        location_hint: str,
        item_category: str = "default",
        brand_name: str = "",      # detected brand (e.g. 'Adidas', 'Prada') or empty
        gemini_celebrity_price: int = None,
        face_box: list = None,     # Optional face detection box
        precomputed_price_data: dict = None,  # [PRICE SYNC] Pass from profile_data['price_data']
        frame_bgr=None,            # Frame array for pixel fabric verification
        debug: bool = False,
    ) -> str:
        """
        Generates a transparent PNG overlay with Smart Price Tag.
        Uses Adaptive Anchor System for consistent visual quality.
        Pass precomputed_price_data to guarantee the overlay and Telegram message show the same prices.
        """
        try:
            # --- Get price psychology data ---
            # Use precomputed prices if available (ensures overlay == Telegram message)
            price_data = get_price_display(
                item_category,
                gemini_celebrity_price,
                precomputed=precomputed_price_data,
            )

            img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            hx, hy, hw, hh = human_box

            anchor_x, target_y, side, _dbg = compute_anchor(
                width,
                height,
                human_box,
                location_hint,
                face_box=face_box,
                frame_bgr=frame_bgr,
            )

            # Force face avoidance logic
            safe_anchor_y = self.compute_anchor(human_box, face_box)
            target_y = max(target_y, safe_anchor_y)

            _box_on_left = (side == "left")
            line_start_x = anchor_x
            SAFE = 18
            line_start_x = max(SAFE, min(line_start_x, width - SAFE))

            # [FIX] Sanitize item_name — strip generic filler words and truncate
            # so it describes the specific garment visible on screen.
            # "High Fashion Ensemble" → meaningless. "Black Style" → also meaningless.
            # If the full name (or just the non-color part) is generic, fall back to
            # the specific category (e.g. "Saree", "Lehenga", "Dress").
            _generic_triggers = {
                "high fashion", "fashion ensemble", "ensemble", "outfit",
                "look", "style", "fashion style", "fashion look",
            }
            _color_prefix_words = {
                "white", "black", "red", "blue", "green", "gold", "yellow",
                "grey", "gray", "purple", "pink", "beige", "ivory", "brown",
                "orange", "silver", "champagne",
            }
            _clean_name = item_name.strip()

            # Check if the name minus any leading color word is a generic trigger
            _name_lower = _clean_name.lower()
            _name_words = _name_lower.split()
            _base_words = [
                w for w in _name_words if w not in _color_prefix_words
            ]
            _base_str = " ".join(_base_words).strip()

            if (
                not _clean_name
                or _name_lower in _generic_triggers
                or _base_str in _generic_triggers
                or _base_str == ""
            ):
                # Fall back to the specific category (e.g. "Saree", "Lehenga")
                _clean_name = item_category if item_category != "default" else "Outfit"
            # Hard truncate to 18 chars so it always fits the box
            item_name = _clean_name[:18].upper()

            # ── AUTO-SCALE: tag size ∝ person size in frame ──────────────────
            # If the person fills 90% of the frame, a normal-sized tag dominates
            # the composition. Scale it down so it feels proportionate.
            # Formula: inverse relationship — more body coverage = smaller tag.
            #   coverage 0.90 → scale 0.55  (very close-up / backless shots)
            #   coverage 0.60 → scale 0.82  (typical 3/4 body shot)
            #   coverage 0.40 → scale 1.00  (half-body / group)
            #   coverage 0.20 → scale 1.10  (far away — boost readability)
            body_coverage = min(1.0, hh / max(height, 1))
            body_scale = round(max(0.50, min(1.10, 1.15 - body_coverage * 0.70)), 3)
            logger.debug(
                f"[SMART_PRICE_TAG] body_coverage={body_coverage:.2f} "
                f"→ body_scale={body_scale:.2f}"
            )



            # Font sizes — all scaled by body_scale
            label_size   = max(14, int(height * 0.014 * body_scale))
            strike_size  = max(13, int(height * 0.013 * body_scale))
            price_size   = max(18, int(height * 0.022 * body_scale))
            saving_size  = max(12, int(height * 0.012 * body_scale))
            urgency_size = max(11, int(height * 0.011 * body_scale))

            try:
                font_label   = ImageFont.truetype(self.font_path, label_size)
                font_strike  = ImageFont.truetype(self.font_path, strike_size)
                font_price   = ImageFont.truetype(self.font_path, price_size)
                font_saving  = ImageFont.truetype(self.font_path, saving_size)
                font_urgency = ImageFont.truetype(self.font_path, urgency_size)
            except Exception:
                font_label = font_strike = font_price = font_saving = font_urgency = ImageFont.load_default()

            # Scale all layout metrics by body_scale too — whole tag shrinks as a unit
            padding_x  = max(6,  int(10 * body_scale))
            padding_y  = max(5,  int(8  * body_scale))
            line_gap   = max(2,  int(4  * body_scale))

            # Text lines to render
            import textwrap
            line_label   = item_name
            # "Orig. Price ₹45,000+" — struck through so viewer knows THAT was the real price
            line_strike  = price_data.get("celebrity_price_label", price_data["celebrity_price_formatted"])
            line_price   = price_data["clone_price_formatted"]        # Amazon price — big gold hero number
            # Brand line: show brand if detected, otherwise just 'Save up to X%'
            _savings_str = f"Save up to {price_data['savings_pct']}%"
            if brand_name and brand_name.strip():
                line_saving = f"{brand_name.strip()[:15]} · {_savings_str}"
            else:
                line_saving = _savings_str
            
            # Wrap long strings to constrain the tag's width
            line_urgency_lines = textwrap.wrap(price_data["urgency"], width=26)
            line_urgency = "\n".join(line_urgency_lines)

            def _tw(text, font):
                bb = draw.textbbox((0, 0), text, font=font)
                return bb[2]-bb[0], bb[3]-bb[1]

            lw, lh     = _tw(line_label,   font_label)
            sw, sh     = _tw(line_strike,  font_strike)
            pw, ph     = _tw(line_price,   font_price)
            savw, savh = _tw(line_saving,  font_saving)
            uw, uh     = _tw(line_urgency, font_urgency)

            box_w = max(lw, sw, pw, savw, uw) + (padding_x * 2)
            box_h = (lh + sh + ph + savh + uh
                     + (line_gap * 4) + (padding_y * 2))

            # ── BOX PLACEMENT ────────────────────────────────────────────────
            # Move slightly inwards from the absolute edge to look more premium
            SAFE_X = max(18, int(width * 0.05))

            # X: screen edge (left or right)
            if _box_on_left:
                box_x = SAFE_X
            else:
                box_x = width - box_w - SAFE_X

            # ── Y: start near the garment target ─────────────
            # CRITICAL: Center box around target_y, but NEVER allow the top of the box
            # to float above safe_anchor_y. Without this, large boxes (big box_h) drift
            # upward by box_h//2, placing them directly on the face even when target_y is safe.
            box_y = target_y - (box_h // 2)
            box_y = max(box_y, int(safe_anchor_y))  # HARD FLOOR: top of box must be below safe zone

            # ── STRICT FACE AVOIDANCE ────────────────────────────────────────
            face_floor = int(safe_anchor_y)
            if face_box:
                fx, fy, fw, fh = face_box
                # Add a very generous safety buffer around the face
                fx_safe = fx - max(40, int(40 * body_scale))
                fy_safe = fy - max(40, int(40 * body_scale))
                fw_safe = fw + max(80, int(80 * body_scale))
                fh_safe = fh + max(100, int(100 * body_scale))
                
                # We update face_floor to this robust boundary if the tag overlaps on the X axis
                if box_x < fx_safe + fw_safe and box_x + box_w > fx_safe:
                    face_floor = max(face_floor, fy_safe + fh_safe)

                # If the box intersects with the face zone, push it down OR flip it
                if (box_x < fx_safe + fw_safe and box_x + box_w > fx_safe and
                    box_y < fy_safe + fh_safe and box_y + box_h > fy_safe):
                    
                    # Try pushing it down first
                    new_box_y = fy_safe + fh_safe
                    if new_box_y + box_h < height * 0.9:
                        box_y = new_box_y
                        logger.info("💰 [PRICE_TAG_AVOID] Pushing tag DOWN to avoid face.")
                    else:
                        # If pushing down is too far, try flipping side
                        _box_on_left = not _box_on_left
                        if _box_on_left:
                            box_x = SAFE_X
                        else:
                            box_x = width - box_w - SAFE_X
                        logger.info(f"💰 [PRICE_TAG_AVOID] Flipping tag to {'LEFT' if _box_on_left else 'RIGHT'} to avoid face.")
                        
                        # Re-check Y overlap on the new side. Update face_floor again.
                        if box_x < fx_safe + fw_safe and box_x + box_w > fx_safe:
                            face_floor = max(face_floor, fy_safe + fh_safe)
                        else:
                            # It no longer overlaps horizontally, so we can relax the floor for this side
                            face_floor = int(safe_anchor_y)
                            
                        if (box_x < fx_safe + fw_safe and box_x + box_w > fx_safe and
                            box_y < fy_safe + fh_safe and box_y + box_h > fy_safe):
                            box_y = fy_safe + fh_safe
                            logger.info("💰 [PRICE_TAG_AVOID] Pushing tag DOWN (post-flip) to avoid face.")

            # Prevent overlap with bottom caption zone
            # Lowered CAPTION_SAFE_TOP to 70% to give the tag more room to exist below the face.
            CAPTION_SAFE_TOP = int(height * 0.70)
            SAFE_Y = max(25, int(height * 0.03)) 
            
            if box_y + box_h > CAPTION_SAFE_TOP:
                suggested_box_y = CAPTION_SAFE_TOP - box_h - SAFE_Y
                # CRITICAL: Face > Captions. NEVER push the tag up into the face zone!
                # Even if suggested_box_y wants it higher, face_floor is the absolute minimum Y.
                box_y = max(face_floor, suggested_box_y)

            # Hard frame boundary clamp
            box_y = max(face_floor, min(height - box_h - SAFE_Y, box_y))

            logger.debug(
                f"[PRICE_TAG_BOX] side={'left' if _box_on_left else 'right'} "
                f"box=({box_x},{box_y},{box_w},{box_h}) target_y={target_y} "
            )


            # [DISABLED] Connector line + dot
            # # Line runs from garment dot (line_start_x, target_y) → nearest edge of corner box
            # if _box_on_left:
            #     # Box is on the LEFT — line exits from RIGHT edge of box
            #     line_end_x = box_x + box_w - 2
            # else:
            #     # Box is on the RIGHT — line exits from LEFT edge of box
            #     line_end_x = box_x + 2
            # line_end_y = box_y + (box_h // 2)

            # draw.line([(line_start_x, target_y), (line_end_x, line_end_y)],
            #           fill=(255, 255, 255, 255), width=2)
            # dot_r = max(3, int(5 * body_scale))
            # draw.ellipse([line_start_x-dot_r, target_y-dot_r,
            #               line_start_x+dot_r, target_y+dot_r],
            #              fill=(255, 255, 255, 255), outline=(0, 0, 0, 100), width=1)
            if debug:
                cross = max(5, int(6 * body_scale))
                draw.line([(line_start_x - cross, target_y), (line_start_x + cross, target_y)], fill=(255, 0, 0, 180), width=2)
                draw.line([(line_start_x, target_y - cross), (line_start_x, target_y + cross)], fill=(255, 0, 0, 180), width=2)

            # Glass box
            self._draw_glass_box(draw, box_x, box_y, box_w, box_h)

            # Draw text lines
            tx = box_x + padding_x
            ty = box_y + padding_y

            # 1. Item label — white, small
            draw.text((tx, ty), line_label, font=font_label, fill=(255, 255, 255, 200))
            ty += lh + line_gap

            # 2. Celebrity price — grey, struck through
            draw.text((tx, ty), line_strike, font=font_strike, fill=(180, 180, 180, 200))
            self._draw_strikethrough(draw, tx, ty, sw)
            ty += sh + line_gap

            # 3. Clone price — big gold (the hero number)
            draw.text((tx, ty), line_price, font=font_price, fill=(255, 215, 0, 255))
            ty += ph + line_gap

            # 4. Savings — green
            draw.text((tx, ty), line_saving, font=font_saving, fill=(100, 220, 100, 255))
            ty += savh + line_gap

            # 5. Urgency — red/orange flash
            draw.text((tx, ty), line_urgency, font=font_urgency, fill=(255, 100, 50, 255))

            # Save
            os.makedirs("temp", exist_ok=True)
            out_path = os.path.join("temp", f"smart_price_{uuid.uuid4().hex[:6]}.png")
            img.save(out_path, "PNG")
            logger.info(
                f"[SMART_PRICE_TAG] Generated: celebrity={line_strike} "
                f"clone={line_price} savings={price_data['savings_pct']}%"
            )
            return out_path.replace("\\", "/")

        except Exception as e:
            logger.error(f"Generate Smart Price Tag Failed: {e}")
            return None


# Singleton
tag_engine = SmartPriceTag()
