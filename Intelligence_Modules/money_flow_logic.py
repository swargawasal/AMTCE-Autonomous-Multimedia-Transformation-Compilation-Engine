"""
Money Flow Logic v3 (High-Conversion Engine — India-First Edition)
-----------------------------------------------------------------
v3 changes over v2:
  1. Completely rewrote all hook copy — scroll-stop openers + price-shock curiosity
  2. Hooks now prime the viewer for the "Comment LINK or... / Buy here" caption structure
  3. Added ATHLEISURE category (gym, sports, activewear)
  4. All hooks end with 👇 to guide eyes down to the buy link
  5. CTA format is now a backup label only — main CTA is handled in caption builder
  6. Hindi/Hinglish hooks updated to match the same punchy, conversational tone
"""

import random
import logging
import json
import os
import time
from typing import Dict, List, Optional

logger = logging.getLogger("money_flow_logic")

# --- CLICK TRACKING FILE ---
_TRACK_FILE = "Monetization_Metrics/hook_performance.json"


# --- OFFER MAP v3 ---
# Hook formula per entry:
#   Line 1: SCROLL-STOP (FOMO / social proof / price shock / curiosity)
#   Line 2: DESIRE AMPLIFIER (one-liner that makes them feel the want)
# CTA ("Comment LINK or... / Buy here") is injected by caption builder — not here.
OFFER_MAP = {

    "LUXURY": {
        "lead_magnet": "Elite Insider Blueprint",
        "offer_types": ["high_ticket_signup", "private_vault"],
        "price_anchor": "Market retail: ₹18,000–₹25,000",
        "our_frame": "Tonight only",
        "hooks": [
            "She walked in wearing this and the entire room went quiet 😶\nGuess the price. Then guess again 👇",
            "3 people in that room recognised this look. You could be the 4th 😏\nPrice will shock you 👇",
            "This sold out in 4 hours. Someone left it in their cart.\nDon't be that person 👇",
            "Looks like ₹20,000. Feels like ₹20,000. Isn't ₹20,000 😱\nSee the actual price 👇",
            "Old money never announces its source. We just did 😏👇",
        ],
        "hindi_hooks": [
            "Woh aayi aur poora room chup ho gaya 😶\nPrice dekh ke aankh phat jayegi 👇",
            "Yeh look ₹20,000 ka lagta hai. Yeh ₹20,000 ka nahi hai 😱👇",
            "4 ghante mein sold out. Kisi ne cart mein chhod diya.\nWoh galti mat karo 👇",
        ],
        "cta_format": "→ Grab it before it's gone",
        "urgency": "Selling fast today.",
    },

    "STREETWEAR": {
        "lead_magnet": "Underground Drop Archive",
        "offer_types": ["app_install", "limited_drop_signup"],
        "price_anchor": "Resale value after drop: ₹4,500–₹7,000",
        "our_frame": "Pre-resale price",
        "hooks": [
            "This dropped. Sold out. Then resold for 3x the price 🔥\nOne restock. Right now 👇",
            "6 hours online before it was gone the first time 😱\nIt's back. Not for long 👇",
            "The colorway nobody was supposed to see publicly 👀\nNow it's yours 👇",
            "She thrifted the vibe. We found the original source 😏\nSame look, real price 👇",
            "Underground. Unstocked. One last restock 🔥\nPrice below 👇",
        ],
        "hindi_hooks": [
            "Yeh drop hua. Sold out. 3x pe resell hua 🔥\nEk restock. Abhi 👇",
            "6 ghante mein gone. Wapas aa gaya. Zyada time nahi 👇",
            "Woh colorway jo publicly aana hi nahi tha 👀\nAbhi available hai 👇",
        ],
        "cta_format": "→ Secure yours now",
        "urgency": "Stock running out fast.",
    },

    "MINIMALIST": {
        "lead_magnet": "Quiet Luxury Dossier",
        "offer_types": ["newsletter_signup", "curated_vault"],
        "price_anchor": "Designer equivalent: ₹12,000+",
        "our_frame": "Direct source",
        "hooks": [
            "No logo. No pattern. No announcement.\nJust the right room — and this outfit 🖤👇",
            "People who dress like this never tell you where it's from.\nWe will 😏👇",
            "₹? and people will ask if it's designer.\nBecause it IS 👇",
            "Quiet luxury has one rule: the cut is everything.\nThis cut is everything 👇",
            "She got 5 compliments before she even sat down.\nThis is what she was wearing 👇",
        ],
        "hindi_hooks": [
            "Logo nahi. Pattern nahi. Sirf ek sahi room aur yeh outfit 🖤👇",
            "Jo log aise pehnte hain woh source nahi batate. Hum batate hain 😏👇",
            "5 compliments aaye baithne se pehle. Yeh outfit tha 👇",
        ],
        "cta_format": "→ Get the exact look",
        "urgency": "Few pieces left.",
    },

    "BOHEMIAN": {
        "lead_magnet": "Free Spirit Aesthetic Archive",
        "offer_types": ["social_follow", "style_guide"],
        "price_anchor": "Typical market price: ₹2,500–₹4,000",
        "our_frame": "Link price",
        "hooks": [
            "She found this at a market nobody photographs. We mapped it 📍\nYours now 👇",
            "This aesthetic is a quiet rejection of everything mass-produced 🌿\nPrice below 👇",
            "Undiscovered. Unseen. Unstocked. Until now 👇",
            "3 people DM'd asking where this is from this week.\nHere's where 👇",
            "The vibe money can't buy. Except it can. Cheaper than you think 😏👇",
        ],
        "hindi_hooks": [
            "Ek market hai jise koi photograph nahi karta. Humne dhundha 📍👇",
            "Yeh aesthetic mass production ka quiet rejection hai 🌿👇",
            "3 logon ne is hafte yeh poochha. Yahan jawab hai 👇",
        ],
        "cta_format": "→ Find it here",
        "urgency": "Limited stock.",
    },

    "FORMAL": {
        "lead_magnet": "Couture Architecture File",
        "offer_types": ["pro_service_signup", "mastery_vault"],
        "price_anchor": "Designer equivalent: ₹12,000+",
        "our_frame": "Direct source",
        "hooks": [
            "The room decided in 7 seconds. This outfit made sure of the outcome 😏\nPrice below 👇",
            "She got the promotion. This was the outfit that walked in first 🔥👇",
            "Power dressing isn't expensive. It's intentional.\nThis is the intention 👇",
            "3 tailoring decisions in this look. All of them correct.\nPrice below 👇",
            "₹? for an outfit that commands a room.\nSpoiler: it's not ₹10,000+ 😱👇",
        ],
        "hindi_hooks": [
            "7 seconds mein room ne decide kar liya. Is outfit ne decision pakka kiya 😏👇",
            "Promotion mili. Yeh outfit pehna tha 🔥👇",
            "Power dressing expensive nahi hoti. Intentional hoti hai.\nYeh woh intention hai 👇",
        ],
        "cta_format": "→ Wear the outcome",
        "urgency": "Ships in 2 days.",
    },

    # ── Indian-specific high-traffic categories ──────────────────────────────

    "BOLLYWOOD": {
        "lead_magnet": "Bollywood Insider Style Vault",
        "offer_types": ["high_ticket_signup", "private_vault"],
        "price_anchor": "Stylist-sourced original: ₹15,000–₹40,000",
        "our_frame": "Dupe price",
        "hooks": [
            "The stylist bill was ₹8 lakh. Your bill today: ₹? 😏\nDo the math 👇",
            "She wore this once and broke the internet 🔥\nThe actual price will break you too 😱👇",
            "Bollywood doesn't shop. It engineers. This is the engineering.\nPrice below 👇",
            "Every outfit has a story. This one got 40M views in 72 hours 👀\nYours now 👇",
            "She wore this to an event nobody was allowed to photograph.\nUntil now 😏👇",
        ],
        "hindi_hooks": [
            "Stylist ka bill ₹8 lakh tha. Aapka bill aaj: ₹? 😏\nMath karo 👇",
            "Ek baar pehna aur internet tod diya 🔥\nPrice bhi tod dega 😱👇",
            "40M views. 72 ghante. Ab tumhara 👀👇",
        ],
        "cta_format": "→ Steal the look today",
        "urgency": "Trending right now.",
    },

    "BRIDAL": {
        "lead_magnet": "Bridal Architect's Secret File",
        "offer_types": ["high_ticket_signup", "curated_vault"],
        "price_anchor": "Boutique price for this silhouette: ₹35,000+",
        "our_frame": "Direct from manufacturer",
        "hooks": [
            "She walked in and nobody — not even the groom — looked away 👀\nThis is what she was wearing 👇",
            "The baraat went silent. This was the look 🔥\nPrice will surprise you 👇",
            "Her mother-in-law asked for the tailor's number.\nHere's something better 👇",
            "Two things stopped traffic that day.\nThis lehenga was one of them 😏👇",
            "6 hours of craftsmanship. 3 seconds to stop the room.\nPrice below 👇",
        ],
        "hindi_hooks": [
            "Woh aayi aur dulhe ne bhi uthke dekha 👀\nYeh tha woh look 👇",
            "Baraat ruk gayi. Yeh look tha 🔥\nPrice sunke hairan reh jaoge 👇",
            "Saas ne darzi ka number maanga.\nYahan kuch better hai 👇",
        ],
        "cta_format": "→ Own this look",
        "urgency": "Booked by 200+ brides this season.",
    },

    "FESTIVAL": {
        "lead_magnet": "Festival Viral Look Archive",
        "offer_types": ["limited_drop_signup", "style_guide"],
        "price_anchor": "Mall price this season: ₹3,500–₹5,500",
        "our_frame": "Factory direct",
        "hooks": [
            "This festival look was copied 40,000 times in one week.\nHere's the original 🔥👇",
            "Everyone wore this. Nobody knew where it was from.\nNow you do 😏👇",
            "The most-saved outfit this season — and it ships in 2 days 📦\nPrice below 👇",
            "Navratri/Diwali is coming and this is already selling out 🔥\nGrab it now 👇",
            "She spent ₹? and out-dressed everyone who spent 10x more.\nLink below 👇",
        ],
        "hindi_hooks": [
            "Is look ko ek hafte mein 40,000 ne copy kiya.\nOriginal yahan hai 🔥👇",
            "Sabne pehna. Kisi ko pata nahi tha kahan se.\nAb pata chal gaya 😏👇",
            "Season ka sabse saved outfit. 2 din mein delivery 📦\nPrice neeche 👇",
        ],
        "cta_format": "→ Get it before the festival",
        "urgency": "Selling out fast.",
    },

    "ATHLEISURE": {
        "lead_magnet": "Performance Aesthetic Archive",
        "offer_types": ["app_install", "limited_drop_signup"],
        "price_anchor": "Gymshark/Decathlon equivalent: ₹3,500–₹6,000",
        "our_frame": "Indian-made alternative",
        "hooks": [
            "She walked into the gym in this and the entire floor looked up 👀\nPrice below 👇",
            "Same gym. Same workout. This outfit changed everything 🔥👇",
            "Gymshark energy. Indian price.\nYou don't need to import it anymore 😏👇",
            "This set sold out in 72 hours the first time.\nBack in stock — for now 🔥👇",
            "Her gym set costs less than your post-workout protein tub 😱\nSee the price 👇",
        ],
        "hindi_hooks": [
            "Gym mein aayi is mein aur sab ne dekha 👀\nPrice neeche hai 👇",
            "Wahi gym. Wahi workout. Is outfit ne sab badal diya 🔥👇",
            "Gymshark energy. Indian price.\nImport karne ki zaroorat nahi 😏👇",
        ],
        "cta_format": "→ Level up your gym look",
        "urgency": "Back in stock — limited units.",
    },

    "GLOBAL": {
        "lead_magnet": "Global Intelligence Vault",
        "offer_types": ["general_signup"],
        "price_anchor": "Typical market price: ₹2,500–₹4,000",
        "our_frame": "Link price",
        "hooks": [
            "Everyone scrolled past this.\nThe ones who didn't are wearing it now 🔥👇",
            "₹? and it looks like you spent 10x.\nWe tested it. It works 😏👇",
            "She got 3 compliments in 10 minutes.\nThis was the reason 😱👇",
            "This almost didn't make it to the feed.\n'Too good to post,' they said 😏👇",
            "The algorithm kept hiding this.\nNow it's yours to find 👀👇",
        ],
        "hindi_hooks": [
            "Sabne scroll kiya.\nJinhone nahi kiya woh ab yeh pehnte hain 🔥👇",
            "₹? mein 10x lagta hai.\nHumne test kiya. Kaam karta hai 😏👇",
            "10 minute mein 3 compliments aayi.\nYeh reason tha 😱👇",
        ],
        "cta_format": "→ Get it now",
        "urgency": "Limited stock today.",
    },
}

