import os
import sys
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

# Fix Windows console encoding issues
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Add parent directory to path to enable local module imports
sys.path.append(os.getcwd())

from Uploader_Modules.telegram_message_ledger import record_telegram_post, update_affiliate_link, LEDGER_FILE

@pytest.fixture(autouse=True)
def setup_teardown_ledger():
    # Backup ledger
    backup_exists = os.path.exists(LEDGER_FILE)
    backup_content = None
    if backup_exists:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            backup_content = f.read()
            
    yield
    
    # Restore ledger
    if backup_exists:
        with open(LEDGER_FILE, "w", encoding="utf-8") as f:
            f.write(backup_content)
    elif os.path.exists(LEDGER_FILE):
        try:
            os.remove(LEDGER_FILE)
        except Exception:
            pass

@pytest.mark.anyio
async def test_record_and_update_links():
    # Clean ledger first
    if os.path.exists(LEDGER_FILE):
        try:
            os.remove(LEDGER_FILE)
        except Exception:
            pass
        
    # Mock reply_markup buttons
    mock_btn = MagicMock()
    mock_btn.text = "🔥 Find Your Match"
    mock_btn.url = "https://r222mrb.casual-honeycasual.com/yd6huy5"
    
    mock_markup = MagicMock()
    mock_markup.inline_keyboard = [[mock_btn]]
    
    # 1. Test record post
    record_telegram_post(
        message_id=99999,
        chat_id="-10012345678",
        title="Test Post Title",
        caption="Check this out: https://r222mrb.casual-honeycasual.com/yd6huy5 #viral",
        reply_markup=mock_markup
    )
    
    assert os.path.exists(LEDGER_FILE)
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    assert len(data) == 1
    assert data[0]["message_id"] == 99999
    assert data[0]["caption"] == "Check this out: https://r222mrb.casual-honeycasual.com/yd6huy5 #viral"
    assert data[0]["buttons"] == [[{"text": "🔥 Find Your Match", "url": "https://r222mrb.casual-honeycasual.com/yd6huy5"}]]
    
    # 2. Test link updater with mock bot
    mock_bot = AsyncMock()
    
    # Run targeted surgical update
    summary = await update_affiliate_link(
        bot=mock_bot,
        old_link="https://r222mrb.casual-honeycasual.com/yd6huy5",
        new_link="https://newly-replaced-link.com"
    )
    
    assert summary["status"] == "success"
    assert summary["posts_updated"] == 1
    
    # Verify bot called edit_message_caption
    mock_bot.edit_message_caption.assert_called_once()
    args, kwargs = mock_bot.edit_message_caption.call_args
    assert kwargs["message_id"] == 99999
    assert kwargs["chat_id"] == "-10012345678"
    assert kwargs["caption"] == "Check this out: https://newly-replaced-link.com #viral"
    
    # Verify ledger updated
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        updated_data = json.load(f)
    assert updated_data[0]["caption"] == "Check this out: https://newly-replaced-link.com #viral"
    assert updated_data[0]["buttons"] == [[{"text": "🔥 Find Your Match", "url": "https://newly-replaced-link.com"}]]
