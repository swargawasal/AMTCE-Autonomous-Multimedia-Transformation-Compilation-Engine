
import os
import subprocess
import logging
try:
    from .ai_blender import blender
except (ImportError, ValueError):
    try:
        from ai_blender import blender
    except ImportError:
        try:
            from .ai_blender_local import blender
        except (ImportError, ValueError):
            from ai_blender_local import blender

logger = logging.getLogger("thumb_gen")

FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
FFMPEG_BIN  = os.getenv("FFMPEG_BIN",  "ffmpeg")
# Thumbnail frame scan budget — reduce to speed up; raise for more accuracy
# Overridable via .env: THUMB_FRAME_SAMPLES=40 restores original behaviour
THUMB_FRAME_SAMPLES = int(os.getenv("THUMB_FRAME_SAMPLES", "20"))

def get_video_duration(video_path):
    try:
        cmd = [
            FFPROBE_BIN, 
            "-v", "error", 
            "-show_entries", "format=duration", 
            "-of", "default=noprint_wrappers=1:nokey=1", 
            video_path
        ]
        result = subprocess.check_output(cmd).decode().strip()
        return float(result)
    except Exception as e:
        logger.error(f"Failed to get duration: {e}")
        return 0

def _find_best_ig_frame(video_path: str, duration: float, intro_offset: float = 0.0, garment_hint: str = None) -> float:
    """
    PRIMARY scorer — optimized for Instagram photo quality.

    Scores each sampled frame on 4 axes:
      1. Sharpness        — Laplacian variance (no motion blur)
      2. Exposure         — brightness not too dark, not blown out
      3. Contrast         — standard deviation of luminance
      4. Body coverage    — HOG-based rough check that a human figure is present

    Returns the timestamp of the best-scoring frame, or -1.0 on failure
    (caller must fall back to motion-based scorer when -1.0 is returned).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("⚠️ [IG_FRAME] OpenCV not available — skipping primary scorer.")
        return -1.0

    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return -1.0

        fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or int(duration * fps)

        # Skip first/last 8% of the usable video — intros and outros are often logos/black frames
        usable_duration = max(0.1, duration - intro_offset)
        skip_frames = max(1, int((usable_duration * fps) * 0.08))
        intro_frames = int(intro_offset * fps)
        start_frame = intro_frames + skip_frames
        end_frame   = max(start_frame + 1, total - skip_frames)

        # Sample THUMB_FRAME_SAMPLES frames evenly across the usable window
        # (default 20 — halves scan time vs original 40 with same quality on short clips)
        n_samples   = THUMB_FRAME_SAMPLES
        step        = max(1, (end_frame - start_frame) // n_samples)

        # Face detector for bonus scoring
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)

        best_score = -1.0
        best_time  = intro_offset + (usable_duration * 0.5)  # safe fallback within this function

        frame_idx = start_frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

        while frame_idx < end_frame:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break

            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # ── 1. Sharpness — Laplacian variance ────────────────────────────
            # A perfectly sharp frame gives 800+; a blurry one gives <100.
            lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            
            # [FIX] Strict Sharpness Floor — reject motion blur transitions entirely
            if lap_var < 200.0:
                frame_idx += step
                continue
                
            # Normalise: map 200→2000 to 0→1, clamp above 2000
            sharpness = min(1.0, (lap_var - 200) / 1800.0)

            # ── 2. Exposure — penalise too dark (<40) or blown out (>215) ────
            mean_brightness = float(gray.mean())
            if 50 <= mean_brightness <= 210:
                exposure = 1.0
            elif mean_brightness < 50:
                exposure = mean_brightness / 50.0
            else:
                exposure = (255 - mean_brightness) / 45.0
            exposure = max(0.0, min(1.0, exposure))

            # ── 3. Contrast — std-dev of luminance ───────────────────────────
            contrast = min(1.0, float(gray.std()) / 80.0)

            # ── 4. Content Awareness (Face & Body) ───────────────────────────
            face_bonus = 0.0
            body_bonus = 0.0
            frame_area = h * w
            
            # A. Face Check (Penalize extreme close-ups, reward well-framed head)
            if not face_cascade.empty():
                faces = face_cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
                )
                if len(faces) > 0:
                    largest_face = max(faces, key=lambda f: f[2] * f[3])
                    face_area_pct = (largest_face[2] * largest_face[3]) / max(frame_area, 1)
                    
                    if face_area_pct > 0.25:
                        # [FIX] Extreme Close-Up Penalty (Restricts outfits from showing)
                        face_bonus = -0.40
                    elif face_area_pct < 0.15:
                        # Well proportioned face means full body/outfit is likely visible
                        face_bonus = 0.30
                    else:
                        face_bonus = 0.10
            
            # ── 4.5 Garment-Aware Focal Logic ────────────────────────────────
            # Identify if we should focus on upper or lower body
            focus_mode = "full" # default
            is_metallic = False
            if garment_hint:
                gh = garment_hint.lower()
                top_keywords = ["shirt", "top", "blouse", "jacket", "kurti", "hoodie", "sweater", "bra", "vest", "crop"]
                bot_keywords = ["pants", "jeans", "shorts", "skirt", "leggings", "joggers", "cargo", "trousers", "lehenga", "dhoti"]
                shiny_keywords = ["metallic", "shiny", "silk", "satin", "glossy", "sheen", "sequin", "glitter", "sparkle"]
                
                if any(k in gh for k in top_keywords): focus_mode = "top"
                elif any(k in gh for k in bot_keywords): focus_mode = "bottom"
                
                if any(k in gh for k in shiny_keywords): is_metallic = True

            
            # B. Body / Outfit Presence (HOG)
            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            # Scale down for speed
            scale = min(1.0, 640 / max(w, 1))
            small = cv2.resize(frame, (int(w * scale), int(h * scale)))
            rects, _ = hog.detectMultiScale(small, winStride=(8, 8), padding=(4, 4), scale=1.05)
            
            if len(rects) > 0:
                # Find largest/most central body
                cx = (w * scale) / 2
                best_rect = max(rects, key=lambda r: r[2] * r[3])
                rx, ry, rw, rh = best_rect
                
                body_area_pct = (rw * rh) / (small.shape[0] * small.shape[1])
                body_center_x = rx + (rw / 2)
                
                # Check centering
                center_offset = abs(body_center_x - cx) / cx
                is_centered = center_offset < 0.30
                
                # Reward good body sizing (outfit visible) and centering
                if 0.15 <= body_area_pct <= 0.70 and is_centered:
                    body_bonus = 0.40
                elif is_centered:
                    body_bonus = 0.20
                else:
                    body_bonus = -0.10 # Edge of frame penalty
                
                # ── Focal Region Adjustment (Product-Centric Scoring) ────────
                # Map detection rect back to full-size gray coordinates (Fixes previous coordinate bug)
                frx, fry = int(rx / scale), int(ry / scale)
                frw, frh = int(rw / scale), int(rh / scale)
                
                # Ensure we stay within bounds
                frx = max(0, min(frx, w - 1))
                fry = max(0, min(fry, h - 1))
                frw = max(1, min(frw, w - frx))
                frh = max(1, min(frh, h - fry))

                target_region = None
                if focus_mode == "top":
                    # Upper 50% of the body rect
                    uh = int(frh * 0.50)
                    target_region = gray[fry : fry + uh, frx : frx + frw]
                elif focus_mode == "bottom":
                    # Lower 55% of the body rect (centered on garment)
                    ly = int(fry + (frh * 0.40))
                    lh = int(frh * 0.50)
                    target_region = gray[ly : ly + lh, frx : frx + frw]
                
                # If we have a specific product focus, boost score for clarity in that region
                if target_region is not None and target_region.size > 100:
                    r_sharp = float(cv2.Laplacian(target_region, cv2.CV_64F).var())
                    r_contrast = float(target_region.std())
                    
                    # Clarity Boost: Highlight frames where the specific product is sharpest
                    # Sequin/Skirt details need high sharpness to look premium.
                    if r_sharp > 350 and r_contrast > 45:
                        body_bonus += 0.35
                        logger.debug(f"🔍 [THUMB] {focus_mode}-region focus match: sharp={r_sharp:.1f}")

                # Metallic/Shiny Detection (Glint check)
                if is_metallic:
                    # Scan the target region or full body if no specific focus
                    glint_region = target_region if target_region is not None else gray[fry : fry + frh, frx : frx + frw]
                    if glint_region.size > 100:
                        _, max_val, _, _ = cv2.minMaxLoc(glint_region)
                        if max_val > 235: # Bright glint detected (sequins/metallic)
                            body_bonus += 0.20
                            logger.debug(f"✨ [THUMB] Sheen/Metallic glint detected: {max_val}")


            # ── Combined score ────────────────────────────────────────────────
            # Tuned weights: sharpness and human presence (body+face) matter most
            score = (sharpness * 0.3) + (exposure * 0.1) + (contrast * 0.1) + face_bonus + body_bonus

            time_sec = frame_idx / fps
            if score > best_score:
                best_score = score
                best_time  = time_sec
                logger.debug(
                    f"[IG_FRAME] New best: t={time_sec:.2f}s "
                    f"score={score:.3f} (sharp={sharpness:.2f} exp={exposure:.2f} "
                    f"face={face_bonus:.2f} body={body_bonus:.2f})"
                )

            frame_idx += step

        cap.release()
        logger.info(
            f"✅ [IG_FRAME] Best Instagram frame: t={best_time:.2f}s score={best_score:.3f}"
        )
        return best_time

    except Exception as e:
        logger.warning(f"⚠️ [IG_FRAME] Primary scorer failed: {e}")
        return -1.0


def _find_best_thumbnail_timestamp(video_path: str, duration: float, hook_moment: dict = None, intro_offset: float = 0.0, garment_hint: str = None) -> float:
    """
    Finds the most visually engaging frame based on motion, face presence, and brightness.
    Returns the timestamp in seconds. Uses 50% of duration as fallback.
    
    If hook_moment is provided, tries hook_time ± 0.8s first.
    If best score in window is < 0.3, falls back to full video scan.
    """
    fallback_time = intro_offset + max(0, (duration - intro_offset) / 2)
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("⚠️ OpenCV not found, falling back to 50% mark thumbnail.")
        return fallback_time

    def score_frame(frame, prev_gray, face_cascade):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small_gray = cv2.resize(gray, (360, 640))
        
        # 1. Motion Score
        motion_score = 0.0
        if prev_gray is not None:
            diff = cv2.absdiff(prev_gray, small_gray)
            motion_score = float(diff.sum()) / float(small_gray.shape[0] * small_gray.shape[1] * 255.0)
        
        # 2. Face Presence
        face_presence = 0
        if face_cascade and not face_cascade.empty():
            faces = face_cascade.detectMultiScale(small_gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
            if len(faces) > 0:
                face_presence = 1

        # 3. Brightness
        brightness = float(small_gray.mean()) / 255.0
        
        # 4. Total Score
        score = (motion_score * 0.6) + (face_presence * 0.3) + (brightness * 0.1)
        return score, small_gray

    def scan_window(start_t, end_t, face_cascade):
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return -1.0, fallback_time

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        sample_every = max(1, int(fps * 0.3))
        
        best_score = -1.0
        best_time = fallback_time
        prev_gray_cached = None
        
        start_frame = int(start_t * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        
        frame_idx = start_frame
        while True:
            ret, frame = cap.read()
            if not ret or (frame_idx / fps) > end_t:
                break

            if (frame_idx - start_frame) % sample_every == 0:
                time_sec = frame_idx / fps
                score, current_gray = score_frame(frame, prev_gray_cached, face_cascade)
                prev_gray_cached = current_gray

                if score > best_score:
                    best_score = score
                    best_time = time_sec

            frame_idx += 1
            
        cap.release()
        return best_score, best_time

    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        score_thresh = 0.3
        override_thresh = 0.45
        
        if hook_moment and "time" in hook_moment:
            h_time = hook_moment["time"]
            
            # Step 0: Direct Hook Evaluation (Override)
            try:
                cap = cv2.VideoCapture(video_path)
                if cap.isOpened():
                    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(h_time * fps))
                    ret, frame = cap.read()
                    if ret:
                        h_score, _ = score_frame(frame, None, face_cascade)
                        logger.info(f"🎣 Hook-frame evaluation: score={h_score:.2f} at {h_time:.2f}s")
                        if h_score >= override_thresh:
                            cap.release()
                            logger.info(f"🚀 Hook frame exceeds override threshold ({override_thresh}) — selected.")
                            logger.info("📊 Hook Used As Thumbnail: True (Direct Override)")
                            return h_time
                    cap.release()
            except Exception as e:
                logger.warning(f"⚠️ Hook override evaluation failed: {e}")

            # Step 1: Window Scan
            window_start = max(0, h_time - 0.8)
            window_end = min(duration, h_time + 0.8)
            
            if window_start < window_end:
                logger.info(f"🎣 Hook-centered window search: {window_start:.2f}s to {window_end:.2f}s")
                h_score, h_best_t = scan_window(window_start, window_end, face_cascade)
                
                if h_score >= score_thresh:
                    logger.info("📊 Hook Used As Thumbnail: True (Window Scan)")
                    return h_best_t
                logger.info(f"⚠️ Hook window score low ({h_score:.2f}) — falling back to full scan")
            else:
                logger.info("⚠️ Hook window range invalid — falling back to full scan")

        # Full video scan fallback
        _, final_time = scan_window(intro_offset, duration, face_cascade)
        if hook_moment:
            logger.info("📊 Hook Used As Thumbnail: False (Full scan fallback)")
        return final_time

    except Exception as e:
        logger.warning(f"⚠️ Smart thumbnail detection failed: {e}. Falling back to 25% mark.")
        return fallback_time






def _opencv_fallback_thumbnail(video_path, output_path, hook_moment=None, intro_offset: float = 0.0, garment_hint: str = None):
    """
    FALLBACK: Extract the best visual frame from the video using OpenCV scoring
    and save it as a clean JPEG (no text overlay, no blending). This is the
    original method and is kept intact as a reliability safety net.

    Returns the saved path on success, None on failure.
    """
    try:
        duration  = get_video_duration(video_path)
        timestamp = _find_best_ig_frame(video_path, duration, intro_offset, garment_hint)

        if timestamp < 0:
            logger.info("📉 [THUMB/FALLBACK] IG scorer returned -1 → motion scorer.")
            timestamp = _find_best_thumbnail_timestamp(video_path, duration, hook_moment, intro_offset, garment_hint)
        else:
            logger.info(f"🌟 [THUMB/FALLBACK] Using IG-quality frame at {timestamp:.2f}s")

        base_name     = os.path.splitext(video_path)[0]
        raw_thumb_path = f"{base_name}_raw_thumb.jpg"

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        cmd = [
            FFMPEG_BIN, "-y",
            "-ss", str(timestamp),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            "-update", "1",
            raw_thumb_path,
        ]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if not os.path.exists(raw_thumb_path):
            logger.error("❌ [THUMB/FALLBACK] Frame extraction failed.")
            return None

        # Move/rename to final path
        import shutil
        shutil.move(raw_thumb_path, output_path)
        logger.info(f"✅ [THUMB/FALLBACK] Frame thumbnail saved: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"❌ [THUMB/FALLBACK] OpenCV fallback failed: {e}")
        return None


def generate_thumbnail(video_path, title_text, accent_color="yellow", output_path=None, hook_moment=None, ai_title=None, intro_offset: float = 0.0):
    """
    Generates the primary thumbnail for the video.

    PIPELINE (in priority order):
      1. AI Ad Image (Gemini Imagen) — clean studio background, outfit-forward, NO text overlay.
         Triggered only when ENABLE_AI_IMAGE_CREATION=true in .env.
         Also generates Pinterest (2:3) and Instagram Feed (4:5) variants into sample_thumbs/.

      2. OpenCV Frame Extractor (original method) — intelligent frame selection from the video.
         Used as a reliable fallback when AI generation fails or quota is exhausted.

    Args:
        ai_title: Override the Imagen prompt with a Gemini-detected product/wear name
                  (e.g. "Red Silk Lehenga Choli"). When None, falls back to title_text.
                  The pipeline passes this from mon_meta["item_name"]. Standalone/Gradio
                  use passes user-typed text directly as title_text (ai_title stays None).

    Returns:
        Path to the primary thumbnail file (Instagram ratio if AI ran, frame JPEG otherwise),
        or None on total failure.
    """
    if not os.path.exists(video_path):
        logger.error(f"Video not found: {video_path}")
        return None

    # Resolve final path
    if output_path:
        final_thumb_path = output_path
        os.makedirs(os.path.dirname(final_thumb_path) or ".", exist_ok=True)
    else:
        base_name        = os.path.splitext(video_path)[0]
        final_thumb_path = f"{base_name}_thumb.jpg"

    output_dir  = os.path.dirname(final_thumb_path) or "sample_thumbs"
    base_no_ext = os.path.splitext(os.path.basename(final_thumb_path))[0]

    # ── BRANCH A: AI Ad Image Generation ─────────────────────────────────────
    enable_ai = os.getenv("ENABLE_AI_IMAGE_CREATION", "true").lower() in ("true", "yes", "on", "1")

    if enable_ai:
        logger.info("🤖 [THUMB] AI image generation is ON — attempting Gemini Imagen...")
        try:
            from Thumb_Modules.ai_media_generator import generate_ad_images
        except ImportError:
            try:
                from ai_media_generator import generate_ad_images
            except ImportError:
                logger.warning("⚠️ [THUMB] ai_media_generator not importable — skipping AI generation.")
                generate_ad_images = None

        if generate_ad_images:
            try:
                # Use Gemini-detected product name when the pipeline supplies it;
                # otherwise fall back to whatever the caller passed as title_text.
                _prompt_title = ai_title if ai_title and len(ai_title.strip()) > 3 else title_text
                logger.info(f"🏷️ [THUMB] AI prompt title: '{_prompt_title}'")
                ad_results = generate_ad_images(
                    title=_prompt_title,
                    output_dir=output_dir,
                    base_name=base_no_ext,
                )

                if ad_results:
                    # Use the Instagram Feed image as the primary thumbnail (best IG upload quality)
                    ig_paths  = ad_results.get("instagram_feed", [])
                    pin_paths = ad_results.get("pinterest", [])

                    primary_path = (ig_paths or pin_paths or [])[0] if (ig_paths or pin_paths) else None

                    if primary_path and os.path.exists(primary_path):
                        # Copy/rename best variant to the canonical thumb path expected by callers
                        import shutil
                        if primary_path != final_thumb_path:
                            shutil.copy2(primary_path, final_thumb_path)

                        logger.info(
                            f"✅ [THUMB] AI Ad Kit ready:"
                            f" IG={len(ig_paths)} Pinterest={len(pin_paths)} paths"
                        )
                        return final_thumb_path
                    else:
                        logger.warning("⚠️ [THUMB] AI generation returned no usable images.")
                else:
                    logger.warning("⚠️ [THUMB] AI generation returned empty — falling back to OpenCV.")

            except Exception as e:
                logger.warning(f"⚠️ [THUMB] AI generation raised exception (falling back): {e}")

    # ── BRANCH B: OpenCV Frame Extractor (original fallback method) ─────────
    logger.info(f"📽️ [THUMB] Using OpenCV frame extractor (fallback). Hint='{ai_title}'")
    return _opencv_fallback_thumbnail(video_path, final_thumb_path, hook_moment, intro_offset, garment_hint=ai_title)


def generate_ad_content(video_path, title_text, output_dir="sample_thumbs"):
    """
    Full creative ad kit generator. Produces both image ads and video ads (if enabled).

    This is an OPTIONAL supplementary call — the pipeline does not depend on it.
    Call after generate_thumbnail() if you want the full ad kit for Pinterest/IG scheduling.

    Args:
        video_path:   Source video (used for Veo image-to-video if AI image failed).
        title_text:   Product/video title.
        output_dir:   Directory for all outputs.

    Returns:
        Dict with:
            "images"   → dict of { format: [paths] }
            "video"    → path to video ad or None
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    kit = {"images": {}, "video": None}

    # Images
    enable_ai = os.getenv("ENABLE_AI_IMAGE_CREATION", "true").lower() in ("true", "yes", "on", "1")
    if enable_ai:
        try:
            from Thumb_Modules.ai_media_generator import generate_ad_images, generate_ad_video
        except ImportError:
            try:
                from ai_media_generator import generate_ad_images, generate_ad_video
            except ImportError:
                logger.warning("⚠️ [AD_KIT] ai_media_generator not importable.")
                return kit

        kit["images"] = generate_ad_images(title_text, output_dir, base_name)

        # Video — use the first AI image as source if available
        source_img = None
        ig_paths = kit["images"].get("instagram_feed", [])
        if ig_paths:
            source_img = ig_paths[0]
        kit["video"] = generate_ad_video(title_text, source_img, output_dir, base_name)

    return kit
