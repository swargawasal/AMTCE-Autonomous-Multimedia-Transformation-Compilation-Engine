"""
Deep Hybrid Inpainting Engine (LaMa Wrapper)
--------------------------------------------
Provides a bridge to the SimpleLama deep learning model for world-class
texture reconstruction, designed to be mathematically welded with OpenCV
edge-preservation algorithms for the final hybrid pass.
"""

import os
import cv2
import numpy as np
import logging
from PIL import Image

try:
    from simple_lama_inpainting import SimpleLama
    LAMA_AVAILABLE = True
except ImportError:
    LAMA_AVAILABLE = False
    
logger = logging.getLogger("lama_engine")

class LamaEngine:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """Singleton pattern so we only load the 500MB model into memory once."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        self.model = None
        if LAMA_AVAILABLE:
            try:
                # Initialize LaMa model (downloads from HuggingFace on first run)
                logger.info("🧠 [LaMa] Booting up SimpleLama deep inpainting engine...")
                self.model = SimpleLama()
                logger.info("✅ [LaMa] Engine Loaded Successfully. Ready for hallucination.")
            except Exception as e:
                logger.error(f"❌ [LaMa] Failed to load deep model: {e}")
                self.model = None
        else:
            logger.warning("⚠️ [LaMa] simple-lama-inpainting not installed. Hybrid mode will fall back to pure OpenCV.")
            
    def is_ready(self):
        return self.model is not None
        
    def inpaint(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """
        Deep hallucinates missing pixels inside the mask using LaMa context logic.
        image: BGR numpy array (OpenCV format)
        mask: Grayscale numpy array (255 = area to be painted)
        Returns: BGR numpy array (OpenCV format) with filled pixels.
        """
        if not self.is_ready():
            return image
            
        try:
            # simple-lama expects PIL Images (RGB)
            img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            img_pil = Image.fromarray(img_rgb)
            
            # Mask must be L mode (grayscale)
            if len(mask.shape) == 3:
                mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
            mask_pil = Image.fromarray(mask).convert("L")
            
            # 🚀 FLUID HALLUCINATION INFERENCE
            result_pil = self.model(img_pil, mask_pil)
            
            # Convert back to OpenCV BGR
            result_rgb = np.array(result_pil)
            result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)
            
            return result_bgr
            
        except Exception as e:
            logger.error(f"🧨 [LaMa] Inference crashed mid-hallucination: {e}")
            return image
