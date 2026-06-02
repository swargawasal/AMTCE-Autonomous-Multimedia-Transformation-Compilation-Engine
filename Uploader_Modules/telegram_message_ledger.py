import os
import json
import logging
import random
import threading
from datetime import datetime
from typing import Dict, List, Optional, Set
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

LEDGER_FILE = "The_json/message_ledger.json"
_ledger_lock = threading.Lock()

def record_telegram_post(message_id: int, chat_id: str, title: str, caption: str, reply_markup) -> None:
    """
    Safely appends a new sent message to the historical ledger.
    """
    try:
        os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)
        
        # Extract buttons from reply_markup
        buttons = []
        if reply_markup:
            try:
                if hasattr(reply_markup, 'inline_keyboard'):
                    for row in reply_markup.inline_keyboard:
                        row_buttons = []
                        for btn in row:
                            row_buttons.append({
                                "text": btn.text,
                                "url": btn.url
                            })
                        buttons.append(row_buttons)
            except Exception as e:
                logger.warning(f"[LEDGER] Failed to parse reply_markup buttons: {e}")

        post_entry = {
            "message_id": message_id,
            "chat_id": chat_id,
            "title": title,
            "caption": caption,
            "buttons": buttons,
            "timestamp": datetime.now().isoformat()
        }
        
        with _ledger_lock:
            data = []
            if os.path.exists(LEDGER_FILE):
                try:
                    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if not isinstance(data, list):
                            data = []
                except Exception as e:
                    logger.error(f"[LEDGER] Error reading ledger: {e}")
                    data = []
            
            data.append(post_entry)
            
            with open(LEDGER_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"💾 [LEDGER] Recorded post message_id={message_id} in {LEDGER_FILE}")
    except Exception as e:
        logger.error(f"❌ [LEDGER] Failed to record Telegram post: {e}")

def replace_link_in_json_file(filepath: str, old_link: str, new_link: str) -> bool:
    """
    Search-and-replaces old_link with new_link in a json file.
    Returns True if a replacement was successfully made.
    """
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        if old_link in content:
            updated_content = content.replace(old_link, new_link)
            # Validate JSON before writing
            json.loads(updated_content)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(updated_content)
            logger.info(f"🔄 [LINK_POOL] Successfully updated links in {filepath}")
            return True
    except Exception as e:
        logger.error(f"❌ [LINK_POOL] Error replacing link in {filepath}: {e}")
    return False

