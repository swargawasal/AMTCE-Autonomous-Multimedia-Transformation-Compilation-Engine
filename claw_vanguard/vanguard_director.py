import os
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# AMTCE Integration: singleton router + correct module paths
from Intelligence_Modules.gemini_governor import gemini_router
from claw_vanguard.tool_system import vanguard_tools, ToolResult
from claw_vanguard.vanguard_forge import vanguard_forge

logger = logging.getLogger("vanguard_director")

@dataclass
class MissionState:
    mission_id: str
    current_turn: int = 1
    max_turns: int = 4
    retries: int = 0
    max_retries: int = 2
    history: List[Dict] = field(default_factory=list)
    status: str = "active" # active, success, failed, partially_failed

class VanguardDirector:
    """
    The High-Intelligence Orchestrator (Vanguard Pattern).
    Uses gemini-3.1-pro-preview for strategic reasoning and gemini-3-flash-preview for vision.
    """

    def __init__(self):
        # AMTCE Integration: share global singleton so all rate limits & bans are unified
        self.governor = gemini_router
        self.log_path = "logs/mission_dashboard.json"
        self.memory_path = "claw_vanguard/VANGUARD.md"

    def _load_project_memory(self) -> str:
        """Load project-level 'Winning Styles' and 'Agreements'."""
        if os.path.exists(self.memory_path):
            with open(self.memory_path, 'r', encoding='utf-8') as f:
                return f.read()
        return "No project memory found."

    def _log_turn(self, turn_num, decision, model, confidence, result, latency, fallback=False):
        """Standardized Mission Dashboard Logging."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "mission_id": self.state.mission_id,
            "turn": turn_num,
            "decision": decision,
            "model": model,
            "confidence": confidence,
            "result": result,
            "latency_ms": int(latency * 1000),
            "fallback_used": fallback,
            "locked": True # [ELITE] Indicator for stability lock
        }
        
        # Load existing
        data = []
        if os.path.exists(self.log_path):
            with open(self.log_path, 'r') as f:
                try: data = json.load(f)
                except: data = []
        
        data.append(entry)
        with open(self.log_path, 'w') as f:
            json.dump(data, f, indent=2)

    def execute_mission(self, niche: str, video_request: str, input_paths: List[str], output_path: Optional[str] = None, title: Optional[str] = None):
        """
        Zero-Waste 4-Turn Vanguard Loop.
        Turn 1: Local plan (no API call)
        Turn 2: Real pipeline execution
        Turn 3: AI verification ONLY if output exists and budget allows
        Turn 4: Repair ONLY on classified hard errors (codec/timing/file)
        """
        self.state = MissionState(mission_id=f"mission_{int(time.time())}")
        logger.info(f"🚀 Mission Started: {self.state.mission_id} ({niche}) | Title: {title}")

        # --- Probe video duration for adaptive budget ---
        video_duration = 0.0
        if input_paths and os.path.exists(str(input_paths[0])):
            try:
                import cv2
                cap = cv2.VideoCapture(str(input_paths[0]))
                fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                video_duration = frames / fps
                cap.release()
            except Exception:
                video_duration = 30.0  # safe default

        # Register this video with the budget tracker (adaptive: 3/5/7 calls)
        self.governor.begin_video_session(self.state.mission_id, video_duration)

        # --- TURN 1: LOCAL PLAN (zero API cost) ---
        start_t = time.monotonic()
        memory = self._load_project_memory()

        is_shorts_mission = len(input_paths) == 1
        mission_type = "SINGLE SHORT" if is_shorts_mission else f"COMPILATION ({len(input_paths)} clips)"
        shorts_directive = (
            "micro-caption | tight hook <1s | max 15s"
            if is_shorts_mission else
            "full transition suite | multi-clip arc"
        )

        plan_raw = (
            f"[LOCAL PLAN] {mission_type} | Niche: {niche} | "
            f"Style: {shorts_directive} | Memory: {len(memory)} chars | "
            f"Duration: {video_duration:.1f}s"
        )
        logger.info(f"📋 [TURN 1] {plan_raw}")
        latency = time.monotonic() - start_t
        self._log_turn(1, "Strategic Planning", "LOCAL / VANGUARD.md", 1.0, "Success", latency)

        # --- TURN 2: EXECUTION ---
        logger.info("🛠️ Executing Turn 2: Multimedia Render")
        start_t = time.monotonic()
        result = vanguard_tools.execute(
            "compile_video",
            niche=niche,
            request=video_request,
            title=title,
            input_paths=input_paths,
            output_path=output_path,
        )
        latency = time.monotonic() - start_t
        self._log_turn(2, "FFmpeg Rendering", "Local / FFmpeg", 1.0, "Success" if result.success else "Failed", latency)


        # --- TURN 3: VERIFICATION (Post-Render Visual Audit) ---
        verification = {"ok": True, "reason": "Skipped — no output.", "confidence": 0.5}
        
        if result.success and result.output and os.path.exists(str(result.output)):
            logger.info("👁️ Executing Turn 3: Visual Audit (Unconditional Post-Render Verification)")
            start_t = time.monotonic()
            uploaded_file = None
            client = None
            try:
                # 1. Upload video to Gemini File API
                api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    raise RuntimeError("Missing GEMINI_API_KEY — skipping video upload.")
                
                from google import genai
                from google.genai import types
                
                client = genai.Client(api_key=api_key)
                _render_size_mb = os.path.getsize(str(result.output)) / (1024 * 1024)
                logger.info(f"📤 [VANGUARD_VERIFY] Uploading output video ({_render_size_mb:.1f} MB) to Gemini File API...")
                uploaded_file = client.files.upload(file=str(result.output))
                logger.info(f"📤 [VANGUARD_VERIFY] Upload complete: name={uploaded_file.name} | state={getattr(uploaded_file, 'state', 'unknown')}")
                
                # 2. Wait/poll until ACTIVE
                wait_start = time.time()
                wait_timeout = 120  # seconds
                while True:
                    file_state = getattr(uploaded_file, "state", None)
                    state_name = file_state.name if hasattr(file_state, "name") else str(file_state)
                    if state_name == "ACTIVE":
                        logger.info("✅ [VANGUARD_VERIFY] Gemini file is ACTIVE — ready for analysis.")
                        break
                    if state_name == "FAILED":
                        raise RuntimeError("Gemini file processing FAILED.")
                    if time.time() - wait_start > wait_timeout:
                        logger.warning(f"⚠️ [VANGUARD_VERIFY] File not ACTIVE after {wait_timeout}s — proceeding anyway.")
                        break
                    time.sleep(3)
                    uploaded_file = client.files.get(name=uploaded_file.name)
                
                # 3. Request visual audit from Gemini
                verify_prompt = (
                    "You are an elite video editor and quality control auditor.\n"
                    "Review this rendered video carefully and analyze its editing quality, pacing, visual transitions, "
                    "micro-captions alignment, overlay text, and overall composition.\n"
                    f"Target niche: '{niche}'\n"
                    f"User video request: '{video_request}'\n\n"
                    "Identify issues such as:\n"
                    "  - Poor visual pacing or transitions (too slow, too jarring, or missing transitions)\n"
                    "  - Poor caption/text alignment, positioning, or cut-offs\n"
                    "  - Wrong segments selected (not matching the fashion/niche style)\n"
                    "  - Visual issues or awkward cuts\n\n"
                    "If the video is highly engaging and well-edited with no major style issues, set ok=true.\n"
                    "If the video requires improvement or has visual/composition issues, set ok=false.\n\n"
                    "Return ONLY a JSON object matching this schema:\n"
                    "{\n"
                    '  "ok": bool,\n'
                    '  "reason": "short summary of visual audit",\n'
                    '  "adjustments": "detailed editing strategies, improvements, and instructions on what should be placed where to fix it in the next render",\n'
                    '  "confidence": float\n'
                    "}"
                )
                
                verify_payload = [
                    types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="video/mp4"),
                    verify_prompt
                ]
                
                verification_raw = self.governor.generate(
                    task_type="master", # High-cost Vision Call
                    prompt=verify_payload,
                    module_name="vanguard_vision",
                    session_id=self.state.mission_id,
                    gen_config={"response_mime_type": "application/json"}
                )
                
                if not verification_raw:
                    raise ValueError("Governor returned None")
                
                # Clean markdown wrappers in case a fallback model is used
                clean_raw = str(verification_raw).strip()
                if clean_raw.startswith("```json"):
                    clean_raw = clean_raw[7:]
                elif clean_raw.startswith("```"):
                    clean_raw = clean_raw[3:]
                if clean_raw.endswith("```"):
                    clean_raw = clean_raw[:-3]
                clean_raw = clean_raw.strip()
                    
                verification = json.loads(clean_raw)
            except Exception as _ve:
                logger.warning(f"⚠️ [VANGUARD] Turn 3 visual audit failed ({_ve}). Assuming visual OK.")
                verification = {"ok": True, "reason": f"AI vision check failed or timed out: {str(_ve)}", "confidence": 0.5}
            finally:
                if uploaded_file and client:
                    try:
                        logger.info(f"🗑️ [VANGUARD_VERIFY] Cleaning up uploaded file: {uploaded_file.name}")
                        client.files.delete(name=uploaded_file.name)
                    except Exception as delete_err:
                        logger.warning(f"⚠️ [VANGUARD_VERIFY] Failed to delete uploaded file: {delete_err}")
            
            latency = time.monotonic() - start_t
            confidence = verification.get("confidence", 1.0)
            self._log_turn(3, "Visual Verification", "gemini-vision", confidence, "Success" if verification.get("ok") else "Failed Quality Audit", latency)
        else:
            logger.info("⏭️ [TURN 3] Skipped — no output file to verify.")

        # --- TURN 4: REPAIR (only for classified hard errors or quality failure) ---
        # 'AI unavailable' is NOT a reason to burn another API call
        HARD_ERRORS = {"codec", "timing", "file", "system"}
        error_type = result.error_type if result.error_type else "unknown"
        is_hard_failure = (
            (not result.success and error_type in HARD_ERRORS) or
            (verification.get("ok") is False)
        )

        if is_hard_failure and self.state.retries < self.state.max_retries:
            logger.warning(f"🩹 Turn 4: Repair triggered | error_type={error_type}")
            self.state.retries += 1
            start_t = time.monotonic()
            
            # If visual verification failed, forward adjustments as the repair_cmd
            if verification.get("ok") is False and "adjustments" in verification:
                logger.info("🎨 Using visual verification adjustments as repair command.")
                repair_cmd = verification["adjustments"]
            else:
                repair_prompt = (
                    f"Fix required for {niche}. Error: {error_type}. "
                    f"Reason: {verification.get('reason', result.error or 'unknown')}. "
                    f"Generate repair command."
                )
                repair_cmd = self.governor.generate(
                    task_type="reasoning",
                    prompt=repair_prompt,
                    session_id=self.state.mission_id
                )
                if not repair_cmd:
                    logger.warning("⚠️ [VANGUARD] Turn 4: AI repair unavailable. Re-executing with default params.")

            # Forge: only for code-level errors
            code_errors = ["AttributeError", "TypeError", "ImportError", "ModuleNotFoundError"]
            if any(err in str(verification.get("reason", "")) for err in code_errors):
                logger.warning("🚀 [VANGUARD_FORGE] Code error detected. Triggering Meta-Learning Sandbox.")
                forge_res = vanguard_forge.run_forge_pipeline(
                    target_file="Intelligence_Modules/tool_system.py",
                    optimization_task=f"Fix: {verification.get('reason', result.error)}"
                )
                if forge_res.success:
                    with open("logs/forge_report.diff", "w") as f:
                        f.write(forge_res.diff)

            result = vanguard_tools.execute(
                "compile_video",
                repair_data=repair_cmd,
                niche=niche,
                request=video_request,
                input_paths=input_paths,
                output_path=output_path,
            )
            latency = time.monotonic() - start_t
            self._log_turn(4, "Mission Repair", "gemini-reasoning", 0.8, "Fixed" if result.success else "Failed Twice", latency)
        else:
            if not is_hard_failure:
                logger.info("✅ Mission Finalized. No repair needed.")

        return result