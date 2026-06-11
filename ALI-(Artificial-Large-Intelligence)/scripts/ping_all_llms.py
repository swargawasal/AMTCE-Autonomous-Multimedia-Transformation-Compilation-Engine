"""
ALI Engine -- Live LLM Ping Test
Tests all 4 LLMs with a real API call and prints results.
Run from project root: .venv\Scripts\python scripts\ping_all_llms.py
"""

import sys
import os
import time

# Force UTF-8 output on Windows so emoji/unicode don't crash
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Load the .env from project root
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# Add project root to path so connectors are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.deepseek import call_deepseek
from connectors.gemini import call_gemini
from connectors.mistral import call_mistral
from connectors.qwen_hf import call_qwen
from connectors.groq_connector import call_groq
from connectors.cerebras_connector import call_cerebras

TASK = (
    "You are one of several AI models in a team called ALI (Artificial Large Intelligence). "
    "Introduce yourself in ONE sentence and suggest one thing you're best at."
)

DIVIDER = "─" * 60

def ping(name, fn, *args, **kwargs):
    print(f"\n{DIVIDER}")
    print(f"🤖  Pinging: {name}")
    print(DIVIDER)
    start = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.time() - start
        if "error" in result:
            print(f"❌  ERROR ({elapsed:.2f}s): {result['error']}")
        else:
            answer = result.get("answer", "")
            tokens = result.get("tokens_used", "?")
            model  = result.get("model_used", name)
            print(f"✅  ({elapsed:.2f}s | tokens: {tokens} | model: {model})")
            print(f"\n💬  {answer.strip()}")
    except Exception as e:
        elapsed = time.time() - start
        print(f"❌  EXCEPTION ({elapsed:.2f}s): {e}")

if __name__ == "__main__":
    print("\n" + "═" * 60)
    print("   ALI ENGINE — LIVE LLM PING TEST")
    print("   All models will receive the same task:")
    print(f"   \"{TASK[:70]}...\"")
    print("═" * 60)

    ping("DeepSeek (Reasoner)",  call_deepseek, TASK, model="deepseek-chat")
    ping("Gemini (Vision/Gov)",  call_gemini,   TASK, task_type="ali_reasoning")
    ping("Mistral (Emotion)",    call_mistral,  TASK)

    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        print(f"\n{DIVIDER}")
        print("⚠️   Groq — SKIPPED: GROQ_API_KEY not set.")
        print(DIVIDER)
    else:
        ping("Groq (Llama 3.3 70B)", call_groq, TASK)

    cerebras_key = os.getenv("CEREBRAS_API_KEY", "")
    if not cerebras_key:
        print(f"\n{DIVIDER}")
        print("⚠️   Cerebras — SKIPPED: CEREBRAS_API_KEY not set.")
        print(DIVIDER)
    else:
        ping("Cerebras (Llama 3.3 70B)", call_cerebras, TASK)

    hf_token = os.getenv("HF_TOKEN", "")
    if not hf_token or hf_token == "hf_YOUR_TOKEN_HERE":
        print(f"\n{DIVIDER}")
        print("⚠️   Qwen (HF) — SKIPPED: HF_TOKEN not set yet.")
        print("    Go to: huggingface.co → Settings → Access Tokens → New Token (Read)")
        print("    Paste into .env:  HF_TOKEN=hf_xxxxxxxxxxxx")
        print(DIVIDER)
    else:
        ping("Qwen 2.5-72B (HF)", call_qwen, TASK)

    print(f"\n{'═' * 60}")
    print("   PING TEST COMPLETE")
    print("═" * 60 + "\n")

