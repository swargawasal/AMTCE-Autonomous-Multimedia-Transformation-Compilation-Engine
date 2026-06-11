import asyncio
from typing import Dict, Any, Optional
from typing_extensions import TypedDict

from emotion.detector import detect_emotion
from router.ali_router import classify_task, get_routing_plan
from chains.reasoning_chain import execute_complex_chain
from emotion.response_layer import apply_emotion_polish
from safety.verdict_engine import run_safety_checks_async

# UBI — User Behaviour Intelligence (Phase 20)
try:
    from ubi.personalizer  import personalize
    from ubi.predictor     import classify_topic, measure_and_record
    from ubi.pattern_learner import update_pattern
    _UBI_AVAILABLE = True
except ImportError:
    _UBI_AVAILABLE = False

class ALIState(TypedDict):
    session_id:    str
    user_input:    str
    emotion_data:  Dict[str, Any]
    ubi_context:   Dict[str, Any]   # injected by ubi_node (Phase 20)
    routing_plan:  Dict[str, str]
    memory_context: Optional[str]
    chain_result:  Dict[str, Any]
    final_response: str

def emotion_node(state: ALIState) -> ALIState:
    print("Node: Emotion Detection")
    emotion_data = detect_emotion(state["user_input"])
    return {"emotion_data": emotion_data}

def ubi_node(state: ALIState) -> ALIState:
    """Phase 20 — User Behaviour Intelligence node.
    Runs AFTER emotion, BEFORE router.
    Builds personalisation context, updates topic model, predicts next topic.
    If UBI deps not installed, returns empty context and continues gracefully.
    """
    print("Node: UBI (User Behaviour Intelligence)")

    if not _UBI_AVAILABLE:
        return {"ubi_context": {}}

    user_input = state["user_input"]
    session_id = state.get("session_id", "anonymous")
    # Use hashed session as stable user ID — no raw PII stored
    import hashlib
    user_id = hashlib.sha256(session_id.encode()).hexdigest()[:16]

    async def _run_ubi():
        # 1. Classify the actual topic of this message
        actual_topic = await classify_topic(user_input)

        # 2. Get the prediction that was made BEFORE this message (from last turn)
        from ubi.predictor import predict as _predict
        prev_prediction = await _predict(user_id)
        predicted_topic = prev_prediction.get("predicted_topic", actual_topic)

        # 3. Measure embedding distance & update MSE tracker
        await measure_and_record(user_id, predicted_topic, actual_topic)

        # 4. Update topic distribution + kurtosis
        await update_pattern(user_id, actual_topic)

        # 5. Build full personalisation context for this turn
        ctx = await personalize(user_id, user_input)
        ctx["actual_topic"]  = actual_topic
        ctx["user_id_hash"]  = user_id
        return ctx

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # FastAPI async context
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _run_ubi())
                ubi_ctx = future.result(timeout=5)
        else:
            ubi_ctx = loop.run_until_complete(_run_ubi())
    except Exception as e:
        print(f"[UBI] Non-critical error: {e}")
        ubi_ctx = {}

    return {"ubi_context": ubi_ctx}


def router_node(state: ALIState) -> ALIState:
    print("Node: Router")
    classification = classify_task(state["user_input"])
    category = classification.get("category", "creative")
    plan = get_routing_plan(category, state.get("emotion_data", {}))
    return {"routing_plan": plan}

def memory_node(state: ALIState) -> ALIState:
    print("Node: Memory Check")
    # Stubbed: Real memory would check vector DB or JSON files for past answers
    return {"memory_context": None}

def chain_node(state: ALIState) -> ALIState:
    print("Node: Chain Execution")
    # For now, using complex chain for all. Later, split based on routing_plan.
    result = execute_complex_chain(state["user_input"], state.get("emotion_data", {}))
    return {"chain_result": result}

def synthesis_node(state: ALIState) -> ALIState:
    print("Node: Synthesis and Emotion Polish")
    chain_res = state.get("chain_result", {})
    raw_answer = chain_res.get("final_answer", "")
    final_answer = apply_emotion_polish(raw_answer, state.get("emotion_data", {}))
    return {"final_response": final_answer}

def safety_node(state: ALIState) -> ALIState:
    """
    Fire-and-forget safety check.
    NOT wired into the LangGraph DAG directly.
    Called from api/main.py as a FastAPI BackgroundTask after the response is returned.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # FastAPI context: schedule on running loop (true fire-and-forget)
            asyncio.ensure_future(
                run_safety_checks_async(state["final_response"], state.get("session_id", "unknown"))
            )
        else:
            # CLI/test context: run synchronously
            loop.run_until_complete(
                run_safety_checks_async(state["final_response"], state.get("session_id", "unknown"))
            )
    except Exception as e:
        print(f"[Safety] Non-blocking check failed silently: {e}")
    return state
