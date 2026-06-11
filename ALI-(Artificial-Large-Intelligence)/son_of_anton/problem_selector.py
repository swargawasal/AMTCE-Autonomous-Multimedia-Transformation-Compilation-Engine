from memory.brain_io import load_anton_solved

# Problems that have already been attempted and exhausted
SKIP_STATUSES = {"SOLVED", "FAILED_MAX_ITERATIONS"}

# The full pool of problems Anton works through
PROBLEM_POOL = [
    "What is the optimal transformer attention pattern for long-context reasoning?",
    "How should a multi-agent system handle conflicting tool outputs?",
    "What is the best strategy for rate-limit-aware API call orchestration?",
    "How can LangGraph state be efficiently checkpointed to disk mid-execution?",
    "What is the correct pattern for async fire-and-forget tasks in FastAPI?",
]

def select_problem() -> str:
    """Pick the next unsolved, unattempted problem from the pool."""
    data = load_anton_solved()
    runs = data.get("runs", []) if data else []

    # Build a set of problems that are already done or failed
    attempted = {
        r["problem"] for r in runs
        if r.get("status") in SKIP_STATUSES
    }

    for problem in PROBLEM_POOL:
        if problem not in attempted:
            return problem

    # All problems exhausted
    return None

