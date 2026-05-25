"""
Higgsfield_Modules/outfit_swap_engine.py
=========================================
Outfit Swap Engine — AMTCE Native

Flow:
  1. AMTCE processes a video → Fashion Scout identifies the wear
  2. The best fashion frame (highlighted wear crop) is saved with the scout result
  3. Video is sent to Telegram group (existing pipeline)
  4. Bot sends admin a ⭐ rating prompt (1–5 stars via inline keyboard)
  5. If admin rates 5 stars → frame is marked "BEST" in the rating store
  6. On next credit-refresh cycle → OutfitSwapEngine picks the highest-rated
     product image and runs Higgsfield product-photoshoot with face.jpg

Rating Store: Influencer_Output/higgsfield/outfit_ratings.json
Best Queue:   Influencer_Output/higgsfield/swap_queue.json

Author: AMTCE / Antigravity
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import urllib.request
from datetime import datetime
from typing import Optional

logger = logging.getLogger("outfit_swap_engine")

# ── Paths ──────────────────────────────────────────────────────────────────────
_BASE          = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT  = os.path.dirname(_BASE)
OUTPUT_DIR     = os.path.join(_PROJECT_ROOT, "Influencer_Output", "higgsfield", "outfit_swaps")
RATING_STORE   = os.path.join(_PROJECT_ROOT, "Influencer_Output", "higgsfield", "outfit_ratings.json")
SWAP_QUEUE     = os.path.join(_PROJECT_ROOT, "Influencer_Output", "higgsfield", "swap_queue.json")
DB_PATH        = os.path.join(_PROJECT_ROOT, "Influencer_Output", "higgsfield", "outfit_events.db")


def _resolve_face_ref(niche: Optional[str] = None) -> str:
    """
    Resolves the best available face reference image for Higgsfield.
    Priority:
      1. Niche-specific actress folder (e.g., Credentials/social_media/<niche>/ai_influencer/face.jpg)
      2. INFLUENCER_FACE_IMAGE env key (set by user in .env)
      3. FACEFUSION_SOURCE_IMAGE env key
      4. Influencer_Modules/influencer_face/face.jpg (auto-scan)
      5. Credentials/social_media/<any>/ai_influencer/face.jpg
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_PROJECT_ROOT, "Credentials", ".env"))

    candidates = []

    # Priority 1: Actress-specific folder if niche is provided
    if niche:
        # Resolve clean folder name (strip path separators just in case)
        clean_niche = os.path.basename(niche.strip())
        sm_root = os.path.join(_PROJECT_ROOT, "Credentials", "social_media")
        for fname in ("face.jpg", "face.png", "face.jpeg"):
            p = os.path.join(sm_root, clean_niche, "ai_influencer", fname)
            candidates.append(p)

    # General priorities
    candidates += [
        os.getenv("INFLUENCER_FACE_IMAGE", "").strip(),
        os.getenv("FACEFUSION_SOURCE_IMAGE", "").strip(),
        os.path.join(_PROJECT_ROOT, "Influencer_Modules", "influencer_face", "face.jpg"),
        os.path.join(_PROJECT_ROOT, "Influencer_Modules", "influencer_face", "face.png"),
    ]

    # Resolve relative paths against project root
    resolved = []
    for c in candidates:
        if not c:
            continue
        if not os.path.isabs(c):
            c = os.path.join(_PROJECT_ROOT, c)
        resolved.append(c)

    for c in resolved:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            logger.info(f"[OUTFIT_SWAP] Using face reference: {c}")
            return c

    # Deep scan fallback: look inside social_media/<actress>/ai_influencer/
    sm_root = os.path.join(_PROJECT_ROOT, "Credentials", "social_media")
    if os.path.isdir(sm_root):
        for actress in os.listdir(sm_root):
            for fname in ("face.jpg", "face.png", "face.jpeg"):
                p = os.path.join(sm_root, actress, "ai_influencer", fname)
                if os.path.exists(p) and os.path.getsize(p) > 0:
                    logger.info(f"[OUTFIT_SWAP] Found face ref via scan: {p}")
                    return p

    logger.warning("[OUTFIT_SWAP] No face reference image found. Swap will fail.")
    return ""


FACE_REF_PATH  = _resolve_face_ref()   # resolved at import time; re-checked at swap time

