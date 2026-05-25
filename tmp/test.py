from dotenv import load_dotenv
load_dotenv('Credentials/.env', override=True)
from Text_Modules.overlay_engine import generate_overlay_text

print("RESULT:", generate_overlay_text({"theme": "luxury"}))
