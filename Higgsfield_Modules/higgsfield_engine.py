"""
Higgsfield Engine — Credit Governor + CLI Wrapper
==================================================
Manages Higgsfield free-tier credits, waits until generation threshold is met,
calls the Higgsfield CLI, and returns the output video path for AMTCE's compiler.

Architecture:
    - Credits are persisted in Higgsfield_Modules/higgsfield_state.json
    - Persona configs live in social_media/<influencer_name>/higgsfield.json
    - Generation is gated: only fires when credit_balance >= generation_cost
    - Output lands in Influencer_Output/higgsfield/<persona_name>/ for the compiler

Free Tier Reality (confirmed from higgsfield.ai, May 2026):
    - 6 one-time welcome credits
    - No guaranteed daily refresh (some accounts get 1-2 occasionally)
    - Cheapest generation: ~2 credits (image), ~6 credits (short video)
    - Watermarks included on free tier (still eligible for Higgsfield Earn)
    - Earn program pays per view performance, NOT per credit or watermark

CLI Commands used:
    npm install -g @higgsfield/cli       # one-time install
    higgsfield auth login                # one-time per account
    higgsfield generate --prompt "..."   # per generation (costs credits)

Usage:
    engine = HiggsfieldEngine(persona_name="Influencer_Luna")
    result = engine.try_generate(
        prompt="Cinematic Indian woman in silk saree, golden hour, editorial",
        generation_type="video",   # or "image"
    )
    if result["status"] == "generated":
        print(result["output_path"])   # ready for AMTCE compiler
    elif result["status"] == "waiting":
        print(f"Waiting — balance {result['balance']}/{result['threshold']} credits")
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("higgsfield.engine")

# ── Constants ──────────────────────────────────────────────────────────────────
STATE_FILE   = os.path.join(os.path.dirname(__file__), "higgsfield_state.json")
OUTPUT_BASE  = os.path.join("Influencer_Output", "higgsfield")

# Confirmed real credit costs from live Higgsfield API (verified 2026-05-18)
CREDIT_COSTS = {
    "image":    2,     # nano_banana_2 static image
    "video":    5,     # seedance1_5 (4.8 credits — rounded up for safety)
    "try_on":   2,     # nano_banana_2 product photoshoot
}

# Minimum credit balance required before triggering generation
GENERATION_THRESHOLD = {
    "image":  2,
    "video":  5,     # seedance1_5 at 4.8 fits within free tier 6-credit balance
    "try_on": 2,
}

# Model selection: cheapest-first per type (costs from live API)
MODEL_MAP = {
    "video": [
        ("seedance1_5",           4.8,  "Seedance 1.5 Pro — cheapest, great quality"),
        ("minimax_hailuo",        6.0,  "Minimax Hailuo — good motion"),
        ("kling2_6",             10.0,  "Kling 2.6 — cinematic quality"),
        ("wan2_6",               13.0,  "Wan 2.6 — strong I2V"),
        ("seedance_2_0",         22.5,  "Seedance 2.0 — top quality, expensive"),
        ("cinematic_studio_3_0", 25.0,  "Cinematic Studio 3.0 — premium"),
    ],
    "image":  [("nano_banana_2", 2.0, "Nano Banana 2 — cheapest image")],
    "try_on": [("nano_banana_2", 2.0, "Nano Banana 2 product photoshoot")],
}

# Best cheapest model per type that fits within free tier credits
DEFAULT_MODEL = {
    "video":  "seedance1_5",    # 4.8 credits
    "image":  "nano_banana_2",  # 2.0 credits
    "try_on": "nano_banana_2",  # 2.0 credits
}


# ── State Persistence ──────────────────────────────────────────────────────────
def _load_state() -> dict:
    """Load persistent credit state from JSON."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Default: 6 welcome credits on first use
    return {
        "credit_balance":    6,
        "lifetime_earned":   0,
        "lifetime_spent":    0,
        "last_generation":   None,
        "total_generations": 0,
        "personas":          {},
        "earn_submissions":  [],
    }


def _save_state(state: dict) -> None:
    """Persist credit state to JSON."""
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[STATE] Failed to save state: {e}")


