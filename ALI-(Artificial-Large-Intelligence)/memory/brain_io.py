import json
import os
from typing import Dict, Any, List

BRAIN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ruflow_brain")

def _load_json(filename: str) -> Dict[str, Any]:
    filepath = os.path.join(BRAIN_DIR, filename)
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_json(filename: str, data: Dict[str, Any]) -> None:
    filepath = os.path.join(BRAIN_DIR, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_solved_problems() -> Dict[str, Any]:
    return _load_json("solved_problems.json")

def save_solved_problems(data: Dict[str, Any]) -> None:
    _save_json("solved_problems.json", data)

def load_anton_solved() -> Dict[str, Any]:
    return _load_json("anton_solved.json")

def save_anton_solved(data: Dict[str, Any]) -> None:
    _save_json("anton_solved.json", data)

def load_knowledge_base() -> Dict[str, Any]:
    return _load_json("knowledge_base.json")

def save_knowledge_base(data: Dict[str, Any]) -> None:
    _save_json("knowledge_base.json", data)

def append_to_safety_log(flag: Dict[str, Any]) -> None:
    log_data = _load_json("safety_log.json")
    if not log_data:
        log_data = {"version": "1.0", "flags": []}
    log_data.setdefault("flags", []).append(flag)
    _save_json("safety_log.json", log_data)
