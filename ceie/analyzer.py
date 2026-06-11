"""
ceie/analyzer.py
----------------
Manages sequential Gemini analysis of video chunks.
Handles file uploading/processing/cleanup, calling gemini_router under model rotation,
rolling context propagation, and final consultation.
"""

import json
import logging
import os
import re
import time
from typing import List, Dict, Any, Optional

from google import genai
from google.genai import types

from ceie.models.edit_schema import ChunkEditBlueprint, MasterEditPlan
from ceie.context_chain import ContextChainManager
from ceie.prompts import SYSTEM_PROMPT, build_chunk_prompt, FINAL_CONSULTATION_PROMPT
from Intelligence_Modules.gemini_governor import gemini_router

logger = logging.getLogger("ceie.analyzer")

def get_gemini_client() -> genai.Client:
    """Retrieve Gemini API key and return a genai Client."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not api_key:
        from dotenv import load_dotenv
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_path = os.path.join(base_dir, "Credentials", ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not api_key:
        raise ValueError(
            "Gemini API key not found in environment variables or Credentials/.env"
        )

    return genai.Client(api_key=api_key)


def clean_json_text(text: str) -> str:
    """Removes markdown wrappers (```json ... ```) from Gemini outputs."""
    text = text.strip()
    # Remove markdown code blocks if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


class VideoAnalyzer:
    def __init__(self, session_id: Optional[str] = None):
        self.client = get_gemini_client()
        self.session_id = session_id or f"ceie_{int(time.time())}"
        
    def wait_for_file_active(self, file_name: str, max_wait: int = 120) -> bool:
        """Poll the File API until the file state is ACTIVE."""
        start_time = time.time()
        while True:
            file_info = self.client.files.get(name=file_name)
            if file_info.state.name == "ACTIVE":
                return True
            elif file_info.state.name == "FAILED":
                logger.error(f"File processing failed on Gemini servers: {file_info.error.message}")
                return False
            elif time.time() - start_time > max_wait:
                logger.error("Timed out waiting for file processing.")
                return False
            
            logger.info("... waiting for video processing on Gemini servers ...")
            time.sleep(5)

    def analyze_chunks(self, chunks: List[Dict[str, Any]], original_video_path: str) -> MasterEditPlan:
        """
        Sequentially analyzes each video chunk, maintaining a rolling story context.
        """
        # Register the video session budget with the governor
        total_duration = sum(c["duration"] for c in chunks)
        gemini_router.begin_video_session(self.session_id, total_duration)
        
        context_manager = ContextChainManager(total_chapters=len(chunks))
        blueprints = []
        
        for idx, chunk in enumerate(chunks):
            chunk_path = chunk["video_path"]
            start_sec = chunk["start_sec"]
            end_sec = chunk["end_sec"]
            
            logger.info(f"🔮 [CEIE_ANALYSIS] Uploading chunk #{idx} ({start_sec:.1f}s - {end_sec:.1f}s) to Gemini...")
            
            # 1. Upload to File API
            uploaded_file = self.client.files.upload(file=chunk_path)
            
            try:
                # 2. Wait for processing
                if not self.wait_for_file_active(uploaded_file.name):
                    raise RuntimeError(f"Failed to process chunk #{idx} on Gemini File API.")
                
                # 3. Format Prompt
                context_str = context_manager.get_prompt_context()
                prompt_text = build_chunk_prompt(idx, start_sec, end_sec, context_str)
                
                # 4. Generate Blueprint using GeminiGovernor model rotation
                logger.info(f"🧠 [CEIE_ANALYSIS] Querying Gemini for chunk #{idx} blueprint...")
                
                # Use "analysis" task type for chunked visual/narrative analysis
                payload = [uploaded_file, SYSTEM_PROMPT, prompt_text]
                
                raw_response = gemini_router.generate(
                    task_type="analysis",
                    prompt=payload,
                    module_name="ceie_analyzer",
                    session_id=self.session_id,
                    gen_config={"response_mime_type": "application/json"}
                )
                
                if not raw_response:
                    raise RuntimeError(f"Failed to get editing blueprint for chunk #{idx} from Gemini.")
                
                cleaned_response = clean_json_text(raw_response)
                
                # 5. Validate JSON against Schema
                blueprint_dict = json.loads(cleaned_response)
                blueprint = ChunkEditBlueprint.model_validate(blueprint_dict)
                
                # Update chunk index and timestamps to be absolutely sure they match the source chunk
                blueprint.chunk_index = idx
                blueprint.chunk_start_sec = start_sec
                blueprint.chunk_end_sec = end_sec
                
                blueprints.append(blueprint)
                
                # 6. Update Rolling Narrative Context
                context_manager.update(blueprint.context_handoff)
                logger.info(f"✅ Chunk #{idx} analyzed. Next narrative thread: {blueprint.context_handoff.narrative_thread}")
                
            except Exception as e:
                logger.error(f"❌ Error analyzing chunk #{idx}: {e}")
                raise e
            finally:
                # Always clean up the uploaded chunk file
                try:
                    logger.info(f"🗑️ Cleaning up chunk file from Gemini API storage: {uploaded_file.name}")
                    self.client.files.delete(name=uploaded_file.name)
                except Exception as cleanup_err:
                    logger.warning(f"⚠️ Cleanup failed for chunk file {uploaded_file.name}: {cleanup_err}")

        # Final step: Master aggregation and consultation
        master_plan = self.consult_master_plan(original_video_path, blueprints)
        return master_plan

    def consult_master_plan(self, original_video_path: str, blueprints: List[ChunkEditBlueprint]) -> MasterEditPlan:
        """
        Aggregates all chunk blueprints and performs a text-only final consultation
        to verify global pacing and eligibility.
        """
        logger.info("✨ [CEIE_MASTER] Aggregating chunk blueprints...")
        
        # Build initial MasterPlan structure
        total_duration = sum(bp.chunk_end_sec - bp.chunk_start_sec for bp in blueprints)
        
        master_plan = MasterEditPlan(
            video_path=original_video_path,
            total_duration_sec=total_duration,
            total_chunks=len(blueprints),
            chunks=blueprints,
            global_narrative="",
            yt_eligibility="borderline",
            yt_eligibility_notes=""
        )
        
        # Serialize the edit blueprint outline for text-only review
        outline = []
        for bp in blueprints:
            outline.append({
                "chunk_index": bp.chunk_index,
                "role": bp.chapter_role,
                "emotional_arc": bp.emotional_arc,
                "energy": bp.energy_score,
                "trims_count": len(bp.trims),
                "cuts_count": len(bp.cuts),
                "voiceovers_count": len(bp.voiceover_segments),
                "transitions": [{"at_sec": t.at_sec, "type": t.type} for t in bp.transitions],
                "pacing_notes": bp.pacing_notes,
                "yt_notes": bp.yt_transformative_notes
            })
            
        consult_prompt = f"""You are reviewing the editing structure of a video compilation.
