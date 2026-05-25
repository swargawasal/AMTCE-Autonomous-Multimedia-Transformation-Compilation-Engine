"""editor_memory.py — Root shim for backward compatibility.
The implementation lives in Core_Modules/editor_memory.py
"""

from Core_Modules.editor_memory import EditorMemory, _pattern_key, find_similar_pattern

__all__ = ["EditorMemory", "_pattern_key", "find_similar_pattern"]