# ── Persona Config ─────────────────────────────────────────────────────────────
def _load_persona_config(persona_name: str) -> dict:
    """
    Load Higgsfield persona config from social_media/<persona_name>/higgsfield.json.
    Falls back to defaults if config does not exist yet.

    Schema of higgsfield.json:
    {
        "soul_id_trained": false,          # whether Soul ID training is done
        "soul_id_reference": "face.jpg",   # path to reference face image
        "niche": "fashion",                # content niche for prompts
        "default_style": "editorial",      # default visual style
        "earn_account_verified": false,     # whether Earn account is connected
        "social_handles": {
            "instagram": "@handle",
            "youtube": "channel_id"
        }
    }
    """
    paths_to_check = [
        os.path.join("Credentials", "social_media", persona_name, "higgsfield.json"),
        os.path.join("social_media", persona_name, "higgsfield.json")
    ]
    for config_path in paths_to_check:
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ref = data.get("soul_id_reference")
                    if ref:
                        data["soul_id_reference_path"] = os.path.join(os.path.dirname(config_path), ref)
                    else:
                        data["soul_id_reference_path"] = None
                    return data
            except Exception:
                pass
    return {
        "soul_id_trained":        False,
        "soul_id_reference":      None,
        "soul_id_reference_path": None,
        "niche":                  "fashion",
        "default_style":          "editorial",
        "earn_account_verified":  False,
        "social_handles":         {},
    }


