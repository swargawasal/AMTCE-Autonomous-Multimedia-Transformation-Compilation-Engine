import os
import shutil
import time
import json
import logging
import subprocess
import contextlib
from typing import Dict, Any, Tuple, Optional
from claw_vanguard.vanguard_forge import vanguard_forge
from claw_vanguard.tool_system import vanguard_tools

# Setup Logger
logger = logging.getLogger("SonOfAntonCodeOptimizer")
logger.setLevel(logging.INFO)

@contextlib.contextmanager
def temp_swap(original_path: str, swap_path: str):
    """
    Temporarily swaps original_path with swap_path. 
    Restores original_path upon exiting the block, even if an exception occurs.
    """
    backup_path = original_path + ".tmp_opt_backup"
    if not os.path.exists(original_path):
        raise FileNotFoundError(f"Original file {original_path} not found.")
        
    shutil.copy(original_path, backup_path)
    try:
        shutil.copy(swap_path, original_path)
        yield
    finally:
        if os.path.exists(backup_path):
            shutil.copy(backup_path, original_path)
            os.remove(backup_path)

def get_visual_critique(video_path: str, niche: str, request: str) -> Dict[str, Any]:
    """Uploads video to Gemini File API and returns structured visual critique."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("Missing GEMINI_API_KEY — skipping vision critique.")
        return {"ok": False, "reason": "Missing Gemini API Key for vision critique."}
        
    from google import genai
    from google.genai import types
    
    client = genai.Client(api_key=api_key)
    logger.info(f"📤 [CODE_OPTIMIZER] Uploading {os.path.basename(video_path)} to Gemini File API...")
    uploaded_file = client.files.upload(file=video_path)
    
    try:
        # Poll file status until ACTIVE
        wait_start = time.time()
        wait_timeout = 120
        while True:
            file_state = getattr(uploaded_file, "state", None)
            state_name = file_state.name if hasattr(file_state, "name") else str(file_state)
            if state_name == "ACTIVE":
                logger.info("✅ [CODE_OPTIMIZER] Gemini file is active.")
                break
            if state_name == "FAILED":
                raise RuntimeError("Gemini file processing failed.")
            if time.time() - wait_start > wait_timeout:
                logger.warning("⏳ [CODE_OPTIMIZER] File processing timed out.")
                break
            time.sleep(3)
            uploaded_file = client.files.get(name=uploaded_file.name)
            
        verify_prompt = (
            "You are an elite video editor and quality control auditor.\n"
            "Review this rendered video carefully and analyze its editing quality, pacing, visual transitions, "
            "micro-captions alignment, overlay text, and overall composition.\n"
            f"Target niche: '{niche}'\n"
            f"User video request: '{request}'\n\n"
            "Identify issues such as poor transitions, caption alignment, bad segment selection or visual cuts.\n"
            "Return ONLY a JSON object matching this schema:\n"
            "{\n"
            '  "ok": bool,\n'
            '  "reason": "short summary of visual quality",\n'
            '  "adjustments": "detailed editing adjustments and improvement actions for the next render",\n'
            '  "confidence": float\n'
            "}"
        )
        
        logger.info("👁️ [CODE_OPTIMIZER] Querying Gemini Vision model...")
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="video/mp4"), verify_prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        
        clean_text = response.text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()
        
        return json.loads(clean_text)
    except Exception as e:
        logger.error(f"❌ [CODE_OPTIMIZER] Vision critique failed: {e}")
        return {"ok": False, "reason": f"Vision critique failed: {str(e)}"}
    finally:
        try:
            logger.info(f"🗑️ [CODE_OPTIMIZER] Cleaning up file: {uploaded_file.name}")
            client.files.delete(name=uploaded_file.name)
        except Exception as de:
            logger.warning(f"Failed to delete uploaded file: {de}")

def judge_improvement(baseline_critique: Dict[str, Any], optimized_critique: Dict[str, Any]) -> Tuple[bool, str]:
    """Uses Mistral/Groq with Gemini fallback to judge if optimized critique is an improvement."""
    prompt = f"""
    Compare the following two visual critiques of a compiled video:
    
    BASELINE CRITIQUE:
    {json.dumps(baseline_critique, indent=2)}
    
    OPTIMIZED CRITIQUE:
    {json.dumps(optimized_critique, indent=2)}
    
    Determine if the optimized critique represents a clear improvement (e.g. resolving visual bugs, transitions, 
    caption positioning, or successfully compiling a video that previously failed to build).
    
    Return ONLY a JSON object matching this schema:
    {{
      "success": bool,
      "reason": "short explanation of the comparison decision"
    }}
    """
    
    system_prompt = "You are an elite video editing director. Analyze visual improvements and output ONLY JSON."
    
    # Try Mistral
    try:
        from connectors.mistral import call_mistral
        res = call_mistral(prompt, system_prompt)
        if "error" not in res:
            return _parse_judge_json(res["answer"])
    except Exception:
        pass
        
    # Try Groq
    try:
        from connectors.groq_connector import call_groq
        res = call_groq(prompt, system_prompt)
        if "error" not in res:
            return _parse_judge_json(res["answer"])
    except Exception:
        pass
        
    # Fallback to Gemini
    try:
        from connectors.gemini import call_gemini
        res = call_gemini(prompt, system_prompt, task_type="ali_reasoning")
        if "error" not in res:
            return _parse_judge_json(res["answer"])
    except Exception as e:
        logger.error(f"Judge routing failed: {e}")
        return False, f"Judge failed to evaluate: {str(e)}"

def _parse_judge_json(text: str) -> Tuple[bool, str]:
    clean_text = text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    elif clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    data = json.loads(clean_text)
    return data.get("success", False), data.get("reason", "")

class SonOfAntonCodeOptimizer:
    """Orchestrates sandboxed self-repair optimization loops."""
    
    def __init__(self):
        self.temp_dir = "temp/forge"
        os.makedirs(self.temp_dir, exist_ok=True)
        
    def _run_pytest(self, original_path: str, test_file: Optional[str] = None) -> Tuple[bool, str]:
        """Runs pytest on target or auto-resolved test file."""
        if not test_file:
            module_name = os.path.basename(original_path).replace(".py", "")
            test_file = f"tests/test_{module_name}.py"
            
        if not os.path.exists(test_file):
            test_file = "tests/test_vanguard_core.py"
            
        cmd = ["pytest", test_file, "--maxfail=1"]
        logger.info(f"🧪 [CODE_OPTIMIZER] Running pytest on {test_file}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        return (result.returncode == 0), result.stdout + result.stderr

    def run_optimization_loop(self, target_file: str, optimization_task: str, test_file: Optional[str] = None) -> Dict[str, Any]:
        """Executes the gated 4-loop code optimization pipeline."""
        logger.info(f"🚀 Initiating Code Optimization Loop on {target_file}")
        
        # 1. Protected brain files check
        base_name = os.path.basename(target_file)
        if base_name in vanguard_forge.PROTECTED_FILES:
            return {"success": False, "message": f"❌ Rejected: {base_name} is a protected core file."}
            
        if not os.path.exists(target_file):
            return {"success": False, "message": f"❌ Target file {target_file} does not exist."}
            
        # 2. Compile baseline video using original code
        logger.info("🎥 Running baseline video compilation...")
        baseline_video = os.path.join(self.temp_dir, "baseline.mp4")
        if os.path.exists(baseline_video):
            try: os.remove(baseline_video)
            except Exception: pass
            
        comp_res = vanguard_tools.execute(
            "compile_video",
            niche="Fashion",
            request="Process short fashion video of Amy showcasing style",
            input_paths=["downloads/Amy.mp4"],
            output_path=baseline_video
        )
        
        baseline_critique = None
        if comp_res.success and os.path.exists(baseline_video):
            logger.info("✅ Baseline compilation succeeded. Requesting critique...")
            baseline_critique = get_visual_critique(baseline_video, "Fashion", "Process short fashion video of Amy showcasing style")
            logger.info(f"Baseline Critique Result: {baseline_critique}")
        else:
            logger.warning(f"⚠️ Baseline compilation failed: {comp_res.error or 'unknown'}")
            baseline_critique = {"ok": False, "reason": f"Baseline compilation failed: {comp_res.error}"}
            
        # 3. Gated Loop (Max 4 iterations)
        max_iterations = 4
        current_adjustments = baseline_critique.get("adjustments") or baseline_critique.get("reason")
        
        with open(target_file, "r", encoding="utf-8") as f:
            current_code = f.read()
            
        secondary_path = os.path.join(self.temp_dir, f"{base_name}_secondary.py")
        
        for iteration in range(1, max_iterations + 1):
            logger.info(f"\n🔄 Iteration {iteration}/{max_iterations}...")
            
            # --- CODE GEN (Gemini/Groq Routing) ---
            prompt = f"""
            ACT AS: Senior AI Security & Performance Architect.
            TASK: Optimize/rewrite the target python file.
            
            GOAL: {optimization_task}
            TARGET FILE PATH: {target_file}
            
            CURRENT CODE:
            {current_code}
            
            PREVIOUS CRITIQUE / COMPILATION FEEDBACK:
            {current_adjustments}
            
            RULES:
            1. ONLY modify the logic specifically requested in the goal/feedback.
            2. DO NOT delete existing imports unless they are replaced or unused.
            3. DO NOT remove existing try/except blocks or key error handling logic.
            4. Keep the output SURGICAL and valid Python.
            5. Return ONLY the full updated python code inside a markdown code block (using ```python ... ```). Do not add any conversational text.
            """
            
            logger.info("🧠 Generating code optimization via Gemini...")
            # Route to Gemini for code generation
            try:
                from connectors.gemini import call_gemini
                res = call_gemini(prompt, system_prompt="You are an expert python coder.", task_type="ali_reasoning")
                ans_text = res.get("answer", "")
            except Exception as ge:
                logger.error(f"Gemini code gen failed: {ge}")
                return {"success": False, "message": f"Gemini call failed during iteration {iteration}."}
                
            # Extract code from markdown block
            if "```python" in ans_text:
                optimized_code = ans_text.split("```python")[1].split("```")[0].strip()
            elif "```" in ans_text:
                optimized_code = ans_text.split("```")[1].split("```")[0].strip()
            else:
                optimized_code = ans_text.strip()
                
            # Write to secondary sandbox file
            with open(secondary_path, "w", encoding="utf-8") as f:
                f.write(optimized_code)
                
            # --- pytest COMPILATION CHECK ---
            with temp_swap(target_file, secondary_path):
                tests_pass, test_msg = self._run_pytest(target_file, test_file)
                
            if not tests_pass:
                logger.warning(f"❌ Pytest failed in iteration {iteration}.")
                current_adjustments = f"Pytest failed with error:\n{test_msg}"
                continue
                
            logger.info("✅ Pytest passed. Running test render...")
            
            # --- TRIAL RENDER ---
            trial_video = os.path.join(self.temp_dir, f"trial_iter_{iteration}.mp4")
            if os.path.exists(trial_video):
                try: os.remove(trial_video)
                except Exception: pass
                
            with temp_swap(target_file, secondary_path):
                render_res = vanguard_tools.execute(
                    "compile_video",
                    niche="Fashion",
                    request="Process short fashion video of Amy showcasing style",
                    input_paths=["downloads/Amy.mp4"],
                    output_path=trial_video
                )
                
            if not render_res.success or not os.path.exists(trial_video):
                logger.warning(f"❌ Trial compile failed in iteration {iteration}.")
                current_adjustments = f"Compilation failed with error:\n{render_res.error or 'file not found'}"
                continue
                
            logger.info("✅ Trial compile succeeded. Auditing visual quality...")
            
            # --- VISUAL CRITIQUE ---
            optimized_critique = get_visual_critique(trial_video, "Fashion", "Process short fashion video of Amy showcasing style")
            logger.info(f"Optimized Critique Result: {optimized_critique}")
            
            # --- JUDGE ROUTING DECISION ---
            logger.info("⚖️ Judging improvements...")
            improved, judge_reason = judge_improvement(baseline_critique, optimized_critique)
            logger.info(f"Judge result: improved={improved} | reason={judge_reason}")
            
            if improved and optimized_critique.get("ok") is True:
                # Success! Promote to primary atomically
                logger.info("🏆 Optimization meets quality standards. Promoting to primary...")
                backup_snap = vanguard_forge.promote_to_primary(target_file, optimized_code)
                
                # Cleanup temp files
                for f_path in [baseline_video, trial_video, secondary_path]:
                    if os.path.exists(f_path):
                        try: os.remove(f_path)
                        except Exception: pass
                        
                return {
                    "success": True,
                    "message": "✅ PROMOTED: Sandbox validation and visual quality criteria fully met.",
                    "iterations": iteration,
                    "backup_snapshot": backup_snap,
                    "judge_reason": judge_reason,
                    "visual_critique": optimized_critique.get("reason", "")
                }
            else:
                logger.warning(f"⚠️ Optimized code rejected by judge or critique ok=False. reason={judge_reason}")
                current_adjustments = optimized_critique.get("adjustments") or optimized_critique.get("reason")
                # Update current_code to the iteration's result to build upon it next iteration
                current_code = optimized_code
                
        # Exhausted all iterations without success
        logger.error(f"❌ Failed: Gated loop ceiling ({max_iterations} iterations) exhausted.")
        return {
            "success": False,
            "message": f"❌ FAILED: Gated loop ceiling ({max_iterations} iterations) exhausted without satisfying quality criteria.",
            "final_adjustments": current_adjustments
        }

# Singleton instance
code_optimizer = SonOfAntonCodeOptimizer()
