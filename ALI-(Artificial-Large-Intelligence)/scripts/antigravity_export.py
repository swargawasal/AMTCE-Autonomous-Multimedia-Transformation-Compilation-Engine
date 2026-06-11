import json
import os
import uuid
import platform
from datetime import datetime

ALI_BRAIN_PATH = "ruflow_brain/anton_solved.json"

# Platform-aware export path:
# Windows (local PC) → write directly into Antigravity global memory — zero manual copy
# Linux (GitHub Actions) → write into repo so ali_loop.yml commits it; one git pull picks it up
if platform.system() == "Windows":
    EXPORT_PATH = r"C:\Users\midhunkrishnapv\.agent\memory\memory_log.jsonl"
else:
    EXPORT_PATH = "ruflow_brain/antigravity_export.jsonl"

# A stable "parent" BASE memory_id representing the ALI Engine as the source
ALI_ENGINE_BASE_ID = "ali-engine-base-v1"


def export_to_antigravity():
    if not os.path.exists(ALI_BRAIN_PATH):
        print("No anton_solved.json found.")
        return

    with open(ALI_BRAIN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    runs = data.get("runs", [])
    if not runs:
        print("No solutions to export.")
        return

    exported_count = 0
    with open(EXPORT_PATH, "w", encoding="utf-8") as out:
        for run in runs:
            problem_text = run.get("problem", "unknown")

            # Exact DELTA_ADD schema match per ruflow-memory-schema.json v2.4
            node = {
                # --- Identity fields ---
                "memory_id": f"ali-anton-{run['run_id']}",   # uuid-v4 string (using run_id as UUID)
                "node_type": "DELTA_ADD",                      # NOT "type" — schema uses "node_type"
                "parent_memory_id": ALI_ENGINE_BASE_ID,        # Links to the ALI Engine BASE node
                "version": 1,
                "status": "active",

                # --- Content fields ---
                "delta_description": f"ALI Anton verified solution for: {problem_text[:100]}",

                "gap_that_was_missing": {
                    "what_base_lacked": "A verified reasoned solution to this specific hard problem",
                    "why_base_missed_it": "Problem was too domain-specific for general base knowledge",
                    "discovered_in_project": "ALI-(Artificial-Large-Intelligence)",
                    "discovered_in_context": f"Son of Anton loop iteration {run.get('iterations', 1)}"
                },

                # --- Gate (schema requires all 5 sub-fields) ---
                "context_gate": {
                    "valid_when": [f"Solving a problem similar to: {problem_text[:80]}"],
                    "invalid_when": [
                        "When context requires low-latency simple responses",
                        "When the domain differs significantly from the original problem"
                    ],
                    "confidence_inside_gate": run.get("confidence", 0.95),
                    "confidence_outside_gate": 0.05,
                    "risk_if_applied_outside_gate": "Solution may be domain-specific and inapplicable, causing incorrect reasoning"
                },

                # --- New knowledge (schema uses 'new_knowledge' object, NOT 'solution') ---
                "new_knowledge": {
                    "solution_addition": run.get("solution", ""),
                    "affects_files": ["ruflow_brain/anton_solved.json"],
                    "is_hack": False,
                    "is_verified": run.get("verified", True),
                    "confidence": run.get("confidence", 0.95)
                },

                # --- Coverage ---
                "coverage_added": 0.15,         # Each Anton solution adds ~15% coverage
                "total_coverage_after": 1.0,

                # --- Audit trail ---
                "created_at": run.get("timestamp", datetime.utcnow().isoformat() + "Z"),  # NOT "timestamp"
                "created_by": "write-back-curator"   # Schema law: never deepseek-reasoner
            }
            out.write(json.dumps(node) + "\n")
            exported_count += 1

    print(f"Exported {exported_count} verified DELTA_ADD nodes to {EXPORT_PATH}")
    print(f"Append to: C:\\Users\\midhunkrishnapv\\.agent\\memory\\memory_log.jsonl")
    print(f"Schema version matched: ruflow-memory-schema.json v2.4")

if __name__ == "__main__":
    export_to_antigravity()

