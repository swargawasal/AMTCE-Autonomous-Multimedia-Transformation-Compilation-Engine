"""
DEBT: One-Time Chunked Video Uploader (deliver_video.py)
----------------------------------------------------
Purpose:
- [DEPRECATED] Used for emergency recovery to send large files via chunked Telegram API calls.
- Hardcoded for: 'D:\\whatsupneyork\\Processed Shorts\\Pooja_hedge_3.mp4'

When to run:
- DO NOT RUN. This is a legacy developer tool from a specific server recovery event.
- Kept only for reference on how to implement the 'ChunkedProgressFile' wrapper.
"""

import asyncio
import os
import sys
import io
import time
from telegram import Bot
from dotenv import load_dotenv

# Add current dir to path to import local modules
sys.path.append(os.getcwd())
load_dotenv('Credentials/.env')

class ChunkedProgressFile(io.RawIOBase):
    def __init__(self, filename, logger_func, chunk_size=64*1024):
        self._f = open(filename, 'rb')
        self._size = os.path.getsize(filename)
        self._seen = 0
        self._last_log = -5
        self._logger = logger_func
        self._chunk_size = chunk_size
        self._path = filename

    def read(self, size=-1):
        read_size = self._chunk_size if size == -1 or size > self._chunk_size else size
        chunk = self._f.read(read_size)
        if chunk:
            self._seen += len(chunk)
            pct = int((self._seen / self._size) * 100)
            if pct >= self._last_log + 5:
                self._logger(f"📤 Progress: {pct}% ({os.path.basename(self._path)})")
                self._last_log = pct
            # Aggressive throttle
            time.sleep(0.05)
        return chunk

    def readable(self): return True
    def seekable(self): return True
    def seek(self, offset, whence=0): return self._f.seek(offset, whence)
    def tell(self): return self._f.tell()
    def close(self): return self._f.close()
    def fileno(self): return self._f.fileno()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

async def send():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("chat_id")
    
    if not token or not chat_id:
        print("ERROR: Missing TELEGRAM_BOT_TOKEN or chat_id in Credentials/.env")
        return
        
    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    else:
        print("ERROR: Missing video path argument")
        return
    if not os.path.exists(video_path):
        print(f"ERROR: {video_path} not found")
        return

    bot = Bot(token)
    async with bot:
        print(f"Starting CHUNKED upload: {os.path.basename(video_path)}")
        for attempt in range(1, 4):
            try:
                # Using our custom chunked wrapper
                with ChunkedProgressFile(video_path, print) as vf:
                    await bot.send_video(
                        chat_id=chat_id,
                        video=vf,
                        caption=f'Recovery (Chunked): Pooja_hedge_3.mp4 (Attempt {attempt})',
                        read_timeout=None,
                        write_timeout=None,
                        connect_timeout=600,
                        pool_timeout=600
                    )
                print("✅ DONE")
                return
            except Exception as e:
                print(f"⚠️ Attempt {attempt} failed: {e}")
                if attempt < 3:
                    print("Cooling down for 15s...")
                    await asyncio.sleep(15)
                else:
                    print("❌ TOTAL FAILURE")

if __name__ == "__main__":
    asyncio.run(send())
