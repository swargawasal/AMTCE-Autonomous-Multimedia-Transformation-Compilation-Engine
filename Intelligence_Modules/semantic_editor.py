import os
import json
import logging
from typing import Dict, List, Optional
from Intelligence_Modules.gemini_governor import gemini_router

logger = logging.getLogger("semantic_editor")

def analyze_transcript_for_cuts(transcript_data: Dict) -> Optional[List[Dict]]:
    """
    Takes transcription data (with word-level timestamps) and uses Gemini 
    to identify "bad takes", silences, and filler words.
    Returns a list of dicts representing ranges to KEEP: [{"start": 0.0, "end": 5.2}, ...]
    """
    if not transcript_data or "words" not in transcript_data:
        logger.error("❌ Invalid transcript data provided to Semantic Editor.")
        return None

    words = transcript_data["words"]
    if not words:
        return None

    # Format the transcript for Gemini so it sees the timestamps
    # To save tokens and avoid confusing Gemini, we group into short sentences/chunks
    formatted_transcript = ""
    current_chunk = ""
    chunk_start = words[0]["start"]
    
    for i, w in enumerate(words):
        current_chunk += w["word"] + " "
        # Add a chunk line every 10 words or at punctuation
        if (i + 1) % 10 == 0 or w["word"].strip()[-1:] in ".!?":
            formatted_transcript += f"[{chunk_start:.2f} - {w['end']:.2f}] {current_chunk.strip()}\n"
            if i + 1 < len(words):
                chunk_start = words[i+1]["start"]
                current_chunk = ""
                
    if current_chunk:
        formatted_transcript += f"[{chunk_start:.2f} - {words[-1]['end']:.2f}] {current_chunk.strip()}\n"

    total_duration = words[-1]["end"]

    prompt = f"""
    SYSTEM ROLE: You are an expert video editor. Your job is to analyze a raw transcript 
    of a 'talking head' video and identify 'bad takes', false starts, long silences, and filler words.
    
    A "bad take" is when the speaker abandons a sentence and starts over. 
    "Filler words" are excessive 'ums', 'uhs', etc.
    
    TRANSCRIPT WITH TIMESTAMPS:
    {formatted_transcript}
    
    Total Audio Duration: {total_duration:.2f} seconds.
    
    INSTRUCTIONS:
    Identify the valid segments of speech that should be KEPT. Ignore the bad takes and fillers.
    You must return a JSON list of objects representing the timestamp ranges to KEEP.
    Example:
    [
        {{"start": 0.5, "end": 4.2}},
        {{"start": 6.1, "end": 12.0}}
    ]
    
    Return ONLY valid JSON. Do not include markdown blocks or any other text.
    """

    logger.info("🧠 Sending transcript to Gemini for Semantic Editing...")
    
    try:
        response_text = gemini_router.generate(
            task_type="semantic_edit",
            prompt=prompt,
            module_name="semantic_editor",
            gen_config={"response_mime_type": "application/json"}
        )
        
        if not response_text:
            logger.error("❌ Gemini returned empty response for semantic edit.")
            return None
            
        # Clean potential markdown
        cleaned = response_text.replace("```json", "").replace("```", "").strip()
        keep_ranges = json.loads(cleaned)
        
        # Validate structure
        valid_ranges = []
        for r in keep_ranges:
            if "start" in r and "end" in r:
                valid_ranges.append({"start": float(r["start"]), "end": float(r["end"])})
                
        logger.info(f"✅ Semantic Editor identified {len(valid_ranges)} clips to KEEP.")
        return valid_ranges

    except Exception as e:
        logger.error(f"❌ Semantic Editor failed: {e}")
        return None

