"""
ALI - Gradio Chat Interface
============================
A premium, highly uniform dark-themed chat UI that talks directly to the ALI FastAPI backend.

Features:
  - Live chat with ALI (all LLMs discussing, synthesised answer returned)
  - Council of Rulers panel -- shows all 6 LLMs with live status badges (Native Markdown)
  - UBI Intelligence Panel -- kurtosis type, MSE trend, predicted topic (Native Markdown)
  - Trace panel -- shows which models participated in each response
  - Typing event sent to /typing endpoint for preloader activation
  - Auto-refreshing status checks via app.load and gr.Timer

Run:
  .venv/Scripts/python frontend/gradio_app.py

Requires ALI backend running at http://127.0.0.1:8000:
  .venv/Scripts/uvicorn api.main:app --reload
"""

import sys
import os
import uuid
import json
import time
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gradio as gr
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# ── Config ─────────────────────────────────────────────────────────────────────
API_URL     = os.getenv("ALI_API_URL", "http://127.0.0.1:8000")
SESSION_ID  = str(uuid.uuid4())   # persistent for this Gradio session

# ── API helpers ────────────────────────────────────────────────────────────────
def _check_backend() -> dict:
    try:
        r = requests.get(f"{API_URL}/status", timeout=3)
        if r.status_code == 200:
            return {"online": True, "data": r.json()}
    except Exception:
        pass
    return {"online": False, "data": {}}


def _send_typing(partial_text: str):
    try:
        requests.post(
            f"{API_URL}/typing",
            json={"session_id": SESSION_ID, "partial_text": partial_text},
            timeout=2,
        )
    except Exception:
        pass


def _send_message(message: str) -> dict:
    try:
        r = requests.post(
            f"{API_URL}/chat",
            json={"message": message, "session_id": SESSION_ID},
            timeout=60,
        )
        if r.status_code == 200:
            return r.json()
        return {"answer": f"[Backend error {r.status_code}]", "trace": {}}
    except requests.exceptions.ConnectionError:
        return {
            "answer": (
                "⚠️ **ALI backend is offline.**\n\n"
                "Start it with:\n```\n.venv\\Scripts\\uvicorn api.main:app --reload\n```"
            ),
            "trace": {},
        }
    except Exception as e:
        return {"answer": f"[Error: {e}]", "trace": {}}


