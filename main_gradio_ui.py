import os
import sys
from Diagnostics_Modules.gemini_trace import GeminiTrace
import google.generativeai as genai

# ==================== GEMINI FORENSIC PATCH ====================
_original_generate = genai.GenerativeModel.generate_content

def patched_generate(self, *args, **kwargs):
    model_name = getattr(self, "model_name", "unknown")
    start = GeminiTrace.log_start(model_name, args, kwargs)
    result = _original_generate(self, *args, **kwargs)
    GeminiTrace.log_end(start)
    return result

genai.GenerativeModel.generate_content = patched_generate
# =============================================================

import gradio as gr
import sys
import logging
from pathlib import Path

# Add root to path so we can import from modules
sys.path.append(os.getcwd())

from Download_Modules.downloader import download_video
from Video_Modules.trimmer import trim_video
from Health_handlers import get_portal
from Uploader_Modules import uploader
from Uploader_Modules.meta_uploader import AsyncMetaUploader

# 2. Initialize authorized modules
portal = get_portal()
globals().update(portal.__dict__)
from Compiler_Modules import compiler

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("amtce_dashboard")

def get_css():
    return """
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    
    body, .gradio-container {
        font-family: 'Inter', sans-serif !important;
        background-color: #f1f5f9 !important;
    }
    
    .glass-card {
        background: white !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 8px !important;
        padding: 20px !important;
        margin-bottom: 15px !important;
    }
    
    .warning-box {
        background: #fef2f2 !important;
        border: 1px solid #fecaca !important;
        color: #991b1b !important;
        padding: 12px !important;
        border-radius: 6px !important;
        margin-top: 10px !important;
        font-weight: 500 !important;
    }
    
    .primary-btn {
        background: #2563eb !important;
        color: white !important;
        font-weight: 600 !important;
        padding: 10px !important;
        border-radius: 6px !important;
    }
    """

async def manual_upload_handler(file_path, title, progress=gr.Progress()):
    if not file_path or not os.path.exists(file_path):
        return "❌ Error: File not found or session expired.", gr.update(visible=False), gr.update(visible=False)
        
    status = f"✅ Processing Manual Approval for: {os.path.basename(file_path)}"
    progress(0.1, desc="🚀 Starting Manual Upload...")
    
    try:
        # 1. YouTube
        link = await uploader.upload_to_youtube(file_path, title=title.strip())
        if link:
            status += f" + 📤 YT: {link}"
        else:
            status += " (YT FAILED)"
        
        # 2. Meta
        progress(0.5, desc="📸 Uploading to Meta...")
        meta_res = await AsyncMetaUploader.upload_to_meta(file_path, caption=title.strip())
        
        ig_status = meta_res.get("instagram", {}).get("status", "skipped")
        fb_status = meta_res.get("facebook", {}).get("status", "skipped")
        
        if ig_status == "success": status += " + 📸 IG: OK"
        elif ig_status != "skipped": status += f" (IG: {ig_status})"
        
        if fb_status == "success": status += " + 📘 FB: OK"
        elif fb_status != "skipped": status += f" (FB: {fb_status})"
        
        status += "\n\n✅ MANUAL UPLOAD COMPLETE."
        return status, gr.update(visible=False), gr.update(visible=False)
        
    except Exception as e:
        return f"❌ MANUAL UPLOAD ERROR: {str(e)}", gr.update(visible=True), gr.update(visible=True)

