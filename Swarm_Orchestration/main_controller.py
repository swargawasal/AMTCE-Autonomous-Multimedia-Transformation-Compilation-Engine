import os
import sys
import operator
from typing import Annotated, Sequence, TypedDict, Union

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_mistralai import ChatMistralAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# 1. Setup paths to include Intelligence_Modules and Credentials
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

# Load environment variables
env_path = os.path.join(project_root, "Credentials", ".env")
load_dotenv(dotenv_path=env_path)

# --- PHASE 3: LANGGRAPH BRAIN LAYER ---

# 2. State Definition
class AgentState(TypedDict):
    """
    The state object passed between nodes.
    - messages: List of conversation messages
    - next_node: The decision from the router
    - result: The final output of the task
    """
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next_node: str
    result: str

# 3. Model Setup
# We use Mistral Large as the primary 'Brain' for orchestration to preserve Gemini quota.
from Intelligence_Modules.gemini_governor import GeminiGovernor
from Intelligence_Modules.router_orchestra import orchestra

# 3. Model Setup
# We use the Governor for Gemini tasks and Orchestra for everything else.
governor = GeminiGovernor()

# 4. Node Functions

def router_node(state: AgentState):
    """
    Evaluates the user's task and decides where to route it.
    Uses GeminiGovernor to select the best available model for routing.
    """
    last_message = state["messages"][-1].content
    
    prompt = f"""
    Analyze this task: "{last_message}"
    Categorize into:
    - 'antigravity': Coding, file operations, logic, or direct bot management.
    - 'crewai': Multimedia strategy, creative ideas, viral hooks, or swarm coordination.
    
    Respond with ONLY 'antigravity' or 'crewai'.
    """
    
    try:
        # Use Governor to pick the best model for this task
        result = governor.generate(
            task_type="reasoning",
            prompt=prompt,
            module_name="main_controller_router"
        )
        decision = str(result).strip().lower()
    except Exception as e:
        print(f"ERROR in Router: {e}. Defaulting to antigravity.")
        decision = "antigravity"
    
    print(f"[ROUTER] Decision: {decision}")
    return {"next_node": "crewai" if "crewai" in decision else "antigravity"}

def call_antigravity(state: AgentState):
    """
    Handles 'Antigravity' (Reasoning/Coding) tasks.
    Uses RouterOrchestra to rotate through Mistral, Groq, and DeepSeek.
    """
    last_message = state["messages"][-1].content
    print("[ANTIGRAVITY] Processing task via Orchestra...")
    
    # Orchestra handles cross-provider routing and retries
    response = orchestra.route(
        prompt=last_message,
        task_type="reasoning"
    )
    
    if not response:
        response = governor.generate(task_type="reasoning", prompt=last_message)
        
    return {"result": response}

def call_crewai(state: AgentState):
    """
    Handles 'CrewAI' (Swarm) tasks.
    """
    from Swarm_Orchestration.multimedia_crew import run_swarm
    
    last_message = state["messages"][-1].content
    print("[CREWAI] Dispatching to Multimedia Swarm...")
    
    result = run_swarm(task_description=last_message)
    return {"result": result}

def call_human_gate(state: AgentState):
    """
    Sovereign Interrupt: Waits for USER approval before allowing Swarm execution.
    """
    print("\n" + "="*50)
    print("!!! SOVEREIGN INTERRUPT !!!")
    print("Antigravity has approved the swarm strategy.")
    print("Awaiting USER confirmation to execute.")
    print("="*50)
    
    # In a real environment, this could pause the graph until a webhook is hit.
    # For local execution, we use a standard input prompt.
    user_input = input("Approve Execution? (y/n): ").strip().lower()
    
    if user_input == 'y':
        print("[HUMAN GATE] Execution APPROVED by Sovereign.")
        return {"next_node": "crewai"}
    else:
        print("[HUMAN GATE] Execution REJECTED by Sovereign. Halting.")
        return {"next_node": "end"}

# 5. Graph Construction

workflow = StateGraph(AgentState)
workflow.add_node("router", router_node)
workflow.add_node("antigravity", call_antigravity)
workflow.add_node("human_gate", call_human_gate)
workflow.add_node("crewai", call_crewai)

workflow.set_entry_point("router")

workflow.add_conditional_edges(
    "router",
    lambda state: state["next_node"],
    {
        "antigravity": "antigravity",
        "crewai": "human_gate" # Route CrewAI tasks through the Human Gate first
    }
)

workflow.add_conditional_edges(
    "human_gate",
    lambda state: state["next_node"],
    {
        "crewai": "crewai",
        "end": END
    }
)

workflow.add_edge("antigravity", END)
workflow.add_edge("crewai", END)

# 6. Persistent Memory
checkpointer = MemorySaver()
amtce_graph = workflow.compile(checkpointer=checkpointer, interrupt_before=["human_gate"])

# 7. Helper to run the graph
def run_task(task_text: str, thread_id: str = "default_session"):
    """
    Execute a task through the LangGraph controller.
    """
    config = {"configurable": {"thread_id": thread_id}}
    inputs = {"messages": [HumanMessage(content=task_text)]}
    
    print(f"--- [AMTCE CONTROLLER] Running Task for Thread: {thread_id} ---")
    
    final_output = "No result generated."
    try:
        for event in amtce_graph.stream(inputs, config):
            for node_name, output in event.items():
                print(f"Node '{node_name}' finished.")
                if "result" in output:
                    final_output = output["result"]
    except Exception as e:
        print(f"CRITICAL ERROR in Graph Execution: {e}")
        final_output = f"Execution failed: {str(e)}"
    
    return final_output

if __name__ == "__main__":
    print("AMTCE LangGraph Controller Active. (Import and use run_task for production)")