# ── Higgsfield product-photoshoot modes ───────────────────────────────────────
# Mode controls the generation style — lifestyle_scene works best for fashion
PHOTOSHOOT_MODE   = "lifestyle_scene"
PHOTOSHOOT_COST   = 2          # credits (nano_banana_2)
CREDIT_THRESHOLD  = 2          # minimum credits needed before firing

# ── Star emoji map ─────────────────────────────────────────────────────────────
STAR_MAP = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐", 4: "⭐⭐⭐⭐", 5: "⭐⭐⭐⭐⭐"}


# ══════════════════════════════════════════════════════════════════════════════
# SQLite Store Setup & Legacy Migration
# ══════════════════════════════════════════════════════════════════════════════

def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _init_db():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                entry_id TEXT PRIMARY KEY,
                frame_path TEXT,
                wear_name TEXT,
                niche TEXT,
                outfit_description TEXT,
                video_id TEXT,
                rating INTEGER,
                rated_at TEXT,
                swap_done INTEGER DEFAULT 0,
                swap_output TEXT,
                registered_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS swap_queue (
                entry_id TEXT PRIMARY KEY,
                frame_path TEXT,
                wear_name TEXT,
                niche TEXT,
                outfit_description TEXT,
                queued_at TEXT,
                swap_done INTEGER DEFAULT 0,
                swap_output TEXT,
                swapped_at TEXT
            )
        """)
        
        # Legacy JSON Migration - Ratings
        if os.path.exists(RATING_STORE):
            try:
                with open(RATING_STORE, "r", encoding="utf-8") as f:
                    old_ratings = json.load(f)
                for eid, data in old_ratings.items():
                    conn.execute("""
                        INSERT OR IGNORE INTO ratings 
                        (entry_id, frame_path, wear_name, niche, outfit_description, video_id, rating, rated_at, swap_done, swap_output, registered_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        data.get("entry_id", eid),
                        data.get("frame_path"),
                        data.get("wear_name"),
                        data.get("niche"),
                        data.get("outfit_description"),
                        data.get("video_id"),
                        data.get("rating"),
                        data.get("rated_at"),
                        1 if data.get("swap_done") else 0,
                        json.dumps(data.get("swap_output")) if data.get("swap_output") else None,
                        data.get("registered_at")
                    ))
                os.rename(RATING_STORE, RATING_STORE + ".migrated")
                logger.info("[OUTFIT_SWAP] Migrated outfit_ratings.json to SQLite.")
            except Exception as e:
                logger.error(f"[OUTFIT_SWAP] Rating JSON migration failed: {e}")

        # Legacy JSON Migration - Swap Queue
        if os.path.exists(SWAP_QUEUE):
            try:
                with open(SWAP_QUEUE, "r", encoding="utf-8") as f:
                    old_queue = json.load(f)
                for data in old_queue:
                    conn.execute("""
                        INSERT OR IGNORE INTO swap_queue 
                        (entry_id, frame_path, wear_name, niche, outfit_description, queued_at, swap_done, swap_output, swapped_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        data.get("entry_id"),
                        data.get("frame_path"),
                        data.get("wear_name"),
                        data.get("niche"),
                        data.get("outfit_description"),
                        data.get("queued_at"),
                        1 if data.get("swap_done") else 0,
                        json.dumps(data.get("swap_output")) if data.get("swap_output") else None,
                        data.get("swapped_at")
                    ))
                os.rename(SWAP_QUEUE, SWAP_QUEUE + ".migrated")
                logger.info("[OUTFIT_SWAP] Migrated swap_queue.json to SQLite.")
            except Exception as e:
                logger.error(f"[OUTFIT_SWAP] Swap Queue JSON migration failed: {e}")

_init_db()


# ══════════════════════════════════════════════════════════════════════════════
# Register a fashion frame for rating (called by AMTCE pipeline after Telegram post)
# ══════════════════════════════════════════════════════════════════════════════

def register_frame_for_rating(
    frame_path: str,
    wear_name:  str,
    niche:      str,
    video_id:   str,
    outfit_description: str = "",
) -> str:
    """
    Called immediately after Telegram group post succeeds.
    Stores the frame path + metadata in the rating store, pending admin rating.

    Returns: entry_id (used by the bot callback to link the rating back)
    """
    entry_id = f"{video_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    with _get_db() as conn:
        conn.execute("""
            INSERT INTO ratings 
            (entry_id, frame_path, wear_name, niche, outfit_description, video_id, swap_done, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        """, (entry_id, os.path.abspath(frame_path), wear_name, niche, outfit_description, video_id, datetime.now().isoformat()))

    logger.info(f"[OUTFIT_SWAP] Registered frame for rating: {entry_id} | {wear_name}")
    return entry_id


# ══════════════════════════════════════════════════════════════════════════════
# Record admin star rating (called by Telegram bot callback handler)
# ══════════════════════════════════════════════════════════════════════════════

def record_rating(entry_id: str, stars: int) -> dict:
    """
    Stores admin's star rating for the given entry.
    If stars == 5 → immediately adds to swap queue for next credit cycle.

    Returns the updated entry dict.
    """
    rated_at = datetime.now().isoformat()
    entry = {}
    with _get_db() as conn:
        row = conn.execute("SELECT * FROM ratings WHERE entry_id = ?", (entry_id,)).fetchone()
        if not row:
            logger.warning(f"[OUTFIT_SWAP] Unknown entry_id: {entry_id}")
            return {}
        
        entry = dict(row)
        entry["rating"] = stars
        entry["rated_at"] = rated_at
        
        conn.execute("UPDATE ratings SET rating = ?, rated_at = ? WHERE entry_id = ?", (stars, rated_at, entry_id))
        logger.info(f"[OUTFIT_SWAP] Rated {entry_id}: {STAR_MAP.get(stars, str(stars))}")

        # 5-star → add to swap queue immediately
        if stars == 5:
            # Avoid duplicates
            existing = conn.execute("SELECT 1 FROM swap_queue WHERE entry_id = ?", (entry_id,)).fetchone()
            if not existing:
                conn.execute("""
                    INSERT INTO swap_queue (entry_id, frame_path, wear_name, niche, outfit_description, queued_at, swap_done)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                """, (entry_id, entry["frame_path"], entry["wear_name"], entry["niche"], entry["outfit_description"], datetime.now().isoformat()))
                logger.info(f"[OUTFIT_SWAP] ⭐⭐⭐⭐⭐ Added to swap queue: {entry_id}")

    return entry


# ══════════════════════════════════════════════════════════════════════════════
# Get current Higgsfield credit balance
# ══════════════════════════════════════════════════════════════════════════════

def _get_credit_balance() -> float:
    """Returns live Higgsfield credit balance, or 0.0 on failure."""
    try:
        proc = subprocess.run(
            ["higgsfield", "account", "status"],
            capture_output=True, text=True, timeout=15,
            shell=(os.name == "nt"),
        )
        match = re.search(r"([\d.]+)\s*credit", proc.stdout.lower())
        if match:
            return float(match.group(1))
    except Exception as e:
        logger.warning(f"[OUTFIT_SWAP] Credit check failed: {e}")
    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Run outfit swap for next queued 5-star item
# ══════════════════════════════════════════════════════════════════════════════

def run_next_swap() -> dict:
    """
    Checks credit balance and fires one outfit swap if credits are sufficient.

    Uses: higgsfield product-photoshoot create
            --mode lifestyle_scene
            --image <face.jpg>
            --image <product_frame.jpg>
            --prompt "<wear_name> fashion editorial"
            --count 3

    Returns: result dict with status, output paths, and credits used.
    """
    balance = _get_credit_balance()
    logger.info(f"[OUTFIT_SWAP] Credit balance: {balance}")

    if balance < CREDIT_THRESHOLD:
        return {
            "status":  "waiting",
            "balance": balance,
            "message": f"Need {CREDIT_THRESHOLD} credits. Currently {balance}. Waiting for refresh.",
        }

    with _get_db() as conn:
        row = conn.execute("SELECT * FROM swap_queue WHERE swap_done = 0 ORDER BY queued_at ASC LIMIT 1").fetchone()
        if not row:
            return {"status": "empty", "message": "No 5-star items pending swap.", "balance": balance}
        item = dict(row)

    entry_id   = item["entry_id"]
    frame_path = item["frame_path"]
    wear_name  = item["wear_name"]
    niche      = item.get("niche", "fashion")

    if not os.path.exists(frame_path):
        logger.warning(f"[OUTFIT_SWAP] Frame not found: {frame_path} — skipping")
        with _get_db() as conn:
            conn.execute("UPDATE swap_queue SET swap_done = 1 WHERE entry_id = ?", (entry_id,))
        return {"status": "skipped", "message": f"Frame file missing: {frame_path}"}

    # Re-resolve face reference at swap time (picks up any newly added face images)
    face_ref = _resolve_face_ref(niche)
    if not face_ref:
        return {"status": "error", "message": f"No face reference image found for niche '{niche}'. Place a face.jpg in Influencer_Modules/influencer_face/ or set INFLUENCER_FACE_IMAGE in .env"}

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Build a focused commercial prompt for this product
    prompt = (
        f"Fashion editorial: model wearing {wear_name}. "
        f"Professional studio or lifestyle setting. "
        f"High fashion photography, magazine quality, soft natural lighting, "
        f"model facing camera, full outfit visible. "
        f"Commercial product showcase. Indian fashion aesthetic."
    )

    cmd = [
        "higgsfield", "product-photoshoot", "create",
        "--mode",    PHOTOSHOOT_MODE,
        "--prompt",  prompt,
        "--image",   face_ref,
        "--image",   frame_path,
        "--count",   "3",
        "--json",
    ]

    logger.info(f"[OUTFIT_SWAP] Firing swap: {wear_name}")
    logger.info(f"[OUTFIT_SWAP] CMD: {' '.join(cmd[:6])}...")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            shell=(os.name == "nt"),
        )

        stdout = proc.stdout.strip()
        output_paths = []

        if proc.returncode == 0:
            # Parse output URLs from JSON response
            urls = re.findall(r'https?://[^\s"]+\.(?:jpg|png|webp)', stdout)
            for idx, url in enumerate(urls):
                local_name = f"swap_{timestamp}_{idx+1}.jpg"
                local_path = os.path.join(OUTPUT_DIR, local_name)
                try:
                    urllib.request.urlretrieve(url, local_path)
                    output_paths.append(local_path)
                    logger.info(f"[OUTFIT_SWAP] Downloaded: {local_path}")
                except Exception as dl_err:
                    logger.warning(f"[OUTFIT_SWAP] Download failed: {dl_err}")
                    output_paths.append(url)   # store remote URL as fallback

            # Mark queue item done and Update rating store too
            swapped_at = datetime.now().isoformat()
            out_json = json.dumps(output_paths)
            with _get_db() as conn:
                conn.execute("UPDATE swap_queue SET swap_done = 1, swap_output = ?, swapped_at = ? WHERE entry_id = ?", 
                             (out_json, swapped_at, entry_id))
                conn.execute("UPDATE ratings SET swap_done = 1, swap_output = ? WHERE entry_id = ?",
                             (out_json, entry_id))

            return {
                "status":       "swapped",
                "entry_id":     entry_id,
                "wear_name":    wear_name,
                "output_paths": output_paths,
                "balance_after": _get_credit_balance(),
                "message":      f"Swap complete: {len(output_paths)} images generated for '{wear_name}'",
            }

        else:
            logger.error(f"[OUTFIT_SWAP] CLI failed (code {proc.returncode}): {proc.stderr[:200]}")
            return {
                "status":  "failed",
                "message": f"Higgsfield CLI error: {proc.stderr[:200]}",
                "balance": balance,
            }

    except subprocess.TimeoutExpired:
        logger.error("[OUTFIT_SWAP] Timeout > 10 min")
        return {"status": "failed", "message": "Timeout during photoshoot generation"}
    except Exception as e:
        logger.error(f"[OUTFIT_SWAP] Error: {e}")
        return {"status": "failed", "message": str(e)}


def _post_swap_results_to_telegram(output_paths: list, wear_name: str) -> None:
    """
    Auto-posts the generated outfit swap images to the Telegram group.
    Called after a successful run_next_swap().
    Non-blocking — runs in a background thread.
    """
    try:
        import asyncio
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_PROJECT_ROOT, "Credentials", ".env"))

        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_GROUP_ID", "")
        admin_id = os.getenv("ADMIN_IDS", "").split(",")[0].strip()

        if not token or not (chat_id or admin_id):
            logger.warning("[OUTFIT_SWAP] Telegram config missing — skipping auto-post.")
            return

        async def _do_post():
            import telegram
            bot = telegram.Bot(token=token)

            caption = (
                f"🎉 **AI Outfit Swap Ready!**\n\n"
                f"👗 `{wear_name}`\n"
                f"🖼️ {len(output_paths)} variations generated by Higgsfield AI.\n"
                f"📁 Saved to Influencer_Output/higgsfield/outfit_swaps/"
            )

            # Post to admin DM first
            if admin_id and admin_id.lstrip("-").isdigit():
                await bot.send_message(
                    chat_id=int(admin_id),
                    text=caption,
                    parse_mode="Markdown",
                )
                for p in output_paths:
                    if os.path.exists(str(p)):
                        with open(p, "rb") as img:
                            await bot.send_photo(chat_id=int(admin_id), photo=img)

        try:
            asyncio.run(_do_post())
        except RuntimeError:
            import threading
            def _run():
                asyncio.run(_do_post())
            threading.Thread(target=_run, daemon=True).start()

    except Exception as e:
        logger.warning(f"[OUTFIT_SWAP] Auto-post to Telegram failed: {e}")


def start_background_swap_processor(interval_minutes: int = 30) -> None:
    """
    Starts a background daemon thread that checks the swap queue every
    `interval_minutes` minutes and fires pending swaps when credits allow.
    Call once at bot startup.
    """
    import threading
    import time

    def _loop():
        logger.info(f"[OUTFIT_SWAP] Background swap processor started (interval={interval_minutes}m)")
        while True:
            try:
                with _get_db() as conn:
                    pending_count = conn.execute("SELECT COUNT(*) FROM swap_queue WHERE swap_done = 0").fetchone()[0]
                if pending_count > 0:
                    logger.info(f"[OUTFIT_SWAP] Auto-check: {pending_count} pending swap(s) in queue.")
                    result = run_next_swap()
                    status = result.get("status")
                    if status == "swapped":
                        paths = result.get("output_paths", [])
                        wear  = result.get("wear_name", "outfit")
                        logger.info(f"[OUTFIT_SWAP] ✅ Auto-swap done: {wear} — {len(paths)} images")
                        _post_swap_results_to_telegram(paths, wear)
                    elif status == "waiting":
                        logger.info(f"[OUTFIT_SWAP] ⏳ Waiting for credits: {result.get('message')}")
                    else:
                        logger.info(f"[OUTFIT_SWAP] Status: {status} — {result.get('message', '')}")
                else:
                    logger.debug("[OUTFIT_SWAP] Auto-check: queue empty.")
            except Exception as e:
                logger.warning(f"[OUTFIT_SWAP] Background processor error: {e}")

            time.sleep(interval_minutes * 60)

    t = threading.Thread(target=_loop, name="OutfitSwapProcessor", daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════════════════════
# Status summary (for bot /swap_status command)
# ══════════════════════════════════════════════════════════════════════════════

def get_status_summary() -> str:
    """Returns a human-readable status string for the Telegram bot."""
    balance = _get_credit_balance()
    
    with _get_db() as conn:
        total_rated = conn.execute("SELECT COUNT(*) FROM ratings WHERE rating IS NOT NULL").fetchone()[0]
        five_star = conn.execute("SELECT COUNT(*) FROM ratings WHERE rating = 5").fetchone()[0]
        pending_swaps = conn.execute("SELECT COUNT(*) FROM swap_queue WHERE swap_done = 0").fetchone()[0]
        done_swaps = conn.execute("SELECT COUNT(*) FROM swap_queue WHERE swap_done = 1").fetchone()[0]
        next_row = conn.execute("SELECT * FROM swap_queue WHERE swap_done = 0 ORDER BY queued_at ASC LIMIT 1").fetchone()
        next_item = dict(next_row) if next_row else None

    lines = [
        "👗 **Outfit Swap Engine Status**",
        "",
        f"💳 Higgsfield Credits: `{balance}`",
        f"🎯 Needed per swap: `{CREDIT_THRESHOLD}` credits",
        "",
        f"📊 Total rated outfits: `{total_rated}`",
        f"⭐⭐⭐⭐⭐ Five-star (queued for swap): `{five_star}`",
        f"⏳ Pending swaps: `{pending_swaps}`",
        f"✅ Completed swaps: `{done_swaps}`",
        "",
    ]

    if balance >= CREDIT_THRESHOLD and pending_swaps > 0:
        lines.append("🟢 **Ready to swap!** Run `/run_swap` to fire.")
    elif pending_swaps > 0:
        lines.append(f"🔴 Waiting for credits ({balance}/{CREDIT_THRESHOLD})")
    else:
        lines.append("💤 No 5-star items queued. Keep rating!")

    # Show next pending item
    if next_item:
        lines.append(f"\n🔜 Next: **{next_item['wear_name']}** ({next_item['niche']})")

    return "\n".join(lines)
