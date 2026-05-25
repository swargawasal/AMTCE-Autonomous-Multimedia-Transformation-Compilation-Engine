import os
import sys
import logging
import gradio as gr
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Thumb_Modules.ai_media_generator import generate_ad_images_from_path, generate_ad_video

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("ad_toolkit_webui")

# Load environment
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Credentials", ".env")
load_dotenv(env_path)

def process_ad_content(title_text, uploaded_image, generate_images_flag, generate_video_flag, progress=gr.Progress()):
    if not uploaded_image:
        return None, None, "Error: Please upload a source image (a photo/frame of the outfit). The engine removes the background from YOUR image — it does not generate a random one."

    outdir = "sample_thumbs/manual_tests"
    os.makedirs(outdir, exist_ok=True)
    base_name = (title_text or "outfit").lower().replace(" ", "_").replace("/", "")[:20]

    image_outputs = []
    video_output  = None
    status_msg    = ""

    # Generate Image Ads — remove background from uploaded image
    if generate_images_flag:
        progress(0.2, desc="Removing background (ONNX/CPU)...")
        try:
            images_generated = generate_ad_images_from_path(title_text or "outfit", uploaded_image, outdir, base_name)
            for format_name, paths in images_generated.items():
                for p in paths:
                    if os.path.exists(p):
                        image_outputs.append((p, f"{format_name.replace('_',' ').title()} Ad"))
            if image_outputs:
                status_msg += f"Generated {len(image_outputs)} ad variant(s) from your image.\n"
            else:
                status_msg += "Image generation failed. Check logs.\n"
        except Exception as e:
            status_msg += f"Error: {e}\n"

    # Video ad
    if generate_video_flag:
        progress(0.8, desc="Video generation...")
        os.environ["ENABLE_AI_VIDEO_CREATION"] = "true"
        source_img = uploaded_image
        if not source_img and image_outputs:
            source_img = image_outputs[0][0]
        if source_img and os.path.exists(source_img):
            try:
                vid_path = generate_ad_video(title_text or "outfit", source_img, outdir, base_name)
                if vid_path and os.path.exists(vid_path):
                    video_output = vid_path
                    status_msg  += "Video ad generated.\n"
                else:
                    status_msg  += "Video generation not available on free tier.\n"
            except Exception as e:
                status_msg += f"Video error: {e}\n"

    progress(1.0, desc="Done!")
    return image_outputs, video_output, status_msg

# Gradio Interface Definition
with gr.Blocks() as demo:
    gr.Markdown("# 🎨 AMTCE AI Advertisement Studio")
    gr.Markdown(
        "Upload a photo or video frame of the outfit. "
        "The engine **removes the background** and places the person on a clean studio backdrop. "
        "Pinterest (2:3) + Instagram (4:5) formats are exported automatically. No GPU needed."
    )

    with gr.Row():
        with gr.Column(scale=1):
            title_input = gr.Textbox(
                label="Product Title (for file naming)",
                placeholder="e.g. Red Bridal Lehenga"
            )

            gr.Markdown("**Upload Outfit Image (Required):** A clear photo or screenshot of the person in the outfit. The exact same outfit will appear in the ad.")
            image_input = gr.Image(label="", type="filepath")

            with gr.Row():
                gen_images_chk = gr.Checkbox(label="Generate Image Ads (Pinterest/IG)", value=True)
                gen_video_chk  = gr.Checkbox(label="Generate Video Ad", value=False)

            generate_btn  = gr.Button("Generate Studio Ads", variant="primary")
            status_output = gr.Textbox(label="Status", lines=3, interactive=False)

        with gr.Column(scale=1):
            gallery_output = gr.Gallery(
                label="Generated Ad Images",
                show_label=True,
                columns=2, rows=2, height="auto"
            )
            video_output_comp = gr.Video(label="Generated Video Ad")

    generate_btn.click(
        fn=process_ad_content,
        inputs=[title_input, image_input, gen_images_chk, gen_video_chk],
        outputs=[gallery_output, video_output_comp, status_output]
    )

if __name__ == "__main__":
    demo.queue().launch(server_name="0.0.0.0", server_port=7860, inbrowser=True, theme=gr.themes.Monochrome())

