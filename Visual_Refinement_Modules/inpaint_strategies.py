"""
Inpaint Strategies Module
-------------------------
Helper strategies for "Smart Second-Attempt" watermark removal.
Used when the primary aggressive removal fails on low-res inputs.
STRICT AUDIT COMPLIANT: Mask Safety Floors, Factor-Driven Logic.
"""

import cv2
import numpy as np
import logging
import os

logger = logging.getLogger("inpaint_strategies")

class InpaintStrategy:
    """
    Defines methods to modify masks or inpainting parameters
    for gentler, less destructive removal.
    """

    @staticmethod
    def shrink_mask(mask_path: str, output_path: str, factor: float = 0.85):
        """
        Strategy A: Erosion/Shrink.
        Reduces the mask size to minimize the "blur blob" effect.
        """
        try:
            if not os.path.exists(mask_path): return False

            if mask_path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                frame = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
                if frame is None: return False
                height, width = frame.shape[:2]
                fps = 30.0
                frame_source = "static"
            else:
                cap = cv2.VideoCapture(mask_path)
                if not cap.isOpened(): return False
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_source = "video"
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(output_path, fourcc, fps, (width, height), isColor=False)
            
            if not out.isOpened():
                if frame_source == "video": cap.release()
                return False

            base_dim = min(width, height)
            
            # 1px erosion removes 2px width/height.
            k_val = max(1, int((1.0 - factor) * 15)) 
            kernel = np.ones((k_val, k_val), np.uint8)

            frame_idx = 0
            aborted = False

            if frame_source == "static":
                gray = frame

                # ERODE
                eroded = cv2.erode(gray, kernel, iterations=1)
                
                orig_mass = np.sum(gray > 0)
                new_mass = np.sum(eroded > 0)
                if orig_mass > 0:
                    loss_ratio = 1.0 - (new_mass / orig_mass)
                    if loss_ratio > 0.40:
                         logger.warning(f"⚠️ Mask Shrink Safety Trigger: Erosion would remove {loss_ratio:.1%} of mask. Aborting.")
                         aborted = True
                    if new_mass == 0:
                         aborted = True
                
                if not aborted:
                    cv2.imwrite(output_path, eroded)
            else:
                while True:
                    ret, frame = cap.read()
                    if not ret: break
                    
                    if len(frame.shape) == 3:
                         gray = frame[:, :, 0]
                    else:
                         gray = frame
    
                    # ERODE
                    eroded = cv2.erode(gray, kernel, iterations=1)
                    
                    if frame_idx == 0:
                        orig_mass = np.sum(gray > 0)
                        new_mass = np.sum(eroded > 0)
                        if orig_mass > 0:
                            loss_ratio = 1.0 - (new_mass / orig_mass)
                            if loss_ratio > 0.40:
                                 logger.warning(f"⚠️ Mask Shrink Safety Trigger: Erosion would remove {loss_ratio:.1%} of mask. Aborting.")
                                 aborted = True
                                 break
                            if new_mass == 0:
                                 aborted = True
                                 break
    
                    out.write(eroded)
                    frame_idx += 1
                    
                cap.release()
                
            out.release()
            
            if aborted:
                if os.path.exists(output_path): os.remove(output_path)
                return False

            return True
            
        except Exception as e:
            logger.error(f"Strategy Shrink Failed: {e}")
            return False

    @staticmethod
    def get_reduced_radius(original_radius: int = 3, factor: float = 0.85) -> int:
        """
        Strategy B: Reduced Radius.
        Calculates a safe radius for second-attempt inpainting.
        """
        target = int(original_radius * factor)
        target = max(3, target)
        min_allowed = int(original_radius * 0.5)
        target = max(min_allowed, target)
        target = max(3, target)
        return target
