import os
import sys
import time
import json
import urllib.request
import urllib.parse
import argparse

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==============================================================================
# AMTCE YouTube Authentication Script
# 
# To refresh or generate a token manually via CLI, run this script from the AMTCE root directory:
#
#   1. Default (Root credentials):
#      python scripts/auth_youtube.py
#
#   2. Niche-specific credentials (e.g., for 'fashion'):
#      python scripts/auth_youtube.py --secret "Credentials/social_media/fashion/client_secret.json" --token "Credentials/social_media/fashion/token.json"
#
# This will trigger the OAuth flow and save the new token.json to the specified path.
# ==============================================================================

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
]

DEFAULT_CLIENT_SECRET_FILE = "Credentials/client_secret.json"
DEFAULT_TOKEN_FILE         = "Credentials/token.json"
AUTH_CODE_FILE             = "Credentials/yt_auth_code.txt"

DEVICE_CODE_URL  = "https://oauth2.googleapis.com/device/code"
TOKEN_URL        = "https://oauth2.googleapis.com/token"


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _send_telegram(message: str, token: str, admin_id: str, button_url: str = None):
    try:
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": admin_id, "text": message, "parse_mode": "HTML"}
        if button_url:
            payload["reply_markup"] = json.dumps({
                "inline_keyboard": [[{"text": "🔗 Tap to Authorize", "url": button_url}]]
            })
        data = urllib.parse.urlencode(payload).encode("utf-8")
        urllib.request.urlopen(api_url, data=data, timeout=10)
        print("📡 Telegram notification sent.")
        return True
    except Exception as e:
        print(f"⚠️ Telegram send failed: {e}")
        return False


def _get_telegram_creds():
    """
    Returns (bot_token, admin_private_chat_id).
    ALWAYS sends to the ADMIN's private chat — NEVER to a group.
    Priority: TELEGRAM_ADMIN_ID > TELEGRAM_OWNER_CHAT_ID > first entry of ADMIN_IDS
    """
    try:
        from dotenv import load_dotenv
        for p in ["Credentials/.env", ".env"]:
            if os.path.exists(p):
                load_dotenv(p, override=False)
                break
    except ImportError:
        pass

    token = os.getenv("TELEGRAM_BOT_TOKEN")

    # Strictly private-chat admin ID — group IDs are negative, we want a positive user ID
    admin_id = (
        os.getenv("TELEGRAM_ADMIN_ID")            # preferred: explicit admin chat ID
        or os.getenv("TELEGRAM_OWNER_CHAT_ID")    # fallback 1
        or (
            os.getenv("ADMIN_IDS", "").split(",")[0].strip()  # fallback 2: first admin
            if os.getenv("ADMIN_IDS") else None
        )
    )

    if admin_id and (str(admin_id).startswith("@") or str(admin_id).startswith("-")):
        print(f"⚠️ WARNING: admin_id='{admin_id}' looks like a public GROUP/CHANNEL. "
              "Auth messages will NOT be sent to groups. Set TELEGRAM_ADMIN_ID to your personal chat ID.")
        admin_id = None  # refuse to send to group

    print(f"📡 Auth will notify: chat_id={admin_id}")
    return token, admin_id


