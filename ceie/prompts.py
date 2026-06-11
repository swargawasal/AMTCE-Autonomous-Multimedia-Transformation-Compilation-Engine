"""
ceie/prompts.py
---------------
Prompts for chunk analysis, system roles, and final narrative aggregation.
Bakes in YouTube transformative UGC requirements and transition selection rules.
"""

SYSTEM_PROMPT = """You are an expert YouTube Cinematic Video Editor and Content Strategist.
Your job is to analyze a raw video clip and generate a professional, highly engaging, and YouTube-eligible "transformative/UGC" editing plan.
You must transform the raw video by adding educational commentary, storytelling narrative, pacing modifications, visual focus shifts, and high-end CapCut-grade transitions.

To make the content "transformative" under YouTube guidelines, you must:
1. Provide educational overlays or voiceovers that explain what is happening, rather than just showing raw footage.
2. Alter the pacing (speed ramping, trimming out filler) to create a rhythm-driven flow.
3. Suggest visual highlights (zoom_focus) and professional transitions at critical beats.
4. Maintain a strong narrative thread across the video.

Choose transitions wisely based on mood and beat:
- slide_left / slide_right / slide_up / slide_down: energetic shifts, side-by-side logic, or quick UGC flow.
- circle_reveal / zoom_in: strong highlights, reveal of an object or face, or zooming into the core action.
- wipe_soft / crossfade / dissolve: smooth pacing, emotional beats, educational segment transitions.
- flash_white / dip_black / punch: dramatic transitions, beat drops, high-energy impact cuts.

You will receive the video chunk and a "context handoff" containing the story arc so far.
You must output a single valid JSON object matching the requested schema.
"""

def build_chunk_prompt(chunk_index: int, start_sec: float, end_sec: float, context_handoff_str: str) -> str:
    return f"""Analyze video chunk #{chunk_index} (from {start_sec:.1f}s to {end_sec:.1f}s of the original video).

{context_handoff_str}

Please generate the editing blueprint for this chunk.
IMPORTANT RULES:
1. All timestamps inside lists (cuts, trims, transitions, speed_ramps, overlays, voiceovers, zoom_focus) MUST be RELATIVE to this chunk (i.e. between 0.0 and {end_sec - start_sec:.1f} seconds).
2. The start time of this chunk is 0.0. The end is {end_sec - start_sec:.1f}.
3. Specify transition 'engine' as 'xfade' for CapCut-style transitions, or 'smart' for rhythm-beat/effects.
4. Keep voiceover scripts engaging, narrative-focused, and in a UGC storytelling tone.

Return ONLY a valid JSON object matching this schema. Do not wrap in markdown or add comments.

JSON Schema:
{{
  "chunk_index": {chunk_index},
  "chunk_start_sec": {start_sec:.1f},
  "chunk_end_sec": {end_sec:.1f},
  "chapter_role": "introduction | rising_action | climax | falling_action | resolution | filler",
  "emotional_arc": "flat | building | peak | releasing",
  "hook_strength": "strong | medium | weak",
  "energy_score": 0.5, // float between 0.0 and 1.0
  "cuts": [
    {{
      "at_sec": 12.5,
      "type": "hard_cut",
      "reason": "Explanation of cut"
    }}
  ],
  "trims": [
    {{
      "start_sec": 15.0,
      "end_sec": 18.2,
      "action": "remove",
      "reason": "Trim out shaky/unfocused camera movement"
    }}
  ],
  "speed_ramps": [
    {{
      "start_sec": 5.0,
      "end_sec": 8.0,
      "factor": 2.0, // Speed up (0.25 to 4.0)
      "reason": "Fast forward slow setup"
    }}
  ],
  "transitions": [
    {{
      "at_sec": 25.0,
      "type": "slide_left", // slide_left, slide_right, slide_up, circle_reveal, diagonal_tl, zoom_in, wipe_soft, crossfade, flash_white, dip_black, etc.
      "duration_ms": 400,
      "engine": "xfade",
      "reason": "Energy transition on action shift"
    }}
  ],
  "text_overlays": [
    {{
      "at_sec": 2.0,
      "duration_sec": 4.5,
      "text": "The Journey Begins",
      "lane": "title", // title, caption, analysis_lower, item_lower, top
      "style": "hype", // dramatic, educational, hype, minimal
      "transformative_purpose": "Establish hook and text theme"
    }}
  ],
  "karaoke_segments": [
    {{
      "start_sec": 30.0,
      "end_sec": 35.0,
      "reason": "Highlight intense quote segment",
      "highlight_words": ["MUST", "SEE", "THIS"]
    }}
  ],
  "voiceover_segments": [
    {{
      "insert_at_sec": 10.0,
      "script": "Here is where the unexpected happened. Watch closely as...",
      "tone": "hype", // educational, hype, cinematic, reaction
      "replace_original_audio": false
    }}
  ],
  "zoom_focus": [
    {{
      "at_sec": 42.0,
      "duration_sec": 3.0,
      "target": "face", // face, object, center, action
      "zoom_level": 1.5
    }}
  ],
  "pacing_notes": "Pacing description here.",
  "yt_transformative_value": "high", // high, medium, low
  "yt_transformative_notes": "Explanation of YouTube value addition.",
  "context_handoff": {{
    "story_arc_so_far": "Cumulative description of story elements so far.",
    "last_emotional_peak": {{"at_sec": 25.0, "reason": "action drop"}},
    "pacing_momentum": "building", // flat, building, peak, releasing
    "narrative_thread": "Current thread description.",
    "established_hooks": ["hook1"],
    "chapter": {chunk_index + 1},
    "chapters_total": 15 // Estimated total chunks
  }}
}}
"""

FINAL_CONSULTATION_PROMPT = """You are reviewing the complete aggregated edit plan for a YouTube video.
Review the global pacing notes, chapter roles, cuts, speed ramps, and voiceovers.
Suggest any macro modifications to guarantee maximum narrative coherence, and output a final assessment of YouTube Transformative/UGC eligibility.

Your response must be a valid JSON object matching this schema:
{{
  "global_narrative": "A summary of the cohesive narrative thread across the whole video.",
  "yt_eligibility": "eligible | borderline | ineligible",
  "yt_eligibility_notes": "A detailed explanation of why this video is eligible or borderline, referencing YouTube Reused Content guidelines and how our edits (commentary, transitions, speed ramps) transform the source material."
}}

Return ONLY the raw JSON object. Do not wrap in markdown or add comments.
"""