# Fallback if category completely unknown
DEFAULT_OFFER = OFFER_MAP["GLOBAL"]


# ── Click Performance Tracker ────────────────────────────────────────────────

class _HookTracker:
    """
    Lightweight file-backed performance tracker.
    Records how many times each hook was served.
    When real click data is available (from your Telegram bot callback or
    Los Pollos postback), call record_click(hook_text) to weight it higher.
    """

    def __init__(self):
        self._data: Dict = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(_TRACK_FILE):
                with open(_TRACK_FILE, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
        except Exception:
            self._data = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(_TRACK_FILE), exist_ok=True)
            with open(_TRACK_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.warning(f"[HOOK_TRACKER] Save failed: {e}")

    def record_serve(self, hook: str):
        entry = self._data.setdefault(hook, {"serves": 0, "clicks": 0})
        entry["serves"] += 1
        self._save()

    def record_click(self, hook: str):
        """Call this from your Telegram callback / Los Pollos postback handler."""
        entry = self._data.setdefault(hook, {"serves": 0, "clicks": 0, "revenue": 0.0})
        entry["clicks"] += 1
        self._save()
        logger.info(f"[HOOK_TRACKER] Click recorded: '{hook[:50]}...'")

    def record_conversion(self, hook: str, revenue: float):
        """
        Call this from your Amazon affiliate callback or Los Pollos postback
        when a click converts to a sale. Records actual revenue per hook.
        This upgrades the picker from CTR-weighted to EPC-weighted.
        EPC = Earnings Per Click = the metric that actually matters.
        """
        entry = self._data.setdefault(hook, {"serves": 0, "clicks": 0, "revenue": 0.0})
        entry["revenue"] = entry.get("revenue", 0.0) + revenue
        self._save()
        logger.info(f"[HOOK_TRACKER] Conversion +₹{revenue:.2f} recorded for hook.")

    def get_ctr(self, hook: str) -> float:
        entry = self._data.get(hook, {})
        serves = entry.get("serves", 0)
        clicks = entry.get("clicks", 0)
        # Laplace smoothing: baseline probability of click = 5%, preventing 0% or 100% lock-outs
        return (clicks + 0.1) / (serves + 2)

    def get_epc(self, hook: str) -> float:
        """Earnings Per Click. Higher = hook that drives PURCHASES, not just clicks."""
        entry = self._data.get(hook, {})
        clicks = entry.get("clicks", 0)
        revenue = entry.get("revenue", 0.0)
        # Laplace smoothing for EPC: add tiny baseline revenue (e.g. ₹0.5) to avoid cold starts
        return (revenue + 0.5) / (clicks + 1)

    def pick_best_epc(self, hooks: list) -> str:
        """
        EPC-weighted random pick. After 10+ clicks of data exists,
        prefer EPC over CTR — because a hook with 40% CTR and ₹0 revenue
        is worse than a hook with 20% CTR and ₹180 average order value.
        Falls back to CTR-weighted if insufficient data.
        Includes a 10% Exploration Bias for new hooks.
        """
        # 10% Exploration Bias (Validator-Y Refinement)
        if random.random() < 0.10:
            chosen = random.choice(hooks)
            self.record_serve(chosen)
            return chosen

        epcs = [self.get_epc(h) for h in hooks]
        total_epc = sum(epcs)

        if total_epc == 0:
            # No revenue data yet — fall back to CTR weighted
            return self.pick_best(hooks)

        r = random.uniform(0, total_epc)
        cumulative = 0.0
        chosen = hooks[-1]
        for hook, epc in zip(hooks, epcs):
            cumulative += epc
            if r <= cumulative:
                chosen = hook
                break

        self.record_serve(chosen)
        return chosen

    def pick_best(self, hooks: List[str]) -> str:
        """
        Weighted random pick — hooks with higher CTR are selected more often.
        Falls back to pure random when no data exists yet (cold start).
        """
        ctrs = [self.get_ctr(h) for h in hooks]
        total = sum(ctrs)
        if total == 0:
            chosen = random.choice(hooks)
        else:
            r = random.uniform(0, total)
            cumulative = 0.0
            chosen = hooks[-1]
            for hook, ctr in zip(hooks, ctrs):
                cumulative += ctr
                if r <= cumulative:
                    chosen = hook
                    break
        self.record_serve(chosen)
        return chosen


_tracker = _HookTracker()


# ── Main Engine ──────────────────────────────────────────────────────────────

class MoneyFlowEngine:
    def __init__(self):
        self.tracker = _tracker

    # Safe, generic high-intent niches — avoid highly specific ones (like BRIDAL) for unknown items
    _HIGH_INTENT_NICHES = ["LUXURY", "GLOBAL", "STREETWEAR", "MINIMALIST"]

    # ── INDIAN COMMERCIAL CALENDAR ──────────────────────────────────────────────
    # These dates represent the highest purchase-intent periods in the Indian market.
    # During these windows, urgency copy is automatically elevated and
    # category routing is biased toward the relevant niche.
    _SEASONAL_WINDOWS = [
        {"name": "Navratri",    "months": [10], "days": range(1, 16),  "bias_niche": "FESTIVAL",  "urgency_mult": 2.0},
        {"name": "Diwali",      "months": [10, 11], "days": range(15, 31), "bias_niche": "FESTIVAL", "urgency_mult": 2.5},
        {"name": "Wedding Peak","months": [11, 12, 1, 2], "days": range(1, 32), "bias_niche": "BRIDAL", "urgency_mult": 1.8},
        {"name": "Valentine",   "months": [2], "days": range(10, 15),  "bias_niche": "MINIMALIST", "urgency_mult": 1.6},
        {"name": "Summer Drop", "months": [4, 5], "days": range(1, 32),"bias_niche": "ATHLEISURE", "urgency_mult": 1.5},
        {"name": "IPL Season",  "months": [3, 4, 5], "days": range(1, 32), "bias_niche": "STREETWEAR", "urgency_mult": 1.4},
    ]

    def get_seasonal_modifier(self) -> dict:
        """
        Returns active seasonal window data if today falls within one.
        Use this to:
        (1) Override category routing to the seasonal niche bias
        (2) Multiply urgency in hooks (stock framing, time framing)
        (3) Override generated_hashtags to include seasonal tags

        Returns: {"active": bool, "name": str, "bias_niche": str, "urgency_mult": float}
        """
        from datetime import datetime as _dt
        now = _dt.now()
        for window in self._SEASONAL_WINDOWS:
            if now.month in window["months"] and now.day in window["days"]:
                return {
                    "active": True,
                    "name": window["name"],
                    "bias_niche": window["bias_niche"],
                    "urgency_mult": window["urgency_mult"],
                    "seasonal_hashtags": [f"#{window['name'].replace(' ', '')}", "#FestiveSeason", "#IndianFashion"]
                }
        return {"active": False, "name": None, "bias_niche": None, "urgency_mult": 1.0, "seasonal_hashtags": []}

    def get_optimized_offer(self, fashion_category: str = "GLOBAL") -> Dict:
        """
        Returns the best offer data for the detected fashion category.
        Uses EPC-weighted hook selection (Upgraded M3).
        Incorporate Temporal Hooks (M1) and Seasonal Calendar (M4).
        """
        # ── TEMPORAL CONVERSION LAYER ─────────────────────────────────────────────
        # Purchase intent follows a predictable daily rhythm in the Indian market.
        _TEMPORAL_HOOK_SUFFIX = {
            "morning":   "\n🌅 Starting your day right. This is the piece to do it with.",
            "afternoon": "\n☀️ You've been thinking about this. You already know.",
            "evening":   "\n🔥 This is selling NOW. Evening is when people stop waiting.",
            "night":     "\n🌙 Last chance today. Tomorrow it might be gone.",
        }

        from datetime import datetime as _dt
        try:
            # We assume the server is set to IST or handles pytz
            import pytz as _pytz
            _ist = _pytz.timezone("Asia/Kolkata")
            _hour = _dt.now(_ist).hour
        except Exception:
            _hour = _dt.now().hour

        if 6 <= _hour < 12:
            _time_window = "morning"
        elif 12 <= _hour < 17:
            _time_window = "afternoon"
        elif 17 <= _hour < 21:
            _time_window = "evening"
        else:
            _time_window = "night"

        _temporal_suffix = _TEMPORAL_HOOK_SUFFIX[_time_window]

        category = fashion_category.upper().strip()

        # Map common aliases
        _ALIASES = {
            "FITNESS": "ATHLEISURE",
            "GYM": "ATHLEISURE",
            "SPORT": "ATHLEISURE",
            "ETHNIC": "FESTIVAL",
            "STAGE": "FESTIVAL",
            "PROGRAM": "FESTIVAL",
            "EVENT": "FESTIVAL",
            "CONCERT": "FESTIVAL",
            "CASUAL": "GLOBAL",
            "PARTY": "LUXURY",
            "WEDDING": "BRIDAL",
            "JACKET": "STREETWEAR",
            "COAT": "STREETWEAR",
        }
        category = _ALIASES.get(category, category)

        # ── SEASONAL OVERRIDE (M4) ──────────────────────────────────────────────
        seasonal = self.get_seasonal_modifier()
        if seasonal['active'] and category == 'GLOBAL':
            category = seasonal['bias_niche']
            logger.info(f"[SEASONAL] {seasonal['name']} active — routing to {category}")

        if category not in OFFER_MAP or category == "GLOBAL":
            category = random.choice(self._HIGH_INTENT_NICHES)
            logger.info(f"💰 [MONEY_FLOW] Uplifted to safe niche: {category}")

        offer_data = OFFER_MAP.get(category, OFFER_MAP["GLOBAL"])

        # EPC-weighted hook selection (Upgraded M3)
        selected_hook = self.tracker.pick_best_epc(offer_data["hooks"])

        # Append temporal suffix (M1) — evening/night hooks get urgency injected
        if _time_window in ['evening', 'night']:
            selected_hook = selected_hook + _temporal_suffix

        return {
            "category": category,
            "lead_magnet": offer_data["lead_magnet"],
            "hook": selected_hook,
            "offer_type": random.choice(offer_data["offer_types"]),
            "cta_format": offer_data["cta_format"],
            "urgency": offer_data.get("urgency", ""),
            "time_window": _time_window,
            "seasonal_context": seasonal if seasonal['active'] else None
        }

    def get_law_bending_cta(self, offer: Dict) -> str:
        """English CTA with price anchor injection (Upgraded M2)."""
        hook = offer.get("hook", "")
        urgency = offer.get("urgency", "")
        category = offer.get("category", "GLOBAL")
        offer_data = OFFER_MAP.get(category, DEFAULT_OFFER)

        # Price anchor: show reference price before our price
        price_anchor = offer_data.get("price_anchor", "")
        our_frame = offer_data.get("our_frame", "Link price")

        anchor_line = f"\n💰 {price_anchor} → {our_frame} 👇" if price_anchor else ""
        urgency_line = f"\n⚡ {urgency}" if urgency else ""

        return f"{hook}{anchor_line}{urgency_line}"

    def get_hinglish_cta(self, offer: Dict) -> str:
        """Hinglish CTA — picks from hindi_hooks if available."""
        category = offer.get("category", "GLOBAL")
        offer_data = OFFER_MAP.get(category, DEFAULT_OFFER)
        hindi_hooks = offer_data.get("hindi_hooks", offer_data["hooks"])
        hook = self.tracker.pick_best(hindi_hooks)
        urgency = offer_data.get("urgency", "")
        return f"{hook}\n⚡ {urgency}" if urgency else hook

    def get_roman_urdu_cta(self, offer: Dict) -> str:
        """Roman Urdu — same hooks, suffix swap."""
        return self.get_hinglish_cta(offer).replace(
            "kar.", "karen."
        ).replace("karo", "karein")

    def get_hindi_cta(self, offer: Dict) -> str:
        """Full Hindi Devanagari CTA."""
        category = offer.get("category", "GLOBAL")
        offer_data = OFFER_MAP.get(category, DEFAULT_OFFER)
        hindi_hooks = offer_data.get("hindi_hooks", offer_data["hooks"])
        hook = random.choice(hindi_hooks)
        urgency = offer_data.get("urgency", "")
        return f"{hook}\n⚡ {urgency}" if urgency else hook

    def record_click(self, hook_text: str):
        """
        Call this from your Telegram inline button callback or Los Pollos postback.
        Feeds real CTR data back into the weighted picker.
        """
        self.tracker.record_click(hook_text)

    def get_performance_report(self) -> Dict:
        """Returns current hook CTR data for monitoring."""
        return {
            hook: {
                "serves": data.get("serves", 0),
                "clicks": data.get("clicks", 0),
                "ctr": round(data.get("clicks", 0) / data.get("serves", 1) * 100, 2),
            }
            for hook, data in self.tracker._data.items()
        }


engine = MoneyFlowEngine()