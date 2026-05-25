"""
Narrative Brain Module (Multimodal Director)
--------------------------------------------
Orchestrates the creation of cohesive, documentary-style narratives for compilations.
Uses Gemini Pro Vision (Multimodal) to "see" the thumbnails and "read" the metadata.

Key Features:
1. Asset Matching: Pairs Processed Shorts/{Name}_X.json with assets/snapped_thumbs/{Name}_00X.jpg.
2. Batch Process: Sends clips in batches (e.g., 10) to Gemini to maintain context.
3. Continuity: Passes context between batches to ensure a smooth story.
"""

import glob
import os
import json
import logging
import os
import re
from typing import Dict, List, Optional

from dotenv import load_dotenv
from PIL import Image

from Intelligence_Modules.gemini_governor import gemini_router

# Load credentials
load_dotenv("Credentials/.env", override=True)

logger = logging.getLogger("narrative_brain")

# NARRATIVE PROMPT loaded dynamically from JSON

class NarrativeDirector:
    def __init__(self):
        self.router = gemini_router
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

        if self.gemini_key:
            try:
                # Use Gemini Model from Env (Default: gemini-2.5-flash)
                model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
                logger.info(f"🎬 Narrative Director: ACTIVE ({model_name})")
            except Exception as e:
                logger.error(f"❌ Narrative Brain Init Failed: {e}")

        # Load niche-specific prompts
        self.niche_prompts = {}
        target_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "The_json", "niche_prompts.json")
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                self.niche_prompts = json.load(f)
            logger.info("✅ Loaded niche_prompts.json for narrative_brain")
        except FileNotFoundError:
            logger.warning(f"⚠️ niche_prompts.json not found at {target_path}")
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON Decode error in niche_prompts.json: {e}")

    def find_associated_assets(self, entity_name: str, limit: int = 10) -> List[Dict]:
        """
        Scans filesystem to pair JSON Metadata with Thumbnail Images.
        Matches Processed Shorts/{Name}_X.json <-> assets/snapped_thumbs/{Name}_00X.jpg
        """
        assets = []

        # 1. Normalize Name for Regex
        # "Avneet Kaur" -> "Avneet_kaur" (loosely)
        base_pattern = entity_name.replace(" ", "_").lower()

        json_dir = "Processed Shorts"
        thumb_dir = "assets/snapped_thumbs"

        # Scan JSONs first (Source of Truth)
        all_jsons = glob.glob(os.path.join(json_dir, "*.json"))

        candidates = []

        for j_path in all_jsons:
            fname = os.path.basename(j_path)
            # Filter by entity name (case insensitive partial match)
            if base_pattern in fname.lower() and not fname.endswith(".final.json"):
                # Extract Numeric ID
                # regex: .*_(\d+).json
                match = re.search(r"_(\d+)\.json$", fname)
                if match:
                    seq_id = int(match.group(1))
                    candidates.append(
                        {
                            "id": seq_id,
                            "json_path": j_path,
                            "base_name": fname.replace(f"_{match.group(1)}.json", ""),
                        }
                    )

        # Sort by ID
        candidates.sort(key=lambda x: x["id"])

        # Apply Limit
        candidates = candidates[:limit]

        # 2. Find Matching Thumbnails
        final_pairs = []
        for item in candidates:
            # Expected Thumb: {base_name}_{seq_id:03d}.jpg
            # e.g. Avneet_kaur_001.jpg
            thumb_name = f"{item['base_name']}_{item['id']:03d}.jpg"
            thumb_path = os.path.join(thumb_dir, thumb_name)

            # Load Metadata (Always required)
            meta = {}
            try:
                with open(item["json_path"], "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                logger.warning(
                    f"⚠️ Failed to read metadata for {item['base_name']}: {e}"
                )
                continue  # Cannot proceed without metadata

            # Check Thumbnail
            final_thumb_path = thumb_path if os.path.exists(thumb_path) else None

            if not final_thumb_path:
                logger.warning(
                    f"⚠️ Missing thumbnail for ID {item['id']}: {thumb_path} (Using Metadata Only)"
                )

            final_pairs.append(
                {
                    "id": item["id"],
                    "json": meta,
                    "image_path": final_thumb_path,  # Logic handles None
                    "video_path": meta.get("video_path", ""),
                }
            )

        logger.info(f"✅ Found {len(final_pairs)} matched assets for '{entity_name}'")
        return final_pairs

    def generate_compilation_script(
        self, assets: List[Dict], intelligence_cache=None, niche_category: str = "generic",
        cinematic_plan: dict = None
    ) -> Dict:
        """
        Generates a continuous script from a list of assets.
        Handles batching if list is long.
        cinematic_plan: optional dict from CinematicDurationEngine (used in cinematic_story mode).
        """
        if not assets:
            return {"script": "", "mood": "N/A", "title_suggestion": "No Assets"}
        if not self.model:
            return {
                "script": "Narrative generation unavailable (No AI).",
                "mood": "Error",
                "title_suggestion": "AI Error",
            }

        full_script = []
        all_story_beats = []   # aggregated across batches for rhythm sync
        batch_mood = None      # first non-null mood wins
        batch_size = 10
        prev_context = "Start of compilation."

        for i in range(0, len(assets), batch_size):
            batch = assets[i : i + batch_size]
            logger.info(
                f"🧠 Processing Batch {i // batch_size + 1} ({len(batch)} clips)..."
            )

            # Prepare Multimodal Payload
            payload = []

            # 1. System Prompt (Text) - Unified Intelligence Migration
            univ = self.niche_prompts.get("_universal", {})
            nb_config = self.niche_prompts.get("_narrative_output_format", {})
            
            active_narrative_prompt = univ.get("narrative_prompt", "You are the Semantic Narration Director.")
            master_intel = univ.get("master_prompt", "")
            
            # Domain-specific constraints (e.g. Fashion Full-Outfit rule)
            hook_strategies = self.niche_prompts.get("_hook_strategies", {})
            strategy = hook_strategies.get(niche_category, hook_strategies.get("generic", {}))
            constraints = strategy.get("domain_constraints", "")
            
            # Determine niche configuration
            _cinematic_constraints = ""
            _adaptive_mode = os.getenv("CINEMATIC_ADAPTIVE_MODE", "yes").strip().lower() == "yes"
            if niche_category == "cinematic_story":
                if not _adaptive_mode:
                    _cinematic_constraints += "\n\n⚠️ SYSTEM OVERRIDE: ADAPTIVE MODE OFF. Stick to a 'Thriller Documentary' persona ONLY."
                
            if niche_category == "cinematic_story" and cinematic_plan:
                _bp = cinematic_plan.get("beat_plan", [])
                _bp_str = " | ".join(
                    f"{b['act'].upper()}: {b['beats']} beats ({b['seconds']}s, ~{b['words']} words)"
                    for b in _bp
                )
                _cinematic_constraints = (
                    f"\n\n══ DURATION CONTRACT (MANDATORY) ══\n"
                    f"Target output:   {cinematic_plan.get('output_seconds', 60)}s total\n"
                    f"Total beats:     {cinematic_plan.get('beat_count', 12)} story beats\n"
                    f"Total words:     {cinematic_plan.get('word_target', 80)} words MAX\n"
                    f"Per-act plan:    {_bp_str}\n"
                    f"RULE: Write EXACTLY {cinematic_plan.get('beat_count', 12)} script entries.\n"
                    f"══════════════════════════════════════"
                )

            prompt_text = (
                f"{master_intel}\n\n"
                f"DOMAIN_HINT: {niche_category}\n"
                f"{active_narrative_prompt}\n"
                f"{constraints}{_cinematic_constraints}\n\n"
                f"PREVIOUS CONTEXT:\n{prev_context}\n\n"
                f"OUTPUT FORMAT (JSON ONLY):\n{nb_config.get('json', '{}')}\n"
            )
            payload.append(prompt_text)


            # 2. Add Images & Metadata (Interleaved)
            for file_idx, item in enumerate(batch):
                # Image
                try:
                    img = Image.open(item["image_path"])
                    payload.append(f"--- CLIP {file_idx + 1} ---")
                    payload.append(img)  # The actual PIL Image
                except:
                    payload.append(f"[Missing Image for Clip {file_idx + 1}]")

                # Metadata Summary
                meta = item.get("json", {})
                fashion = (
                    meta.get("brain_analysis", {})
                    .get("fashion_scout", {})
                    .get("outfit_description", "Fashion details unavailable")
                )
                facts = meta.get("brain_analysis", {}).get("visual_facts", [])
                caption = meta.get("caption", "No caption available")

                # [JOURNALIST CONTEXT] - Injecting the "Ghost" Script
                journalist_notes = meta.get("brain_analysis", {}).get(
                    "editorial_script", ""
                )
                if journalist_notes:
                    logger.info(
                        f"📰 Injecting Journalist Context ({len(journalist_notes)} chars) for Clip {file_idx + 1}"
                    )

                # [FASHION_ID] — Pull from intelligence cache fashion_identification block
                fashion_id = meta.get("extensions", {}).get("fashion", {})
                if not fashion_id:
                    # Fallback: check brain_analysis for any fashion_identification block
                    fashion_id = meta.get("brain_analysis", {}).get("fashion_identification", {})

                designer = fashion_id.get("designer_or_brand", "Unknown Designer")
                outfit_type = fashion_id.get("outfit_type", "")
                rarity = fashion_id.get("rarity_status", "")
                technique = fashion_id.get("key_technique", "")
                event = fashion_id.get("collection_or_event", "")
                edu_fact = fashion_id.get("educational_fact", "")
                luxury_tier = fashion_id.get("luxury_tier", "")
                wearer = fashion_id.get("wearer_name", meta.get("title", "Subject"))

                meta_text = f"""
                FASHION IDENTIFICATION (Clip {file_idx + 1}):
                - Wearer: {wearer}
                - Designer / Brand: {designer}
                - Outfit Type: {outfit_type}
                - Collection / Event: {event}
                - Rarity Status: {rarity}
                - Key Craft Technique: {technique}
                - Luxury Tier: {luxury_tier}
                - Educational Fact: {edu_fact}

                SUPPLEMENTARY CONTEXT:
                - Outfit Description: {fashion[:300]}
                - Key Visual Facts: {", ".join(facts[:3])}
                - Journalist Notes: {journalist_notes[:500]}
                - Original Caption: {caption[:200]}
                """
                payload.append(meta_text)

            # 3. Call Gemini — 120s timeout (heavy multimodal: up to 10 images per batch)
            try:
                res_txt = self.router.generate(
                    task_type="master", prompt=payload, module_name="narrative_brain"
                )
                if not res_txt:
                    full_script.append("[Narrative gap]")
                    continue
                resp_text = res_txt.strip()

                logger.info("Gemini Raw Response:")
                logger.info(resp_text)

                # Extract JSON
                match = re.search(r"(\{.*\})", resp_text, re.DOTALL)
                if match:
                    data = json.loads(match.group(1))
                    
                    # --- [NEW] ADAPTIVE PARSER FOR SEMANTIC INTELLIGENCE ---
                    script_data = data.get("script", "")
                    if isinstance(script_data, list):
                        # Handle new structured list of objects
                        script_part = " ".join([item.get("line", "") for item in script_data if isinstance(item, dict)])
                        
                        # Map to story_beats format for the rest of the pipeline
                        beats = []
                        for i, item in enumerate(script_data):
                            if isinstance(item, dict):
                                beats.append({
                                    "beat": i + 1,
                                    "text": item.get("line", ""),
                                    "timestamp": item.get("timestamp_beat", 0.0),
                                    "style": item.get("caption_style", "bold_center"),
                                    "role": item.get("story_beat", "build"),
                                    "emotion": data.get("mood", "Cinematic")
                                })
                    else:
                        # Handle legacy string format
                        script_part = script_data
                        beats = data.get("story_beats", [])

                    full_script.append(script_part)

                    # Capture mood from AI response (don't hardcode)
                    if not batch_mood:
                        batch_mood = data.get("mood", data.get("genre", "Cinematic"))

                    # Aggregate story_beats across batches
                    if beats:
                        # Re-number beats continuously across batches
                        for b in beats:
                            b["beat"] = len(all_story_beats) + 1
                            all_story_beats.append(b)

                    # Update context for next batch
                    prev_context = f"Previous batch ended with: {script_part[-100:]}"
                else:
                    logger.warning(
                        "⚠️ Narrative Brain returned raw text (no JSON). Using raw."
                    )
                    full_script.append(resp_text)

            except Exception as e:
                logger.error(f"❌ Batch Generation Failed: {e}")
                full_script.append(f"[Narrative gap for clips {i}-{i + len(batch)}]")

        final_narrative = " ".join(full_script)
        return {
            "script": final_narrative,
            "mood": batch_mood or "Cinematic",
            "story_beats": all_story_beats,
            "title_suggestion": data.get("title_suggestion", "Visual Story") if 'data' in locals() else "Visual Story",
            "visual_read": data.get("visual_read", "") if 'data' in locals() else "",
            "semantic_context": data.get("semantic_context", "") if 'data' in locals() else "",
            "genre": data.get("genre", "") if 'data' in locals() else "",
            "tone": data.get("tone", "") if 'data' in locals() else "",
            "rhythm": data.get("rhythm", "") if 'data' in locals() else "",
            "voice": data.get("voice", "") if 'data' in locals() else "",
            "caption_overlay_notes": data.get("caption_overlay_notes", "") if 'data' in locals() else "",
        }

    def generate(self, input_path, frames):
        """Standardized legacy entry point."""
        base_name = (
            os.path.basename(input_path).rsplit(".", 1)[0].replace("_", " ").title()
        )
        assets = []
        for f in frames:
            assets.append(
                {
                    "image_path": f,
                    "msg": f"Clip from {base_name}",
                    "json": {
                        "caption": f"Visual of {base_name}",
                        "brain_analysis": {
                            "editorial_script": f"Focusing on {base_name}'s high-end aesthetic."
                        },
                    },
                }
            )
        return self.generate_compilation_script(assets)


# Sentinel
try:
    director = NarrativeDirector()
except Exception as _nd_init_err:
    import logging as _nl

    _nl.getLogger("narrative_brain").warning(
        f"NarrativeDirector init failed: {_nd_init_err}"
    )
    director = None