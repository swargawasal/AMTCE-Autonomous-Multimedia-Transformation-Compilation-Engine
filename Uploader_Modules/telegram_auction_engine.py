import os
import json
import time
import logging
import threading
import schedule
import urllib.request
import urllib.parse
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from contextlib import contextmanager
try:
    import google.generativeai as genai
except ImportError:
    import google.genai as genai

from PIL import Image

logger = logging.getLogger("auction_engine")
logger.setLevel(logging.INFO)

AUCTION_LEDGER_FILE = "The_json/auction_ledger.json"
AUCTION_SCHEDULE_FILE = "The_json/auction_schedule.json"

class AuctionState:
    """Manages the real-time state of the auction. Fully Thread-Safe."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        # Initial lock for singleton creation — safe because it only happens once at startup
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(AuctionState, cls).__new__(cls)
                    cls._instance._init()
        return cls._instance

    @contextmanager
    def safe_lock(self, timeout=10):
        """Non-blocking lock with timeout to prevent bot hangs."""
        acquired = self._lock.acquire(timeout=timeout)
        try:
            if not acquired:
                logger.error("❌ AuctionState Lock Timeout! Deadlock prevented.")
                raise TimeoutError("Auction system is temporarily busy. Please try again.")
            yield
        finally:
            if acquired:
                self._lock.release()

    def _init(self):
        self.ledger_file = AUCTION_LEDGER_FILE
        # Initial load is already inside the singleton's lock via __new__
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if not os.path.exists(self.ledger_file):
            return {
                "active": False,
                "product_name": "",
                "affiliate_link": "",
                "base_deposit": 549.0,
                "pot_total": 0.0,
                # bidders: user_id -> {"username": str, "total_paid": float, "verified": bool, "pending_bid": float, "bid_timestamp": float}
                "bidders": {}, 
                "cheater_watchlist": []
            }
        with open(self.ledger_file, 'r') as f:
            return json.load(f)

    def save_state(self):
        """Uses atomic writes to prevent JSON corruption during concurrency."""
        with self.safe_lock():
            temp_file = f"{self.ledger_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(self.state, f, indent=4)
            os.replace(temp_file, self.ledger_file)

    def get_leader(self) -> tuple:
        with self.safe_lock():
            if not self.state["bidders"]:
                return (None, 0.0)
            verified = {k: v for k, v in self.state["bidders"].items() if v.get("verified", False)}
            if not verified:
                return (None, 0.0)
            leader_id = max(verified, key=lambda k: verified[k]["total_paid"])
            return (verified[leader_id]["username"], verified[leader_id]["total_paid"])
            
    def update_pot_total(self):
        """Recalculates the pot based on verified extra bids."""
        with self.safe_lock():
            pot = 0.0
            base = self.state.get("base_deposit", 549.0)
            for uid, data in self.state["bidders"].items():
                if data.get("verified", False):
                    extra = max(0.0, data["total_paid"] - base)
                    pot += extra
            self.state["pot_total"] = pot
            self.save_state_locked() # Dedicated method for internal use
        
    def save_state_locked(self):
        """Internal save for when lock is already held."""
        temp_file = f"{self.ledger_file}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(self.state, f, indent=4)
        os.replace(temp_file, self.ledger_file)
        
    def register_bid(self, user_id: str, username: str, amount: float) -> str:
        """Called by main.py /bid handler"""
        with self.safe_lock():
            if not self.state.get("active", False):
                return "The auction is not currently active."
            
            uid = str(user_id)
            if uid not in self.state["bidders"]:
                return "You must /join the auction first and pay the base deposit."
                
            current_total = self.state["bidders"][uid]["total_paid"]
            new_total = current_total + amount
            
            # --- WHALE PROTOCOL (Upgraded A2) ---
            # Identify "Whales" (bidders > ₹5,000) for special announcement.
            if new_total >= 5000 and not self.state["bidders"][uid].get("whale_status"):
                self.state["bidders"][uid]["whale_status"] = True
                LiveLeaderboard.send_broadcast(f"🐳 <b>WHALE ALERT!</b> @{username} just crossed the ₹5,000 mark! The stakes are getting serious. 🔥")

            # Find current leader to ensure they are outbidding
            verified = {k: v for k, v in self.state["bidders"].items() if v.get("verified", False)}
            current_leader_total = 0.0
            if verified:
                leader_id = max(verified, key=lambda k: verified[k]["total_paid"])
                current_leader_total = verified[leader_id]["total_paid"]
                
            if new_total <= current_leader_total:
                return f"Your total bid (₹{new_total}) must be higher than the current leader (₹{current_leader_total})."
            
            self.state["bidders"][uid]["pending_bid"] = amount
            self.state["bidders"][uid]["bid_timestamp"] = time.time()
            self.state["bidders"][uid]["verified"] = False
            self.save_state_locked()
        return "SUCCESS"

class PaymentVerifier:
    """Handles manual UPI screenshot verification via Gemini Vision OCR."""
    @staticmethod
    def verify_screenshot(image_path: str) -> dict:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"status": "error", "message": "API key missing"}
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = """
            You are a payment verification assistant. Extract exact data from this UPI screenshot.
            Return ONLY a valid JSON object:
            { "utr_number": "string", "amount": "float", "payer_name": "string", "timestamp": "string" }
            """
            image = Image.open(image_path)
            response = model.generate_content([prompt, image])
            raw_text = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw_text)
            data["status"] = "success"
            return data
        except Exception as e:
            return {"status": "error", "message": str(e)}

class LiveLeaderboard:
    """Handles broadcasting pot updates and tier crossing alerts."""
    @staticmethod
    def send_broadcast(message: str, force_admin: bool = False):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_ADMIN_ID") if force_admin else os.getenv("TELEGRAM_GROUP_ID", os.getenv("TELEGRAM_ADMIN_ID"))
        if not token or not chat_id:
            return
        def _bg_send():
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = urllib.parse.urlencode({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode("utf-8")
                urllib.request.urlopen(url, data=data, timeout=10)
            except Exception as e:
                logger.error(f"Broadcast failed: {e}")
        
        threading.Thread(target=_bg_send, daemon=True).start()

    @staticmethod
    def announce_tier_crossing(old_pot: float, new_pot: float):
        """
        FOMO ESCALATION CASCADE (Upgraded A1)
        Changes tone based on pot size to drive maximum greed/social proof.
        """
        tiers = [
            {"limit": 500,  "msg": "🌱 <b>POT IS GROWING!</b> Small entry, big potential. Join now! 👇"},
            {"limit": 1500, "msg": "🚀 <b>1.5k REACHED!</b> This is becoming a major event. Don't watch from the sidelines! 👇"},
            {"limit": 3000, "msg": "💎 <b>3k MILESTONE!</b> Platform cut just DROPPED. The winner's prize is massive now! 👇"},
            {"limit": 6000, "msg": "🌋 <b>6k VOLCANO!</b> This is the biggest pot of the week! WHALES ARE IN. 🔥👇"},
            {"limit": 10000,"msg": "👑 <b>10k LEGENDARY STATUS!</b> Historical payout incoming. Final chance to be the winner! 👇"}
        ]
        for tier in tiers:
            if old_pot < tier["limit"] <= new_pot:
                LiveLeaderboard.send_broadcast(tier["msg"])

class FriendInviteEngine:
    @staticmethod
    def get_viral_message(pot_estimate: str = "500") -> str:
        return f"""🔥 TONIGHT'S FLASH DEAL — 7PM TO 9PM ONLY

