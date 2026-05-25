import os
import json
import time
import logging
import hashlib
from typing import List, Dict

import threading

logger = logging.getLogger("gemini_status")
STATUS_FILE = "The_json/gemini_status.json"
BAN_DURATION = 30 * 60  # 30 Minutes in seconds (Reduced from 2h based on 429 reset times)

class GeminiStatusManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = {"bans": {}, "api_key_hash": ""}
        self._load_status()
        self._check_key_change()

    def _load_status(self):
        with self.lock:
            try:
                if os.path.exists(STATUS_FILE):
                    with open(STATUS_FILE, "r") as f:
                        loaded = json.load(f)
                        self.status.update(loaded)
                else:
                    self.status = {"bans": {}, "api_key_hash": ""}
            except Exception as e:
                logger.error(f"Failed to load gemini status: {e}")
                self.status = {"bans": {}, "api_key_hash": ""}

    def _save_status(self):
        with self.lock:
            try:
                os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
                with open(STATUS_FILE, "w") as f:
                    json.dump(self.status, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save gemini status: {e}")

    def _check_key_change(self):
        """Checks if GEMINI_API_KEY has changed, and resets bans if so."""
        current_key = os.getenv("GEMINI_API_KEY", "")
        if not current_key:
            return

        current_hash = hashlib.sha256(current_key.encode()).hexdigest()
        
        with self.lock:
            old_hash = self.status.get("api_key_hash", "")
            if old_hash and old_hash != current_hash:
                logger.info("✨ [QUOTA RESET] New GEMINI_API_KEY detected. Clearing all previous bans.")
                self.status["bans"] = {}
            
            self.status["api_key_hash"] = current_hash
            
        self._save_status()

    def is_banned(self, model_name: str) -> bool:
        """Checks if a model is currently banned due to quota exceeded."""
        self._check_key_change() # Dynamic check
        
        with self.lock:
            ban_info = self.status.get("bans", {}).get(model_name)
            if not ban_info:
                return False
            
            ban_time = ban_info.get("timestamp", 0)
            if time.time() - ban_time < BAN_DURATION:
                return True
            else:
                return False

    def mark_banned(self, model_name: str):
        """Marks a model as banned for 24 hours."""
        logger.warning(f"🚫 [QUOTA BAN] Marking {model_name} as BANNED for 2h.")
        with self.lock:
            self.status.setdefault("bans", {})[model_name] = {
                "timestamp": time.time(),
                "reason": "429 Quota Exceeded"
            }
        self._save_status()

    def filter_models(self, model_list: List[str]) -> List[str]:
        """Returns a list of models that are not currently banned."""
        self._check_key_change() # Dynamic check
        
        current_time = time.time()
        valid_models = []
        with self.lock:
            # Clean expired bans while we are here
            banned_models = list(self.status.get("bans", {}).keys())
            for m in banned_models:
                if current_time - self.status["bans"][m].get("timestamp", 0) >= BAN_DURATION:
                    self.status["bans"].pop(m, None)
            
            for m in model_list:
                if m not in self.status.get("bans", {}):
                    valid_models.append(m)
        
        self._save_status()
        return valid_models

# Singleton Instance
manager = GeminiStatusManager()
