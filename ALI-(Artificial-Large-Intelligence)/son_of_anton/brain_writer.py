import uuid
from datetime import datetime
from typing import Optional
from memory.brain_io import load_anton_solved, save_anton_solved

def write_solution_to_brain(
    problem: str,
    iterations: int,
    solution: Optional[str] = None,
    status: str = "SOLVED"
):
    data = load_anton_solved()
    if not data:
        data = {"version": "1.0", "total_solved": 0, "runs": []}

    is_solved = status == "SOLVED"

    entry = {
        "run_id": str(uuid.uuid4()),
        "created_by": "son-of-anton",
        "problem": problem,
        "solution": solution,            # None when failed
        "status": status,                # SOLVED | FAILED_MAX_ITERATIONS
        "reasoning_path": ["deepseek", "gemini"],
        "confidence": 0.95 if is_solved else 0.0,
        "iterations": iterations,
        "verified": is_solved,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    data["runs"].append(entry)
    data["total_solved"] = sum(1 for r in data["runs"] if r.get("status") == "SOLVED")

    save_anton_solved(data)
    if is_solved:
        print(f"✅ Stored verified solution in anton_solved.json (Total solved: {data['total_solved']})")
    else:
        print(f"⚠️  Marked problem as {status} in anton_solved.json — will be skipped next run.")