def reject_handler(file_path):
    print(f"🛑 [REJECT HANDLER] Triggered for: {file_path}") # Direct Console Print
    logger.info(f"🛑 [REJECT HANDLER] Triggered for: {file_path}")
    
    deleted_files = []
    if file_path and os.path.exists(file_path):
        try:
            # 1. Delete the main video file
            os.remove(file_path)
            deleted_files.append(os.path.basename(file_path))
            
            # 2. Find and delete related assets (JSON, Thumbnails)
            # Strategy: Strip extension, look for files starting with that basename
            import glob
            import re
            directory = os.path.dirname(file_path)
            filename_no_ext = os.path.splitext(os.path.basename(file_path))[0]
            
            # Pattern: /path/to/video.* -> matches video.json, video.jpg, video_thumb.jpg
            search_pattern = os.path.join(directory, f"{filename_no_ext}.*")
            print(f"   └─ Search Pattern: {search_pattern}")
            
            related_files = glob.glob(search_pattern)
            
            for related in related_files:
                try:
                    # Skip if we already deleted main file (though os.remove handles it)
                    if not os.path.exists(related): continue
                    
                    os.remove(related)
                    deleted_files.append(os.path.basename(related))
                except Exception as del_err:
                     print(f"   ⚠️ Failed to delete {related}: {del_err}")

            # 3. DELETE EXTRACTED AUDIO from Original_audio/ (active/cooldown/root)
            # Derive the stem — strip trailing index (_1, _2 …) to also match base title
            _audio_stem_full  = filename_no_ext          # e.g. "Avneet_kaur_3"
            _audio_stem_clean = re.sub(r"_\d+$", "", _audio_stem_full)  # e.g. "Avneet_kaur"
            _orig_audio_root  = "Original_audio"
            _audio_sub_dirs = [
                os.path.join(_orig_audio_root, "active"),
                os.path.join(_orig_audio_root, "cooldown"),
                _orig_audio_root,
            ]
            for _adir in _audio_sub_dirs:
                if not os.path.isdir(_adir):
                    continue
                for _acandidate in os.listdir(_adir):
                    _aname_no_ext = os.path.splitext(_acandidate)[0]
                    if _aname_no_ext in (_audio_stem_full, _audio_stem_clean):
                        _apath = os.path.join(_adir, _acandidate)
                        if os.path.isfile(_apath):
                            try:
                                os.remove(_apath)
                                deleted_files.append(f"[AUDIO] {_acandidate}")
                                print(f"   🗑️ Deleted audio: {_apath}")
                            except Exception as _ae:
                                print(f"   ⚠️ Could not delete audio {_apath}: {_ae}")
                
            msg = f"🗑️ REJECTED & DELETED:\n{', '.join(deleted_files)}"
            print(msg)
            return msg, gr.update(visible=False), gr.update(visible=False), None
            
        except Exception as e:
            err_msg = f"⚠️ CRITICAL ERROR deleting file: {e}"
            print(err_msg)
            return err_msg, gr.update(visible=False), gr.update(visible=False), None
            
    else:
        print(f"   ⚠️ File not found or path empty: {file_path}")
        return "🚫 Upload Rejected (File not found).", gr.update(visible=False), gr.update(visible=False), None


