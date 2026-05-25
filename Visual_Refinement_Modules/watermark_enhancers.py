"""
Watermark Enhancers
-------------------
Shared logic for high-quality watermark refinement.
Used by:
- opencv_watermark.py (Legacy/Dynamic Fallback)
- static_patch_engine.py (Optimized Static/Rigid Path)

Contains:
1. AlphaNeutralizer: Pre-processing to flatten ghosting.
2. ContrastHealer: Post-processing to restore lighting/shadows.
3. EdgeIntegrator: Advanced seam blending (Gradient Bleed + Poisson-like Dissolve).
4. MicroTextureBlender: Grain matching to prevent 'plastic' look.
"""

import cv2
import numpy as np
import logging

logger = logging.getLogger("watermark_enhancers")

class AlphaNeutralizer:
    """
    PRE-PROCESSING: Neutralizes semi-transparent watermarks to aid inpainting.
    Reduces local contrast in the L-channel (LAB) to flatten ghosting.
    """
    @staticmethod
    def neutralize(frame, mask):
        try:
            # 1. Convert to LAB
            if len(frame.shape) < 3: return frame # Grayscale skip
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            
            # 2. Calculate Surround Mean
            # Dilate mask to find local context
            kernel = np.ones((15,15), np.uint8)
            dilated = cv2.dilate(mask, kernel, iterations=1)
            surround_mask = cv2.subtract(dilated, mask)
            
            if cv2.countNonZero(surround_mask) == 0: return frame
            
            mean_l = cv2.mean(l, mask=surround_mask)[0]
            
            # 3. Blend ROI with Mean (85% Original / 15% Mean)
            # Only inside mask
            # L_new = (L * 0.85) + (Mean * 0.15)
            # This reduces the peak brightness of white text overlays
            
            # Create float buffer for precision
            l_float = l.astype(np.float32)
            
            # Vectorized blend where mask > 0
            # We use a masked operation to update only relevant pixels
            roi_indices = (mask > 0)
            
            l_float[roi_indices] = (l_float[roi_indices] * 0.85) + (mean_l * 0.15)
            
            # Merge back
            l = l_float.astype(np.uint8)
            merged = cv2.merge([l, a, b])
            out = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
            
            return out
            
        except Exception as e:
            return frame

class ContrastHealer:
    """
    POST-PROCESSING: Restores natural texture and removes shadow remnants.
    Optimized to run on ROI only.
    """
    @staticmethod
    def heal(frame, mask):
        try:
            # ROI Optimization
            if cv2.countNonZero(mask) == 0: return frame
            
            h_img, w_img = frame.shape[:2]
            x, y, w, h = cv2.boundingRect(mask)
            
            # Add safe margin for filter context (Bilateral needs space)
            pad = 50 
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(w_img, x + w + pad)
            y2 = min(h_img, y + h + pad)
            
            roi_frame = frame[y1:y2, x1:x2]
            roi_mask = mask[y1:y2, x1:x2]
            
            if roi_frame.size == 0: return frame

            # 1. Bilateral Filter vs Gaussian (Speed Optimization)
            # Bilateral is O(N^2) on kernel size. 
            # If CPU mode (implied by slowness), fallback to Gaussian for 5x speedup.
            import os
            if os.getenv("COMPUTE_MODE", "auto") == "cpu":
                 # Gaussian Blur (Much faster, slightly less edge-preserving but acceptable for texture base)
                 blurred = cv2.GaussianBlur(roi_frame, (5, 5), 0)
            else:
                 # Bilateral (High Quality, Expensive)
                 blurred = cv2.bilateralFilter(roi_frame, 9, 75, 75)
            
            # 2. Histogram/Moment Match (Simple L-channel Transfer)
            lab = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            
            kernel = np.ones((20,20), np.uint8)
            dilated = cv2.dilate(roi_mask, kernel, iterations=1)
            surround_mask = cv2.subtract(dilated, roi_mask)
            
            if cv2.countNonZero(surround_mask) > 0:
                mean_s, std_s = cv2.meanStdDev(l, mask=surround_mask)
                mean_m, std_m = cv2.meanStdDev(l, mask=roi_mask)
                
                mean_s = mean_s[0][0]; std_s = std_s[0][0]
                mean_m = mean_m[0][0]; std_m = std_m[0][0]
                
                if std_m > 0:
                    gain = std_s / std_m
                    gain = max(0.5, min(1.5, gain)) # Safety Limiter
                    
                    l_float = l.astype(np.float32)
                    roi_indices = (roi_mask > 0)
                    
                    l_float[roi_indices] = (l_float[roi_indices] - mean_m) * gain + mean_s
                    l = np.clip(l_float, 0, 255).astype(np.uint8)
            
            merged = cv2.merge([l, a, b])
            final_roi = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
            
            # Blend: Keep original outside mask, Processed inside
            frame_out = frame.copy()
            np.copyto(frame_out[y1:y2, x1:x2], final_roi, where=(roi_mask > 0)[:,:,None])
            
            return frame_out
            
        except Exception as e:
            logger.error(f"ContrastHealer Error: {e}")
            return frame

