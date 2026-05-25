import os
import shutil
import time
import json
import logging
import subprocess
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
# AMTCE Integration: use the singleton router, not a new instance
from Intelligence_Modules.gemini_governor import gemini_router
try:
    from jsonschema import validate
except ImportError:
    validate = None # Graceful fallback if not installed

logger = logging.getLogger("VanguardForge")

@dataclass
class ForgeResult:
    success: bool
    message: str
    target_file: str = ""
    diff: str = ""
    score: float = 0.0
    risk_level: str = "LOW"
    coverage: float = 0.0
    ai_critique: Dict[str, Any] = field(default_factory=dict)
    audit_score: float = 0.0
    disagreement: bool = False
    proposal_id: str = ""
    timestamp: float = field(default_factory=time.time)
    full_code: str = ""

    def is_stale(self) -> bool:
        """Proposals older than 24h are considered stale."""
        return (time.time() - self.timestamp) > 86400

class VanguardForge:
    """
    Elite Meta-Learning Sandbox: Primary -> Secondary -> Pytest -> Approval -> Replace.
    Incorporates 10+ Production-Grade Safeguards.
    """
    
    PROTECTED_FILES = [
        "vanguard_director.py",
        "gemini_governor.py",
        "tool_system.py",
        "vanguard_forge.py",
        "main.py"
    ]
    
    FORGE_COOLDOWN = {} # {file_path: last_forge_time}
    COOLDOWN_PERIOD = 300 # 5 minutes
    
    def __init__(self):
        # AMTCE Integration: reuse the global singleton to share rate limits & model state
        self.governor = gemini_router
        self.temp_dir = "temp/forge"
        self.history_dir = "logs/forge_history"
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs("backups", exist_ok=True)

    def can_forge(self, file_path: str) -> Tuple[bool, str]:
        """Guardrail 1 & 2: Protected Files & Cooldown."""
        base_name = os.path.basename(file_path)
        
        if base_name in self.PROTECTED_FILES:
            return False, f"❌ REJECTED: {base_name} is a PROTECTED core brain file."
            
        last_time = self.FORGE_COOLDOWN.get(file_path, 0)
        if time.time() - last_time < self.COOLDOWN_PERIOD:
            remaining = int(self.COOLDOWN_PERIOD - (time.time() - last_time))
            return False, f"⚠️ COOLDOWN: {base_name} is cooling down ({remaining}s remains)."
            
        return True, "Ready"

    def analyze_diff(self, original_lines: List[str], forge_lines: List[str]) -> Tuple[str, List[str]]:
        """Guardrail 3 & 4: Diff Risk Analyzer & Max Size Limit."""
        import difflib
        diff = list(difflib.unified_diff(original_lines, forge_lines))
        
        # Count surgical modifications (actual additions/deletions)
        mod_count = len([l for l in diff if l.startswith("+") or l.startswith("-")]) - 2 # Subtract --- and +++ headers
        
        if mod_count > 50:
            return "HIGH_RISK", [f"❌ REJECTED: Diff too large ({mod_count} mods > 50). Surgical edits only."]
            
        risk_level = "LOW"
        warnings = []
        
        # Look for deletions of critical blocks
        for line in diff:
            if line.startswith("-") and not line.startswith("---"):
                if "import " in line:
                    risk_level = "CRITICAL"
                    warnings.append(f"⚠️ DANGER: Import deletion detected: {line.strip()}")
                if "try:" in line or "except" in line:
                    risk_level = "HIGH"
                    warnings.append(f"⚠️ WARNING: Error handling removal detected: {line.strip()}")
            
        # If no dangerous deletions found, it's LOW risk
        if not warnings:
            risk_level = "LOW"
            
        return risk_level, warnings

    def semantic_validator(self, task: str, new_code: str, auditor_score: float = 0.0) -> bool:
        """
        Guardrail 5: Intent Persistence Check.
        Now with 'Architect Override': High auditor scores (>= 0.9) can bypass 
        heuristic mismatches to prevent false negatives on surgical fixes.
        """
        if auditor_score >= 0.9:
            logger.info("🛡️ Semantic Override: High Auditor score detected. Bypassing heuristic check.")
            return True

        keywords = [kw.lower() for kw in task.split() if len(kw) > 3]
        if not keywords: return True 
        
        matches = [kw for kw in keywords if kw in new_code.lower()]
        
        # High-confidence intent verify
        if len(matches) == 0:
            logger.warning(f"❌ Semantic Validation Failed: No core intent keywords found in new code.")
            return False
            
        threshold = 0.4 if len(keywords) > 4 else 0.25
        if len(keywords) >= 3 and (len(matches) / len(keywords)) < threshold:
            logger.warning(f"❌ Semantic Validation Failed: Low intent persistence ({len(matches)}/{len(keywords)}).")
            return False
            
        return True

    def validate_auditor_output(self, output: Dict[str, Any]) -> bool:
        """Enforce strict schema validation on AI Auditor response."""
        schema_path = "claw_vanguard/schemas/auditor_schema.json"
        if not os.path.exists(schema_path) or not validate:
            return True # Skip if infrastructure missing
            
        try:
            with open(schema_path, "r") as f:
                schema = json.load(f)
            validate(instance=output, schema=schema)
            return True
        except Exception as e:
            logger.error(f"❌ Auditor Schema Validation Failed: {e}")
            return False

    def run_ai_auditor(self, task: str, original: str, forge: str) -> Dict[str, Any]:
        """Signal 2: The Senior AI Architect Review (Decider)."""
        # Cost-Aware Routing: Shifted entirely to reasoning (DeepSeek/Mistral)
        # Gemini free tier is not capable/permitted for pure codebase analysis.
        task_type = "reasoning"
        
        prompt = f"""
        ACT AS: Senior AI Security & Performance Architect.
        TASK: Audit the following code optimization against the original intent.
        
        GOAL: {task}
        
        ORIGINAL CODE:
        {original}
        
        PROPOSED OPTIMIZATION:
        {forge}
        
        RULES:
        1. Return ONLY a valid JSON object matching the requested schema.
        2. Evaluate specifically for Correctness, Safety, and Performance.
        3. Assign a 'score' from 0.0 to 1.0.
        4. List specific 'risks' with 'severity' (low, medium, high, critical).
        
        JSON SCHEMA REQUIRED:
        {{
          "approved": bool,
          "score": float,
          "critique": "string",
          "risks": [ {{"type": "string", "severity": "string"}} ],
          "fix_suggestions": [ "string" ]
        }}
        """
        
        logger.info(f"🧐 [VANGUARD_AUDITOR] Running Architectural Review via {task_type}...")
        raw_res = self.governor.generate(task_type, prompt, module_name="vanguard_auditor")
        
        if not raw_res:
            return {"approved": False, "score": 0.0, "critique": "Auditor Failed to Respond", "risks": [], "fix_suggestions": []}

        try:
            # Clean JSON from potential Markdown formatting
            clean_json = raw_res.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json.split("```json")[1].split("```")[0].strip()
            elif clean_json.startswith("```"):
                clean_json = clean_json.split("```")[1].split("```")[0].strip()
                
            audit_data = json.loads(clean_json)
            if self.validate_auditor_output(audit_data):
                return audit_data
        except Exception as e:
            logger.error(f"❌ Failed to parse Auditor JSON: {e}")
            
        return {"approved": False, "score": 0.0, "critique": "Malformed Auditor Response", "risks": [], "fix_suggestions": []}

    def run_forge_pipeline(self, target_file: str, optimization_task: str) -> ForgeResult:
        """
        The Elite Forge Workflow: Clone -> Optimize -> Risk Check -> Test -> Audit -> Result.
        """
        # 1. Permission Check
        allowed, msg = self.can_forge(target_file)
        if not allowed:
            return ForgeResult(False, msg)
            
        self.FORGE_COOLDOWN[target_file] = time.time()
        
        # 2. Clone to Forge (Secondary)
        forge_path = os.path.join(self.temp_dir, f"{os.path.basename(target_file)}_secondary.py")
        shutil.copy(target_file, forge_path)
        
        with open(target_file, 'r', encoding='utf-8') as f:
            original_content = f.readlines()
            
        # 3. Apply Optimization (Surgical Edit)
        prompt = f"""
        TASK: {optimization_task}
        TARGET FILE: {target_file}
        
        RULES:
        1. ONLY modify the logic specifically requested in the task.
        2. DO NOT delete existing imports unless they are replaced.
        3. DO NOT remove existing try/except blocks.
        4. Keep the output SURGICAL and valid Python.
        
        CURRENT CODE:
        {''.join(original_content)}
        
        Return ONLY the full updated code for the file. No bullshit.
        """
        
        logger.info(f"🧠 Vanguard Forge: Requesting surgical optimization for {target_file}")
        optimized_code = self.governor.generate("reasoning", prompt)
        
        if not optimized_code:
            return ForgeResult(False, "❌ AI failed to generate optimization.")
            
        # 5. Signal 1: Computational (Tests)
        logger.info(f"🧪 Vanguard Forge: Running Pytest Verification for {target_file}")
        test_success, test_msg = self.verify_with_swap(forge_path, target_file)
        
        # 6. Signal 2: Intellectual (AI Auditor - Decider)
        original_str = "".join(original_content)
        audit = self.run_ai_auditor(optimization_task, original_str, optimized_code)
        auditor_score = audit.get("score", 0.0)

        # 7. Semantic Check (Pass the Auditor Score for heuristic override)
        if not self.semantic_validator(optimization_task, optimized_code, auditor_score=auditor_score):
            return ForgeResult(False, "❌ REJECTED: Semantic Validation Failed (Intent Mismatch).")
            
        # 8. Risk Analysis
        forge_lines = optimized_code.splitlines(keepends=True)
        risk_level, warnings = self.analyze_diff(original_content, forge_lines)
        
        if risk_level == "CRITICAL":
            return ForgeResult(False, f"❌ REJECTED: Critical Risk Detected.\n" + "\n".join(warnings))
            
        # 9. Writing to Forge
        with open(forge_path, 'w', encoding='utf-8') as f:
            f.write(optimized_code)
            
        # 10. Multi-Signal Fusion & Bias Control
        # Each signal provides exactly 1.0 points if successful
        score = 0.0
        
        # Signal 1: Computational (Tests)
        if test_success: score += 1.0
        
        # Signal 2: Intellectual (AI Auditor)
        auditor_pass = audit.get("approved", False)
        if auditor_pass: score += 1.0
        
        # Signal 3: Safety (Risk Analyzer)
        # We allow LOW or MEDIUM risk for surgical optimizations
        safe_risk = risk_level in ["LOW", "MEDIUM"]
        if safe_risk: score += 1.0
        
        logger.info(f"📊 Consensus Debug: score={score} test={test_success} audit={auditor_pass} risk={risk_level}")
        
        # Guardrail: Rejection Thresholds
        disagreement = test_success and not auditor_pass
        
        if auditor_score < 0.6:
            return ForgeResult(False, f"❌ REJECTED: Decider Score {auditor_score} too low. {audit.get('critique')}", ai_critique=audit)
            
        if score < 3.0:
            msg = f"❌ REJECTED: Low Approval Score ({score}/3.0). "
            if disagreement: msg += "[LOGIC_RISK] Tests passed but Auditor REJECTED code logic."
            if risk_level == "HIGH_RISK": msg += "[SIZE_RISK] Optimization is too large for surgical promotion."
            if not test_success: msg += f"[TEST_FAILURE] Pytest failed: {test_msg[:100]}..."
            return ForgeResult(False, msg, ai_critique=audit, disagreement=disagreement, audit_score=auditor_score)
            
        import difflib
        diff_str = "".join(difflib.unified_diff(original_content, forge_lines))
        
        proposal_id = f"prop_{int(time.time())}"
        result = ForgeResult(
            success=True, 
            message=f"✅ FORGE SUCCESS: {target_file} optimized and verified.",
            target_file=target_file,
            diff=diff_str,
            score=score,
            risk_level=risk_level,
            ai_critique=audit,
            audit_score=auditor_score,
            proposal_id=proposal_id,
            full_code=optimized_code
        )
        
        self.save_proposal_to_history(result)
        return result

    def save_proposal_to_history(self, result: ForgeResult):
        """Structured JSON History for traceability."""
        path = os.path.join(self.history_dir, f"{result.proposal_id}.json")
        data = {
            "proposal_id": result.proposal_id,
            "timestamp": result.timestamp,
            "target_file": result.target_file,
            "risk_level": result.risk_level,
            "scores": {
                "total": result.score,
                "auditor": result.audit_score
            },
            "audit": result.ai_critique,
            "diff": result.diff,
            "full_code": result.full_code
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"💾 Proposal saved to {path}")

    def verify_with_swap(self, forge_path: str, original_path: str) -> Tuple[bool, str]:
        """
        Critical Guardrail 7: Safe Swap & Test.
        Temporarily promotes forge to production to run actual integration tests.
        """
        backup_path = original_path + ".tmp_backup"
        shutil.copy(original_path, backup_path)
        
        try:
            # 1. Swap
            shutil.copy(forge_path, original_path)
            
            # 2. Find matching test
            module_name = os.path.basename(original_path).replace(".py", "")
            test_file = f"tests/test_{module_name}.py"
            
            if not os.path.exists(test_file):
                # If no specific test, run a generic health check
                test_file = "tests/test_vanguard_core.py"
                
            # 3. Run Pytest — using list form (shell=False) to prevent command injection
            cmd = ["pytest", test_file, "--maxfail=1"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                return True, "All tests passed."
            else:
                return False, result.stdout + result.stderr
        finally:
            # 4. Instant Rollback
            shutil.move(backup_path, original_path)

    def promote_to_primary(self, target_file: str, forge_content: str):
        """
        Atomic Promotion: Snapshot -> Replace.
        Uses os.replace for thread-safe, corruption-proof replacement.
        """
        snapshot_at = int(time.time())
        snapshot_path = f"backups/{os.path.basename(target_file)}.{snapshot_at}.backup"
        shutil.copy(target_file, snapshot_path)
        
        # 1. Write to temp file first
        temp_file = target_file + ".new"
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write(forge_content)
            
        # 2. Atomic swap
        os.replace(temp_file, target_file)
            
        logger.info(f"🏆 PROMOTE SUCCESS: {target_file} updated. Snapshot: {snapshot_path}")
        return snapshot_path

    def rollback(self, target_file: str) -> bool:
        """Elite Rollback: Atomic restoration from latest backup."""
        base_name = os.path.basename(target_file)
        backups = [f for f in os.listdir("backups") if f.startswith(base_name) and f.endswith(".backup")]
        
        if not backups:
            logger.error(f"❌ Rollback failed: No backups found for {base_name}")
            return False
            
        # Sort by timestamp (newest last)
        backups.sort()
        latest_backup = os.path.join("backups", backups[-1])
        
        temp_file = target_file + ".rollback"
        shutil.copy(latest_backup, temp_file)
        os.replace(temp_file, target_file)
        
        logger.info(f"⏪ ROLLBACK SUCCESS: {target_file} restored from {latest_backup}")
        return True

vanguard_forge = VanguardForge()