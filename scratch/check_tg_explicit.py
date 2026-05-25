import os
import httpx
import asyncio
from dotenv import load_dotenv

async def main():
    load_dotenv('Credentials/.env')
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    chats_to_check = [
        "@fitsbysakshitha",
        "@swargawasal",
        "1363193987",
        "-1003762065314"
    ]

    async with httpx.AsyncClient() as client:
        for cid in chats_to_check:
            url = f"https://api.telegram.org/bot{token}/getChat"
            try:
                resp = await client.get(url, params={'chat_id': cid})
                print(f"Checking {cid}: {resp.status_code} - {resp.text}")
            except Exception as e:
                print(f"Error checking {cid}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