class EdgeIntegrator:
    """
    Sub-Pixel Alpha Feather + Gradient Bleed Integration.
    Optimized for ROI processing.
    """
    @staticmethod
    def integrate(original_frame, inpainted_frame, mask):
        try:
            if cv2.countNonZero(mask) == 0: return inpainted_frame, 0

            # ROI Config
            h_img, w_img = original_frame.shape[:2]
            x, y, w, h = cv2.boundingRect(mask)
            
            # Need larger margin for Bleed (Radius 15) + Context
            pad = 40 
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(w_img, x + w + pad)
            y2 = min(h_img, y + h + pad)
            
            # Extract ROIs
            roi_o = original_frame[y1:y2, x1:x2]
            roi_i = inpainted_frame[y1:y2, x1:x2]
            roi_m = mask[y1:y2, x1:x2]
            
            if roi_o.size == 0: return inpainted_frame, 0

            # --- PART A: CONTEXT SAMPLING ---
            kernel = np.ones((7,7), np.uint8)
            dilated = cv2.dilate(roi_m, kernel, iterations=2)
            surround_mask = cv2.subtract(dilated, roi_m)
            
            if cv2.countNonZero(surround_mask) < 10: return inpainted_frame, 0

            # --- PART B: LAB COLOR HARMONIZATION ---
            lab_o = cv2.cvtColor(roi_o, cv2.COLOR_BGR2LAB).astype(np.float32)
            lab_i = cv2.cvtColor(roi_i, cv2.COLOR_BGR2LAB).astype(np.float32)
            
            l_mean_s, l_std_s = cv2.meanStdDev(lab_o, mask=surround_mask)
            l_mean_i, l_std_i = cv2.meanStdDev(lab_i, mask=roi_m)
            
            mask_bool = roi_m > 0
            
            for k in range(3):
                mi = l_mean_i[k][0]; si = l_std_i[k][0]
                ms = l_mean_s[k][0]; ss = l_std_s[k][0]
                
                if si > 1e-5:
                    scaling = ss / si
                    scaling = max(0.8, min(1.2, scaling))
                    lab_i[:,:,k][mask_bool] = (lab_i[:,:,k][mask_bool] - mi) * scaling + ms
                else:
                    lab_i[:,:,k][mask_bool] += (ms - mi)

            # --- PART C: ADAPTIVE SEAM DISSOLVE ---
            dist_map = cv2.distanceTransform(roi_m, cv2.DIST_L2, 5)
            blend_width = 8.0
            alpha_map = np.clip(dist_map / blend_width, 0.0, 1.0)
            
            # Gradient Bleed (Projected Surround)
            # Use original frame ROI for source
            bleed_radius = 5 # Reduced for NS CPU Speed
            bleed_layer = cv2.inpaint(roi_o, roi_m, bleed_radius, cv2.INPAINT_NS)
            bleed_lab = cv2.cvtColor(bleed_layer, cv2.COLOR_BGR2LAB).astype(np.float32)

            # Composite
            lab_final = lab_i.copy()
            alpha_3c = np.dstack([alpha_map]*3)
            
            # Blend logic: Inside mask, transition from Bleed (edge) to Inpaint (center)
            # Alpha 1.0 = Center (Deep), Alpha 0.0 = Edge
            # Formula: Final = Inpaint*Alpha + Bleed*(1-Alpha)
            lab_final[mask_bool] = lab_i[mask_bool] * alpha_3c[mask_bool] + bleed_lab[mask_bool] * (1.0 - alpha_3c[mask_bool])
            
            # --- PART D: NOISE RESYNTHESIS ---
            if l_std_s[0][0] > 1.0:
                 noise_sigma = l_std_s[0][0] * 0.7
                 noise = np.random.normal(0, noise_sigma, lab_final.shape[:2])
                 lab_final[:,:,0][mask_bool] += noise[mask_bool]
            
            lab_final = np.clip(lab_final, 0, 255).astype(np.uint8)
            integrated_roi = cv2.cvtColor(lab_final, cv2.COLOR_LAB2BGR)
            
            # Write back
            frame_out = inpainted_frame.copy()
            np.copyto(frame_out[y1:y2, x1:x2], integrated_roi, where=(roi_m > 0)[:,:,None])
            
            return frame_out, blend_width
            
        except Exception as e:
            logger.error(f"EdgeIntegrator Error: {e}")
            return inpainted_frame, 0