def _load_client_secret(secret_path):
    with open(secret_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Supports both "installed" and "tv" / "web" top-level keys
    for key in ("installed", "tv", "web"):
        if key in data:
            return data[key]
    raise ValueError(f"Unrecognised client_secret.json format (top-level keys: {list(data.keys())})")


# ── Device Flow (fully automatic — user just goes to URL and signs in) ────────

def _try_device_flow(client_id, client_secret, tg_token, tg_admin, token_path):
    """
    Google Device Authorization Grant.
    Requires app type = 'TV and Limited Input devices' in Google Cloud Console.
    Returns True on success, False if device flow is unsupported.
    """
    print("📺 Trying Device Authorization Flow...")

    # Step 1 — request device + user code
    try:
        req_data = urllib.parse.urlencode({
            "client_id": client_id,
            "scope": " ".join(SCOPES)
        }).encode("utf-8")
        resp = urllib.request.urlopen(DEVICE_CODE_URL, data=req_data, timeout=15)
        device_resp = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"ℹ️ Device flow not available: {e}")
        return False

    if "error" in device_resp:
        print(f"ℹ️ Device flow rejected by Google: {device_resp.get('error')}")
        return False

    device_code      = device_resp["device_code"]
    user_code        = device_resp["user_code"]
    verification_url = device_resp.get("verification_url", "https://www.google.com/device")
    expires_in       = int(device_resp.get("expires_in", 1800))
    interval         = int(device_resp.get("interval", 5))

    print(f"\n📺 DEVICE FLOW ACTIVE")
    print(f"   Go to: {verification_url}")
    print(f"   Enter code: {user_code}\n")

    # Step 2 — tell user via Telegram
    if tg_token and tg_admin:
        msg = (
            f"🔐 <b>YouTube Auth Required</b>\n\n"
            f"1️⃣ Open this link on your phone:\n"
            f"<a href='{verification_url}'>{verification_url}</a>\n\n"
            f"2️⃣ Enter this code:\n"
            f"<code>  {user_code}  </code>\n\n"
            f"3️⃣ Sign in with Google\n\n"
            f"✅ Authorization will complete automatically!"
        )
        _send_telegram(msg, tg_token, tg_admin, button_url=verification_url)

    # Step 3 — poll for completion
    deadline = time.time() + expires_in
    print("⏳ Polling for authorization...")
    while time.time() < deadline:
        time.sleep(interval)
        try:
            poll_data = urllib.parse.urlencode({
                "client_id":     client_id,
                "client_secret": client_secret,
                "device_code":   device_code,
                "grant_type":    "urn:ietf:params:oauth:grant-type:device_code"
            }).encode("utf-8")
            poll_resp = urllib.request.urlopen(TOKEN_URL, data=poll_data, timeout=15)
            token_data = json.loads(poll_resp.read().decode("utf-8"))

            if "access_token" in token_data:
                # Build a token.json compatible with google-auth
                token_json = {
                    "token":         token_data["access_token"],
                    "refresh_token": token_data.get("refresh_token"),
                    "token_uri":     TOKEN_URL,
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "scopes":        SCOPES,
                }
                os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
                with open(token_path, "w", encoding="utf-8") as f:
                    json.dump(token_json, f, indent=2)
                print(f"✅ Authorized! Token saved to {token_path}")
                if tg_token and tg_admin:
                    _send_telegram(
                        "✅ <b>YouTube Authorized!</b>\n\nToken saved. Uploads will resume automatically.",
                        tg_token, tg_admin
                    )
                return True

        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            err  = body.get("error", "")
            if err == "authorization_pending":
                continue          # normal — user hasn't approved yet
            if err == "slow_down":
                interval += 5
                continue
            if err in ("access_denied", "expired_token"):
                print(f"❌ Device flow failed: {err}")
                if tg_token and tg_admin:
                    _send_telegram(f"❌ Auth failed: {err}. Send /ytcode to try again.", tg_token, tg_admin)
                return True       # Handled (even if denied)
            print(f"⚠️ Unexpected device flow error: {body}")
            return False
        except Exception as e:
            print(f"⚠️ Poll error: {e}")
            time.sleep(interval)

    print("❌ Device flow timed out.")
    if tg_token and tg_admin:
        _send_telegram("⏱️ Auth timed out. Send /ytcode to start again.", tg_token, tg_admin)
    return True  # Handled (just timed out)


# ── Fallback: URL + manual code paste flow ────────────────────────────────────