async def process_harvest(url, title, do_trim, start_time, end_time, do_wm, do_upscale, do_upload, do_overlay, do_vo, force_immediate, progress=gr.Progress()):
    if not url.strip():
        yield "❌ Error: Missing URL", None
        return
    
    if not title.strip():
        yield "❌ Error: Please provide a Title to name your file.", None
        return
    
    try:
        progress(0, desc="🛰️ Starting...")
        yield "🛰️ Initializing Harvester...", None
        
        # Log precisely what the UI sends to the terminal
        logger.info(f">>> UI SIGNAL RECEIVED: Title='{title}', Trim={do_trim}, WM={do_wm}, Upscale={do_upscale}, Upload={do_upload}, Overlay={do_overlay}, VO={do_vo}")

        result = download_video(url, custom_title=title.strip())
        if result and result[0]:
            v_path, is_cached = result
            final_path = os.path.abspath(v_path)
            status = "✅ READY"
            
            # STAGE 1: Custom Trimming (Trim-First Sequence)
            # Done BEFORE Engine so we process exactly what the user wants.
            if do_trim:
                progress(0.3, desc="✂️ Custom Trimming...")
                yield "✂️ Executing pre-processing trim...", final_path
                try:
                    final_path = trim_video(final_path, start_time, end_time)
                    status += " + ✂️ TRIMMED"
                    yield f"{status}\n\n📁 {final_path}", final_path
                except Exception as trim_err:
                    yield f"❌ TRIM ERROR: {str(trim_err)}", final_path
                    return

            # STAGE 2: Engine Core Processing (Full AI Engine)
            progress(0.5, desc="🚀 Processing...")
            yield "🚀 Engaging Engine Core (Full Processing)...", final_path
            try:
                # Force Reprocess if User Trimmed manually (Avoids Batching Logic for user-defined clips)
                should_force = force_immediate or do_trim
                
                import time
                safe_title = title.strip().replace(" ", "_")
                os.makedirs("Processed Shorts", exist_ok=True)
                tmp_out = os.path.join("Processed Shorts", f"{safe_title}_{int(time.time())}.mp4")
                
                compiled_path, metrics = compiler.compile_with_transitions(
                    Path(final_path), 
                    tmp_out,
                    title=title.strip(),
                    remove_watermark=do_wm,
                    do_upscale=do_upscale,
                    force_reprocess=should_force,
                    do_vo=do_vo,
                    do_overlay=do_overlay
                )
                if compiled_path and os.path.exists(compiled_path):
                    final_path = os.path.abspath(str(compiled_path))
                    # Dynamic Status labels
                    if do_wm: status += " + 🧼 WM-REMOVED"
                    if do_upscale: status += " + 💎 UPSCATED"
                    status += " + ✨ PROCESSED"
                else:
                    reason = metrics.get('reason', 'N/A')
                    status += f" (STATUS: {reason})"
                    if "Pooled" in reason:
                        status += "\n\n⚠️ NOTE: This clip is too short (<13s). The engine is pooling it for batching.\nIt will process once 3 such clips are collected.\nTICK 'Force Immediate' to process it now."
                
                # Yield Result EARLY after engine
                yield f"{status}\n\n📁 {final_path}", final_path

            except Exception as ai_err:
                status += f" (ENGINE ERROR: {str(ai_err)})"

            # STAGE 3: Auto Upload (Awaited)
            if do_upload:
                progress(0.8, desc="📤 Uploading to YouTube...")
                yield "📤 Sending final version to YouTube...", final_path
                try:
                    # 1. YouTube
                    link = await uploader.upload_to_youtube(final_path, title=title.strip())
                    if link:
                        status += f" + 📤 YT: {link}"
                    else:
                        status += " (YT FAILED)"
                    
                    # 2. Meta (Instagram + Facebook)
                    progress(0.9, desc="📸 Uploading to Meta...")
                    yield "📸 Sending to Instagram & Facebook...", final_path
                    
                    meta_res = await AsyncMetaUploader.upload_to_meta(final_path, caption=title.strip())
                    
                    # Parse Meta Results
                    ig_status = meta_res.get("instagram", {}).get("status", "skipped")
                    fb_status = meta_res.get("facebook", {}).get("status", "skipped")
                    
                    if ig_status == "success": status += " + 📸 IG: OK"
                    elif ig_status != "skipped": status += f" (IG: {ig_status})"
                    
                    if fb_status == "success": status += " + 📘 FB: OK"
                    elif fb_status != "skipped": status += f" (FB: {fb_status})"

                except Exception as up_err:
                    status += f" (UPLOAD ERROR: {str(up_err)})"

            # Final Summary Yield
            yield f"{status}\n\n📁 {final_path}", final_path
        else:
            yield "❌ DOWNLOAD FAILED", None
    except Exception as e:
        yield f"❌ CRITICAL ERROR: {str(e)}", None

