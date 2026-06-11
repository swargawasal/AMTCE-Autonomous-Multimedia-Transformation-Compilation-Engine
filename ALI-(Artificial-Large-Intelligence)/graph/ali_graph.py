from langgraph.graph import StateGraph, END
from graph.nodes import (
    ALIState,
    emotion_node,
    ubi_node,
    router_node,
    memory_node,
    chain_node,
    synthesis_node,
    # safety_node is NOT in the DAG — it is a FastAPI BackgroundTask (fire-and-forget)
    # to avoid blocking the user response. See api/main.py /chat endpoint.
)

def build_ali_graph() -> StateGraph:
    workflow = StateGraph(ALIState)
    
    workflow.add_node("emotion",   emotion_node)
    workflow.add_node("ubi",       ubi_node)      # Phase 20: UBI between emotion and router
    workflow.add_node("router",    router_node)
    workflow.add_node("memory",    memory_node)
    workflow.add_node("chain",     chain_node)
    workflow.add_node("synthesis", synthesis_node)
    # NOTE: safety is intentionally NOT a node — it runs as a FastAPI BackgroundTask
    # after the response is sent to the user, keeping the critical path fast.
    
    workflow.set_entry_point("emotion")
    
    # Clean linear DAG: emotion → ubi → router → memory → chain → synthesis
    workflow.add_edge("emotion",   "ubi")
    workflow.add_edge("ubi",       "router")
    workflow.add_edge("router",    "memory")
    workflow.add_edge("memory",    "chain")
    workflow.add_edge("chain",     "synthesis")
    workflow.add_edge("synthesis", END)
    
    return workflow.compile()
