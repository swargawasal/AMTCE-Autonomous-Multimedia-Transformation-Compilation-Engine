import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import jsonschema
from Intelligence_Modules.gemini_governor import gemini_router
try:
    from claw_vanguard.vanguard_director import VanguardDirector
except ImportError:
    try:
        from Intelligence_Modules.vanguard_director import VanguardDirector
    except ImportError:
        VanguardDirector = None  # optional — only used in fashion niche path
from jsonschema import validate

logger = logging.getLogger("unified_intelligence")


@dataclass
class CoreAnalysis:
    watermarks: List[Dict] = field(default_factory=list)
    quality_score: float = 0.0
    forensic_strategy: Dict[str, Any] = field(default_factory=dict)
    editing_plan: Dict[str, Any] = field(default_factory=dict)
    feature_proposals: Dict[str, Any] = field(default_factory=dict)
    editing_strategy: Dict[str, Any] = field(default_factory=dict)
    transformation_score: float = 0.0
    safety: str = "unknown"


@dataclass
class Extensions:
    fashion: Dict[str, Any] = field(default_factory=dict)
    monetization: Dict[str, Any] = field(default_factory=dict)
    narrative: Dict[str, Any] = field(default_factory=dict)
    hook_variants: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class IntelligenceCache:
    """
    Centralized container for all AI-generated intelligence.
    Nested structure for improved stability and modularity.
    """

    core_analysis: CoreAnalysis = field(default_factory=CoreAnalysis)
    extensions: Extensions = field(default_factory=Extensions)
    raw_data: Dict[str, Any] = field(default_factory=dict)  # [V5] Store raw Gemini JSON here
    content_director: Dict[str, Any] = field(default_factory=dict) # [V5]

    # Metadata
    source_video: str = ""
    processing_time: float = 0.0
    api_calls_made: int = 0
    token_usage: Dict[str, int] = field(
        default_factory=lambda: {"prompt": 0, "completion": 0}
    )


# --- NESTED MASTER SCHEMA ---

# --- FLAT CANONICAL SCHEMA ---

MASTER_SCHEMA = {
    "type": "object",
    "properties": {

        # ─────────────────────────────────────────
        # CORE SIGNAL
        # ─────────────────────────────────────────
        "intent": {"type": "string"},
        "confidence": {"type": "number"},
        "signal_health": {"type": "number"},

        "signal_flags": {
            "type": "object",
            "properties": {
                "emotion_missing": {"type": "boolean"},
                "face_missing": {"type": "boolean"},
                "retention_weak": {"type": "boolean"},
                "fallback_active": {"type": "boolean"},
                "signal_mode": {"type": "string"}
            }
        },

        "signal_quality": {
            "type": "object",
            "properties": {
                "emotion": {"type": "string"},
                "retention": {"type": "string"},
                "final_mode": {"type": "string"}
            }
        },

        # ─────────────────────────────────────────
        # FEATURE CONTROL
        # ─────────────────────────────────────────
        "feature_proposals": {
            "type": "object",
            "properties": {
                "scene_reconstruction": {"type": "boolean"},
                "voiceover_generation": {"type": "boolean"},
                "caption_generation": {"type": "boolean"},
                "price_tag_engine": {"type": "boolean"},
                "music_engine": {"type": "boolean"},
                "smart_crop": {"type": "boolean"},
            }
        },

        # (Removed watermark_present to prevent redundant Gemini analysis)

        # ─────────────────────────────────────────
        # 🧠 UNIVERSAL DOMAIN UNDERSTANDING
        # ─────────────────────────────────────────
        "domain_insights": {
            "type": "object",
            "properties": {
                "primary_subject": {"type": "string"},
                "secondary_subjects": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "context_type": {"type": "string"},
                "environment": {"type": "string"},
                "action_type": {"type": "string"},
                "emotion_detected": {"type": "string"},
                "intensity_level": {"type": "string"},
                "visual_style": {"type": "string"},

                "key_elements": {
                    "type": "array",
                    "items": {"type": "string"}
                },

                "notable_details": {
                    "type": "array",
                    "items": {"type": "string"}
                },

                # 🔥 EXTENSIBLE PER NICHE (REPLACES fashion_identification)
                "domain_specific": {
                    "type": "object",
                    "additionalProperties": True
                }
            }
        },

        # ─────────────────────────────────────────
        # 🧠 HUMAN EDITOR THINKING LAYER
        # ─────────────────────────────────────────
        "editorial_intelligence": {
            "type": "object",
            "properties": {

                "moment_analysis": {
                    "type": "object",
                    "properties": {
                        "peak_moment": {"type": "string"},
                        "moment_type": {"type": "string"},
                        "emotional_peak": {"type": "string"},
                        "attention_trigger": {"type": "string"},
                        "why_it_matters": {"type": "string"}
                    }
                },

                "visual_hierarchy": {
                    "type": "object",
                    "properties": {
                        "primary_focus": {"type": "string"},
                        "secondary_elements": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "ignored_elements": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    }
                },

                "creative_decision": {
                    "type": "object",
                    "properties": {
                        "editing_angle": {"type": "string"},
                        "hook_strategy": {"type": "string"},
                        "pacing_reason": {"type": "string"},
                        "platform_bias": {"type": "string"},
                        "expected_viewer_reaction": {"type": "string"}
                    }
                }
            }
        },

        # ─────────────────────────────────────────
        # 🎬 EDITING STRATEGY
        # ─────────────────────────────────────────
        "editing_strategy": {
            "type": "object",
            "properties": {
                "pacing": {"type": "string"},
                "transition_style": {"type": "string"},
                "hook_time_hint": {"type": "number"}
            }
        },

        "editing_modifiers": {
            "type": "object",
            "properties": {
                "cut_bias": {"type": "string", "enum": ["aggressive", "steady", "minimal"]},
                "transition_bias": {"type": "string", "enum": ["dynamic", "smooth", "none"]},
                "intensity_curve": {"type": "string", "enum": ["rising", "constant", "pulsating"]},
                "hook_emphasis": {"type": "number"}
            }
        },

        # ─────────────────────────────────────────
        # 🎞️ SEGMENT EXECUTION (CORE ENGINE)
        # ─────────────────────────────────────────
        "edited_segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "integer"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "role": {"type": "string"},
                    "transition": {
                        "type": "string",
                        "enum": ["whip_pan", "hard_cut", "zoom", "blur", "match_cut"]
                    },
                    "reason": {"type": "string"},
                    "trim_tightness": {
                        "type": "string",
                        "enum": ["tight", "medium", "wide"]
                    },

                    # 🔥 NEW (INTELLIGENCE-DRIVEN CUTS)
                    "emotion_score": {"type": "number"},
                    "importance_score": {"type": "number"}
                },
                "required": ["start", "end", "role", "transition", "reason"]
            }
        },

        # ─────────────────────────────────────────
        # 🧾 EDITING PLAN (LIGHT STRUCTURE)
        # ─────────────────────────────────────────
        "editing_plan": {
            "type": "object",
            "properties": {
                "mode": {"type": "string"},
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "clip_id": {"type": "integer"},
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "reason": {"type": "string"}
                        }
                    }
                }
            }
        },

        # ─────────────────────────────────────────
        # 🎯 CONTENT DIRECTOR (OUTPUT LAYER)
        # ─────────────────────────────────────────
        "content_director": {
            "type": "object",
            "properties": {
                "visual_theme": {"type": "string"},
                "caption_candidates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 3,
                    "maxItems": 3
                },
                "tone": {"type": "string"},
                "viewer_hook": {"type": "string"},

                "hook_variations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2
                },

                "visual_event": {"type": "string"},
                "viewer_attention": {"type": "string"},
                "viral_angle": {"type": "string"}
            }
        },

        # ─────────────────────────────────────────
        # 🗣️ OUTPUTS
        # ─────────────────────────────────────────
        "editorial_script": {"type": "string"},

        "overlay_data": {
            "type": ["object", "null"],
            "properties": {
                "brand_text": {"type": ["string", "null"]},
                "trend_text": {"type": ["string", "null"]},
                "context_text": {"type": ["string", "null"]},
                "item_name": {"type": ["string", "null"]},
                "price_tag": {"type": ["string", "null"]},
                "price_tag_time": {"type": ["number", "null"]}
            }
        },

        # ─────────────────────────────────────────
        # 🛡️ SAFETY
        # ─────────────────────────────────────────
        "safety": {
            "type": "object",
            "properties": {
                "classification": {"type": "string"},
                "monetization_safe": {"type": "boolean"}
            }
        },

        # ─────────────────────────────────────────
        # 📊 FINAL EVALUATION
        # ─────────────────────────────────────────
        "transformation_score": {"type": "number"},

        "hook_analysis": {"type": "string"},
        "climax_validation": {"type": "string"},
        "attention_flow": {"type": "string"},
        "final_verdict": {"type": "string"},

        "status": {"type": "string"},
        "reason": {"type": "string"}
    },

    # ⚠️ KEEP REQUIRED MINIMAL TO AVOID BREAKING PIPELINE
    "required": [
        "intent",
        "feature_proposals",
        "watermark_present",
        "edited_segments"
    ]
}