# ── CLI Availability Check ─────────────────────────────────────────────────────
def _check_cli_available() -> bool:
    """Returns True if the Higgsfield CLI is installed and accessible."""
    try:
        result = subprocess.run(
            ["higgsfield", "--version"],
            capture_output=True, text=True, timeout=10,
            shell=(os.name == "nt")
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── Core Engine ────────────────────────────────────────────────────────────────
class HiggsfieldEngine:
    """
    Credit-governed Higgsfield CLI wrapper.

    AMTCE Integration Points:
        1. Fashion Scout → finds product image → engine.try_generate(type="try_on")
        2. Influencer Pipeline → local stages done → engine.try_generate(type="video")
        3. Output lands in Influencer_Output/higgsfield/ for AMTCE Compiler
        4. HiggsfieldEarn.submit() is called after successful social media post
    """

    def __init__(self, persona_name: str = "General"):
        self.persona_name   = persona_name
        self.state          = _load_state()
        self.persona_config = _load_persona_config(persona_name)
        self.cli_available  = _check_cli_available()

        if not self.cli_available:
            logger.warning(
                "[HIGGSFIELD] CLI not found. Install with: npm install -g @higgsfield/cli"
            )

        # Ensure persona output directory exists
        self.output_dir = os.path.join(OUTPUT_BASE, persona_name)
        os.makedirs(self.output_dir, exist_ok=True)

        logger.info(
            f"[HIGGSFIELD] Engine ready | Persona: {persona_name} | "
            f"Balance: {self.state['credit_balance']} credits | "
            f"CLI: {'✅' if self.cli_available else '❌ Not installed'}"
        )

    # ── Credit Management ──────────────────────────────────────────────────────
    def get_balance(self) -> int:
        """Return current credit balance."""
        return self.state.get("credit_balance", 0)

    def add_credits(self, amount: int, reason: str = "manual") -> None:
        """
        Add credits to the balance (use when Higgsfield gives free credits,
        Earn payouts, or promotional grants).
        """
        self.state["credit_balance"] = self.state.get("credit_balance", 0) + amount
        self.state["lifetime_earned"] = self.state.get("lifetime_earned", 0) + amount
        logger.info(f"[HIGGSFIELD] +{amount} credits ({reason}). New balance: {self.state['credit_balance']}")
        _save_state(self.state)

    def _spend_credits(self, amount: int, reason: str) -> bool:
        """
        Deduct credits. Returns False if insufficient balance (never goes negative).
        """
        if self.state.get("credit_balance", 0) < amount:
            logger.warning(
                f"[HIGGSFIELD] Insufficient credits: need {amount}, "
                f"have {self.state['credit_balance']}. Waiting."
            )
            return False
        self.state["credit_balance"]  -= amount
        self.state["lifetime_spent"]   = self.state.get("lifetime_spent", 0) + amount
        self.state["total_generations"] = self.state.get("total_generations", 0) + 1
        self.state["last_generation"]   = datetime.now().isoformat()
        _save_state(self.state)
        logger.info(
            f"[HIGGSFIELD] Spent {amount} credits ({reason}). "
            f"Remaining: {self.state['credit_balance']}"
        )
        return True

    # ── Generation ─────────────────────────────────────────────────────────────
    def try_generate(
        self,
        prompt:          str,
        generation_type: str = "video",          # "image" | "video" | "try_on"
        product_image:   Optional[str] = None,   # for try_on: path to outfit image
        duration:        int = 3,                # seconds (for video)
        style_preset:    Optional[str] = None,   # "editorial" | "street" | "luxury"
        force:           bool = False,           # bypass credit threshold (manual override)
        images:          Optional[list] = None,  # list of image references
    ) -> dict:
        """
        Attempt to generate via Higgsfield CLI.

        Returns:
            {
                "status":      "generated" | "waiting" | "no_cli" | "failed",
                "output_path": str | None,      # path to output file
                "balance":     int,             # current credit balance
                "threshold":   int,             # credits needed to generate
                "cost":        int,             # credits that were spent
                "message":     str,             # human readable status
            }
        """
        cost      = CREDIT_COSTS.get(generation_type, 6)
        threshold = GENERATION_THRESHOLD.get(generation_type, 6)

        # ── Gate 1: CLI installed? ─────────────────────────────────────────────
        if not self.cli_available:
            return {
                "status":      "no_cli",
                "output_path": None,
                "balance":     self.get_balance(),
                "threshold":   threshold,
                "cost":        cost,
                "message":     "Higgsfield CLI not installed. Run: npm install -g @higgsfield/cli",
            }

        # ── Gate 2: Credit threshold ───────────────────────────────────────────
        if not force and self.get_balance() < threshold:
            return {
                "status":      "waiting",
                "output_path": None,
                "balance":     self.get_balance(),
                "threshold":   threshold,
                "cost":        cost,
                "message":     (
                    f"Saving credits: {self.get_balance()}/{threshold} needed. "
                    f"Will generate when balance reaches {threshold}."
                ),
            }

        # ── Resolve Prompt & Style ─────────────────────────────────────────────
        if prompt.startswith("[System:"):
            # It is an advanced structured Seedance 2.0 prompt, preserve it exactly as-is
            full_prompt = prompt
        else:
            style = style_preset or self.persona_config.get("default_style", "editorial")
            full_prompt = f"{prompt}, {style} photography, cinematic, high quality"

        # ── Resolve Reference Images ───────────────────────────────────────────
        ref_images = images or []
        if not ref_images:
            # Fall back to persona face reference if present
            ref_path = self.persona_config.get("soul_id_reference_path")
            if ref_path and os.path.exists(ref_path):
                ref_images = [ref_path]

        # ── Build CLI command  (correct higgsfield CLI syntax) ──────────────
        model_id    = DEFAULT_MODEL.get(generation_type, "seedance1_5")
        timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(
            self.output_dir,
            f"hf_{generation_type}_{timestamp}.mp4" if generation_type == "video"
            else f"hf_{generation_type}_{timestamp}.jpg"
        )

        # Sanitise prompt for CLI (max ~2000 chars for safety)
        safe_prompt = full_prompt[:2000]

        # Base command: higgsfield generate create <model> --prompt "..." --wait
        cmd = ["higgsfield", "generate", "create", model_id,
               "--prompt", safe_prompt,
               "--wait", "--json"]

        if generation_type == "try_on" and product_image and os.path.exists(product_image):
            cmd += ["--image", os.path.abspath(product_image)]
        else:
            for img in ref_images:
                if os.path.exists(img):
                    cmd += ["--image", os.path.abspath(img)]

        # ── Execute ────────────────────────────────────────────────────────────
        logger.info(f"[HIGGSFIELD] Generating {generation_type} via {model_id} | Prompt: {safe_prompt[:60]}...")
        logger.info(f"[HIGGSFIELD] CMD: {' '.join(cmd[:6])}...")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 min timeout for video generation (Higgsfield can be slow)
                shell=(os.name == "nt"),
            )

            # Higgsfield --wait --json outputs JSON when done; parse the URL and download
            if proc.returncode == 0:
                # Try to parse JSON response and download output
                import re
                stdout = proc.stdout.strip()
                url_match = re.search(r'https?://[^\s"]+\.(?:mp4|jpg|png|webm)', stdout)
                if url_match:
                    url = url_match.group(0)
                    logger.info(f"[HIGGSFIELD] Downloading output from: {url}")
                    try:
                        import urllib.request
                        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
                        urllib.request.urlretrieve(url, output_file)
                    except Exception as dl_err:
                        logger.warning(f"[HIGGSFIELD] Download failed: {dl_err}")

                if os.path.exists(output_file):
                    self._spend_credits(cost, f"{generation_type} generation via {model_id}")
                    logger.info(f"[HIGGSFIELD] Generated: {output_file}")

                    p = self.state.setdefault("personas", {}).setdefault(self.persona_name, {})
                    p["total_generations"] = p.get("total_generations", 0) + 1
                    p["last_generated"]    = datetime.now().isoformat()
                    _save_state(self.state)

                    return {
                        "status":      "generated",
                        "output_path": output_file,
                        "balance":     self.get_balance(),
                        "threshold":   threshold,
                        "cost":        cost,
                        "model":       model_id,
                        "message":     f"Generated successfully via {model_id}. {self.get_balance()} credits remaining.",
                    }
                else:
                    # CLI succeeded (code 0) but no local file — return stdout URL if present
                    logger.warning(f"[HIGGSFIELD] Output file not saved locally. stdout: {stdout[:200]}")
                    return {
                        "status":      "generated_remote",
                        "output_path": url_match.group(0) if url_match else None,
                        "stdout":      stdout[:500],
                        "balance":     self.get_balance(),
                        "threshold":   threshold,
                        "cost":        cost,
                        "model":       model_id,
                        "message":     f"Generated on Higgsfield servers. URL: {url_match.group(0) if url_match else 'check dashboard'}.",
                    }
            else:
                logger.error(
                    f"[HIGGSFIELD] Generation failed (code {proc.returncode})\n"
                    f"stderr: {proc.stderr[:300]}"
                )
                return {
                    "status":      "failed",
                    "output_path": None,
                    "balance":     self.get_balance(),
                    "threshold":   threshold,
                    "cost":        cost,
                    "model":       model_id,
                    "message":     f"CLI error (code {proc.returncode}): {proc.stderr[:200]}",
                }

        except subprocess.TimeoutExpired:
            logger.error("[HIGGSFIELD] Generation timed out (>10 min)")
            return {
                "status":  "failed", "output_path": None,
                "balance": self.get_balance(), "threshold": threshold,
                "cost":    cost, "message": "Timeout: generation took >10 minutes",
            }
        except Exception as e:
            logger.error(f"[HIGGSFIELD] Unexpected error: {e}")
            return {
                "status":  "failed", "output_path": None,
                "balance": self.get_balance(), "threshold": threshold,
                "cost":    cost, "message": str(e),
            }

    # ── Fashion Scout Integration ──────────────────────────────────────────────
    def generate_product_tryon(
        self,
        product_image_path: str,
        outfit_description:  str,
        niche:               str = "ethnic",
    ) -> dict:
        """
        Convenience method for Fashion Scout integration.
        Takes a product image from Fashion Scout and generates a try-on video.

        Called by:  Fashion Scout → Higgsfield → Compiler → Uploader

        AMTCE Log flow:
            [fashion_scout]    Detected: Silk Lehenga (₹2,499)
            [higgsfield.engine] try_on queued | balance=4/4 credits — FIRING
            [higgsfield.engine] ✅ Generated: Influencer_Output/higgsfield/General/hf_try_on_....jpg
            [amtce.compiler]   Stitching try-on into final reel
        """
        style_map = {
            "ethnic":    "Amalfi Summer preset, warm golden light",
            "saree":     "editorial Indian fashion, studio lighting",
            "western":   "Tokyo Streetstyle, natural daylight",
            "luxury":    "Quiet Luxury preset, soft shadows",
            "activewear": "energetic lifestyle, bright natural light",
        }
        style = style_map.get(niche.lower(), "editorial fashion, studio lighting")
        prompt = (
            f"Professional fashion model wearing {outfit_description}, "
            f"{style}, clean background, ultra-realistic, 4K"
        )
        logger.info(
            f"[HIGGSFIELD] Fashion Scout try-on | item={outfit_description[:40]} "
            f"| image={os.path.basename(product_image_path)}"
        )
        return self.try_generate(
            prompt=prompt,
            generation_type="try_on",
            product_image=product_image_path,
        )

    # ── Status Report ──────────────────────────────────────────────────────────
    def status_report(self) -> str:
        """Returns a human-readable status string for Telegram bot / logs."""
        s = self.state
        lines = [
            f"🎬 Higgsfield Engine — {self.persona_name}",
            f"💳 Credits: {s.get('credit_balance', 0)} remaining",
            f"📊 Lifetime spent: {s.get('lifetime_spent', 0)} | earned: {s.get('lifetime_earned', 0)}",
            f"🎥 Total generations: {s.get('total_generations', 0)}",
            f"🕐 Last generation: {s.get('last_generation', 'never')}",
            f"🖥️  CLI installed: {'✅' if self.cli_available else '❌'}",
            "",
            "📌 Credit thresholds:",
            f"   Image (try-on): {GENERATION_THRESHOLD['try_on']} credits",
            f"   Video (3-4s):   {GENERATION_THRESHOLD['video']} credits",
        ]
        return "\n".join(lines)


# ── Module-level singleton (lazy init) ────────────────────────────────────────
_engine_cache: dict = {}

def get_engine(persona_name: str = "General") -> HiggsfieldEngine:
    """Return a cached HiggsfieldEngine for the given persona."""
    if persona_name not in _engine_cache:
        _engine_cache[persona_name] = HiggsfieldEngine(persona_name)
    return _engine_cache[persona_name]