def _fallback_url_flow(secret_path, token_path, tg_token, tg_admin):
    """Used when device flow is unavailable (app type = Desktop)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    is_headless = (
        os.getenv("GITHUB_ACTIONS") == "true"
        or os.getenv("CI") == "true"
        or not sys.stdin.isatty()
    )

    flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)

    if not is_headless:
        try:
            creds = flow.run_local_server(port=0, access_type="offline", prompt="consent", open_browser=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            print(f"✅ Token saved to {token_path}")
            return
        except Exception as e:
            print(f"ℹ️ Browser flow failed: {e}")

    # Headless — send link, wait for user to paste code back via /ytcode
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print(f"\n🔗 AUTH URL:\n{auth_url}\n")

    if tg_token and tg_admin:
        msg = (
            f"🔐 <b>YouTube Re-Auth Required</b>\n\n"
            f"⚠️ <i>Your Google app type is 'Desktop' — one extra step needed:</i>\n\n"
            f"1️⃣ Tap <b>Tap to Authorize</b> below\n"
            f"2️⃣ Sign in with Google\n"
            f"3️⃣ Copy the code shown (or the full URL from your browser)\n"
            f"4️⃣ Send it here: <code>/ytcode YOUR_CODE</code>\n\n"
            f"<b>To make this fully automatic (no code needed):</b>\n"
            f"Change your Google Cloud app type to <b>TV and Limited Input Devices</b>."
        )
        _send_telegram(msg, tg_token, tg_admin, button_url=auth_url)
        _send_telegram(f"🔗 Auth URL:\n{auth_url}", tg_token, tg_admin)

    # Poll for code dropped by /ytcode bot command
    print("⏳ Waiting for /ytcode code (10 min)...")
    deadline = time.time() + 600
    while time.time() < deadline:
        if os.path.exists(AUTH_CODE_FILE):
            try:
                with open(AUTH_CODE_FILE, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                os.remove(AUTH_CODE_FILE)

                # Auto-extract from full URL
                if raw.startswith("http"):
                    parsed = urllib.parse.urlparse(raw)
                    qs = urllib.parse.parse_qs(parsed.query)
                    raw = qs.get("code", [raw])[0]

                flow.fetch_token(code=raw)
                creds = flow.credentials
                os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                print(f"✅ Token saved to {token_path}")
                if tg_token and tg_admin:
                    _send_telegram("✅ <b>YouTube Authorized!</b>\nToken saved. Uploads resume automatically.", tg_token, tg_admin)
                return
            except Exception as e:
                print(f"❌ Code exchange failed: {e}")
                if tg_token and tg_admin:
                    _send_telegram(f"❌ Code exchange failed:\n<code>{e}</code>\n\nSend /ytcode to try again.", tg_token, tg_admin)
                deadline = time.time() + 300
        time.sleep(5)

    print("❌ Timed out.")
    if tg_token and tg_admin:
        _send_telegram("⏱️ Auth timed out. Send /ytcode to start again.", tg_token, tg_admin)


# ── Main entry point ──────────────────────────────────────────────────────────

def authenticate(client_secret_file=None, token_file=None):
    secret_path = client_secret_file or DEFAULT_CLIENT_SECRET_FILE
    token_path  = token_file or DEFAULT_TOKEN_FILE
    tg_token, tg_admin = _get_telegram_creds()

    print("🚀 Starting YouTube Authentication...")

    if not os.path.exists(secret_path):
        msg = (
            f"❌ <b>YouTube Auth FAILED</b>\n\n"
            f"<b>client_secret.json</b> missing at <code>{secret_path}</code>\n\n"
            f"Download from Google Cloud Console → APIs &amp; Services → Credentials."
        )
        print(f"❌ {secret_path} not found!")
        if tg_token and tg_admin:
            _send_telegram(msg, tg_token, tg_admin)
        return

    try:
        secret = _load_client_secret(secret_path)
    except Exception as e:
        print(f"❌ Failed to read client_secret.json: {e}")
        return

    client_id     = secret["client_id"]
    client_secret = secret["client_secret"]

    # Try Device Flow first (fully automatic — no code pasting)
    handled = _try_device_flow(client_id, client_secret, tg_token, tg_admin, token_path)

    if not handled:
        # Device flow unsupported → fallback to URL + /ytcode paste
        print("⬇️ Falling back to URL auth flow...")
        _fallback_url_flow(secret_path, token_path, tg_token, tg_admin)


if __name__ == "__main__":
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root_dir)

    parser = argparse.ArgumentParser(description="AMTCE YouTube Authentication")
    parser.add_argument("--secret", help="Path to client_secret.json")
    parser.add_argument("--token",  help="Path to save token.json")
    args = parser.parse_args()

    authenticate(client_secret_file=args.secret, token_file=args.token)