class BilateralTextureMatcher:
    """
    POST-INPAINT QUALITY (RULE 5): Bilateral Texture Matching.
    Matches local noise statistics and removes block artifacts using Bilateral filtering.
    """
    @staticmethod
    def match(frame, mask):
        try:
            if cv2.countNonZero(mask) == 0: return frame
            
            # 1. Extract ROI
            h_img, w_img = frame.shape[:2]
            x, y, w, h = cv2.boundingRect(mask)
            pad = 20
            x1 = max(0, x - pad); y1 = max(0, y - pad)
            x2 = min(w_img, x + w + pad); y2 = min(h_img, y + h + pad)
            
            roi = frame[y1:y2, x1:x2]
            roi_mask = mask[y1:y2, x1:x2]
            
            # 2. Bilateral Filter (Deblock/Smoothing)
            # "Remove all block artifacts"
            # We filter the whole ROI to get a "clean slate" base
            filtered = cv2.bilateralFilter(roi, 9, 75, 75)
            
            # 3. Texture Synthesis (Match Local Noise)
            # Calculate variance in the SURROUND (outside mask)
            kernel = np.ones((5,5), np.uint8)
            dilated = cv2.dilate(roi_mask, kernel, iterations=1)
            surround = cv2.subtract(dilated, roi_mask)
            
            if cv2.countNonZero(surround) > 0:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                mean_s, std_s = cv2.meanStdDev(gray, mask=surround)
                noise_sigma = std_s[0][0]
                
                # --- TEXTURE SYNTHESIS ---
                # Matches local noise
                if noise_sigma > 2.0:
                    noise = np.random.normal(0, noise_sigma, roi.shape).astype(np.int16)
                    filtered_float = filtered.astype(np.float32)
                    noised = filtered_float + noise
                    filtered = np.clip(noised, 0, 255).astype(np.uint8)
            
            # 4. Strict Masked Writeback
            frame_out = frame.copy()
            np.copyto(frame_out[y1:y2, x1:x2], filtered, where=(roi_mask > 0)[:,:,None])
            
            return frame_out
            
        except Exception as e:
            logger.error(f"Bilateral Match Failed: {e}")
            return None # Fail safe -> Abort

class MicroTextureBlender:
    """
    POST-INPAINT FINISHER: Adds micro-texture back to smooth inpainted regions.
    Target: Text removals that look "too smooth" or "smudged".
    """
    @staticmethod
    def apply_texture_blend(video_path, mask_paths=None, output_path=None, frame_override=None, mask_override=None):
        if frame_override is not None and mask_override is not None:
            return BilateralTextureMatcher.match(frame_override, mask_override)
        return MicroTextureBlender._process_video(video_path, mask_paths, output_path)

    @staticmethod
    def _process_video(video_path, mask_paths, output_path):
        try:
            cap = cv2.VideoCapture(video_path)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            
            mask_caps = [cv2.VideoCapture(mp) for mp in mask_paths]
            
            temp_out = output_path.replace(".mp4", "_textured.mp4")
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(temp_out, fourcc, fps, (width, height))
            
            texture_applied_count = 0
            
            while True:
                ret, frame = cap.read()
                if not ret: break
                
                final_mask = np.zeros((height, width), dtype=np.uint8)
                for mc in mask_caps:
                    mret, mframe = mc.read()
                    if mret:
                        if len(mframe.shape) == 3: mframe = cv2.cvtColor(mframe, cv2.COLOR_BGR2GRAY)
                        final_mask = cv2.bitwise_or(final_mask, mframe)
                
                if cv2.countNonZero(final_mask) > 0:
                    frame = BilateralTextureMatcher.match(frame, final_mask)
                    texture_applied_count += 1 
                
                out.write(frame)
                
            cap.release()
            out.release()
            for mc in mask_caps: mc.release()
            
            if texture_applied_count > 0:
                import os
                if os.path.exists(output_path): os.remove(output_path)
                os.rename(temp_out, output_path)
                return True
            else:
                 if os.path.exists(temp_out): os.remove(temp_out)
                 return False
        except Exception as e:
            logger.error(f"MicroTextureBlend Failed: {e}")
            return False
