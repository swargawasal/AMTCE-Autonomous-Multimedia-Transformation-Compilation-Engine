
import os
import logging
import cv2
import numpy as np

# Configure Logger
logger = logging.getLogger("ai_blender")

class AIBlender:
    """
    Handles AI-driven thumbnail composition and text overlay.
    """
    
    def create_blended_thumbnail(self, image_path, output_path, title_text, accent_color="yellow"):
        """
        Creates a thumbnail with a title overlay.
        Args:
            image_path: Path to the input image (hero frame).
            output_path: Path to save the final thumbnail.
            title_text: Text to overlay on the thumbnail.
            accent_color: Color for the text or accent elements (not fully implemented in this base version).
        Returns:
            True if successful, False otherwise.
        """
        try:
            if not os.path.exists(image_path):
                logger.error(f"Input image not found: {image_path}")
                return False

            img = cv2.imread(image_path)
            if img is None:
                logger.error("Failed to read image.")
                return False

            h, w = img.shape[:2]

            # --- Clean Text Overlay Logic ---

            # 1. Overlay setup (Black box completely removed per user request)
            # The name is clean and floating without a dark overlay strip.
            title_text = title_text.upper()
            font = cv2.FONT_HERSHEY_DUPLEX
            font_scale = 2.5
            thickness = 6
            color = (255, 255, 255) # White, as requested

            # Calculate text size to center it
            text_size = cv2.getTextSize(title_text, font, font_scale, thickness)[0]
            text_x = (w - text_size[0]) // 2
            
            # Position the text neatly ABOVE the standard video caption area
            # Moving it from 0.65 down to 0.78 to hover directly near the caption
            text_y = int(h * 0.78)  

            # If text is too wide, scale it down
            if text_size[0] > w - 40:
                 font_scale = font_scale * ((w - 40) / text_size[0])
                 text_size = cv2.getTextSize(title_text, font, font_scale, thickness)[0]
                 text_x = (w - text_size[0]) // 2

            # 2. Draw Text with a solid drop-shadow/outline for readability
            # Draw Outline (Thick Black)
            outline_thickness = thickness + 4
            cv2.putText(img, title_text, (text_x, text_y), font, font_scale, (0, 0, 0), outline_thickness)
            
            # Draw Inner Text (Yellow)
            cv2.putText(img, title_text, (text_x, text_y), font, font_scale, color, thickness)

            # 3. Save
            cv2.imwrite(output_path, img)
            logger.info(f"✅ Thumbnail created: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Thumbnail creation failed: {e}")
            return False

# Export Singleton
blender = AIBlender()
