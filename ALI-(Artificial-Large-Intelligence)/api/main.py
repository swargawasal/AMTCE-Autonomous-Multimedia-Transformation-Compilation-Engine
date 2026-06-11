import os
import uuid
from dotenv import load_dotenv
load_dotenv()
import hashlib
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from fastapi.middleware.cors import CORSMiddleware

from graph.ali_graph import build_ali_graph
from memory.brain_io import load_knowledge_base, load_anton_solved
from chains.vision_chain import execute_vision_chain
from safety.verdict_engine import run_safety_checks_async

app = FastAPI(title="ALI - Artificial Large Intelligence")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compile LangGraph
ali_graph = build_ali_graph()

def verify_token(authorization: Optional[str] = Header(None)):
    expected_token = os.getenv("ALI_API_TOKEN")
    if not expected_token:
        return
    if not authorization or authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

@app.post("/chat")
async def chat_endpoint(req: ChatRequest, background_tasks: BackgroundTasks):
    session_id = req.session_id or str(uuid.uuid4())
    
    # LangGraph Execution (emotion → router → memory → chain → synthesis)
    state = {
        "session_id": session_id,
        "user_input": req.message,
    }
    
    result = ali_graph.invoke(state)
    final_response = result.get("final_response", "")
    
    # Fire-and-forget safety check — runs AFTER response is returned, zero blocking
    background_tasks.add_task(
        run_safety_checks_async, final_response, session_id
    )
    
    return {
        "session_id": session_id,
        "answer": final_response,
        "trace": result.get("chain_result", {}).get("chain_trace", {})
    }

class VisionRequest(BaseModel):
    message: str
    images: List[str] # base64 encoded images
    session_id: Optional[str] = None

@app.post("/vision")
async def vision_endpoint(req: VisionRequest):
    session_id = req.session_id or str(uuid.uuid4())
    
    # Fast path for vision using vision_chain
    # In a full flow, this would go through LangGraph with a vision router override
    result = execute_vision_chain(req.message, req.images, {"emotion": "neutral"})
    
    return {
        "session_id": session_id,
        "answer": result.get("final_answer", ""),
        "trace": result.get("chain_trace", {})
    }

@app.get("/status")
def status_endpoint():
    return {"status": "online", "version": "1.0"}

@app.post("/solve")
def trigger_son_of_anton(background_tasks: BackgroundTasks):
    from son_of_anton.loop import run_anton_loop
    background_tasks.add_task(run_anton_loop)
    return {"status": "Anton reasoning loop triggered"}

class OptimizeRequest(BaseModel):
    target_file: str
    optimization_task: str
    test_file: Optional[str] = None

@app.post("/optimize_code")
def trigger_code_optimization(req: OptimizeRequest, background_tasks: BackgroundTasks):
    from son_of_anton.code_optimizer import code_optimizer
    background_tasks.add_task(
        code_optimizer.run_optimization_loop,
        target_file=req.target_file,
        optimization_task=req.optimization_task,
        test_file=req.test_file
    )
    return {"status": "Code optimization background loop triggered successfully"}

@app.get("/memory")
def memory_endpoint():
    kb = load_knowledge_base()
    anton = load_anton_solved()
    return {
        "knowledge_base_entries": len(kb.get("base_nodes", [])),
        "anton_solved_problems": anton.get("total_solved", 0)
    }

@app.post("/ruflow", dependencies=[Depends(verify_token)])
def ruflow_sync_endpoint():
    """Protected endpoint for external agents (like Antigravity) to sync or trigger actions."""
    return {"status": "Authorized", "message": "RuFlow Sync available."}


# ── Phase 20: UBI Endpoints ───────────────────────────────────────────────────
class TypingEvent(BaseModel):
    session_id: str
    partial_text: str = ""

@app.post("/typing")
async def typing_endpoint(req: TypingEvent):
    """
    Called by the frontend when the user starts typing.
    For FOCUSED users, pre-fires a cheap Gemini call with the predicted topic
    so the answer is ready when the user hits Send.
    """
    try:
        from ubi.preloader import trigger_preload
        user_id = hashlib.sha256(req.session_id.encode()).hexdigest()[:16]
        result  = await trigger_preload(
            session_id=req.session_id,
            user_id=user_id,
            partial_text=req.partial_text,
        )
        return result
    except Exception as e:
        # Preload failures are always silent — never surface to the user
        return {"preload_fired": False, "reason": str(e)}


@app.get("/ubi/profile/{session_id}")
async def ubi_profile_endpoint(session_id: str):
    """
    Diagnostic endpoint: returns the current UBI profile for a session.
    Useful for debugging kurtosis, MSE trends, and topic distribution.
    """
    try:
        from ubi.pattern_learner import get_user_profile
        from ubi.mse_tracker    import get_mse_stats
        user_id = hashlib.sha256(session_id.encode()).hexdigest()[:16]
        profile  = await get_user_profile(user_id)
        mse      = await get_mse_stats(user_id)
        return {"user_id_hash": user_id, "profile": profile, "mse": mse}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