with gr.Blocks(title="AMTCE Control Center", css=get_css()) as demo:
    gr.Markdown("# 🛰️ AMTCE CONTROL CENTER")
    
    with gr.Row():
        with gr.Column(elem_classes="glass-card", scale=1):
            gr.Markdown("### 📥 Input")
            url_input = gr.Textbox(label="Source URL (Required)", placeholder="Instagram/YouTube link")
            title_input = gr.Textbox(label="Title (any name is okay)", placeholder="Enter a name for this video")
            
            gr.Markdown("### ⚙️ Pipeline Controls")
            gr.HTML("""
                <div class="warning-box" style="margin-top: 5px; margin-bottom: 15px;">
                    ⚠️ NOTE: Engine auto-trims 1s from start/end.<br>
                    Short clips (<13s) are batched together by default.
                </div>
            """)
            with gr.Row():
                cb_trim = gr.Checkbox(label="Enable Custom Trimming", value=False)
                cb_wm = gr.Checkbox(label="Enable Visual Refinement", value=True)
            with gr.Row():
                cb_upscale = gr.Checkbox(label="Enable AI Upscaling", value=False)
                cb_upload = gr.Checkbox(label="Enable Auto Upload", value=False)
            
            with gr.Row():
                cb_overlay = gr.Checkbox(label="Enable Text Overlays (Luxury)", value=True)
                cb_vo = gr.Checkbox(label="Enable Editorial Brain / VO", value=True)
            
            force_immediate = gr.Checkbox(label="Force Immediate (Keep Batching)", value=False)
            
            with gr.Accordion("Timestamp Settings (Custom Trim Only)", open=False):
                with gr.Row():
                    start_time = gr.Number(label="Start (sec)", value=0)
                    end_time = gr.Number(label="End (sec)", value=10)
            
            run_btn = gr.Button("🚀 EXECUTE PIPELINE", elem_classes="primary-btn")
        
        with gr.Column(scale=1):
            with gr.Column(elem_classes="glass-card"):
                gr.Markdown("### 📊 Status & Output")
                status_log = gr.Textbox(label="Engine Communication", interactive=False, lines=10)
                video_preview = gr.Video(label="Final Render Preview")
                
                # APPROVE / REJECT BUTTONS
                with gr.Row():
                    approve_btn = gr.Button("✅ APPROVE & UPLOAD", variant="primary", visible=False)
                    reject_btn = gr.Button("❌ REJECT / DISCARD", variant="stop", visible=False)
    
    # Hidden state to store file path for manual upload
    state_filepath = gr.State()

    # Main Pipeline Handler
    # Main Pipeline Handler
    # Main Pipeline Handler
    async def process_wrapper(url, title, do_trim, start, end, do_wm, do_upscale, do_upload, do_overlay, do_vo, force, progress=gr.Progress()):
        last_status = ""
        last_file = None
        
        # Call generator
        async for status_update, file_path in process_harvest(url, title, do_trim, start, end, do_wm, do_upscale, do_upload, do_overlay, do_vo, force, progress):
            last_status = status_update
            if file_path: last_file = file_path # PROTECT against None overwrites
            
            # Pass through updates, keep buttons HIDDEN during processing
            # We must yield 5 outputs: status, video, state_path, approve_btn, reject_btn
            yield status_update, file_path, None, gr.update(visible=False), gr.update(visible=False)
            
        # LOOP FINISHED
        # If we have a file and Auto Upload is OFF => SHOW BUTTONS
        file_exists = last_file and os.path.exists(last_file)
        
        if file_exists and not do_upload:
             logger.info(f"✅ Pipeline Finished. Revealing Manual Control Buttons for: {last_file}")
             yield last_status, last_file, last_file, gr.update(visible=True), gr.update(visible=True)
        else:
             logger.info(f"ℹ️ Pipeline Finished. Buttons hidden. DoUpload={do_upload}, File={last_file}, Exists={file_exists}")
             yield last_status, last_file, None, gr.update(visible=False), gr.update(visible=False)
        
        # FINAL FORENSIC SUMMARY
        GeminiTrace.print_summary()

    run_btn.click(
        fn=process_wrapper,
        inputs=[url_input, title_input, cb_trim, start_time, end_time, cb_wm, cb_upscale, cb_upload, cb_overlay, cb_vo, force_immediate],
        outputs=[status_log, video_preview, state_filepath, approve_btn, reject_btn]
    )
    
    approve_btn.click(
        fn=manual_upload_handler,
        inputs=[state_filepath, title_input],
        outputs=[status_log, approve_btn, reject_btn]
    )
    
    reject_btn.click(
        fn=reject_handler,
        inputs=[state_filepath],
        outputs=[status_log, approve_btn, reject_btn, video_preview]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7861, share=False)