Hey, I just joined a competition where I can win ₹{pot_estimate}+ in cash tonight just by bidding on a product I already wanted to buy anyway. 

Here's the deal: We both put in ₹549 (fully refundable if we lose). Whoever bids the highest wins the whole pot minus a small platform fee.

Even if YOU win, I don't care — at least my ₹549 comes back and one of us gets rich 😂

Join before 7PM 👇
{os.getenv('TELEGRAM_GROUP_LINK', 'Join our group!')}

P.S. If your friend group has that ONE person who always asks for money and never returns — invite them here. This time THEY pay YOU 😅"""

class SettlementEngine:
    """Calculates final payout math and platform cuts."""
    @staticmethod
    def calculate_platform_cut(pot_total: float, is_sunday: bool = False) -> dict:
        if pot_total <= 500: base_cut = 30
        elif pot_total <= 1500: base_cut = 25
        elif pot_total <= 3000: base_cut = 20
        elif pot_total <= 6000: base_cut = 15
        else: base_cut = 12
            
        final_cut_pct = max(0, base_cut - 5) if is_sunday else base_cut
        platform_revenue = (final_cut_pct / 100.0) * pot_total
        return {
            "pot_total": pot_total,
            "platform_cut_pct": final_cut_pct,
            "platform_revenue": platform_revenue,
            "winner_prize": pot_total - platform_revenue
        }
        
    @staticmethod
    def generate_settlement_report(state_dict: dict, is_sunday: bool = False) -> dict:
        bidders = state_dict.get("bidders", {})
        base = state_dict.get("base_deposit", 549.0)
        verified = {k: v for k, v in bidders.items() if v.get("verified", False)}
        
        if not verified:
            return {"status": "cancelled", "reason": "No verified bidders"}
            
        leader_id = max(verified, key=lambda k: verified[k]["total_paid"])
        winner = verified[leader_id]
        
        pot_total = sum(max(0.0, d["total_paid"] - base) for d in verified.values())
        math_res = SettlementEngine.calculate_platform_cut(pot_total, is_sunday)
        
        # Winner gets their base back TOO
        refunds = [{"username": d["username"], "refund_amount": base} for uid, d in verified.items()]
                
        return {
            "status": "success",
            "winner_username": winner["username"],
            "winner_total_bid": winner["total_paid"],
            "winner_cash_prize": math_res["winner_prize"],
            "platform_revenue": math_res["platform_revenue"],
            "refunds": refunds
        }

# ---------------------------------------------------------------------------
# FEATURED REEL PICKER
# ---------------------------------------------------------------------------

OUTPUT_BATCH_STATE_FILE = "The_json/output_batch_state.json"

def pick_featured_reel() -> Optional[str]:
    """
    Always returns a valid on-disk video path for the auction opening.
    Priority:
      0. The reel that was published today, from auction_schedule.json
      1. Clips processed/updated today (within last 24h) — freshest harvest
      2. Most recently updated clip from the output ledger (any day)
      3. None — caller sends text-only announcement
    """
    # Priority 0: The reel that was published today, from auction_schedule.json
    if os.path.exists("The_json/auction_schedule.json"):
        try:
            with open("The_json/auction_schedule.json") as f:
                sched = json.load(f)
            fpath = sched.get("featured_video_path", "")
            posted_at = sched.get("posted_at", 0)
            if fpath and os.path.exists(fpath) and (time.time() - posted_at) < 14400:
                logger.info("🎬 pick_featured_reel: using today's auction reel → %s", fpath)
                return fpath
        except Exception:
            pass

    try:
        if not os.path.exists(OUTPUT_BATCH_STATE_FILE):
            return None
        with open(OUTPUT_BATCH_STATE_FILE, 'r') as f:
            ledger: dict = json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ pick_featured_reel: could not load ledger: {e}")
        return None

    now = time.time()
    cutoff_24h = now - 86400  # 24 hours ago

    # Flatten all (actress, path, updated_at) tuples
    candidates = []
    for actress, data in ledger.items():
        updated_at = data.get("updated_at", 0)
        for fpath in data.get("files", []):
            candidates.append((actress, fpath, updated_at))

    if not candidates:
        return None

    # Sort newest first
    candidates.sort(key=lambda x: x[2], reverse=True)

    # Pass 1: today's clips (last 24h) that exist on disk
    todays = [(a, p, u) for a, p, u in candidates if u >= cutoff_24h and os.path.exists(p)]
    if todays:
        chosen = random.choice(todays)
        logger.info(f"🎬 pick_featured_reel: today's clip → {chosen[1]} ({chosen[0]})")
        return chosen[1]

    # Pass 2: any clip from ledger that exists on disk (most recent first)
    for actress, fpath, updated_at in candidates:
        if os.path.exists(fpath):
            logger.info(f"🎬 pick_featured_reel: fallback clip → {fpath} ({actress})")
            return fpath

    logger.warning("⚠️ pick_featured_reel: no on-disk clips found — text-only auction open")
    return None


def send_video_to_telegram(video_path: str, caption: str) -> bool:
    """
    Sends a local video file to the Telegram group using the Bot API.
    Returns True on success, False on failure.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_GROUP_ID", os.getenv("TELEGRAM_ADMIN_ID"))
    if not token or not chat_id:
        logger.warning("send_video_to_telegram: missing token or chat_id")
        return False
    try:
        import urllib.request
        import mimetypes
        url = f"https://api.telegram.org/bot{token}/sendVideo"
        boundary = "----AMTCE_BOUNDARY"
        mime_type = mimetypes.guess_type(video_path)[0] or "video/mp4"
        with open(video_path, 'rb') as vf:
            video_data = vf.read()
        filename = os.path.basename(video_path)

        # Build multipart/form-data body manually (no external deps)
        def encode_field(name, value):
            return (f"--{boundary}\r\n"
                    f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                    f"{value}\r\n").encode("utf-8")

        def encode_file(name, fname, data, ctype):
            header = (f"--{boundary}\r\n"
                      f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{fname}\"\r\n"
                      f"Content-Type: {ctype}\r\n\r\n").encode("utf-8")
            return header + data + b"\r\n"

        body = b""
        body += encode_field("chat_id", chat_id)
        body += encode_field("caption", caption)
        body += encode_field("parse_mode", "HTML")
        body += encode_file("video", filename, video_data, mime_type)
        body += f"--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                logger.info(f"✅ send_video_to_telegram: sent {filename}")
                return True
            else:
                logger.error(f"❌ send_video_to_telegram: API error: {result}")
                return False
    except Exception as e:
        logger.error(f"❌ send_video_to_telegram: {e}")
        return False