Here is the outline of the generated edit blueprints for each chunk:
{json.dumps(outline, indent=2)}

Please perform the final consultation:
1. Summarize the global narrative arc.
2. Determine if the overall visual edits, pacing alterations, and voiceovers qualify the video as YouTube-transformative UGC.
"""
        
        logger.info("🧠 [CEIE_MASTER] Performing final Gemini consultation for global narrative...")
        raw_consult_res = gemini_router.generate(
            task_type="master",
            prompt=[SYSTEM_PROMPT, consult_prompt, FINAL_CONSULTATION_PROMPT],
            module_name="ceie_analyzer",
            session_id=self.session_id,
            gen_config={"response_mime_type": "application/json"}
        )
        
        if raw_consult_res:
            try:
                cleaned_consult = clean_json_text(raw_consult_res)
                consult_data = json.loads(cleaned_consult)
                master_plan.global_narrative = consult_data.get("global_narrative", "")
                master_plan.yt_eligibility = consult_data.get("yt_eligibility", "borderline")
                master_plan.yt_eligibility_notes = consult_data.get("yt_eligibility_notes", "")
                logger.info(f"✅ Master consultation complete. Eligibility: {master_plan.yt_eligibility}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to parse master consultation response: {e}. Using defaults.")
                master_plan.global_narrative = "Failed to compile narrative."
                master_plan.yt_eligibility = "borderline"
                master_plan.yt_eligibility_notes = "Failed to parse final consultation results."
        else:
            logger.warning("⚠️ No response received for master consultation. Using defaults.")
            master_plan.global_narrative = "No response from master consultant."
            master_plan.yt_eligibility_notes = "No response from master consultant."
            
        return master_plan