async def update_affiliate_link(bot: telegram.Bot, old_link: Optional[str] = None, new_link: Optional[str] = None) -> dict:
    """
    Main execution routine for link rotation and replacement.
    
    If new_link is None:
      - Reads active pool links from The_json/los_pollos_links.json and Monetization_Metrics/los_pollos_links.json.
      - Randomly rotates/picks one as the new link.
      
    If old_link is None:
      - Automatically detects whatever old/matching links are currently in each message and replaces them.
    """
    summary = {
        "status": "success",
        "new_link": None,
        "posts_scanned": 0,
        "posts_updated": 0,
        "pools_updated": [],
        "details": []
    }
    
    # 1. Load active CPA links pool to identify targets
    cpa_links: Set[str] = set()
    pool_paths = [
        "The_json/los_pollos_links.json",
        "Monetization_Metrics/los_pollos_links.json"
    ]
    for pool_path in pool_paths:
        if os.path.exists(pool_path):
            try:
                with open(pool_path, "r", encoding="utf-8") as f:
                    pool_data = json.load(f)
                    if isinstance(pool_data, list):
                        for item in pool_data:
                            if isinstance(item, list):
                                for sub_item in item:
                                    cpa_links.add(sub_item.strip())
                            elif isinstance(item, str):
                                cpa_links.add(item.strip())
            except Exception as e:
                logger.error(f"[LINK_UPDATER] Error loading links from {pool_path}: {e}")

    # 2. Determine target links
    if not new_link:
        if not cpa_links:
            summary["status"] = "failed"
            summary["details"].append("Error: No CPA links found in the active pools to select from.")
            return summary
        # Randomly rotate and select one link from the pool
        new_link = random.choice(list(cpa_links))
        summary["details"].append(f"Auto-selected new link from pool: {new_link}")
    
    summary["new_link"] = new_link
    
    # If old_link is explicitly provided, we only replace that specific link
    # Otherwise, we replace any matching link from our pool (or any old CPA link)
    targets_to_replace = {old_link} if old_link else cpa_links
    
    # 3. Read the message ledger
    ledger_entries = []
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                ledger_entries = json.load(f)
        except Exception as e:
            logger.error(f"[LINK_UPDATER] Error loading ledger: {e}")
            summary["status"] = "failed"
            summary["details"].append(f"Error loading ledger: {e}")
            return summary
            
    if not ledger_entries:
        summary["details"].append("No sent posts found in the ledger to update.")
        return summary
        
    summary["posts_scanned"] = len(ledger_entries)
    
    # 4. Iterate and update matching posts on Telegram
    updated_entries = []
    has_changes = False
    
    for entry in ledger_entries:
        msg_id = entry.get("message_id")
        chat_id = entry.get("chat_id")
        caption = entry.get("caption", "")
        buttons = entry.get("buttons", [])
        title = entry.get("title", "Post")
        
        post_updated = False
        new_caption = caption
        new_buttons = []
        
        # Check and replace links in Caption
        for target in targets_to_replace:
            if target and target in new_caption:
                new_caption = new_caption.replace(target, new_link)
                post_updated = True
        
        # Check and replace links in Inline Buttons
        for row in buttons:
            new_row = []
            for btn in row:
                btn_url = btn.get("url", "")
                btn_text = btn.get("text", "")
                
                # Check for link replacement
                btn_updated = False
                for target in targets_to_replace:
                    if target and target == btn_url:
                        btn_url = new_link
                        btn_updated = True
                        post_updated = True
                
                new_row.append({
                    "text": btn_text,
                    "url": btn_url
                })
            new_buttons.append(new_row)
            
        if post_updated:
            # Attempt to edit the Telegram message
            try:
                # Reconstruct reply markup
                kb_rows = []
                for row in new_buttons:
                    row_btns = []
                    for btn in row:
                        row_btns.append(InlineKeyboardButton(btn["text"], url=btn["url"]))
                    kb_rows.append(row_btns)
                reply_markup = InlineKeyboardMarkup(kb_rows) if kb_rows else None
                
                # Edit caption
                try:
                    await bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=msg_id,
                        caption=new_caption[:1024],
                        reply_markup=reply_markup
                    )
                    summary["posts_updated"] += 1
                    summary["details"].append(f"✅ Updated post '{title}' (ID: {msg_id}) in chat {chat_id}")
                except telegram.error.BadRequest as bre:
                    if "message is not modified" in str(bre).lower():
                        # Link was already updated or identical, ignore
                        summary["details"].append(f"ℹ️ Post '{title}' (ID: {msg_id}) was already up to date.")
                    else:
                        raise bre
                
                # Save updated state
                entry["caption"] = new_caption
                entry["buttons"] = new_buttons
                has_changes = True
                
            except Exception as ex:
                logger.error(f"[LINK_UPDATER] Failed to edit telegram message {msg_id} in {chat_id}: {ex}")
                summary["details"].append(f"❌ Failed to update post '{title}' (ID: {msg_id}): {ex}")
                
        updated_entries.append(entry)

    # Save ledger if any post was modified
    if has_changes:
        with _ledger_lock:
            try:
                with open(LEDGER_FILE, "w", encoding="utf-8") as f:
                    json.dump(updated_entries, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"[LINK_UPDATER] Failed to save updated ledger: {e}")
                summary["details"].append(f"Failed to write to ledger file: {e}")

    # 5. Surgical replacement in active JSON pools (only if old_link and new_link are explicitly provided)
    if old_link and new_link:
        pools = [
            "The_json/los_pollos_links.json",
            "Monetization_Metrics/los_pollos_links.json",
            "Monetization_Metrics/Amazon_affliate_link.json"
        ]
        for pool in pools:
            if replace_link_in_json_file(pool, old_link, new_link):
                summary["pools_updated"].append(pool)
                summary["details"].append(f"🔄 Replaced link in active pool file: {pool}")
                
    return summary