def _get_ubi_profile() -> dict:
    try:
        r = requests.get(f"{API_URL}/ubi/profile/{SESSION_ID}", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


# ── Markdown Builders ────────────────────────────────────────────────────────
def _build_council_md(backend_online: bool) -> str:
    status_text = "🟢 **ONLINE**" if backend_online else "🔴 **OFFLINE**"
    
    return f"""### ⚔️ Council of Rulers
**Status:** {status_text}

Each ruler contributes to every answer. They discuss, debate, and return the best collective response.

| Ruler | Role |
|---|---|
| 🧠 **DeepSeek** | The Reasoner - Deep chain-of-thought |
| ⚡ **Gemini** | The Governor - Vision, synthesis |
| 🎭 **Mistral** | The Empath - Emotion detection |
| 🚀 **Groq** | The Sprinter - Ultra-fast backup |
| 🔱 **Cerebras** | The Titan - Batch processing |
| 📚 **Qwen / HF**| The Scholar - Multilingual |
"""

def _build_ubi_md(profile_data: dict) -> str:
    if not profile_data:
        return """### 🧬 User Intelligence Profile
*UBI profile builds after your first message. It learns your topics, depth preference, and predicts your next question.*"""

    p = profile_data.get("profile", {})
    m = profile_data.get("mse", {})

    ktype       = p.get("kurtosis_type", "MODERATE")
    kscore      = p.get("kurtosis_score", 0.0)
    total       = p.get("total_interactions", 0)
    top_topics  = p.get("top_topics", [])
    mse_current = m.get("mse_current", 0.0)
    mse_trend   = m.get("mse_trend", "STABLE")
    pred_acc    = m.get("prediction_accuracy", 0.0)
    conf_multi  = m.get("confidence_multiplier", 1.0)

    ktype_emojis = {"FOCUSED": "🎯", "MODERATE": "⚖️", "RANDOM": "🎲"}
    trend_emojis = {"RISING": "📈", "FALLING": "📉", "STABLE": "➡️"}

    kemoji = ktype_emojis.get(ktype, "⚖️")
    temoji = trend_emojis.get(mse_trend, "➡️")
    
    topics_str = ", ".join([f"`{t}`" for t in top_topics]) if top_topics else "None yet"

    return f"""### 🧬 User Intelligence Profile

- **Type:** {kemoji} **{ktype} USER**
- **Kurtosis:** {kscore:.2f} | **Interactions:** {total}
- **Top Topics:** {topics_str}
- **MSE Trend:** {temoji} {mse_trend}
- **Prediction MSE:** {mse_current:.4f} *(Accuracy: {pred_acc:.0%})*
- **Confidence Multiplier:** **{conf_multi:.2f}×**
"""


# ── Chat function ──────────────────────────────────────────────────────────────
def chat_with_ali(message: str, history: list):
    """Main chat handler -- sends message to ALI and streams response."""
    if not message.strip():
        return history, _build_council_md(False), _build_ubi_md({})

    # Fire typing event (preloader for FOCUSED users)
    _send_typing(message[:50])

    # Add user message
    history = list(history) + [{"role": "user", "content": message}]
    yield history, _build_council_md(True), _build_ubi_md({})

    # Send to ALI
    start_time = time.time()
    result      = _send_message(message)
    elapsed     = time.time() - start_time

    answer      = result.get("answer", "")
    trace       = result.get("trace", {})
    backend_ok  = "ALI backend is offline" not in answer and "[Error" not in answer

    # Build trace footer
    trace_lines = []
    if trace:
        for k, v in trace.items():
            if isinstance(v, dict) and v.get("model_used"):
                trace_lines.append(f"`{k}` -> {v['model_used']}")
            elif isinstance(v, str) and v:
                trace_lines.append(f"`{k}` -> {v}")
    trace_str = "  .  ".join(trace_lines) if trace_lines else ""

    footer = f"\n\n---\n*{elapsed:.1f}s*"
    if trace_str:
        footer += f"  |  *{trace_str}*"

    history = history + [{"role": "assistant", "content": answer + footer}]

    # Refresh UBI profile
    ubi_data = _get_ubi_profile()

    yield history, _build_council_md(backend_ok), _build_ubi_md(ubi_data)


def refresh_status():
    status = _check_backend()
    ubi_data = _get_ubi_profile() if status["online"] else {}
    return _build_council_md(status["online"]), _build_ubi_md(ubi_data)


CUSTOM_CSS = """
/* Custom variables for theme */
:root {
  --primary-glow: linear-gradient(135deg, #7b2cbf, #3c096c);
  --accent-glow: linear-gradient(135deg, #9d4edd, #ff007f);
  --bg-dark: #090a0f;
  --bg-card: rgba(15, 18, 28, 0.65);
  --border-glow: rgba(157, 78, 221, 0.15);
  --border-glow-hover: rgba(157, 78, 221, 0.35);
  --text-main: #f3f4f6;
  --text-muted: #9ca3af;
}

/* Global body modifications */
body {
  background-color: var(--bg-dark) !important;
  color: var(--text-main) !important;
}

/* App title style */
.app-title h1 {
  font-family: 'Outfit', 'Inter', sans-serif !important;
  background: linear-gradient(135deg, #f3f4f6, #9d4edd) !important;
  -webkit-background-clip: text !important;
  -webkit-text-fill-color: transparent !important;
  font-weight: 800 !important;
  text-shadow: 0 0 30px rgba(157, 78, 221, 0.1) !important;
}

/* Dashboard cards (Left and Right columns) */
.dashboard-card {
  background: var(--bg-card) !important;
  border: 1px solid var(--border-glow) !important;
  border-radius: 16px !important;
  padding: 1.5rem !important;
  backdrop-filter: blur(16px) !important;
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37) !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.dashboard-card:hover {
  border-color: var(--border-glow-hover) !important;
  box-shadow: 0 8px 32px 0 rgba(157, 78, 221, 0.08) !important;
}

/* Chat container card styling */
.chat-container {
  background: rgba(13, 16, 24, 0.45) !important;
  border: 1px solid rgba(255, 255, 255, 0.04) !important;
  border-radius: 16px !important;
  padding: 1rem !important;
  box-shadow: inset 0 0 20px rgba(0, 0, 0, 0.2) !important;
}

/* Chatbot customization */
.ali-chatbot {
  background: transparent !important;
  border: none !important;
  border-radius: 12px !important;
}

.ali-chatbot .message-wrap .message {
  border-radius: 12px !important;
  border: 1px solid rgba(255, 255, 255, 0.05) !important;
  font-size: 0.95rem !important;
  line-height: 1.6 !important;
}

/* User message bubbles */
.ali-chatbot .message-wrap .user {
  background: linear-gradient(135deg, #4c1d95, #2e1065) !important;
  border-color: rgba(157, 78, 221, 0.3) !important;
  color: #ffffff !important;
  box-shadow: 0 4px 12px rgba(76, 29, 149, 0.25) !important;
}

/* Assistant message bubbles */
.ali-chatbot .message-wrap .bot {
  background: rgba(20, 24, 38, 0.8) !important;
  border-left: 4px solid #9d4edd !important;
  color: var(--text-main) !important;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
}

/* Textbox styling */
.msg-textbox textarea {
  background: rgba(15, 18, 28, 0.8) !important;
  border: 1px solid var(--border-glow) !important;
  border-radius: 12px !important;
  color: var(--text-main) !important;
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.2) !important;
  transition: all 0.3s ease !important;
}

.msg-textbox textarea:focus {
  border-color: #9d4edd !important;
  box-shadow: 0 0 8px rgba(157, 78, 221, 0.4) !important;
}

/* Buttons styling */
.action-btn-primary {
  background: linear-gradient(135deg, #9d4edd, #7b2cbf) !important;
  border: none !important;
  border-radius: 12px !important;
  color: white !important;
  font-weight: 600 !important;
  box-shadow: 0 4px 14px rgba(157, 78, 221, 0.4) !important;
  transition: all 0.2s ease !important;
  cursor: pointer !important;
}

.action-btn-primary:hover {
  transform: translateY(-1px) !important;
  box-shadow: 0 6px 20px rgba(157, 78, 221, 0.6) !important;
}

.action-btn-primary:active {
  transform: translateY(1px) !important;
}

.action-btn-secondary {
  background: rgba(30, 35, 50, 0.6) !important;
  border: 1px solid rgba(255, 255, 255, 0.08) !important;
  border-radius: 12px !important;
  color: var(--text-main) !important;
  transition: all 0.2s ease !important;
  cursor: pointer !important;
}

.action-btn-secondary:hover {
  background: rgba(40, 45, 65, 0.8) !important;
  border-color: rgba(255, 255, 255, 0.15) !important;
}

/* Custom modern scrollbars */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background: rgba(0, 0, 0, 0.1);
}

::-webkit-scrollbar-thumb {
  background: rgba(157, 78, 221, 0.3);
  border-radius: 10px;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(157, 78, 221, 0.5);
}
"""


# ── Gradio UI ──────────────────────────────────────────────────────────────────

def build_app():
    initial_status  = _check_backend()
    initial_council = _build_council_md(initial_status["online"])
    initial_ubi     = _build_ubi_md({})

    with gr.Blocks(
        title="ALI - Artificial Large Intelligence",
        css=CUSTOM_CSS
    ) as app:

        gr.Markdown("""
# 🤖 ALI — Artificial Large Intelligence
**6 LLMs · Council of Rulers · User Behaviour Intelligence · Phase 20**
""", elem_classes=["app-title"])

        with gr.Row(equal_height=True):

            # ── Left Panel: Council of Rulers ────────────────────────────────
            with gr.Column(scale=2, min_width=240, elem_classes=["dashboard-card"]):
                council_panel = gr.Markdown(value=initial_council)
                refresh_btn = gr.Button("↻ Force Refresh Status", size="sm", elem_classes=["action-btn-secondary"])

            # ── Centre: Chat ─────────────────────────────────────────────────
            with gr.Column(scale=6, elem_classes=["chat-container"]):
                chatbot = gr.Chatbot(
                    label="ALI Engine",
                    height=560,
                    show_label=True,
                    avatar_images=(None, None),
                    render_markdown=True,
                    elem_classes=["ali-chatbot"],
                    value=[
                        {
                            "role": "assistant",
                            "content": (
                                "Welcome to ALI - the Council of Rulers awaits your question.\n\n"
                                "Your message is discussed by **6 LLMs simultaneously** -- "
                                "DeepSeek reasons, Gemini synthesises, Mistral shapes the tone, "
                                "Groq sprints for speed, Cerebras handles scale, "
                                "and the HF Scholar pool adds domain depth.\n\n"
                                "The final answer is their **collective best response.** "
                                "What would you like to explore?"
                            ),
                        },
                    ],
                )

                with gr.Row():
                    msg_box = gr.Textbox(
                        placeholder="Ask ALI anything — the Council will deliberate...",
                        show_label=False,
                        lines=2,
                        max_lines=5,
                        scale=5,
                        container=True,
                        elem_classes=["msg-textbox"],
                    )
                    with gr.Column(scale=1, min_width=100):
                        send_btn  = gr.Button("Send ⚔️", variant="primary", elem_classes=["action-btn-primary"])
                        clear_btn = gr.Button("Clear", size="sm", elem_classes=["action-btn-secondary"])

                gr.Markdown(
                    f"<center><small>Session: <code>{SESSION_ID[:8]}...</code> | Backend: <code>{API_URL}</code></small></center>"
                )

            # ── Right Panel: UBI Profile ─────────────────────────────────────
            with gr.Column(scale=2, min_width=240, elem_classes=["dashboard-card"]):
                ubi_panel = gr.Markdown(value=initial_ubi)

        # ── Events ────────────────────────────────────────────────────────────
        def submit(message, history):
            yield from chat_with_ali(message, history)

        send_btn.click(
            fn=submit,
            inputs=[msg_box, chatbot],
            outputs=[chatbot, council_panel, ubi_panel],
        ).then(
            fn=lambda: "",
            outputs=[msg_box],
        )

        msg_box.submit(
            fn=submit,
            inputs=[msg_box, chatbot],
            outputs=[chatbot, council_panel, ubi_panel],
        ).then(
            fn=lambda: "",
            outputs=[msg_box],
        )

        clear_btn.click(
            fn=lambda: ([], ""),
            outputs=[chatbot, msg_box],
        )

        refresh_btn.click(
            fn=refresh_status,
            outputs=[council_panel, ubi_panel],
        )

        # ── Auto-refresh: fire immediately on load + every 5s via Timer ───────
        app.load(
            fn=refresh_status,
            outputs=[council_panel, ubi_panel],
        )

        try:
            timer = gr.Timer(value=5)
            timer.tick(
                fn=refresh_status,
                outputs=[council_panel, ubi_panel],
            )
        except Exception:
            pass

    return app


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("   ALI — Gradio Chat Interface")
    print(f"   Backend: {API_URL}")
    print(f"   Session: {SESSION_ID[:8]}...")
    print("=" * 60 + "\n")

    app = build_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
        favicon_path=None,
        theme=gr.themes.Monochrome()
    )
