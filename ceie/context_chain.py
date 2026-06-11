"""
ceie/context_chain.py
---------------------
Manages the rolling ContextHandoff state across sequential video chunk analyses.
Allows loading, updating, and formatting the context handoff as a prompt segment.
"""

from typing import Dict, Any, List
from ceie.models.edit_schema import ContextHandoff

class ContextChainManager:
    def __init__(self, total_chapters: int = 1):
        self.current_handoff = ContextHandoff(
            story_arc_so_far="No context yet. This is the start of the video.",
            last_emotional_peak={},
            pacing_momentum="flat",
            narrative_thread="Beginning the sequence, establishing hook.",
            established_hooks=[],
            chapter=1,
            chapters_total=total_chapters
        )
        self.history: List[ContextHandoff] = []

    def update(self, handoff_data: ContextHandoff):
        """Update the current handoff state and save history."""
        self.history.append(self.current_handoff)
        self.current_handoff = handoff_data

    def get_prompt_context(self) -> str:
        """Formats the current handoff state as a prompt segment for Gemini."""
        h = self.current_handoff
        prompt = f"""--- STORY CONTEXT HANDOFF FROM PREVIOUS CHUNK ---
Story Arc So Far: {h.story_arc_so_far}
Narrative Thread: {h.narrative_thread}
Pacing Momentum: {h.pacing_momentum}
Established Hooks: {", ".join(h.established_hooks) if h.established_hooks else "None"}
Last Emotional Peak: {h.last_emotional_peak}
Chapter: {h.chapter} of {h.chapters_total}
--------------------------------------------------"""
        return prompt

    def to_dict(self) -> Dict[str, Any]:
        return self.current_handoff.model_dump()
