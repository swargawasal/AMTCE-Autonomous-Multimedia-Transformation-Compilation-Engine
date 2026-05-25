import os
import textwrap
import logging
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("caption_renderer")

# Configuration
FONT_PATH = os.path.join("assets", "fonts", "Inter-Bold.ttf")
TEXT_COLOR = "#FFFF00"  # Yellow
OUTLINE_COLOR = "black"
OUTLINE_WIDTH = 4

def render_caption_png(text: str, video_width: int, video_height: int) -> str:
    """
    Renders a caption as a transparent PNG overlay.
    Returns the path to the generated PNG.
    """
    try:
        # Enforcement: STRICTLY 2-3 words
        words = text.split()
        if len(words) > 3:
            text = " ".join(words[:3])
            logger.info(f"Sub-rendering: Truncated caption to 3 words: '{text}'")
        elif len(words) < 2 and len(words) > 0:
            logger.warning(f"Sub-rendering: Caption is too short ({len(words)} words)")

        # 2. Canvas Setup - FULL VIDEO RESOLUTION for exact absolute positioning
        canvas = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        # 3. Font Setup
        font_size = int(video_height * 0.045)
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)
        except Exception as e:
            logger.warning(f"Failed to load preferred font {FONT_PATH}: {e}. Falling back to default.")
            font = ImageFont.load_default()

        # 4. Text Wrapping
        # Estimate max characters based on font size.
        # Max width is 80% of frame.
        max_px_width = video_width * 0.8
        # Average character width is roughly 0.6 * font_size for Inter-Bold.
        avg_char_width = font_size * 0.6
        max_chars = int(max_px_width / avg_char_width)
        wrapped_text = "\n".join(textwrap.wrap(text, width=max_chars))

        # 5. Rendering with Centering
        # Calculate bounding box for centering
        left, top, right, bottom = draw.multiline_textbbox((0, 0), wrapped_text, font=font, align="center")
        text_w = right - left
        text_h = bottom - top

        # Center horizontally, and set Y position to video_height - 140
        # Positioning: Exactly h-140 from the bottom.
        pos_x = (video_width - text_w) // 2
        pos_y = video_height - 140 - text_h # Adjusting so bottom of text is roughly above brand

        draw.multiline_text(
            (pos_x, pos_y),
            wrapped_text,
            font=font,
            fill=TEXT_COLOR,
            stroke_width=OUTLINE_WIDTH,
            stroke_fill=OUTLINE_COLOR,
            align="center"
        )

        # 6. Save to temporary file
        temp_dir = os.path.join("temp", "captions")
        os.makedirs(temp_dir, exist_ok=True)
        out_path = os.path.join(temp_dir, f"caption_{os.getpid()}_{hash(text) % 100000}.png")
        canvas.save(out_path)
        
        logger.info(f"🎨 [CAPTION_RENDERER] png_generated={out_path}")
        return out_path

    except Exception as e:
        logger.error(f"❌ [CAPTION_RENDERER] Failed to render caption: {e}")
        return ""
