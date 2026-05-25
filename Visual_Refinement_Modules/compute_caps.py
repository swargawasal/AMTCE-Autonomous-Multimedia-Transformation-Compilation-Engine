"""
Compute Capability Authority
----------------------------
Standardizes hardware detection across the bot.
Authorized Source of Truth for:
- CUDA Availability
- VRAM Capacity
- CPU-Fast Mode Enforcement

Rule: Calculated ONCE at startup. Reused everywhere.
"""

import os
import sys
import logging
import subprocess
import shutil

logger = logging.getLogger("compute_caps")

class ComputeCaps:
    _instance = None
    _caps = {
        "has_cuda": False,
        "vram_gb": 0.0,
        "gpu_fast": False,
        "cpu_only": True,
        "allow_ai_enhance": False
    }
    _initialized = False

    @classmethod
    def get(cls):
        if not cls._initialized:
            cls._detect()
        return cls._caps

    @classmethod
    def _detect(cls):
        """
        Detects hardware capabilities WITHOUT importing torch/tensorflow if possible initially.
        Uses nvidia-smi as primary, falls back to torch if absolutely needed but wary of import cost.
        Actually, to be 100% accurate for 'torch' usage, we might need torch, BUT 
        we want to avoid importing it if we are in CPU_FAST mode.
        """
        config = os.environ
        
        # 1. User Override via new .env standard (CPU_MODE / GPU_MODE)
        cpu_mode = config.get("CPU_MODE", "on").lower() == "on"
        gpu_mode = config.get("GPU_MODE", "auto").lower()
        
        # 1.1 CPU Only Force
        if not cpu_mode and gpu_mode == "off":
             logger.warning("⚠️ ComputeCaps: BOTH CPU and GPU modes are OFF! Defaulting to CPU for safety.")
             cls._set_cpu_only()
             return

        if gpu_mode == "off" or config.get("COMPUTE_MODE", "auto").lower() == "cpu":
            logger.info("⚙️ ComputeCaps: GPU Mode Disabled via Env.")
            cls._set_cpu_only()
            return
            
        # 2. Nvidia-SMI Check (Lightweight)
        has_nvidia = False
        vram = 0.0
        try:
            # Check for nvidia-smi
            smi = shutil.which("nvidia-smi")
            if smi:
                # Query memory
                cmd = [smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"]
                result = subprocess.check_output(cmd, encoding='utf-8')
                # Parse first GPU
                lines = result.strip().split('\n')
                if lines:
                    vram_mb = float(lines[0])
                    vram = vram_mb / 1024.0
                    has_nvidia = True
        except Exception as e:
            logger.debug(f"ComputeCaps: nvidia-smi check failed: {e}")
            has_nvidia = False
            
        # 3. Decision
        if has_nvidia:
            logger.info(f"⚙️ ComputeCaps: GPU Detected (VRAM={vram:.1f}GB).")
            cls._caps["vram_gb"] = vram
            cls._caps["has_cuda"] = False # Default until torch confirmed
            
            # --- VRAM Gating ---
            if vram < 6.0:
                 logger.info(f"   └─ Status: LOW_VRAM ({vram:.1f}GB < 6GB). Skipping Heavy AI (Avoiding Torch).")
                 cls._caps["allow_ai_enhance"] = False
                 cls._caps["gpu_fast"] = False
                 cls._caps["cpu_only"] = True
                 cls._initialized = True
                 return

            # 4. Verify Torch for Heavy AI (>= 6GB)
            logger.info("   └─ Status: HIGH_VRAM. Verifying Heavy Dependencies (Torch, Kokoro)...")
            try:
                import torch
            except ImportError:
                logger.warning("   └─ Warning: Heavy dependencies (torch) not found. Auto-installing... (this may take a while)")
                try:
                    logger.info("      └─ Installing PyTorch with CUDA 12.1...")
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "torch", "torchvision", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu121"])
                    logger.info("      └─ Installing Kokoro and upscale dependencies...")
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "kokoro", "soundfile", "diffusers", "transformers", "accelerate"])
                except Exception as e:
                    logger.error(f"   └─ Error: Auto-install failed: {e}. Falling back to CPU.")
                    cls._set_cpu_only()
                    cls._caps["vram_gb"] = vram
                    cls._initialized = True
                    return

            try:
                import torch
                if torch.cuda.is_available():
                     cls._caps["has_cuda"] = True
                     cls._caps["cpu_only"] = False
                     cls._caps["gpu_fast"] = True
                     cls._caps["allow_ai_enhance"] = True
                     logger.info("   └─ Status: PRO (Heavy AI / GPU Path Enabled)")
                else:
                    logger.warning("   └─ Warning: Torch found but CUDA is unresponsive. Falling back to CPU.")
                    cls._set_cpu_only()
                    cls._caps["vram_gb"] = vram # Preserve info
                    
            except ImportError:
                logger.warning("   └─ Warning: Heavy dependencies (torch) still not found. Falling back to CPU.")
                cls._set_cpu_only()
                cls._caps["vram_gb"] = vram # Preserve info

                
        else:
             logger.info("⚙️ ComputeCaps: No GPU detected. Defaulting to CPU.")
             cls._set_cpu_only()

        cls._initialized = True

    @classmethod
    def _set_cpu_only(cls):
        cls._caps["has_cuda"] = False
        cls._caps["vram_gb"] = 0.0
        cls._caps["gpu_fast"] = False
        cls._caps["cpu_only"] = True
        cls._caps["allow_ai_enhance"] = False # CPU too slow for heavy AI