# Niche prompts are loaded dynamically from JSON in the class init


class UnifiedIntelligence:
    def __init__(self):
        self.router = gemini_router
        self.cache_dir = "intelligence_cache"
        os.makedirs(self.cache_dir, exist_ok=True)
        # Model is now managed by gemini_router
        self.model = None

        # Load niche-specific prompts
        self.niche_prompts = {}
        target_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "The_json", "niche_prompts.json")
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                self.niche_prompts = json.load(f)
            logger.info("✅ Loaded niche_prompts.json for unified_intelligence")
        except FileNotFoundError:
            logger.warning(f"⚠️ niche_prompts.json not found at {target_path}, falling back to hardcoded defaults")
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON Decode error in niche_prompts.json: {e}")

    def validate_schema(self, data: Dict, schema: Dict) -> bool:
        try:
            validate(instance=data, schema=schema)
            return True
        except jsonschema.exceptions.ValidationError as e:
            logger.warning(f"❌ Schema Validation Failed: {e.message}")
            return False

    def _get_video_hash(self, video_path: str) -> str:
        try:
            stats = os.stat(video_path)
            raw = f"{video_path}_{stats.st_size}_{stats.st_mtime}"
            return hashlib.sha256(raw.encode()).hexdigest()
        except Exception:
            return hashlib.sha256(video_path.encode()).hexdigest()

    def _compress_frames(self, frames: List[Any], target_count: int = 24) -> List[Any]:
        if not frames:
            return []
        if len(frames) <= target_count:
            return frames
        indices = [
            int(i * (len(frames) - 1) / (target_count - 1)) for i in range(target_count)
        ]
        return [frames[i] for i in indices]

    def _derive_from_rag(self, editing_strategy: Dict) -> Dict:
        """Fallback: Derive editing modifiers from strategy heuristics."""
        return {
            "cut_bias": "aggressive" if editing_strategy.get("pacing") == "fast" else "steady",
            "transition_bias": "dynamic" if editing_strategy.get("pacing") == "fast" else "smooth",
            "intensity_curve": "rising",
            "hook_emphasis": 0.8
        }

    def _generate_simple_local(self) -> List[str]:
        """Fallback: Generate simple outfit-name style captions locally (educational format)."""
        return ["Luxury Couture Ensemble", "Designer Heritage Piece", "Bespoke Artisan Creation"]

    def normalize_master_schema(self, data: Dict) -> Dict:
        """PHASE 2 & 3 — Safe Schema Normalizer with Fallback Recovery."""

        def safe_dict(val):
            return val if isinstance(val, dict) else {}

        def safe_float(value, default=0.0):
            try:
                return float(value)
            except:
                return default

        try:
            core = safe_dict(data.get("core_analysis"))
            forensic = safe_dict(core.get("forensic"))

            # 1. Intent lookup (safe)
            intent = (
                data.get("intent")
                or forensic.get("intent")
                or "unknown"
            )

            # 2. Watermark lookup (safe)
            watermark_present = (
                data.get("watermark_present")
                or safe_dict(data.get("watermark")).get("present")
                or safe_dict(core.get("watermark")).get("present")
                or False
            )

            # 3. Editing Plan lookup (safe)
            editing_plan = safe_dict(
                data.get("editing_plan")
                or core.get("editing_plan")
            )

            # 4. Feature Proposals & Editing Strategy lookup (safe)
            feature_proposals = safe_dict(
                data.get("feature_proposals")
                or core.get("feature_proposals")
            )
            editing_strategy = safe_dict(
                data.get("editing_strategy")
                or core.get("editing_strategy")
            )

            # Sanitize numeric fields in editing_strategy (Issue 2)
            for k, v in list(editing_strategy.items()):
                if isinstance(v, str):
                    try:
                        editing_strategy[k] = float(v)
                    except ValueError:
                        editing_strategy[k] = 0.0

            # 5. Content Director & Safety
            content_director = safe_dict(
                data.get("content_director")
                or forensic.get("content_director")
            )
            safety = (
                data.get("safety")
                or forensic.get("safety")
                or core.get("safety_verdict")
                or "unknown"
            )

            # 6. Transformation Score
            transformation_score = (
                data.get("transformation_score")
                or core.get("transformation_score")
                or 0.0
            )

            # --- RESILIENCE FALLBACKS (Architect Specs) ---
            fallback_used = False
            
            # 7. Editing Modifiers Fallback
            editing_modifiers = data.get("editing_modifiers")
            if not editing_modifiers or not isinstance(editing_modifiers, dict):
                editing_modifiers = self._derive_from_rag(editing_strategy)
                fallback_used = True
                logger.info("[MASTER] Fallback used: editing_modifiers derived from RAG-heuristic.")

            # ── [SEGMENT EXTRACTION] Multi-format segment resolution ──────────────────
            # Gemini's lite/flash models return segments under different keys depending
            # on which schema they followed.  We must check ALL known locations:
            #   1. Top-level "edited_segments"          (full schema, rare from lite models)
            #   2. editing_plan["segments"]             (most common — lite + flash models)
            #   3. Top-level "transformed_timeline"     (retry/creative human-editor mode)
            # Without this, EVERY response from lite models gave edited_segments=[] and
            # the Schema Gate discarded all segments, causing Elite Refusal every run.
            _segs_candidates = (
                data.get("edited_segments")
                or editing_plan.get("segments")
                or data.get("transformed_timeline")
                or []
            )
            if not isinstance(_segs_candidates, list):
                _segs_candidates = []
            _segs_candidates = [s for s in _segs_candidates if isinstance(s, dict)]

            if _segs_candidates and not data.get("edited_segments"):
                _src = "editing_plan.segments" if editing_plan.get("segments") else "transformed_timeline"
                logger.info(f"[SEGMENT_RECOVERY] Promoted {len(_segs_candidates)} segments from '{_src}' -> edited_segments")

            # 8. Caption Candidates Fallback
            if "caption_candidates" not in content_director or not content_director["caption_candidates"]:
                _harvested = [s.get("caption") for s in _segs_candidates if s.get("caption")]
                if _harvested:
                    content_director["caption_candidates"] = _harvested
                    logger.info(f"[MASTER] Caption candidates harvested directly from segments ({len(_harvested)} found).")
                else:
                    content_director["caption_candidates"] = self._generate_simple_local()
                    fallback_used = True
                    logger.info("[MASTER] Fallback used: caption_candidates derived locally.")

            # 9. Intent Fallback
            if intent == "unknown":
                intent = "generic_engagement"
                fallback_used = True

            # Issue 2: Cast all targets securely
            if "duration_target" in editing_plan:
                editing_plan["duration_target"] = safe_float(
                    editing_plan["duration_target"]
                )
            if "hook_time_hint" in editing_strategy:
                editing_strategy["hook_time_hint"] = safe_float(
                    editing_strategy["hook_time_hint"], 1.8
                )
            transformation_score = safe_float(transformation_score, 0.0)

            overlay_data = safe_dict(
                data.get("overlay_data")
                or forensic.get("overlay_data")
            )
            if "price_tag_time" in overlay_data:
                overlay_data["price_tag_time"] = safe_float(
                    overlay_data["price_tag_time"], 2.0
                )

            logger.info(f"[MASTER] fallback_used={fallback_used}")

            return {
                "intent": intent,
                "editing_plan": editing_plan,
                "edited_segments": _segs_candidates,
                "feature_proposals": feature_proposals,
                "editing_strategy": editing_strategy,
                "editing_modifiers": editing_modifiers,
                "watermark_present": watermark_present,
                "content_director": content_director,
                "safety": safety,
                "transformation_score": transformation_score,
                "fallback_used": fallback_used
            }
        except Exception as e:
            logger.error(f"❌ [MASTER_SCHEMA_RECOVERY] Mapping Error: {e}")
            return {
                "intent": "generic_engagement",
                "editing_plan": {},
                "feature_proposals": {},
                "editing_strategy": {},
                "editing_modifiers": {"cut_bias": "steady", "transition_bias": "smooth", "intensity_curve": "constant", "hook_emphasis": 0.5},
                "watermark_present": False,
                "content_director": {"caption_candidates": ["Visual rhythm", "Modern pace", "Dynamic flow"]},
                "safety": "unknown",
                "transformation_score": 0.0,
                "fallback_used": True
            }

    def perform_intelligence_cycle(
        self, video_path: str, frames: List[Any], context: Dict
    ) -> Optional[IntelligenceCache]:
        """
        Single Master Call to Gemini 1.5.
        Consolidates architectural and creative analysis into a nested structure.
        Supports multi-clip input: pass clip_count and input_paths in context to
        enable Gemini to generate segments referencing clip_id 0..N-1.
        """
        # [MULTI_CLIP] Build cache key from ALL clip paths so each unique
        # combination gets its own cached result — not just the first clip.
        input_paths = context.get("input_paths", [video_path])
        clip_count = context.get("clip_count", len(input_paths))
        if len(input_paths) > 1:
            combined_raw = "_".join(self._get_video_hash(p) for p in input_paths)
            video_hash = hashlib.sha256(combined_raw.encode()).hexdigest()[:32]
        else:
            video_hash = self._get_video_hash(video_path)
        cache_file = os.path.join(self.cache_dir, f"{video_hash}.json")
        start_time = time.time()

        # 1. Load from Disk Cache
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"💾 Found Intelligence Cache for {video_path}")
                # Reconstruct dataclass from nested dict
                core_data = data.get("core_analysis", {})
                ext_data = data.get("extensions", {})

                cache = IntelligenceCache(
                    core_analysis=CoreAnalysis(**core_data),
                    extensions=Extensions(**ext_data),
                    raw_data=data.get("raw_data", {}),
                    content_director=data.get("content_director", {}),
                    source_video=video_path,
                    processing_time=data.get("processing_time", 0.0),
                    api_calls_made=data.get("api_calls_made", 0),
                )
                return cache
            except Exception as e:
                logger.warning(f"⚠️ Failed to load disk cache: {e}")

        # 2. Prepare Request
        key_frames = self._compress_frames(frames)

        # 0. VANGUARD BYPASS (Agentic Turn Mode)
        # If the niche is 'fashion', we bypass the linear 1-pass intelligence
        # and delegate to the Vanguard Director for a full 4-turn mission loop.
        niche_category = context.get("niche_category", "generic")
        if niche_category.lower() == "fashion" and VanguardDirector is not None:
            logger.info("🚀 [VANGUARD_MISSION_BYPASS] Niche = Fashion. Delegating to Vanguard Director (4-Turn Loop).")
            director = VanguardDirector()
            mission_request = context.get("title", "Fashion Compilation Request")
            # The real VanguardDirector.execute_mission requires input_paths
            _v_input_paths = context.get("input_paths", [video_path])
            _v_output_path = context.get("output_path", None)
            try:
                mission_result = director.execute_mission(
                    niche="fashion",
                    video_request=mission_request,
                    input_paths=_v_input_paths,
                    output_path=_v_output_path,
                )
                # ToolResult has .success; stub _MissionResult also has .success
                if getattr(mission_result, "success", False):
                    logger.info(f"✅ [VANGUARD_MISSION_SUCCESS] Mission: {mission_request} COMPLETED.")
                    cache = IntelligenceCache(source_video=video_path)
                    cache.core_analysis.editing_plan = {"logic": "vanguard_completed", "segments": []}
                    return cache
                else:
                    reason = getattr(mission_result, "reason", getattr(mission_result, "error", "unknown"))
                    logger.error(f"❌ [VANGUARD_MISSION_FAILURE] Mission: {mission_request} FAILED. Reason: {reason}")
                    # Fallback to standard intelligence path below
            except Exception as _vanguard_err:
                logger.error(f"❌ [VANGUARD_MISSION_EXCEPTION] {_vanguard_err} — falling through to standard path.")
        elif niche_category.lower() == "fashion" and VanguardDirector is None:
            logger.warning("⚠️ [VANGUARD_BYPASS_SKIPPED] VanguardDirector module not found — falling through to standard intelligence path.")
        

        # Determine niche category from context (Legacy Path) - Unified Migration
        univ = self.niche_prompts.get("_universal", {})
        hook_strategies = self.niche_prompts.get("_hook_strategies", {})
        
        # Determine the base frame_context to inject
        base_frame_context = univ.get(
            "frame_context", 
            "# VISUAL INSPECTION\nReview the frames and ensure JSON outputs accurately reflect the visual events."
        )
        base_frame_context_with_count = base_frame_context.replace("{frame_count}", str(len(key_frames)))

        # ── [DEEP VISUAL SCRUB] Build enriched per-frame manifest ─────────────────
        # Previously: bare timestamp list  ("Frame @3.8s → ")
        # Now:        per-frame metadata block so Gemini can "scrub" like a human:
        #   Frame @3.83s | motion=0.72 | emotion=0.88 | face=yes
        _frame_timestamps  = context.get("frame_timestamps",  [])  # list of floats
        _frame_manifest    = context.get("frame_manifest",    [])  # list of dicts
        if _frame_timestamps or _frame_manifest:
            visual_lines = []
            if _frame_manifest:
                # Rich manifest: dict per frame with time + signals
                for entry in _frame_manifest:
                    t     = float(entry.get("time",    entry.get("t", 0.0)))
                    mot   = float(entry.get("motion",  0.0))
                    emo   = float(entry.get("emotion", 0.0))
                    face  = "yes" if entry.get("face_present") else "no"
                    expr  = f" expr_delta={entry['expression_change']:.2f}" if entry.get("expression_change") else ""
                    score = f" score={entry['score']:.3f}" if entry.get("score") else ""
                    visual_lines.append(
                        f"  Frame @{t:.2f}s | motion={mot:.2f} | emotion={emo:.2f} | face={face}{score}{expr}"
                    )
            else:
                # Fallback: bare timestamps
                for t in _frame_timestamps:
                    visual_lines.append(f"  Frame @{t:.1f}s")

            visual_context = "\n".join(visual_lines)

            frame_context = (
                f"{base_frame_context_with_count}\n\n"
                f"# FRAME-BY-FRAME MANIFEST ({len(visual_lines)} frames)\n"
                f"(motion/emotion are continuous scores 0–1; face=yes means a person is visible)\n"
                f"{visual_context}\n"
                f"Study these frames carefully. Identify the exact moments of:\n"
                f"  - Highest emotion spike (strongest audience hook)\n"
                f"  - Fastest motion (action / energy peak)\n"
                f"  - Expression changes (face + emotion delta) — cut here!\n"
                f"  - Stillness before a spike (anticipation moment)\n"
                f"Use this analysis to populate edited_segments with precise start/end times."
            )
        else:
            frame_context = base_frame_context_with_count

        logger.info(
            f"📸 [FRAME_CONTEXT] {len(key_frames)} frames | "
            f"timestamps={_frame_timestamps[:4]}{'...' if len(_frame_timestamps) > 4 else ''}"
        )

        reused = context.get("reused", False)
        comm_instr = ""
        if reused:
            base_commentary = univ.get(
                "narrative_prompt", 
                "# COMMENTARY MODE:\nYou are a professional Voiceover Artist narrating the scene.\n\nOUTPUT FORMAT (STRICT JSON):\n{{\n\"voiceover_script\": \"Full narration here...\"\n}}"
            )
            comm_instr = base_commentary.replace("{title}", context.get("title", "Unknown Video")).replace("{word_target}", str(context.get("word_target", 40)))

        # Pull master prompt
        active_master_prompt = univ.get("master_prompt", "ROLE: You are an ELITE HUMAN VIDEO EDITOR.")

        # [MULTI_CLIP] Build a plain-text block that Gemini sees before the schema.
        # Without this, it has no idea other clips exist and always emits clip_id: 0.
        if clip_count > 1:
            valid_ids = list(range(clip_count))
            clip_context = (
                f"# MULTI-CLIP INPUT: {clip_count} source clips are queued.\n"
                f"Frames are labelled intel_clip0_*, intel_clip1_*, intel_clip2_*, etc. "
                f"matching their clip index.\n"
                f"Valid clip_id values you MUST use in editing_plan.segments: {valid_ids}\n"
                f"Distribute segments across ALL {clip_count} clips to create a "
                f"dynamic multi-source edit. Do NOT assign every segment to clip_id 0."
            )
        else:
            clip_context = ""

        # [V5] Timeline Continuity Context
        timeline_context_raw = context.get("timeline_context", [])
        if timeline_context_raw:
            timeline_lines = ["# TIMELINE VISUAL CONTINUITY (MOTION & EMOTION FLOW):"]
            for entry in timeline_context_raw:
                t = entry.get("time", entry.get("t", 0.0))
                desc = entry.get("description", "visual event")
                mot = entry.get("motion", 0.0)
                emo = entry.get("emotion", 0.0)
                timeline_lines.append(f"@{t:.1f}s: {desc} | motion={mot:.2f} | emotion={emo:.2f}")
            timeline_context = "\n".join(timeline_lines)
        else:
            timeline_context = ""

        # [SIGNAL_HEALTH] Inject repair layer outputs into timeline context
        _signal_health = context.get("signal_health", 0.5) 
        _signal_flags = context.get("signal_flags", {})
        _health_line = (
            f"\n# SIGNAL HEALTH REPORT\n"
            f"signal_health={_signal_health:.2f} | "
            f"mode={'FALLBACK' if _signal_health < 0.4 else 'NORMAL'} | "
            f"flags={_signal_flags}\n"
            f"{'⚠️ SIGNAL_WEAKNESS_DETECTED — use fallback reasoning' if _signal_health < 0.4 else '✅ Signals available'}"
        )
        timeline_context = timeline_context + _health_line

        # ── PATCH 13 — SEMANTIC STRENGTH REPORT ─────────────────────────────────────
        _semantic_strength = context.get("semantic_strength", "UNKNOWN")
        _semantic_mean_val = context.get("semantic_mean", 0.5)

        _semantic_report_lines = [
            "\n# SEMANTIC CONTENT REPORT",
            f"semantic_strength={_semantic_strength} | mean_score={_semantic_mean_val:.2f}",
        ]
        if _semantic_strength == "LOW":
            _semantic_report_lines += [
                "⚠️ VISUAL WITHOUT MEANING — most moments lack subject and narrative context.",
                "INSTRUCTION: Prioritise ANY moment with face_present=True or expression_change > 0.",
                "INSTRUCTION: Prefer beats-aligned moments over random motion peaks.",
                "INSTRUCTION: If no semantically meaningful moment exists, return NO_EDIT.",
            ]
        elif _semantic_strength == "MEDIUM":
            _semantic_report_lines += [
                "📊 MIXED CONTENT — some meaningful moments exist. Select from semantic top-50% only.",
                "INSTRUCTION: Do not use bottom-50% semantic moments as hook or climax.",
            ]
        else:  # HIGH
            _semantic_report_lines += [
                "✅ STRONG CONTENT — meaningful subject-present moments available.",
                "INSTRUCTION: Prefer semantic_score > 0.55 for hook and climax roles.",
            ]
        timeline_context = timeline_context + "\n" + "\n".join(_semantic_report_lines)

        # ── PATCH 14 — FLOW REPORT ────────────────────────────────────────────────
        _flow_quality = context.get("flow_quality", "UNKNOWN")
        _flow_score   = float(context.get("flow_score", 0.5))

        _flow_lines = ["\n# NARRATIVE FLOW REPORT",
                       f"flow_quality={_flow_quality} | flow_score={_flow_score:.2f}"]
        if _flow_quality == "LOW":
            _flow_lines += [
                "⚠️ DISCONNECTED SEQUENCE — clips do not form a coherent story.",
                "INSTRUCTION: Prioritise continuity — select from same scene or subject arc.",
                "INSTRUCTION: Ensure energy progression: LOW → MID → HIGH across the edit.",
                "INSTRUCTION: Use cause-effect logic: action → reaction → resolution.",
            ]
        elif _flow_quality == "MEDIUM":
            _flow_lines += [
                "📊 PARTIAL FLOW — story holds but transitions are weak.",
                "INSTRUCTION: Avoid energy drops between consecutive segments.",
            ]
        else:
            _flow_lines.append("✅ STRONG NARRATIVE FLOW — story progression detected.")
        timeline_context = timeline_context + "\n" + "\n".join(_flow_lines)
        # ── Inject candidate moment timestamps ───────────────────────────────
        # Give Gemini the actual timestamps so it CANNOT invent new ones.
        _candidate_moments = context.get("candidate_moments", [])
        _video_duration    = context.get("duration", 0.0)
        if _candidate_moments:
            _ts_lines = []
            for _m in _candidate_moments[:20]:  # Cap to 20 to stay within token budget
                _t   = float(_m.get("time", _m.get("timestamp", 0.0)))
                _sc  = float(_m.get("score", _m.get("rank_base", 0.0)))
                _fp  = _m.get("face_present", False)
                _mot = float(_m.get("motion_intensity", _m.get("motion", 0.0)))
                _str_fp = "true" if _fp else "false"
                _ts_lines.append(
                    f'  {{"time": {_t:.2f}, "score": {_sc:.3f}, "face_present": {_str_fp}, "motion": {_mot:.2f}}}'
                )
            _moments_block = (
                f"\ncandidate_moments_with_scores:\n[\n"
                + ",\n".join(_ts_lines)
                + "\n]\n"
                f"# video_duration={_video_duration:.2f}s | clip_id_must_be=0 | max_segments=5\n"
            )
        else:
            _moments_block = f"\n# video_duration={_video_duration:.2f}s | clip_id=0\n"
        timeline_context = _moments_block + timeline_context

        # ── [TWO-PASS] Inject Pass 1 creative intent into Pass 2 prompt ──────────────
        # If creative_brain already ran (Pass 1), inject its editorial decisions
        # as a directive BEFORE the master prompt.  This transforms Gemini from
        # "pick moments" into "execute THIS story arc I already decided on."
        creative_intent = context.get("creative_intent")
        intent_directive = ""
        if creative_intent and isinstance(creative_intent, dict):
            _ci_theme    = creative_intent.get("narrative_theme", "")
            _ci_hook_t   = creative_intent.get("hook_time", "?")
            _ci_climax_t = creative_intent.get("climax_time", "?")
            _ci_pacing   = creative_intent.get("pacing_style", "rhythm_driven")
            _ci_philos   = creative_intent.get("cut_philosophy", "")
            _ci_hook_str = creative_intent.get("hook_strategy", "")
            _ci_pairs    = creative_intent.get("contrast_pairs", [])
            _ci_avoid    = creative_intent.get("avoid_segments", [])
            _ci_creator  = creative_intent.get("creator_style_note", "")

            _pairs_text = ""
            if _ci_pairs:
                _pair_lines = [
                    f"  ACTION@{p.get('action_time','?'):.2f}s → REACTION@{p.get('reaction_time','?'):.2f}s  ({p.get('label', '')})"
                    for p in _ci_pairs[:4]
                ]
                _pairs_text = "\n# ACTION-REACTION PAIRS (cut between these for maximum impact):\n" + "\n".join(_pair_lines)

            _avoid_text = ""
            if _ci_avoid:
                _avoid_text = "\n# AVOID THESE TIMESTAMPS (boring/repetitive/bad quality):\n  " + ", ".join(f"{t:.2f}s" for t in _ci_avoid)

            _creator_text = f"\n# CREATOR STYLE NOTE: {_ci_creator}" if _ci_creator else ""

            intent_directive = (
                f"\n{'='*60}\n"
                f"# EDITORIAL INTENT (decided in Pass 1 — FOLLOW THIS EXACTLY)\n"
                f"Story: {_ci_theme}\n"
                f"Hook: START at {_ci_hook_t:.2f}s — {_ci_hook_str}\n"
                f"Climax: END near {_ci_climax_t:.2f}s — this MUST be your final segment\n"
                f"Pacing: {_ci_pacing}\n"
                f"Cut philosophy: {_ci_philos}"
                f"{_pairs_text}"
                f"{_avoid_text}"
                f"{_creator_text}\n"
                f"{'='*60}\n"
                f"Execute this intent. Do NOT deviate from the hook/climax times.\n"
            )
            logger.info(
                f"[UNIFIED_INTEL] 🎬 Pass 2 with creative intent injected | "
                f"hook={_ci_hook_t} | climax={_ci_climax_t} | pacing={_ci_pacing} | "
                f"contrast_pairs={len(_ci_pairs)}"
            )

        prompt = (
            f"{intent_directive}"
            f"{active_master_prompt}\n\n"
            f"--- CONTEXT ---\n"
            f"TITLE: {context.get('title', 'Unknown Video')}\n"
            f"{clip_context}\n"
            f"{frame_context}\n"
            f"{timeline_context}\n"
            f"{comm_instr}\n"
        )

        if context.get("retry_hint"):
            prompt += context["retry_hint"]

        cache = IntelligenceCache(source_video=video_path)

        # [FRAME_VALIDATION] Ensure frames are proper PIL Images before sending to Gemini.
        # If frames are file paths (strings), load them. If they're neither PIL Images nor
        # valid paths, drop them and log a warning — silent drops were causing Gemini to
        # answer with zero visual input, producing hallucinated outfit descriptions.
        validated_frames = []
        try:
            from PIL import Image as _PILImage
            for _f in key_frames:
                if isinstance(_f, _PILImage.Image):
                    validated_frames.append(_f)
                elif isinstance(_f, str) and os.path.exists(_f):
                    try:
                        validated_frames.append(_PILImage.open(_f).convert("RGB"))
                    except Exception as _img_e:
                        logger.warning(f"⚠️ [FRAME_VALIDATION] Failed to load frame {_f}: {_img_e}")
                else:
                    logger.warning(f"⚠️ [FRAME_VALIDATION] Dropping invalid frame (type={type(_f).__name__}): {str(_f)[:60]}")
        except ImportError:
            validated_frames = key_frames  # PIL not available, pass through as-is

        if not validated_frames:
            logger.error("❌ [FRAME_VALIDATION] Zero valid frames after validation. Gemini will run TEXT-ONLY — visual output will be unreliable.")
        else:
            logger.info(f"✅ [FRAME_VALIDATION] {len(validated_frames)}/{len(key_frames)} frames validated as PIL Images.")

        key_frames = validated_frames
        logger.info(
            f"🧠 [GEMINI_MASTER_REQUEST] Starting nested analysis for {os.path.basename(video_path)}..."
        )
        master_data = self._call_gemini_with_retry(
            prompt, key_frames, MASTER_SCHEMA, cache
        )

        if not master_data:
            # Phase D — Fail-Safe Re-Prompt
            logger.warning(
                "💾 [MASTER_RETRY_SCHEMA_ENFORCEMENT] Initial response failed. Attempting corrections..."
            )
            correction_msg = (
                prompt
                + "\n\nYour previous response did not follow the required JSON schema. Return ONLY the schema."
            )
            master_data = self._call_gemini_with_retry(
                correction_msg, key_frames, MASTER_SCHEMA, cache
            )

        if not master_data:
            logger.error(
                "❌ [MASTER_FAILURE_RECOVERY] Gemini request failed twice. Using minimal fallback."
            )
            # Return a minimal valid structure to keep pipeline alive
            cache.core_analysis.safety = "unknown"
            cache.core_analysis.quality_score = 0.5
            cache.core_analysis.editing_plan = {
                "logic": "heuristic_fallback",
                "segments": [],
            }
            cache.core_analysis.feature_proposals = {
                "caption_generation": True,
                "voiceover_generation": True,
                "music_engine": True,
            }
            return cache

        # Guard against lists or strings when a dict is expected.
        # Gemini sometimes returns a flat list of segment-like dicts instead of the
        # nested master schema.  Detect that pattern and wrap it properly so that
        # normalize_master_schema receives a valid skeleton — rather than a single
        # segment object which would produce empty editing_plan / feature_proposals.
        if isinstance(master_data, list):
            if len(master_data) > 0 and isinstance(master_data[0], dict):
                first = master_data[0]
                # Heuristic: if the first element looks like a segment (has clip_id/start/end
                # but NOT top-level master-schema keys), treat the whole list as segments.
                _segment_keys = {"clip_id", "start", "end", "role"}
                _master_keys  = {"editing_plan", "core_analysis", "feature_proposals", "content_director"}
                _is_segment_list = bool(_segment_keys & set(first.keys())) and not bool(_master_keys & set(first.keys()))
                if _is_segment_list:
                    logger.info(
                        f"🩹 [SCHEMA_RECOVERY] Wrapped flat segment list ({len(master_data)} items) into master skeleton."
                    )
                    master_data = {
                        "editing_plan": {"segments": master_data},
                        "intent": "unknown",
                        "feature_proposals": {},
                        "editing_strategy": {},
                        "content_director": {},
                    }
                else:
                    # First element looks like a master schema — extract it silently if length 1
                    if len(master_data) == 1:
                        logger.info("🩹 [SCHEMA_RECOVERY] Extracted single-element list as master object.")
                    else:
                        logger.warning(f"⚠️ [SCHEMA_RECOVERY] List of {len(master_data)} master objects found. Taking first.")
                    master_data = first

            else:
                logger.error(
                    "❌ [MASTER_SCHEMA_RECOVERY] Got empty or non-dict list. Falling back."
                )
                master_data = {}
        elif not isinstance(master_data, dict):
            logger.error(f"❌ [MASTER_SCHEMA_RECOVERY] Expected dict but got {type(master_data).__name__}. Falling back.")
            master_data = {}

        # 4. Phase 4 — Replace Old Mapping with Safe Normalization
        logger.info(f"🔍 [MASTER_RAW_RESPONSE] {master_data}")
        master_analysis = self.normalize_master_schema(master_data)

        # [Phase C] Response Validation & Correction
        required_fields = [
            "intent",
            "editing_plan",
            "feature_proposals",
            "watermark_present",
        ]
        for rf in required_fields:
            if rf not in master_analysis or master_analysis[rf] is None:
                logger.warning(
                    f"⚠️ [MASTER_SCHEMA_CORRECTION] Missing required field '{rf}'. Inserting default."
                )
                if rf == "editing_plan":
                    master_analysis[rf] = {}
                elif rf == "feature_proposals":
                    master_analysis[rf] = {}
                elif rf == "intent":
                    master_analysis[rf] = "unknown"
                elif rf == "watermark_present":
                    master_analysis[rf] = False

        try:
            # Map Watermarks (Safe items access)
            wm_items = (
                master_data.get("core_analysis", {}).get("watermark", {}).get("items")
                or master_data.get("watermarks")
                or []
            )
            cache.core_analysis.watermarks = []
            for item in wm_items:
                if not isinstance(item, dict):
                    continue
                box = item.get("box_2d")
                if box and len(box) == 4:
                    ymin, xmin, ymax, xmax = box
                    cache.core_analysis.watermarks.append(
                        {
                            "x": xmin,
                            "y": ymin,
                            "w": max(1, xmax - xmin),
                            "h": max(1, ymax - ymin),
                            "type": item.get("type", "logo"),
                        }
                    )

            # Map Normalized Fields to Cache
            cache.core_analysis.quality_score = (
                master_data.get("core_analysis", {}).get("quality", {}).get("score")
                or 0.5
            )
            cache.core_analysis.forensic_strategy = {
                "content_director": master_analysis["content_director"],
                "intent": master_analysis["intent"],
            }

            # [Phase 4] Important addition #2 — validate timestamps
            plan_data = master_analysis["editing_plan"]
            segments = plan_data.get("segments", [])
            valid_segments = []
            for s in segments:
                if isinstance(s, dict) and "start" in s and "end" in s:
                    try:
                        start, end = float(s["start"]), float(s["end"])
                        if start < end:
                            valid_segments.append(
                                {
                                    "clip_id": s.get("clip_id", 0),
                                    "start": start,
                                    "end": end,
                                    "reason": s.get("reason", "ai_cut"),
                                }
                            )
                    except (ValueError, TypeError):
                        continue

            cache.core_analysis.editing_plan = {
                "logic": plan_data.get("logic")
                or plan_data.get("mode")
                or "story-driven",
                "segments": valid_segments,
            }
            cache.core_analysis.feature_proposals = master_analysis.get(
                "feature_proposals", {}
            )
            cache.core_analysis.editing_strategy = master_analysis.get(
                "editing_strategy", {}
            )
            cache.core_analysis.transformation_score = master_analysis.get(
                "transformation_score", 0.0
            )

            # [V5] Persistence for orchestrator
            cache.raw_data = master_analysis
            cache.content_director = master_analysis.get("content_director", {})

            cache.core_analysis.safety = master_analysis["safety"]

            # Map Extensions (Safe access)
            ext = master_data.get("extensions") or {}
            cache.extensions.fashion = ext.get("fashion") or {}
            cache.extensions.monetization = ext.get("monetization") or {}

            # [Added] Handle flattened commentary data from Master response
            # Prefer caption_text from content_director for viral captions
            content_director = master_data.get("content_director", {})
            if content_director.get("caption_text"):
                try:
                    from Text_Modules.caption_sanitizer import sanitize_caption_text
                    cache.extensions.monetization["caption_text"] = sanitize_caption_text(
                        content_director["caption_text"]
                    )
                except Exception:
                    cache.extensions.monetization["caption_text"] = content_director["caption_text"]
            if master_data.get("editorial_script"):
                cache.extensions.monetization["editorial_script"] = master_data[
                    "editorial_script"
                ]
            if master_data.get("overlay_data"):
                overlay = master_data["overlay_data"]
                try:
                    from Text_Modules.caption_sanitizer import sanitize_caption_text
                    for key in ("brand_text", "trend_text", "context_text", "item_name", "commercial_item_name"):
                        if overlay.get(key):
                            overlay[key] = sanitize_caption_text(overlay[key], target_max=4, hard_max=6)
                except Exception:
                    pass
                cache.extensions.monetization["overlay_data"] = overlay
            if master_data.get("transformation_score"):
                cache.extensions.monetization["transformation_score"] = master_data[
                    "transformation_score"
                ]

            cache.extensions.narrative = ext.get("narrative") or {}
            cache.extensions.hook_variants = ext.get("hook_variants") or []

            # [FASHION_ID] Persist fashion_identification block — used by voiceover, narrative brain, and overlays
            fashion_id = master_data.get("fashion_identification") or {}
            if fashion_id:
                # Merge into extensions.fashion so all downstream modules have access
                existing_fashion = cache.extensions.fashion or {}
                existing_fashion.update(fashion_id)
                cache.extensions.fashion = existing_fashion
                logger.info(
                    f"👗 [FASHION_ID] designer={fashion_id.get('designer_or_brand', 'Unknown')} | "
                    f"outfit={fashion_id.get('outfit_type', 'Unknown')} | "
                    f"rarity={fashion_id.get('rarity_status', 'Unknown')}"
                )

            cache.processing_time = round(time.time() - start_time, 2)

            # 5. Save to Disk
            self._save_cache(cache_file, cache)
            logger.info(
                f"✅ [MASTER_ANALYSIS_SUCCESS] Processed. Time: {cache.processing_time}s | Calls: {cache.api_calls_made}"
            )
            return cache

        except Exception as e:
            logger.error(
                f"❌ [MASTER_SCHEMA_RECOVERY] Mapping Error: {e}. Returning partial cache."
            )
            cache.processing_time = round(time.time() - start_time, 2)
            return cache

    def _save_cache(self, path: str, cache: IntelligenceCache):
        try:
            # Simple serialization
            data = {
                "core_analysis": cache.core_analysis.__dict__,
                "extensions": cache.extensions.__dict__,
                "raw_data": cache.raw_data,
                "content_director": cache.content_director,
                "source_video": cache.source_video,
                "processing_time": cache.processing_time,
                "api_calls_made": cache.api_calls_made,
                "token_usage": cache.token_usage,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"⚠️ Cache Save Failed: {e}")

    def _heal_json(self, json_str: str) -> str:
        """
        Aggressive Multi-Stage JSON Healing for LLM outputs.
        Fixes missing commas, unescaped quotes, and dangling markers.
        """
        # 1. Basic cleaning
        healed = json_str.strip()
        
        # 2. Fix missing commas between objects
        # e.g. } "key" -> }, "key"
        healed = re.sub(r'}\s*"', '}, "', healed)
        # e.g. ] "key" -> ], "key"
        healed = re.sub(r']\s*"', '], "', healed)
        # e.g. "val" "key" -> "val", "key"
        healed = re.sub(r'"\s*"([^"]+)":', '", "\1":', healed)
        
        # 3. Fix missing commas between array elements
        # e.g. "val" { -> "val", {
        healed = re.sub(r'"\s*{', '", {', healed)
        # e.g. } { -> }, {
        healed = re.sub(r'}\s*{', '}, {', healed)
        
        # 4. Remove trailing commas in objects and arrays
        healed = re.sub(r",\s*([\]}])", r"\1", healed)
        
        # 5. Fix common newline capitalization issues in multi-line strings 
        # (Gemini sometimes breaks strings with newlines)
        healed = re.sub(r'([^\\])\n', r'\1\\n', healed)
        
        return healed

    def _call_gemini_with_retry(
        self,
        prompt: str,
        frames: List[Any],
        schema: Dict,
        cache: IntelligenceCache,
        retries: int = 2,
    ) -> Optional[Dict]:
        if not self.router:
            return None
        
        for attempt in range(retries):
            try:
                cache.api_calls_made += 1
                logger.info(f"🧠 [GEMINI_MASTER_REQUEST] Sent via Router (Attempt {attempt + 1})")

                res_txt = self.router.generate(
                    task_type="master",
                    prompt=[prompt] + frames,
                    module_name="unified_intelligence",
                    gen_config={"response_mime_type": "application/json"}
                )

                if not res_txt:
                    # FAIL-FAST: Router already tried all fallbacks/retries and returned None
                    logger.error("🛑 [FAIL-FAST] Router or Pool exhausted. Stopping retries.")
                    break

                raw_text = res_txt.strip()
                # Strip markdown fences
                clean_text = re.sub(r"^```json\s*", "", raw_text)
                clean_text = re.sub(r"\s*```$", "", clean_text)

                # Extract first JSON block
                json_match = re.search(
                    r"(\{.*\}|\[.*\])", clean_text, re.DOTALL | re.MULTILINE
                )

                if not json_match:
                    logger.warning(f"⚠️ No JSON block found (Attempt {attempt + 1})")
                    continue

                json_str = json_match.group()
                
                # FIRST ATTEMPT: Standard Parse
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    # SECOND ATTEMPT: Healed Parse
                    logger.info("🩹 [MASTER_PARSE_RECOVERY] Decode failed. Healing JSON...")
                    try:
                        healed = self._heal_json(json_str)
                        return json.loads(healed)
                    except Exception as e2:
                        logger.warning(f"❌ [MASTER_PARSE_RECOVERY] Healing failed: {e2}")
                        continue

            except Exception as e:
                logger.warning(f"⚠️ Gemini Attempt {attempt + 1} failed: {e}")
        
        return None

    def perform_intelligence_cycle_retry(
        self, video_path: str, frames: List[Any], context: Dict, retry_hint: str = "", attempt: int = 0
    ) -> Optional[IntelligenceCache]:
        """Wrapper for retrying intelligence cycle with error correction hint. Bypasses disk cache on retries."""
        if attempt > 0:
            _paths = context.get("input_paths", [video_path])
            if len(_paths) > 1:
                combined_raw = "_".join(self._get_video_hash(p) for p in _paths)
                v_hash = hashlib.sha256(combined_raw.encode()).hexdigest()[:32]
            else:
                v_hash = self._get_video_hash(video_path)
            
            c_file = os.path.join(self.cache_dir, f"{v_hash}.json")
            if os.path.exists(c_file):
                try:
                    os.remove(c_file)
                    logger.info(f"🗑️ [RETRY] Cleared stale cache file for attempt {attempt}.")
                except OSError:
                    pass
            
            context["retry_hint"] = retry_hint
        
        return self.perform_intelligence_cycle(video_path, frames, context)


# Singleton
engine = UnifiedIntelligence()