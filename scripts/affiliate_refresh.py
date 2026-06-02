import os
import sys
import asyncio
import argparse
from dotenv import load_dotenv

# Add parent directory to path to enable local module imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set working directory to project root
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(root_dir)

from Uploader_Modules.telegram_message_ledger import update_affiliate_link

async def main():
    parser = argparse.ArgumentParser(description="AMTCE Standalone Telegram Affiliate/CPA Link Refresh Script")
    parser.add_argument("old_link", nargs="?", default=None, help="The old link to surgically replace (optional)")
    parser.add_argument("new_link", nargs="?", default=None, help="The new link to replace it with (optional)")
    args = parser.parse_args()

    # Load credentials from .env
    load_dotenv("Credentials/.env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token:
        print("❌ Error: TELEGRAM_BOT_TOKEN not found in Credentials/.env")
        sys.exit(1)

    import telegram
    bot = telegram.Bot(token=token)

    print("🚀 Starting Standalone Link Refresh...")
    if args.old_link and args.new_link:
        print(f"🎯 Mode: Surgical replacement of exact link")
        print(f"   Old Link: {args.old_link}")
        print(f"   New Link: {args.new_link}")
    else:
        print(f"🔄 Mode: Auto-Rotation & Fallback Refresh")
        print(f"   (Will fetch active CPA links from pools, select a fresh one, and update all posts)")

    try:
        summary = await update_affiliate_link(bot, old_link=args.old_link, new_link=args.new_link)
        
        print("\n📊 --- REFRESH COMPLETE SUMMARY ---")
        print(f"Status        : {summary['status'].upper()}")
        print(f"New Active Link: {summary['new_link']}")
        print(f"Posts Scanned : {summary['posts_scanned']}")
        print(f"Posts Updated : {summary['posts_updated']}")
        print(f"Pools Updated : {', '.join(summary['pools_updated']) if summary['pools_updated'] else 'None'}")
        print("\n📝 Execution Details:")
        for detail in summary["details"]:
            print(f" - {detail}")
            
    except Exception as e:
        print(f"❌ Execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
