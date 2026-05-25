import os
import sys
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, Process

# --- PHASE 3: MULTI-BRAIN HYBRID SWARM (SAFE MODE) ---

# --- THE VAULT: DeepSeek Protection ---
DEEPSEEK_LOCKED = True  # Set to False only for critical architectural repairs
# --------------------------------------

# 1. Setup paths to include Intelligence_Modules (looking at parent)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

# Load credentials from parent
env_path = os.path.join(project_root, "Credentials", ".env")
load_dotenv(dotenv_path=env_path)

# 2. BRAIN SELECTION
# We switch to Mistral Large to save your Gemini Quota for actual video processing
# Mistral Large is a reasoning powerhouse with high rate limits
llm_model = "mistral/mistral-large-latest"
print(f"QUOTA SAFETY: Switching Swarm to {llm_model} (Gemini Quota Saved)")

# 2. Agents
strategist = Agent(
    role='Multimedia Strategist',
    goal='Analyze niche configurations and design viral hook blueprints.',
    backstory="You are the lead architect. You use Mistral reasoning to save Gemini resources.",
    llm=llm_model,
    verbose=True,
    allow_delegation=False
)

commander = Agent(
    role='Swarm Commander',
    goal='Finalize the autonomous verification.',
    backstory="You coordinate the final output while respecting the system limits.",
    llm=llm_model,
    verbose=True,
    allow_delegation=True
)

# 3. Tasks
niche_list = ["Fashion & Style", "Adult Content", "Comedy & Meme", "Fitness"]

analysis_task = Task(
    description=f"Active niches: {niche_list}. Create a concise 'Viral Blueprint' for 2026 Fashion Trends.",
    expected_output="A strategy document.",
    agent=strategist
)

execution_task = Task(
    description="Update 'ruflow_test.md' with 'SWARM_GOVERNED_SUCCESS_VIA_MISTRAL'.",
    expected_output="Verification log updated.",
    agent=commander
)

# 4. Initialize
multimedia_crew = Crew(
    agents=[strategist, commander],
    tasks=[analysis_task, execution_task],
    process=Process.sequential,
    verbose=True
)

def run_swarm(task_description: str = None) -> str:
    """
    Programmatic entry point for the Multimedia Swarm.
    Allows LangGraph to pass specific task overrides.
    """
    # Ensure LiteLLM has the Mistral API Key
    os.environ["MISTRAL_API_KEY"] = os.getenv("MISTRAL_API_KEY")

    if task_description:
        # Override the strategist task with the custom instruction
        analysis_task.description = task_description
    
    print(f"[SWARM] Launching with task: {analysis_task.description[:50]}...")
    result = multimedia_crew.kickoff()
    return str(result)

if __name__ == "__main__":
    print("LAUNCHING: Hybrid Intelligence Swarm (Standalone Mode)...")
    res = run_swarm()
    print("\n\n########################")
    print("## SWARM EXECUTION COMPLETE")
    print(f"## RESULT: {res[:100]}...")
    print("########################\n")
