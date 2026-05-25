import logging
import os

logger = logging.getLogger("source_detector")

def detect_source(input_path, source_type_hint=None):
    """
    Detects whether the video source is reused content.
    Rules:
    - If input comes from a URL (Instagram, TikTok, YouTube, Twitter): reused_content = True, source_type = "link"
    - If input comes from direct upload (Telegram or local file): reused_content = False, source_type = "raw_upload"
    """
    reused = False
    source_type = "raw_upload"

    # Use explicit hint from main.py if available
    if source_type_hint:
        source_type = source_type_hint
        if source_type == "link":
            reused = True
    else:
        # Heuristic fallback if hint isn't passed (e.g. CLI usage)
        if hasattr(input_path, 'startswith') and (input_path.startswith("http") or "downloads" in input_path):
            # downloads/ usually contains files from links in this architecture
            reused = True
            source_type = "link"

    result = {
        "content_source": {
            "reused": reused,
            "source_type": source_type
        }
    }
    
    logger.info(f"🔍 Source Detection: {source_type} (Reused: {reused})")
    return result
