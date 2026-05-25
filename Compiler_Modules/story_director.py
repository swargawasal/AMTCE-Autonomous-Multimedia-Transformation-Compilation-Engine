import os
import json
import logging
import tempfile
import time
from typing import List, Dict

logger = logging.getLogger("story_director")

class StoryDirector:
    """
    Creates Educational Reused-Content monetizable compilations by injecting
    an overarching narrative documentary script and sorting clips by energy.
    """
    
    def __init__(self):
        self.enabled = os.getenv("ENABLE_STORY_COMPILATION", "yes").lower() == "yes"
        self.sort_by_energy = os.getenv("STORY_SORT_BY_ENERGY", "yes").lower() == "yes"
        self.words_per_clip = int(os.getenv("STORY_NARRATION_WORDS_PER_CLIP", 40))

    def _extract_sidecar(self, video_path: str) -> Dict:
        """Reads the adjacent .json file for a clip if it exists."""
        base, _ = os.path.splitext(video_path)
        json_path = f"{base}.json"
        
        metadata = {
            "path": video_path,
            "filename": os.path.basename(video_path),
            "title": "Unknown Clip",
            "fusion_score": 0.5,
            "item_name": "General Look",
            "narrative": ""
        }
        
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    metadata["title"] = data.get("editorial_title", data.get("title", metadata["title"]))
                    metadata["fusion_score"] = float(data.get("brain_analysis", {}).get("fusion_score", 0.5))
                    
                    fashion = data.get("brain_analysis", {}).get("fashion_data", {})
                    metadata["item_name"] = fashion.get("outfit_type", metadata["item_name"])
                    
                    # Grab public caption or narrative
                    metadata["narrative"] = data.get("brain_analysis", {}).get("final_caption", "")
            except Exception as e:
                logger.warning(f"Failed to read sidecar for {os.path.basename(video_path)}: {e}")
                
        return metadata

    def _generate_documentary_script(self, items: List[Dict]) -> str:
        """Creates a single overarching script using the Gemini router."""
        try:
            from Intelligence_Modules.gemini_governor import gemini_router
            
            if not gemini_router:
                logger.warning("Gemini router unavailable for story script.")
                return ""
            
            # Build Context
            context_blocks = []
            for idx, item in enumerate(items):
                context_blocks.append(
                    f"Clip {idx+1}: {item['title']}\n"
                    f"Subject/Item: {item['item_name']}\n"
                    f"Original Context: {item['narrative']}\n"
                )
                
            prompt = [
                "SYSTEM ROLE:",
                "You are an elite editorial narrator for a high-end fashion and lifestyle documentary channel.",
                "Your objective is to write the voiceover script for a compilation video of several short clips.",
                "The voiceover must have an overarching narrative that connects these distinct clips chronologically into a single cohesive story.",
                "It must provide EDUCATIONAL value (e.g. fashion history, styling techniques, industry secrets, or storytelling).",
                "",
                "RULES:",
                "1. DO NOT include speaker tags or stage directions like [Narrator] or (Pause). Output strictly the spoken text.",
                "2. Create a strong, curiosity-driven introductory sentence.",
                f"3. Keep the entire script around {len(items) * self.words_per_clip} words.",
                "4. Flow naturally from one clip's theme to the next without saying 'In the next clip'.",
                "5. End with a subtle call to action: 'Check the link in the description to explore these styles.'",
                "",
                "CONTEXT OF CLIPS (IN ORDER OF APPEARANCE):",
                "\n".join(context_blocks)
            ]
            
            script = gemini_router.generate(
                task_type="copywriter",
                prompt="\n".join(prompt),
                module_name="story_director"
            )
            
            return script.strip() if script else ""
        except Exception as e:
            logger.error(f"Story script generation failed: {e}")
            return ""

    def build_story_compilation(self, video_paths: List[str], output_path: str, **kwargs) -> str:
        """
        Main pipeline element.
        Transforms raw concat compilation into a documentary.
        """
        if not video_paths:
            return None
            
        if not self.enabled:
            logger.info("🎬 [STORY_DIRECTOR] Engine disabled. Falling back to Basic Batch Concat.")
            from Compiler_Modules.orchestrator import compile_batch
            return compile_batch(video_paths, output_path)

        logger.info(f"🎬 [STORY_DIRECTOR] Building storytelling compilation with {len(video_paths)} clips.")
        
        # 1. Harvest & Sort
        items = [self._extract_sidecar(vp) for vp in video_paths]
        
        if self.sort_by_energy and len(items) >= 3:
            logger.info("🎬 [STORY_DIRECTOR] Sorting clips by fusion energy curve.")
            # Build an energy curve: Middle -> Lowest -> Highest (Climax)
            items = sorted(items, key=lambda x: x["fusion_score"])
            mid_idx = len(items) // 2
            peak = items.pop()
            intro = items.pop(mid_idx)
            items = [intro] + items + [peak]
                
        sorted_paths = [item["path"] for item in items]
        
        # 2. Write Script
        script = self._generate_documentary_script(items)
        audio_vo_path = None
        
        # 3. Generate Audio
        if script:
            try:
                logger.info("🎬 [STORY_DIRECTOR] Generated Documentary Script. Rendering TTS.")
                # Save script log to disk for visibility
                log_path = output_path.replace(".mp4", "_script.txt")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(script)

                from Audio_Modules.voiceover import voice_engine
                
                # Render to a temp file
                tmp_vo = os.path.join(tempfile.gettempdir(), f"vo_{int(time.time())}.mp3")
                raw_vo = voice_engine.generate_voiceover(
                    script_text=script,
                    output_file=tmp_vo
                )
                
                # If generate_voiceover returns tuple (success, path) or just path based on implementation
                if isinstance(raw_vo, tuple) and len(raw_vo) == 2:
                    success, final_path = raw_vo
                    if success and final_path:
                         audio_vo_path = final_path
                elif isinstance(raw_vo, str) and os.path.exists(raw_vo):
                    audio_vo_path = raw_vo
                elif raw_vo is True and os.path.exists(tmp_vo):
                    audio_vo_path = tmp_vo

            except Exception as e:
                logger.warning(f"Voiceover generation failed in StoryDirector: {e}")
        else:
            logger.info("🎬 [STORY_DIRECTOR] No script generated. Proceeding as silent/BGM only.")

        # 4. Assembly Phase
        from Compiler_Modules.compiler import assemble_narrated_compilation
        
        # Find BGM
        bgm_path = None
        try:
            # We'll try to pick a random track since orchestrator music matching requires beats
            music_dir = os.environ.get("BGM_DIR", "music")
            if os.path.exists(music_dir):
                import glob
                import random
                tracks = glob.glob(os.path.join(music_dir, "*.mp3"))
                if tracks:
                    bgm_path = random.choice(tracks)
        except Exception as e:
            logger.warning(f"BGM selection failed: {e}")

        logger.info(f"🎬 [STORY_DIRECTOR] Firing Assembly Pipeline...")
        # assemble_narrated_compilation safely falls back if VO/BGM is missing.
        success = assemble_narrated_compilation(
            video_paths=sorted_paths,
            voiceover_path=audio_vo_path,
            bgm_path=bgm_path,
            output_path=output_path
        )
        
        return output_path if success else None

# Singleton instance
story_director = StoryDirector()
