import os
import logging
import requests
import time

logger = logging.getLogger("font_manager")

FONT_URL = "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf"
# Anchor font dir to the project root (this file lives at Text_Modules/font_manager.py,
# so the root is two levels up: Text_Modules -> project root)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_FONT_DIR = os.path.join(_PROJECT_ROOT, "assets", "fonts")
LOCAL_FONT_PATH = os.path.join(LOCAL_FONT_DIR, "Montserrat-Bold.ttf")
MIN_FONT_SIZE_BYTES = 80 * 1024  # Reject if under 80KB

def ensure_montserrat_font():
    """
    Downloads Montserrat-Bold.ttf if it does not exist or is corrupted.
    Returns the absolute path to the font file.
    """
    os.makedirs(LOCAL_FONT_DIR, exist_ok=True)
    
    if _validate_font():
        return os.path.abspath(LOCAL_FONT_PATH)
        
    logger.info("⬇️ Downloading Montserrat-Bold.ttf...")
    for attempt in range(3):
        try:
            response = requests.get(FONT_URL, timeout=15)
            response.raise_for_status()
            
            with open(LOCAL_FONT_PATH, "wb") as f:
                f.write(response.content)
                
            if _validate_font():
                logger.info("✅ Montserrat font downloaded and verified.")
                return os.path.abspath(LOCAL_FONT_PATH)
            else:
                logger.error("❌ Downloaded font validation failed (file too small).")
                
        except Exception as e:
            logger.warning(f"⚠️ Font download attempt {attempt + 1} failed: {e}")
            time.sleep(1)
            
    logger.error("❌ Failed to download Montserrat font. ASS subtitles may fallback or fail.")
    return None

def _validate_font():
    if not os.path.exists(LOCAL_FONT_PATH):
        return False
    if os.path.getsize(LOCAL_FONT_PATH) < MIN_FONT_SIZE_BYTES:
        return False
    return True
