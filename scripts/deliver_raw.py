"""
DEBT: One-Time Raw Video Uploader (deliver_raw.py)
-----------------------------------------------
Purpose:
- [DEPRECATED] Used as an emergency recovery script to send a specific file to Telegram via raw HTTPX.
- Hardcoded to upload: 'D:\\whatsupneyork\\Processed Shorts\\Pooja_hedge_3.mp4'

When to run:
- DO NOT RUN. This is a legacy developer tool from a specific server recovery event.
- Kept only for internal reference of the 'file_generator' throttling logic.
"""

import httpx
import os
import sys
import sys
import sys
import time
import asyncio
from dotenv import load_dotenv

load_dotenv('Credentials/.env')

async def upload():
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
        
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    
    if not os.path.exists(video_path):
        print(f"File not found: {video_path}")
        return

    file_size = os.path.getsize(video_path)
    print(f"Starting RAW upload: {os.path.basename(video_path)} ({file_size / 1024 / 1024:.2f} MB)")

    # Custom Generator for Progress and Throttling
    async def file_generator():
        chunk_size = 64 * 1024
        seen = 0
        last_log = -5
        with open(video_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                seen += len(chunk)
                pct = int((seen / file_size) * 100)
                if pct >= last_log + 5:
                    print(f"📤 Progress: {pct}%")
                    last_log = pct
                yield chunk
                # Throttle
                await asyncio.sleep(0.1)

    # Increase timeout to infinite for large file
    timeout = httpx.Timeout(None, connect=600.0)
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        # We need to use 'data' and 'files' properly for multipart
        files = {'video': (os.path.basename(video_path), file_generator(), 'video/mp4')}
        data = {'chat_id': chat_id, 'caption': 'Recovery (Raw HTTPX): Pooja_hedge_3.mp4'}
        
        try:
            print("Sending request...")
            response = await client.post(url, data=data, files=files)
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
        except Exception as e:
            print(f"❌ Failed: {e}")

if __name__ == "__main__":
    asyncio.run(upload())
