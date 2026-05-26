
import os
import logging
import platform
import time
import json
import shutil

# Setup logger
logger = logging.getLogger("health")

# Try imports
try:
    import psutil
except ImportError:
    psutil = None

# Lazy torch import in check_gpu_health instead
torch = None

# ==================== CONFIGURATION ====================
def get_env_float(key, default):
    try:
        return float(os.getenv(key, default))
    except (ValueError, TypeError):
        return float(default)

CPU_USAGE_WARN_PERCENT = get_env_float("CPU_USAGE_WARN_PERCENT", 85.0)
CPU_TEMP_WARN_C = get_env_float("CPU_TEMP_WARN_C", 85.0)
MIN_RAM_FREE_MB = get_env_float("MIN_RAM_FREE_MB", 600.0)
MIN_VRAM_FREE_MB = get_env_float("MIN_VRAM_FREE_MB", 500.0)
MIN_DISK_FREE_GB = get_env_float("MIN_DISK_FREE_GB", 2.0)
MAX_FFMPEG_PROCESSES = int(os.getenv("MAX_FFMPEG_PROCESSES", "4"))

# ==================== HEALTH CHECKS ====================

def check_cpu_health() -> dict:
    """
    Checks CPU usage and temperature.
    Returns: {"safe": bool, "reason": str}
    """
    if not psutil:
        return {"safe": True, "reason": "psutil_missing_assumed_safe"}
    
    # 1. CPU Usage (Rolling average would be better, but snapshot is okay for gate)
    # We take a small sample if needed, but blocking is bad. 
    # psutil.cpu_percent(interval=None) returns immediate since last call.
    # First call is 0.0.
    cpu_usage = psutil.cpu_percent(interval=None) 
    
    if cpu_usage > CPU_USAGE_WARN_PERCENT:
        return {"safe": False, "reason": f"CPU Load High: {cpu_usage}% > {CPU_USAGE_WARN_PERCENT}%"}

    # 2. CPU Temperature (Linux Only usually)
    if hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    for entry in entries:
                        if entry.current and entry.current > CPU_TEMP_WARN_C:
                            return {"safe": False, "reason": f"CPU Overheating: {entry.current}C > {CPU_TEMP_WARN_C}C"}
        except Exception as e:
            logger.warning(f"Failed to read temperatures: {e}")

    return {"safe": True, "reason": "optimal"}

def check_ram_health() -> dict:
    """
    Checks System RAM.
    Returns: {"safe": bool, "free_mb": float, "reason": str}
    """
    if not psutil:
        return {"safe": True, "free_mb": 0, "reason": "psutil_missing"}
    
    try:
        mem = psutil.virtual_memory()
        free_mb = mem.available / (1024 * 1024)
        
        if free_mb < MIN_RAM_FREE_MB:
            return {"safe": False, "free_mb": free_mb, "reason": f"Low RAM: {int(free_mb)}MB < {MIN_RAM_FREE_MB}MB"}
            
        return {"safe": True, "free_mb": free_mb, "reason": "optimal"}
    except Exception as e:
        logger.error(f"RAM check failed: {e}")
        return {"safe": True, "free_mb": 0, "reason": "check_failed"}

def check_gpu_health() -> dict:
    """
    Checks GPU VRAM (Advisory).
    Returns: {"safe": bool, "free_mb": float, "reason": str}
    """
    try:
        import Upscale_Modules.compute_caps as compute_caps
        caps = compute_caps.ComputeCaps.get()
        
        if not caps["has_cuda"]:
            return {"safe": True, "free_mb": 0, "reason": "no_gpu_detected", "available": False}
        
        # Lazy Import
        global torch
        if torch is None: import torch
        
        if not torch.cuda.is_available():
             return {"safe": True, "free_mb": 0, "reason": "cuda_unavailable", "available": False}
             
        device = torch.device("cuda")
        # torch.cuda.mem_get_info returns (free, total) in bytes
        free_bytes, total_bytes = torch.cuda.mem_get_info(device)
        free_mb = free_bytes / (1024 * 1024)
        
        if free_mb < MIN_VRAM_FREE_MB:
            # We strictly only flag unsafe if we really can't fit a model
            return {"safe": False, "free_mb": free_mb, "reason": f"Low VRAM: {int(free_mb)}MB < {MIN_VRAM_FREE_MB}MB", "available": True}
            
        return {"safe": True, "free_mb": free_mb, "reason": "optimal", "available": True}
    except Exception as e:
        logger.warning(f"GPU check failed: {e}")
        return {"safe": True, "free_mb": 0, "reason": "check_failed", "available": False}

