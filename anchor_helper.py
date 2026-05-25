import math
from typing import Optional, Tuple

import cv2
import numpy as np


_LOC_ALIASES = {
    "eyes": "glasses",
    "eye": "glasses",
    "shades": "glasses",
    "sunglasses": "glasses",
    "torso": "torso",
    "chest": "torso",
    "dress": "torso",
    "top": "torso",
    "blouse": "torso",
    "shirt": "torso",
    "wrist": "wrist",
    "watch": "wrist",
    "bracelet": "wrist",
    "neck": "neck",
    "necklace": "neck",
    "chain": "neck",
    "hand": "bag",
    "clutch": "bag",
    "bag": "bag",
    "feet": "feet",
    "shoes": "feet",
    "boots": "feet",
    "sandals": "feet",
    "heels": "feet",
    "head": "head",
    "hat": "head",
    "hair": "head",
}

_HEAD_ITEMS = {"glasses", "eyes", "eye", "sunglasses", "shades", "head", "hat", "hair"}
_LOWER_ITEMS = {"wrist", "watch", "bracelet", "hand", "bag", "clutch", "feet", "shoes", "heels", "sandals", "boots"}


def _is_skin_or_hair(frame_bgr: np.ndarray, x: int, y: int) -> bool:
    h_img, w_img = frame_bgr.shape[:2]
    x = max(0, min(x, w_img - 1))
    y = max(0, min(y, h_img - 1))
    pixel = frame_bgr[y, x]
    hsv = cv2.cvtColor(np.uint8([[pixel]]), cv2.COLOR_BGR2HSV)[0][0]
    h, s, v = hsv
    if (0 <= h <= 25) and (15 <= s <= 75) and (v >= 100):
        return True
    if (5 <= h <= 20) and (20 <= s <= 60) and (v < 100):
        return True
    return False


def compute_anchor(
    frame_width: int,
    frame_height: int,
    human_box: list,
    location_hint: str = "torso",
    face_box: Optional[list] = None,
    frame_bgr: Optional[np.ndarray] = None,
    side_override: Optional[str] = None,
) -> Tuple[int, int, str, dict]:
    """
    Returns (anchor_x, anchor_y, side, debug_info).
    - side: "left" or "right" chosen by empty space unless overridden.
    - anchor_x: body edge on that side (prevents cross-body lines).
    - anchor_y: garment-zone fraction per location_hint, clamped below face zone.
    """
    hx, hy, hw, hh = human_box

    space_left = hx
    space_right = frame_width - (hx + hw)
    side = side_override or ("left" if space_left >= space_right else "right")
    anchor_x = hx if side == "left" else hx + hw

    # Face exclusion zone
    if face_box:
        # Increase buffer from 20px to 60px to clear the jawline/neck more aggressively
        face_zone_bottom = max(hy + int(hh * 0.35), face_box[1] + face_box[3] + 60)
    else:
        # No face box: be more conservative and skip top 48% (was 42%)
        face_zone_bottom = hy + int(hh * 0.48)
    face_zone_bottom = min(face_zone_bottom, hy + int(hh * 0.60))

    _raw_loc = location_hint.lower() if location_hint else "torso"
    loc = _LOC_ALIASES.get(_raw_loc, "torso")

    # Map location to vertical ratio
    if loc in {"glasses"}:
        target_y = hy + int(hh * 0.18)
    elif loc in {"head"}:
        target_y = hy + int(hh * 0.08)
    elif loc in {"neck"}:
        target_y = face_zone_bottom + int(hh * 0.03)
    elif loc in {"wrist", "watch", "bracelet", "bag", "clutch"}:
        garment_zone_h = (hy + hh) - face_zone_bottom
        target_y = face_zone_bottom + int(garment_zone_h * 0.65)
    elif loc in {"feet"}:
        target_y = hy + int(hh * 0.88)
    else:
        garment_zone_h = (hy + hh) - face_zone_bottom
        target_y = face_zone_bottom + int(garment_zone_h * 0.35)

    # Clamp away from face for non-head items
    if loc not in _HEAD_ITEMS:
        target_y = max(face_zone_bottom + 5, target_y)
    # Keep above waistline for torso items to avoid stomach
    if loc == "torso":
        waist_guard = hy + int(hh * 0.70)
        target_y = min(target_y, waist_guard)
    # Final safety within body box
    target_y = min(max(target_y, hy + 5), hy + hh - 5)

    # Pixel step off skin/hair if needed
    if frame_bgr is not None and loc not in _HEAD_ITEMS:
        if _is_skin_or_hair(frame_bgr, anchor_x, target_y):
            direction = 1 if loc in _LOWER_ITEMS else -1
            for offset in range(1, 60):
                test_y = target_y + (offset * direction)
                if 0 <= test_y < frame_height and not _is_skin_or_hair(frame_bgr, anchor_x, test_y):
                    target_y = test_y
                    break

    debug = {
        "side": side,
        "space_left": space_left,
        "space_right": space_right,
        "loc": loc,
        "face_zone_bottom": face_zone_bottom,
    }
    return int(anchor_x), int(target_y), side, debug
