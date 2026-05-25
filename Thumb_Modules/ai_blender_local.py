
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

            # --- Simple Text Overlay Logic ---
            # (Can be expanded with more improved styling later)
            
            # 1. Darken bottom area for text readability
            overlay = img.copy()
            cv2.rectangle(overlay, (0, int(h * 0.7)), (w, h), (0, 0, 0), -1)
            alpha = 0.6
            cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)

            # 2. Add Text
            title_text = title_text.upper()
            font = cv2.FONT_HERSHEY_DUPLEX
            font_scale = 2.5
            thickness = 6
            color = (255, 255, 255) # White, as requested

            # Calculate text size to center it
            text_size = cv2.getTextSize(title_text, font, font_scale, thickness)[0]
            text_x = (w - text_size[0]) // 2
            text_y = int(h * 0.78)  # Directly above caption

            # If text is too wide, scale it down
            if text_size[0] > w - 40:
                 font_scale = font_scale * ((w - 40) / text_size[0])
                 text_size = cv2.getTextSize(title_text, font, font_scale, thickness)[0]
                 text_x = (w - text_size[0]) // 2

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
