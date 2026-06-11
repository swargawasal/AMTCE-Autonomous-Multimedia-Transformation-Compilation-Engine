import re
import logging
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger("vanguard_tools")

@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: Any
    error: Optional[str] = None
    error_type: Optional[str] = None # codec, timing, file, logic

class ErrorClassifier:
    """Heuristic-based classifier for common multimedia errors (Vanguard Pattern)."""
    
    PATTERNS = {
        "codec": [
            r"unknown encoder",
            r"codec not found",
            r"unsupported codec",
            r"failed to open codec"
        ],
        "timing": [
            r"invalid duration",
            r"out of range",
            r"too short",
            r"no stream found",
            r"pts has no value"
        ],
        "file": [
            r"no such file",
            r"permission denied",
            r"failed to open",
            r"zero bytes"
        ],
        "system": [
            r"not recognized",
            r"command not found",
            r"out of memory"
        ]
    }

    @classmethod
    def classify(cls, error_msg: str) -> str:
        error_msg = error_msg.lower()
        for category, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_msg):
                    return category
        return "unknown"

class ToolRegistry:
    """Unified Gated Tool Registry inspired by Claude Code."""
    
    def __init__(self):
        self.tools = {}

    def register(self, name: str, responsibility: str, func: Callable):
        self.tools[name] = {
            "name": name,
            "responsibility": responsibility,
            "func": func
        }
        logger.info(f"Registered Vanguard Tool: {name} ({responsibility})")

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        if tool_name not in self.tools:
            return ToolResult(tool_name, False, None, f"Tool {tool_name} not found")
        
        try:
            logger.info(f"🛠️ Executing {tool_name} with args: {kwargs}")
            result = self.tools[tool_name]["func"](**kwargs)
            return ToolResult(tool_name, True, result)
        except Exception as e:
            error_msg = str(e)
            error_type = ErrorClassifier.classify(error_msg)
            logger.error(f"❌ Tool {tool_name} failed ({error_type}): {error_msg}")
            return ToolResult(tool_name, False, None, error_msg, error_type)

# --- Singleton Registry Instance ---
vanguard_tools = ToolRegistry()

# --- Tool Wrappers for AMTCE Modules ---

def tool_compile_video(**kwargs):
    """
    Responsibility: Compiler_Modules.orchestrator
    Duty: Executes a high-quality video compilation using agentic parameters.
    """
    from Compiler_Modules.orchestrator import compile_video
    import uuid
    import time
    
    # 1. Extract Agentic Parameters
    input_paths = kwargs.get("input_paths", [])
    niche = kwargs.get("niche", "generic")
    request = kwargs.get("request", "Mission Compilation")
    repair_data = kwargs.get("repair_data")
    
    # [FIX] Use explicit title if provided, otherwise extract from request or fallback
    raw_title = kwargs.get("title")
    if not raw_title:
        # Fallback extraction from legacy 'titled '...' ' pattern
        match = re.search(r"titled '(.*?)'", request)
        raw_title = match.group(1) if match else request[:30]
    
    # Sanitize: ensure no conversational prefixes, niche prefixes, or CLI info survive
    clean_title = str(raw_title)
    clean_title = re.sub(r"(?i)^(?:viral|fashion|entertainment|nsfw|adult|paparazzi|general):\s*", "", clean_title)
    clean_title = re.sub(r"(?i)^(?:cli:\s*)?process\s+(?:short\s+)?titled\s+", "", clean_title)
    clean_title = re.sub(r"(?i)^cli:\s*process\s+", "", clean_title)
    clean_title = re.sub(r"(?i)^retry\s+#\d+:\s*reprocess\s+", "", clean_title)
    clean_title = re.sub(r"(?i)^retry\s+#\d+:\s*", "", clean_title)
    clean_title = re.sub(r"(?i)^reprocess\s+", "", clean_title)
    clean_title = re.sub(r"(?i)^cli\s+mission", "", clean_title)
    clean_title = clean_title.strip(" '\".,-_")

    # 2. Generate Metadata
    mission_id = f"vanguard_{int(time.time())}"
    # Allow caller to specify output_path (e.g. "Processed Shorts/title_001.mp4")
    # so shorts land in the right directory instead of Compilations/
    output_path = kwargs.get("output_path") or f"Compilations/{niche}_{mission_id}.mp4"
    os.makedirs(os.path.dirname(output_path) or "Compilations", exist_ok=True)
    
    # 3. Build Profile Data (Fix hints for the orchestrator)
    profile_data = {
        "niche_category": niche,
        "vanguard_repair_data": repair_data,
        "mission_mode": "agentic"
    }
    
    success, result_data = compile_video(
        uuid_str=mission_id,
        input_path=input_paths,
        output_path=output_path,
        title=clean_title,
        description=request,
        profile_data=profile_data
    )
    
    if success and os.path.exists(output_path):
        return output_path
    elif success:
        logger.warning(f"⚠️ [VANGUARD_TOOLS] compile_video returned success but output_path {output_path} missing.")
        return output_path
    else:
        err = result_data.get("error", "Unknown compilation error") if isinstance(result_data, dict) else "Failed"
        raise RuntimeError(f"Video compilation engine failed: {err}")

def tool_music_scout(**kwargs):
    """
    Responsibility: Audio_Modules.music_manager
    Duty: Finds and prepares high-velocity background audio.
    """
    from Audio_Modules.music_manager import ContinuousMusicManager
    manager = ContinuousMusicManager()
    return manager.get_active_playlist(**kwargs)

def tool_vision_analysis(**kwargs):
    """
    Responsibility: Intelligence_Modules — batched frame sampling for visual verification.
    Duty: 2s interval frame sampling as per VANGUARD Working Agreement 1.
    """
    try:
        from Intelligence_Modules.vision_intelligence import VisionIntelligence
        vi = VisionIntelligence()
        return vi.analyze_video_context(**kwargs)
    except ImportError:
        # Graceful degradation: return minimal structure so Director can continue
        logger.warning("[VANGUARD_TOOLS] VisionIntelligence module not found. Returning safe default.")
        return {"ok": True, "reason": "Vision module unavailable — skipped.", "confidence": 0.5}

# Registration
vanguard_tools.register("compile_video", "Video Compilation & Rendering", tool_compile_video)
vanguard_tools.register("music_scout", "Audio Selection & Lifecycle", tool_music_scout)
vanguard_tools.register("vision_analysis", "Batched visual verification", tool_vision_analysis)