def check_disk_health() -> dict:
    """
    Checks free disk space on the current drive.
    Returns: {"safe": bool, "free_gb": float, "reason": str}
    """
    try:
        # Get disk usage for the project root
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024**3)
        
        if free_gb < MIN_DISK_FREE_GB:
            return {"safe": False, "free_gb": free_gb, "reason": f"Low Disk: {free_gb:.1f}GB < {MIN_DISK_FREE_GB}GB"}
            
        return {"safe": True, "free_gb": free_gb, "reason": "optimal"}
    except Exception as e:
        logger.error(f"Disk check failed: {e}")
        return {"safe": True, "free_gb": 0, "reason": "check_failed"}

def check_process_health() -> dict:
    """
    Checks for runaway processes or resource exhaustion.
    Returns: {"safe": bool, "count": int, "reason": str}
    """
    if not psutil:
        return {"safe": True, "count": 0, "reason": "psutil_missing"}
    
    try:
        # Count ffmpeg processes
        ffmpeg_count = 0
        for proc in psutil.process_iter(['name']):
            try:
                if "ffmpeg" in proc.info['name'].lower():
                    ffmpeg_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        if ffmpeg_count > MAX_FFMPEG_PROCESSES:
            return {"safe": False, "count": ffmpeg_count, "reason": f"Too many FFmpeg processes: {ffmpeg_count} > {MAX_FFMPEG_PROCESSES}"}
            
        return {"safe": True, "count": ffmpeg_count, "reason": "optimal"}
    except Exception as e:
        logger.error(f"Process check failed: {e}")
        return {"safe": True, "count": 0, "reason": "check_failed"}

# ==================== PUBLIC API ====================

# Global Cache for Health Results
_health_cache = {"verdict": None, "last_check": 0}
HEALTH_CACHE_TTL = 5.0 # 5 seconds

def check_health() -> dict:
    """
    Master Health Gate with 5s Caching.
    Returns structured dict with final verdict.
    """
    now = time.time()
    if _health_cache["verdict"] and (now - _health_cache["last_check"] < HEALTH_CACHE_TTL):
        return _health_cache["verdict"]

    cpu = check_cpu_health()
    ram = check_ram_health()
    gpu = check_gpu_health()
    disk = check_disk_health()
    proc = check_process_health()
    
    # Aggregated Safety
    is_safe = cpu["safe"] and ram["safe"] and disk["safe"] and proc["safe"]
    
    # Construct Verdict
    reason = []
    if not cpu["safe"]: reason.append(cpu["reason"])
    if not ram["safe"]: reason.append(ram["reason"])
    if not disk["safe"]: reason.append(disk["reason"])
    if not proc["safe"]: reason.append(proc["reason"])
    if not gpu["safe"]: reason.append(f"GPU Degraded ({gpu['reason']})")
    
    verdict = {
        "safe": is_safe,
        "timestamp": time.time(),
        "platform": platform.system(),
        "cpu_safe": cpu["safe"],
        "ram_safe": ram["safe"],
        "disk_safe": disk["safe"],
        "proc_safe": proc["safe"],
        "gpu_safe": gpu["safe"],
        "gpu_available": gpu.get("available", False),
        "cpu_reason": cpu["reason"],
        "ram_free_mb": ram["free_mb"],
        "disk_free_gb": disk["free_gb"],
        "ffmpeg_count": proc["count"],
        "vram_free_mb": gpu["free_mb"],
        "summary": " | ".join(reason) if reason else "System Healthy ✅"
    }
    
    # Update Cache
    _health_cache["verdict"] = verdict
    _health_cache["last_check"] = now
    
    return verdict

def is_system_safe() -> bool:
    """
    High-level safety check for core loops.
    Returns True if system is within operational bounds.
    """
    return check_health()["safe"]

def print_health_summary():
    """
    Prints a formatted summary of the system health.
    """
    verdict = check_health()
    logger.info(f"🏥 System Health: {verdict.get('summary', 'Unknown')}")
    if not verdict["safe"]:
         logger.warning(f"⚠️  Health Issues: {verdict['summary']}")
    else:
         logger.info(f"Scale Ready: CPU={verdict['cpu_safe']}, RAM={verdict['ram_free_mb']:.0f}MB, Disk={verdict['disk_free_gb']:.1f}GB")

if __name__ == "__main__":
    # Manual Test
    logging.basicConfig(level=logging.INFO)
    status = check_health()
    print(json.dumps(status, indent=2))
    if status["safe"]:
        print("✅ SYSTEM READY")
    else:
        print("⛔ SYSTEM UNSAFE")