class SchedulerDaemon:
    """Time-loop wrapper for 6PM announce, 7PM open, 9PM close loops."""
    _thread = None
    _stop_event = threading.Event()
    
    @classmethod
    def start_background_polling(cls):
        if cls._thread and cls._thread.is_alive(): return
        cls._stop_event.clear()
        cls._thread = threading.Thread(target=cls._run_scheduler, daemon=True)
        cls._thread.start()
        logger.info("🚀 SchedulerDaemon for Auction Engine started.")
        
    @classmethod
    def _run_scheduler(cls):
        try:
            from dotenv import dotenv_values
            fashion_env = dotenv_values("Credentials/social_media/Fashion & Style/.env")
            manual_str = fashion_env.get("AUCTION_MANUAL_MODE", os.getenv("AUCTION_MANUAL_MODE", "False"))
        except:
            manual_str = os.getenv("AUCTION_MANUAL_MODE", "False")
            
        manual_mode = manual_str.lower() in ["true", "1", "yes"]
        
        if not manual_mode:
            # Standard Daily Schedule
            schedule.every().day.at("12:30").do(cls.job_announce_deal) # 6 PM IST
            schedule.every().day.at("13:30").do(cls.job_open_auction)  # 7 PM IST
            
            # --- WAR MODE: THE 15-MIN TICK (Upgraded A5) ---
            # Every 5 minutes in the final 15-minute window, we inject high-pressure alerts.
            schedule.every().day.at("15:15").do(cls.job_war_mode_tick, tick=3) # 8:45 PM IST
            schedule.every().day.at("15:20").do(cls.job_war_mode_tick, tick=2) # 8:50 PM IST
            schedule.every().day.at("15:25").do(cls.job_war_mode_tick, tick=1) # 8:55 PM IST
            
            schedule.every().day.at("15:30").do(cls.job_close_auction) # 9 PM IST
        else:
            logger.info("🛠️ AUCTION_MANUAL_MODE is ON. Daily automatic schedule is disabled. Use /auction_start to begin.")
            
        # Runs every 1 minute to clear out cheaters who haven't uploaded proof
        schedule.every(1).minutes.do(cls.job_prune_cheaters)
        
        while not cls._stop_event.is_set():
            schedule.run_pending()
            time.sleep(30)
            
    @classmethod
    def job_prune_cheaters(cls):
        state = AuctionState()
        with state.safe_lock():
            if not state.state.get("active", False): return
            now = time.time()
            changed = False
            for uid, data in list(state.state["bidders"].items()):
                if not data.get("verified", False) and data.get("pending_bid", 0) > 0:
                    bid_time = data.get("bid_timestamp", now)
                    if (now - bid_time) > 120: # 2 minutes
                        logger.warning(f"Pruning cheater {data['username']} for unpaid bid.")
                        state.state["cheater_watchlist"].append(data["username"])
                        # Revert their pending status
                        state.state["bidders"][uid]["pending_bid"] = 0
                        # If this was their initial join deposit, remove them entirely
                        if state.state["bidders"][uid]["total_paid"] == 0:
                            del state.state["bidders"][uid]
                        changed = True
            if changed:
                state.save_state_locked()

    @classmethod
    def job_announce_deal(cls):
        if datetime.now().weekday() == 6: return # Skip Sunday
        state = AuctionState()
        if os.path.exists(AUCTION_SCHEDULE_FILE):
            try:
                with open(AUCTION_SCHEDULE_FILE, 'r') as f:
                    data = json.load(f)
                    state.state["product_name"] = data.get("product_name", "Mystery Deal")
                    state.state["affiliate_link"] = data.get("affiliate_link", "")
                    state.save_state()
            except Exception: pass
        
        product_name = state.state.get("product_name", "Mystery Deal")
        affiliate_link = state.state.get("affiliate_link", "")

        msg = (
            f"🛍️ <b>TONIGHT'S FLASH DEAL — 7PM IST</b>\n\n"
            f"🎯 Item: <b>{product_name}</b>\n"
            f"💰 Base deposit: ₹549 (refundable if you lose)\n"
            f"🏆 Winner takes the full pot!\n\n"
            f"{'🔗 ' + affiliate_link if affiliate_link else ''}\n\n"
            f"Watch the reel we just posted, then join the auction at 7PM! 🔥"
        )
        LiveLeaderboard.send_broadcast(msg)
        
    @classmethod
    def job_open_auction(cls):
        if datetime.now().weekday() == 6: return # Skip Sunday
        state = AuctionState()
        state.state["active"] = True
        state.save_state()

        # --- ALWAYS OPEN WITH A FEATURED REEL (even if today's harvest hasn't run yet) ---
        opening_caption = (
            "⚔️ <b>THE WAR HAS BEGUN!</b>\n"
            "Flash Deal is OPEN. 🔥\n"
            "Use /join to enter the auction and place your bid!"
        )
        featured_reel = pick_featured_reel()
        if featured_reel:
            # Send the reel as a video in a background thread so the scheduler isn't blocked
            def _send_reel():
                success = send_video_to_telegram(featured_reel, caption=opening_caption)
                if not success:
                    # Fallback: plain text broadcast
                    LiveLeaderboard.send_broadcast(opening_caption)
            threading.Thread(target=_send_reel, daemon=True).start()
        else:
            # No clip on disk — send text-only announcement
            LiveLeaderboard.send_broadcast(opening_caption)
        
    @classmethod
    def job_war_mode_tick(cls, tick: int):
        """High-pressure FOMO alerts for the final 15 minutes."""
        msgs = {
            3: "⚔️ <b>WAR MODE ACTIVATED!</b> 15 minutes left. Verified bids only. No mercy. 👇",
            2: "🚨 <b>10 MINUTES LEFT!</b> The pot is at a record high. Who wants it more? 👇",
            1: "🔥 <b>5 MINUTE FINAL STRIKE!</b> This is it. The last bid wins the legacy. GO! 👇"
        }
        LiveLeaderboard.send_broadcast(msgs.get(tick, "⏰ Closing soon!"))
        # Increase admin awareness
        LiveLeaderboard.send_broadcast(f"🚨 WAR MODE TICK {tick}/3. Monitor screenshots now!", force_admin=True)

    @classmethod
    def job_close_auction(cls):
        if datetime.now().weekday() == 6: return
        state = AuctionState()
        
        # Red Team Option B: The Auto-Extend Logic
        with state.safe_lock():
            unverified_pending = any(not d.get("verified", False) and d.get("pending_bid", 0) > 0 for d in state.state["bidders"].values())
            
        if unverified_pending:
            logger.warning("Unverified bids pending at 9PM! Extending by 5 minutes.")
            LiveLeaderboard.send_broadcast("🚨 <b>PENDING BIDS VERIFICATION!</b>\nAuction close extended by 5 minutes to verify final screenshots!")
            LiveLeaderboard.send_broadcast(f"🚨 ADMIN WAKE UP! Pending unverified bids holding up the close!", force_admin=True)
            # Re-schedule this close job to run exactly 5 minutes from now
            schedule.every().day.at((datetime.now() + schedule.datetime.timedelta(minutes=5)).strftime("%H:%M")).do(cls.job_close_auction)
            return

        state.state["active"] = False
        state.save_state()
        is_sunday = False # Sunday is skipped anyway
        report = SettlementEngine.generate_settlement_report(state.state, is_sunday)
        
        if report["status"] == "cancelled":
            LiveLeaderboard.send_broadcast("🛑 Auction Closed. No verified bidders.")
        else:
            msg = f"🏆 <b>AUCTION OVER!</b>\nWinner: @{report['winner_username']}\nPrize Won: ₹{report['winner_cash_prize']:.2f}\nProduct Link: {state.state['affiliate_link']}\nAll standby members will be refunded ₹549 shortly."
            LiveLeaderboard.send_broadcast(msg)

