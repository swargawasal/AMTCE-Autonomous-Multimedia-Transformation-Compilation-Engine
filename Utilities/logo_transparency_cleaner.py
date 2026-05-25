import os
import logging

from PIL import Image

logger = logging.getLogger("logo_cleaner")


def clean_logo_background(input_path: str, output_path: str) -> bool:
    """Remove white/near-white background from a logo and save a transparent PNG.

    The algorithm treats any pixel where all three RGB components exceed 235 as
    "white" (covers anti-aliased edges) and forces its alpha channel to 0.
    Non-white or colored pixels are preserved.

    Args:
        input_path: path to the original logo (e.g. "logo/Brand_logo.png").
        output_path: destination for cleaned PNG (should be under assets/logo).

    Returns:
        True if any white pixels were detected and made transparent, False if no
        modification was necessary.
    """

    # Ensure input exists first so callers don't accidentally wipe the target.
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Logo file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    img = Image.open(input_path).convert("RGBA")
    width, height = img.size
    pixels = img.load()

    detected_white = False

    # iterate manually for clarity; images will typically be quite small
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            # Threshold tuned for anti‑aliased edges (slightly lower than pure 255)
            if r > 235 and g > 235 and b > 235:
                # mark pixel as fully transparent
                pixels[x, y] = (r, g, b, 0)
                detected_white = True

    # Save the cleaned file (RGBA ensures alpha channel present)
    img.save(output_path, format="PNG")

    logger.info("[LOGO_CLEANER] detected_white_background=%s", detected_white)
    if detected_white:
        logger.info("[LOGO_CLEANER] transparency_applied")
    logger.info("[LOGO_CLEANER] output=%s", output_path)

    return detected_white
