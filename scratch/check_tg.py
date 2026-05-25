import os
import httpx
import asyncio
from dotenv import load_dotenv

async def main():
    # Load root env
    load_dotenv('Credentials/.env')
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    group_id = os.getenv('TELEGRAM_GROUP_ID')

    print(f"Token: {token[:10]}...")
    print(f"Group ID from .env: {group_id}")

    async with httpx.AsyncClient() as client:
        async def check_chat(cid):
            url = f"https://api.telegram.org/bot{token}/getChat"
            try:
                resp = await client.get(url, params={'chat_id': cid})
                print(f"Checking {cid}: {resp.status_code} - {resp.text}")
            except Exception as e:
                print(f"Error checking {cid}: {e}")

        if group_id:
            await check_chat(group_id)
            if not group_id.startswith('@') and not group_id.lstrip('-').isdigit():
                await check_chat(f"@{group_id}")

        # Check the numeric chat_id if it exists
        chat_id = os.getenv('chat_id')
        if chat_id:
            print(f"Chat ID from .env: {chat_id}")
            await check_chat(chat_id)

if __name__ == "__main__":
    asyncio.run(main())
