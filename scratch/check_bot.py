import os
import httpx
import asyncio
from dotenv import load_dotenv

async def main():
    load_dotenv('Credentials/.env')
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        print(f"getMe: {resp.text}")
        
        # Check updates to see if we see any groups
        resp = await client.get(f"https://api.telegram.org/bot{token}/getUpdates")
        print(f"getUpdates: {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
