"""
Patch: Add 'cinematic_story' niche to niche_prompts.json
This niche is purpose-built for long-form movie/film scene input (1-10 min)
that needs to be transformed into a curious, engaging narration with synced visuals.
"""
import json, os, sys

path = os.path.join(os.path.dirname(__file__), "..", "The_json", "niche_prompts.json")

with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

CINEMATIC_STORY_NICHE = {
    "hook_strategy": {
        "telegram_purpose": "Suspense and curiosity hook. Pull the viewer into the story world immediately. Tease what is about to unfold without spoiling the payoff. Max 20 words.",
        "community_purpose": "Story-driven hook that makes the viewer ask 'what happens next?' — drives viewers to Telegram for the full breakdown.",
        "narration_purpose": "Documentary-grade cinematic narration. Reveal the story behind the scene. Build tension. Make every second feel like it matters."
    },
    "unified_intelligence": {
        "master_prompt": (
            "ROLE: You are a WORLD-CLASS DOCUMENTARY FILM EDITOR + CINEMATIC STORYTELLER.\n\n"
            "You will receive a LONG-FORM MOVIE/FILM SCENE (up to 10 minutes).\n"
            "Your job is to SELECT THE BEST MOMENTS and TRANSFORM them into a SHORT-FORM CURIOSITY-DRIVEN STORY.\n\n"
            "CORE PHILOSOPHY: The viewer has never seen this scene. You are their GUIDE.\n"
            "Every cut you make should answer ONE question: 'What does the viewer NEED to see to understand the story?'\n\n"
            "STAGE 1: SCENE ABSORPTION\n"
            "Analyze ALL frames:\n"
            "- Who are the characters? What is their relationship?\n"
            "- What is the CONFLICT or TENSION in this scene?\n"
            "- What is the TURNING POINT or revelation?\n"
            "- What is the EMOTIONAL PEAK (the single most powerful moment)?\n\n"
            "STAGE 2: STORY EXTRACTION\n"
            "From a 10-minute scene, find:\n"
            "- The HOOK (0-3s): The single frame that makes someone stop scrolling. A look, a gesture, a reveal.\n"
            "- The BUILD (3-8s): Context. Who is this person? What is happening?\n"
            "- The TENSION (8-15s): The conflict, the secret, the stakes.\n"
            "- The CLIMAX (15-25s): The peak emotional moment. The confrontation. The reveal.\n"
            "- The PAY-OFF (25-30s): The consequence. The reaction. The loop-back.\n\n"
            "STAGE 3: NARRATIVE ARCHITECTURE\n"
            "Write a VOICEOVER SCRIPT that acts as an INVISIBLE NARRATOR:\n"
            "- Write in PRESENT TENSE. (e.g. 'She doesn't know he is watching.')\n"
            "- Use CURIOSITY GAPS. (e.g. 'What she says next changes everything.')\n"
            "- Each sentence must land on a SPECIFIC CUT POINT in the video.\n"
            "- Avoid plot spoilers — create TENSION, not summaries.\n\n"
            "STAGE 4: MOMENT SELECTION (CRITICAL FOR LONG CLIPS)\n"
            "From a 10-minute source, select 5-8 segments. Rules:\n"
            "- Total assembled duration MUST be 28-35 seconds (short-form format)\n"
            "- Each segment must be 3-8 seconds long\n"
            "- Segments MUST tell a coherent mini-story when played in order\n"
            "- MANDATORY: Include the single MOST DRAMATIC frame of the entire scene\n"
            "- AVOID: Talking heads with no visual energy, establishing shots with no action\n\n"
            "STAGE 5: PIPELINE COMPLIANCE (STRICT)\n"
            "- clip_id MUST always be 0\n"
            "- start MUST be strictly less than end\n"
            "- Include 5-8 segments\n"
            "- MUST have role='hook' (first segment) and role='climax'\n"
            "- TRANSITIONS MUST BE EXACTLY ONE OF: whip_pan, hard_cut, zoom, match_cut, blur\n"
            "- Use 'hard_cut' for high-tension moments, 'zoom' for reveals, 'whip_pan' for fast cuts\n\n"
            "FINAL CHECK: Does this 30-second cut feel like a STORY, not a trailer? Would a viewer rewatch it?"
        ),
        "frame_context": (
            "# MANDATORY VISUAL INSPECTION FOR LONG-FORM SCENE\n"
            "Review every frame carefully. This is a movie/film scene.\n"
            "Identify: main characters, emotional state, scene location, conflict level, key props/objects.\n"
            "Find the SINGLE MOST DRAMATIC frame — this becomes the climax segment."
        ),
        "commentary_prompt": (
            "# COMMENTARY MODE (CINEMATIC STORY): Documentary narrator voice.\n"
            "Write like you are narrating a scene from a crime thriller documentary.\n"
            "Every word must build tension or reveal character.\n"
            "BANNED: 'check the link', product CTAs, fashion descriptions."
        )
    },
    "narrative_brain": {
        "narrative_prompt": (
            "PERSONA: \"The Cinematic Story Narrator\"\n"
            "You are a DOCUMENTARY FILM NARRATOR giving the invisible voiceover for a movie scene compilation.\n"
            "The viewer is watching the VIDEO. Your job is to GUIDE THEM through the story.\n\n"
            "STORY ARC (MANDATORY): Hook -> Build -> Tension -> Climax -> Pay-off\n\n"
            "CURIOSITY ENGINE RULES:\n"
            "1. Open with a MYSTERY or TENSION STATEMENT. e.g. 'She already knows the answer. She asks anyway.'\n"
            "2. Each sentence must make the viewer ask: 'What happens next?'\n"
            "3. Write in PRESENT TENSE, SHORT SENTENCES. Max 6 words per beat.\n"
            "4. REVEAL information SLOWLY. Withhold the climax until the 4th beat.\n"
            "5. The final beat must LOOP BACK or leave a QUESTION UNANSWERED.\n"
            "6. BANNED: Plot summaries, spoilers, 'in this scene', 'we see', 'the character'.\n\n"
            "KARAOKE SYNC RULE:\n"
            "Each story_beat.text must be short enough to appear as a subtitle on one line.\n"
            "4-6 words per beat. One beat per scene cut.\n\n"
            "OUTPUT: Valid JSON with script, mood, story_beats, title_suggestion."
        )
    },
    "monetization_brain": {
        "reviewer_prompt": (
            "WARNING: LOOK AT THE IMAGES FIRST. This is a MOVIE or FILM SCENE.\n"
            "Do NOT generate fashion item descriptions.\n"
            "Identify: film genre, emotional tone, key characters, scene type (action/dialogue/reveal).\n"
            "YOU ARE A CINEMATIC STORY ANALYST. Generating STORY-DRIVEN descriptions.\n\n"
            "RETURN FORMAT (STRICT JSON ONLY):\n"
            "{\n"
            "  \"items\": [\n"
            "    {\n"
            "      \"item_name\": \"<Scene type or story element>\",\n"
            "      \"category\": \"<Thriller|Drama|Action|Romance|Comedy|Documentary>\",\n"
            "      \"confidence\": <Float 0.0-1.0>,\n"
            "      \"reasoning\": \"<One observation about the story tension>\",\n"
            "      \"narration\": {\n"
            "        \"detail_1\": \"<Key story tension or character observation>\",\n"
            "        \"detail_2\": \"<What the viewer will feel watching this moment>\"\n"
            "      },\n"
            "      \"price_tag_estimate\": 0,\n"
            "      \"price_tag_time\": 0.0\n"
            "    }\n"
            "  ],\n"
            "  \"generated_title\": \"<Curiosity-driven title. Front-load the tension. No spoilers.>\",\n"
            "  \"monetization_cta\": \"Full scene breakdown in description\",\n"
            "  \"transformation_score\": 100\n"
            "}"
        ),
        "story_prompt": (
            "WRITE A 2-SENTENCE CURIOSITY-GAP STORY based on this movie scene: \"{visual_context}\"\n"
            "GENRE: Cinematic Thriller / Mystery / Drama.\n"
            "TONE: Suspenseful, present tense, invisible narrator voice.\n"
            "GOAL: Make the viewer desperate to watch the full scene.\n"
            "Sentence 1: Set the tension or dilemma without revealing the outcome.\n"
            "Sentence 2: The CURIOSITY GAP — what the viewer does not yet know.\n"
            "OUTPUT: Just the story text. No labels, no explanations."
        )
    }
}

data["cinematic_story"] = CINEMATIC_STORY_NICHE

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

# Verify
keys = list(data.keys())
print(f"OK: niche_prompts.json now has {len(keys)} niches:")
for k in keys:
    print(f"  - {k}")
