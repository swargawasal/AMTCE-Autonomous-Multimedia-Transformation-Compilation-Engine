import os
import gradio as gr
import logging
from downloader import download_video
from Video_Modules.trimmer import trim_video

# Setup logging for the UI
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("gradio_harvester")

def harvest_action(url, title):
    """Bridge between Gradio and the Downloader module."""
    if not url.strip():
        return "❌ Error: Please provide a URL.", None
    
    logger.info(f"🎨 Gradio Request: {url} | Title: {title}")
    
    try:
        result = download_video(url, custom_title=title if title.strip() else None)
        
        if result and result[0]:
            v_path, is_cached = result
            abs_path = os.path.abspath(v_path)
            status = "♻️ Reused from Cache" if is_cached else "✅ Downloaded Successfully"
            
            message = f"{status}\n📍 Path: {abs_path}"
            return message, abs_path
        else:
            return "❌ Failed: Could not download the video. Check logs for details.", None
            
    except Exception as e:
        logger.error(f"❌ Gradio Error: {e}")
        return f"❌ Error: {str(e)}", None

# Professional Themes for AMTCE
THEMES = {
    "Midnight (Dark)": {
        "bg": "#0f172a",
        "card": "rgba(30, 41, 59, 0.7)",
        "text": "#f8fafc",
        "accent": "linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)",
        "border": "rgba(51, 65, 85, 0.5)"
    },
    "Daybreak (Light)": {
        "bg": "#f8fafc",
        "card": "#ffffff",
        "text": "#0f172a",
        "accent": "linear-gradient(135deg, #2563eb 0%, #3b82f6 100%)",
        "border": "#e2e8f0"
    },
    "Cyberpunk (Neon)": {
        "bg": "#050505",
        "card": "rgba(20, 20, 20, 0.8)",
        "text": "#00ffcc",
        "accent": "linear-gradient(135deg, #ff00ff 0%, #7000ff 100%)",
        "border": "#ff00ff"
    }
}

def get_css(theme_name):
    t = THEMES.get(theme_name, THEMES["Midnight (Dark)"])
    return f"""
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    
    body, .gradio-container {{
        font-family: 'Inter', sans-serif !important;
        background-color: {t['bg']} !important;
        color: {t['text']} !important;
    }}
    
    .glass-card {{
        background: {t['card']} !important;
        backdrop-filter: blur(12px) !important;
        border: 2px solid {t['border']} !important;
        border-radius: 12px !important;
        padding: 24px !important;
        margin-bottom: 16px !important;
    }}
    
    .primary-btn {{
        background: {t['accent']} !important;
        border: none !important;
        color: white !important;
        font-weight: 700 !important;
        font-size: 1.1rem !important;
        padding: 12px !important;
        border-radius: 8px !important;
        cursor: pointer !important;
    }}
    
    .amtce-header {{
        text-align: center;
        margin-bottom: 32px !important;
    }}
    
    .amtce-title {{
        font-size: 3rem !important;
        font-weight: 800 !important;
        letter-spacing: -0.05em !important;
        margin: 0 !important;
        color: {t['text']} !important;
    }}
    
    .subtitle {{
        font-size: 1.1rem !important;
        opacity: 0.8 !important;
        margin-top: 8px !important;
        color: {t['text']} !important;
    }}

    /* Fix label visibility */
    .gr-form label, .gr-box label, label span {{
        color: {t['text']} !important;
        font-weight: 600 !important;
        font-size: 1rem !important;
        display: block !important;
        margin-bottom: 8px !important;
    }}

    input, textarea {{
        background: rgba(255, 255, 255, 0.05) !important;
        border: 1px solid {t['border']} !important;
        color: {t['text']} !important;
        font-size: 1rem !important;
    }}
    
    /* Success/Error coloring */
    .status-msg {{
        font-weight: 600 !important;
        padding: 10px !important;
        border-radius: 6px !important;
    }}
    """

def harvest_action(url, title, start_time, end_time, do_trim):
    """Bridge between Gradio and the Downloader module with optional trimming."""
    if not url.strip():
        return "❌ Error: Please provide a URL.", None
    
    try:
        result = download_video(url, custom_title=title if title.strip() else None)
        if result and result[0]:
            v_path, is_cached = result
            final_path = os.path.abspath(v_path)
            status = "♻️ DISK CACHE HIT" if is_cached else "✅ HARVEST SUCCESSFUL"
            
            if do_trim:
                try:
                    final_path = trim_video(final_path, start_time, end_time)
                    status += " + ✂️ TRIMMED"
                except Exception as trim_err:
                    return f"{status} but ❌ TRIM FAILED: {str(trim_err)}", final_path

            return f"{status}\n\n📁 {final_path}", final_path
        return "❌ HARVEST FAILED: Site not supported or timeout.", None
    except Exception as e:
        return f"❌ CRITICAL ERROR: {str(e)}", None

def change_theme(theme_name):
    # This acts as a trigger for UI refresh in Gradio if linked correctly, 
    # but since Gradio 6.0 handles static CSS at launch, we'll provide 
    # a highly visible high-contrast default and a launch parameter.
    return gr.update(value=get_css(theme_name))

# Launch with High Contrast by Default
with gr.Blocks(title="AMTCE Harvester Pro", css=get_css("Midnight (Dark)")) as demo:
    with gr.Column(elem_id="main-container"):
        gr.HTML(f"""
            <div class="amtce-header">
                <h1 class="amtce-title">AMTCE HARVESTER</h1>
                <p class="subtitle">High-Performance Media Extraction Engine</p>
            </div>
        """)
        
        with gr.Row():
            with gr.Column(elem_classes="glass-card", scale=2):
                gr.Markdown("### 🛠️ Configuration")
                url_input = gr.Textbox(
                    label="Source Intelligence (URL)", 
                    placeholder="Instagram, TikTok, YouTube...", 
                    value=""
                )
                title_input = gr.Textbox(
                    label="Identity Override (Optional)", 
                    placeholder="Enter custom filename", 
                    value=""
                )
                with gr.Accordion("✂️ Optional Trimming", open=False):
                    do_trim = gr.Checkbox(label="Enable Trimming", value=False)
                    start_time = gr.Number(label="Start Time (seconds)", value=0)
                    end_time = gr.Number(label="End Time (seconds)", value=10)
                run_btn = gr.Button("🚀 EXECUTE HARVEST", elem_classes="primary-btn")
            
            with gr.Column(scale=3):
                with gr.Column(elem_classes="glass-card"):
                    gr.Markdown("### 🛰️ Telemetry & Output")
                    status_output = gr.Textbox(label="Status Log", interactive=False)
                    video_output = gr.Video(label="Captured Asset")
            
    run_btn.click(
        fn=harvest_action,
        inputs=[url_input, title_input, start_time, end_time, do_trim],
        outputs=[status_output, video_output]
    )
    
    with gr.Row(elem_classes="glass-card"):
        with gr.Column():
            gr.Markdown("#### 🌐 Supported Nodes")
            gr.Markdown("IG, FB, TikTok, YT, X, & 1K+ others.")
        with gr.Column():
            active_theme = gr.Dropdown(
                choices=list(THEMES.keys()), 
                value="Daybreak (Light)", 
                label="🎨 System Aesthetic (Restart Required for full change)"
            )

if __name__ == "__main__":
    # In Gradio 6.0, the most reliable way to ensure visibility is high-contrast CSS at launch
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False, css=get_css("Daybreak (Light)"), theme=gr.themes.Default())
