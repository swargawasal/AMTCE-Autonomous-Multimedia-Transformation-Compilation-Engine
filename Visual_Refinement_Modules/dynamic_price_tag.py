import os
import logging
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import subprocess
import uuid
from collections import deque
import re

logger = logging.getLogger("dynamic_price_tag")

class DynamicPriceTag:
    def __init__(self):
        self.font_path = os.path.abspath("assets/fonts/Inter-Bold.ttf")
        if not os.path.exists(self.font_path):
             self.font_path = "arial.ttf" 

    def _draw_glass_box(self, draw, x, y, w, h, radius=15):
        """Draws a premium solid background for lower-third style wipe."""
        draw.rounded_rectangle([x, y, x+w, y+h], radius=radius, fill=(20, 10, 30, 240))
        draw.rounded_rectangle([x, y, x+w, y+h], radius=radius, outline=(200, 180, 50, 255), width=2)

    def _split_text(self, text):
        splitters = [" and ", " & ", ", "]
        for s in splitters:
            if s in text.lower():
                return [t.strip().upper() for t in re.split(s, text, flags=re.IGNORECASE)]
        return [text.strip().upper()]

    def _is_skin(self, frame, x, y):
        """Checks if a specific pixel is skin-toned using HSV bounds."""
        if x < 0 or x >= frame.shape[1] or y < 0 or y >= frame.shape[0]:
            return False
            
        hsv_pixel = cv2.cvtColor(np.uint8([[frame[y, x]]]), cv2.COLOR_BGR2HSV)[0][0]
        h, s, v = hsv_pixel
        
        # Standard skin bounds under various lighting
        if (0 <= h <= 20 and 30 <= s <= 150 and 60 <= v <= 255) or \
           (160 <= h <= 180 and 30 <= s <= 150 and 60 <= v <= 255):
            return True
            
        return False

    def generate_tracking_video(self, video_path: str, output_path: str, human_box: list, item_name: str, price_text: str, location_hint: str, start_sec: float = 1.0, delay_sec: float = 0.75) -> bool:
        """
        Tracks the item and generates a transparent .mov overlay video.
        """
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return False

            fps = cap.get(cv2.CAP_PROP_FPS)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if fps <= 0: fps = 30.0
            
            # Sync tracker to the exact trimmed start timestamp!
            if start_sec > 0:
                cap.set(cv2.CAP_PROP_POS_MSEC, start_sec * 1000)
            
            ret, frame = cap.read()
            if not ret: return False

            try:
                tracker = cv2.TrackerKCF_create()
            except AttributeError:
                # Fallback depending on opencv-contrib-python version
                tracker = cv2.TrackerKCF_create()
            
            hx, hy, hw, hh = human_box
            hx, hy = max(0, hx), max(0, hy)
            hw, hh = min(width - hx, hw), min(height - hy, hh)
            bbox = (hx, hy, hw, hh)
            tracker.init(frame, bbox)

            ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
            cmd = [
                ffmpeg_bin, '-y',
                '-f', 'rawvideo', '-vcodec', 'rawvideo',
                '-s', f'{width}x{height}', '-pix_fmt', 'rgba',
                '-r', str(fps), '-i', '-', '-c:v', 'qtrle',
                output_path
            ]
            process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

            # --- PARSE SPLIT TAGS ---
            items = self._split_text(item_name)
            
            if "est" not in price_text.lower():
                price_text = f"Est. {price_text}"
            prices = self._split_text(price_text)
            
            # Match lengths
            if len(prices) < len(items):
                prices.extend([prices[-1]] * (len(items) - len(prices)))
            
            # Limit to 2 max to prevent massive overlays
            items = items[:2]
            prices = prices[:2]
            
            title_size = int(height * 0.014)
            price_size = int(height * 0.017)
            
            try:
                font_title = ImageFont.truetype(self.font_path, title_size)
                font_price = ImageFont.truetype(self.font_path, price_size)
            except:
                font_title = ImageFont.load_default()
                font_price = ImageFont.load_default()

            delay_frames = int(delay_sec * fps)
            max_frames = delay_frames + int(5.0 * fps)
            frame_count = 0
            
            y_ratios = {
                'head': 0.15, 'torso': 0.45, 'legs': 0.75, 'feet': 0.95,
                'bag': 0.50, 'accessories': 0.35, 'unknown': 0.50
            }
            target_ratio = y_ratios.get(location_hint.lower(), 0.50)

            # --- PRECALCULATE TAG DIMENSIONS ---
            temp_img = Image.new('RGBA', (1, 1))
            temp_draw = ImageDraw.Draw(temp_img)
            
            padding_x, padding_y, gap = 10, 8, 4
            box_spacing = 15
            
            tag_dims = []
            max_w = 0
            
            for item, price in zip(items, prices):
                t_bbox = temp_draw.textbbox((0, 0), item, font=font_title)
                p_bbox = temp_draw.textbbox((0, 0), price, font=font_price)
                t_w, t_h = t_bbox[2]-t_bbox[0], t_bbox[3]-t_bbox[1]
                p_w, p_h = p_bbox[2]-p_bbox[0], p_bbox[3]-p_bbox[1]
                
                box_w = max(t_w, p_w) + (padding_x * 2)
                box_h = t_h + p_h + gap + (padding_y * 2)
                if box_w > max_w: max_w = box_w
                tag_dims.append({"w": box_w, "h": box_h, "t_h": t_h})
                
            total_h = sum([d["h"] for d in tag_dims]) + (box_spacing * (len(items)-1))
            
            # --- CREATE THE STATIC FULL TAG IMAGE FOR WIPING ---
            full_tag_img = Image.new('RGBA', (max_w, total_h), (0,0,0,0))
            tag_draw = ImageDraw.Draw(full_tag_img)
            
            curr_y = 0
            for i, (item, price) in enumerate(zip(items, prices)):
                d = tag_dims[i]
                # Right align text elements within the max width, or just draw box?
                # Actually, standardizing box width looks better.
                d["w"] = max_w
                self._draw_glass_box(tag_draw, 0, curr_y, max_w, d["h"])
                tag_draw.text((padding_x, curr_y + padding_y), item, font=font_title, fill=(255, 255, 255, 255))
                tag_draw.text((padding_x, curr_y + padding_y + d["t_h"] + gap), price, font=font_price, fill=(255, 215, 0, 255))
                curr_y += d["h"] + box_spacing

            # History for smoothing "shivering"
            history = deque(maxlen=20)
            history_y = deque(maxlen=20)
            frames_since_inactive = 0
            skin_violations = 0
            skin_violation_log_count = 0  # Throttle: log once per 50 triggers
            
            line_start_x, target_y = width // 2, int(height * 0.65)
            anchor_y = target_y
            box_target_y = target_y
            current_side = None
            
            tracker_active = True
            has_logged_tracker_drop = False

            while True:
                if frame_count > 0:
                    ret, frame = cap.read()
                    if not ret: break
                
                # Verify tracker hasn't catastrophically failed via bounding box
                if tracker_active:
                    success, tracking_box = tracker.update(frame)
                    
                    if success:
                        history.append(tracking_box)
                    else:
                        if not has_logged_tracker_drop:
                            logger.warning("Tracker Dropped: OpenCV Success = False (Using last known position)")
                            has_logged_tracker_drop = True
                        # Do NOT kill tracker_active here! Use the history fallback and freeze position.
                        
                    if len(history) > 0:
                        # Apply moving average to stop shivering
                        avg_box = np.mean(history, axis=0)
                        tx, ty, tw, th = [int(v) for v in avg_box]

                        
                        # Lock absolute center horizontally
                        safe_x = tx + tw // 2
                        
                        # Calculate initial vertical anchor
                        base_y = int(ty + (th * target_ratio))
                        
                        # SMART VERTICAL SEEKER (For Strapless/Low-Cut Items)
                        target_y = base_y
                        found_fabric = True
                        
                        if getattr(self, '_is_skin', None):
                            # Default check at the anchor point
                            if self._is_skin(frame, safe_x, target_y):
                                found_fabric = False
                                
                                # If it's a torso tag pointing at bare chest, scan downwards
                                if target_ratio < 0.8: 
                                    max_scan_y = int(ty + (th * 0.85))
                                    for scan_y in range(base_y, max_scan_y, 4):
                                        if not self._is_skin(frame, safe_x, scan_y):
                                            # We found fabric! Anchor here safely.
                                            target_y = scan_y + 4 
                                            found_fabric = True
                                            break
                                            
                            # DANGEROUS: Pink/blush clothing triggers skin-detection.
                            # We NO LONGER drop out on skin. The tag stays for the full 5 seconds.
                            if target_ratio < 0.75:
                                if not found_fabric:
                                    skin_violations += 1
                                else:
                                    skin_violations = max(0, skin_violations - 1)
                                    
                                if skin_violations > 5:
                                    skin_violation_log_count += 1
                                    if skin_violation_log_count % 50 == 1:  # 1 log per 50 hits
                                        logger.warning("Skin Touch Threshold reached, but KEEPING tracker active.")

                        anchor_y = target_y
                        
                        # Protect the face (increase face zone to top 55% of bounding box)
                        face_zone_bottom = ty + int(th * 0.55)
                        box_target_y = target_y
                        tag_top = box_target_y - (total_h // 2)
                        
                        if tag_top < face_zone_bottom:
                            box_target_y = face_zone_bottom + (total_h // 2) + 20
                            
                        # Smooth final box_target_y to eliminate vertical jumping
                        history_y.append(box_target_y)
                        box_target_y = int(np.mean(history_y))
                            
                        space_left, space_right = tx, width - (tx + tw)
                        
                        # Dynamic leader length: ensure the tag box starts OUTSIDE the human's bounding box
                        dynamic_leader = max(40, int((tw // 2) + 15))
                        required_space = max_w + dynamic_leader + 10
                        
                        if current_side is None:
                            current_side = "right" if space_right > space_left else "left"
                        elif current_side == "right" and space_right < required_space and space_left > required_space:
                            current_side = "left"
                        elif current_side == "left" and space_left < required_space and space_right > required_space:
                            current_side = "right"
                            
                        is_right_side = (current_side == "right")
                        line_start_x = safe_x
                            
                        if line_start_x < 0 or line_start_x > width or target_y < 0 or target_y > height:
                             logger.warning(f"Tracker warning: Anchor out of bounds ({line_start_x}, {target_y})")
                             # Do NOT kill the tracker here, just let it clip.

                # Visibility logic
                active_display = tracker_active and (frame_count >= delay_frames) and (frame_count < max_frames)
                
                img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
                
                if frame_count >= delay_frames:
                    if not active_display:
                        frames_since_inactive += 1
                        
                    if active_display or (not active_display and frames_since_inactive <= 15):
                        draw = ImageDraw.Draw(img)
                        
                        LEADER_LENGTH = dynamic_leader if 'dynamic_leader' in locals() else 40
                        box_y = box_target_y - (total_h // 2)
                        
                        if is_right_side:
                            target_box_x = line_start_x + LEADER_LENGTH
                            line_end_x = target_box_x
                        else:
                            target_box_x = line_start_x - LEADER_LENGTH - max_w
                            line_end_x = target_box_x + max_w
                            
                        line_end_y = box_y + (total_h // 2)
                        
                        # --- LOWER THIRD WIPE ANIMATION ---
                        anim_frames = 15
                        if not active_display:
                            # OUT ANIMATION
                            anim_progress = max(0.0, (anim_frames - frames_since_inactive) / anim_frames)
                        else:
                            # IN ANIMATION
                            display_frame = frame_count - delay_frames
                            anim_progress = min(1.0, display_frame / anim_frames)
                            
                        # Wipe ease curve
                        if active_display:
                            # In-Animation: Snappy ease-out
                            ease = 1.0 - pow(1.0 - anim_progress, 4)
                        else:
                            # Out-Animation: Smooth ease-in
                            ease = 1.0 - pow(anim_progress, 3)
                        
                        # Calculate cropped width for horizontal wipe
                        curr_w = int(max_w * ease)
                        curr_leader = int(LEADER_LENGTH * ease)
                        
                        if ease > 0.05:
                            # Draw Dynamic Line growing with the wipe
                            if is_right_side:
                                dynamic_target_x = line_start_x + curr_leader
                                draw.line([(line_start_x, anchor_y), (dynamic_target_x, line_end_y)], fill=(255, 255, 255, 255), width=3)
                                if curr_w > 0:
                                    cropped_tag = full_tag_img.crop((0, 0, curr_w, total_h))
                                    img.paste(cropped_tag, (dynamic_target_x, box_y), cropped_tag)
                            else:
                                dynamic_target_x = line_start_x - curr_leader
                                draw.line([(line_start_x, anchor_y), (dynamic_target_x, line_end_y)], fill=(255, 255, 255, 255), width=3)
                                if curr_w > 0:
                                    cropped_tag = full_tag_img.crop((max_w - curr_w, 0, max_w, total_h))
                                    img.paste(cropped_tag, (dynamic_target_x - curr_w, box_y), cropped_tag)
                                
                            # Draw Anchor Dot
                            dot_r = 5
                            draw.ellipse([line_start_x-dot_r, anchor_y-dot_r, line_start_x+dot_r, anchor_y+dot_r], fill=(255, 255, 255, 255), outline=(0,0,0,255), width=2)

                try:
                    process.stdin.write(img.tobytes())
                except BrokenPipeError:
                    break
                    
                frame_count += 1

            process.stdin.close()
            process.wait()
            cap.release()
            return os.path.exists(output_path)
            
        except Exception as e:
            logger.error(f"Generate Tracking Video Failed: {e}")
            return False

tracker_engine = DynamicPriceTag()
