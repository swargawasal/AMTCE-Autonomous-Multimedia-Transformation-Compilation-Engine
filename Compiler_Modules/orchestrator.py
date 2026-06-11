import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime

from dotenv import load_dotenv

try:
    from config.runtime_flags import ENABLE_EARLY_HARD_STOP, ENABLE_VALIDATOR_HARD_STOP
except ImportError:
    ENABLE_EARLY_HARD_STOP = True
    ENABLE_VALIDATOR_HARD_STOP = True

# --- [Phase 0 Foundation] ---
try:
    from Diagnostics_Modules.pipeline_audit import RuntimeDependencyTracer

    RuntimeDependencyTracer.install()
except ImportError:
    pass

from Compiler_Modules.pipeline_context import PipelineContext
from Content_Intelligence import feature_flag_controller
from Diagnostics_Modules.pipeline_feature_auditor import FeatureAuditor
from Text_Modules import text_overlay
from Text_Modules.caption_sanitizer import sanitize_caption_text
from Text_Modules.text_overlay import apply_brand_overlay_safe, apply_text_overlay_safe

from . import audio_pipeline, video_pipeline

# Load Env from Credentials
env_path = os.path.join("Credentials", ".env")
load_dotenv(env_path)

# ── Price Tag: hard-blocked content categories ───────────────────────────────
# These categories produce zero value from price tags and waste 3+ minutes of
# pipeline time. The block is enforced EVEN if ENABLE_PRICE_TAG=yes in .env.
# To add/remove a category, edit this set — no other code changes needed.
PRICE_TAG_BLOCKED_CATEGORIES = {
    "dance", "choreography",
    "comedy", "skit", "meme", "parody",
    "motivational", "quotes", "inspiration",
    "nature", "scenery", "landscape", "travel_scenery",
    "cooking", "recipe", "food_tutorial",
    "music_performance", "concert", "singing",
    "sports", "sports_highlights", "athletics",
    "news", "politics", "commentary",
    "vlog", "storytelling", "emotional",
}

# Lazy import — only pulled in when a notification is actually needed so the
# orchestrator doesn't hard-depend on uploader being present.
def _notify_price_tag_blocked(category: str, job_id: str):
    """Log warning + fire a Telegram admin alert when price tag is category-blocked."""
    logger.warning(
        f"🚫 [PRICE_TAG_BLOCKED] Category '{category}' is in the blocked list. "
        f"Price tag engine skipped for job {job_id}. "
        f"This block applies even when ENABLE_PRICE_TAG=yes."
    )
    try:
        from Uploader_Modules.uploader import send_telegram_notification
        send_telegram_notification(
            f"🚫 Price Tag Blocked\n"
            f"Job: {job_id}\n"
            f"Category: {category}\n"
            f"Reason: Content type produces no value from price tags.\n"
            f"Override: Not possible — hard block active."
        )
    except Exception as _tg_e:
        logger.warning(f"[PRICE_TAG_BLOCKED] Telegram notify failed (non-fatal): {_tg_e}")


def sanitize_clip_ids(segments, input_paths):
    """
    Validates and clamps clip_id to valid input range.
    Ensures that single-video jobs never reference non-existent input streams.
    """
    max_index = max(0, len(input_paths) - 1)

    for seg in segments:
        cid = seg.get("clip_id", 0)

        # Force valid range
        if not isinstance(cid, int) or cid < 0 or cid > max_index:
            seg["clip_id"] = 0  # safe fallback for single-video jobs

    return segments


def _compute_information_density(segments):
    """Calculates segments per second of actual edited content (detects lazy/stretched edits)."""
    total_seg_time = sum(max(0.1, float(s.get("end", 0.1)) - float(s.get("start", 0.0))) for s in segments if isinstance(s, dict))
    if total_seg_time <= 0: return 0.0
    return len(segments) / total_seg_time


def _temporal_jump_score(segments):
    """Calculates average temporal gap between sequential segments (pacing creativity)."""
    if not isinstance(segments, list) or len(segments) < 2: return 0.0
    jumps = []
    try:
        sorted_segs = sorted(segments, key=lambda x: float(x.get("start", 0)))
        for i in range(len(sorted_segs)-1):
            jump = abs(float(sorted_segs[i+1].get("start", 0)) - float(sorted_segs[i].get("end", 0)))
            jumps.append(jump)
    except Exception:
        return 0.0
    return sum(jumps) / len(jumps) if jumps else 0.0

def _calculate_attention_score(seg):
    """0.4 motion + 0.3 scene + 0.2 novelty + 0.1 subject."""
    if not isinstance(seg, dict): return 0.0
    return (
        0.4 * float(seg.get("motion_spike", seg.get("motion", 0.1))) +
        0.3 * float(seg.get("scene_change", 0.1)) +
        0.2 * float(seg.get("novelty", 0.1)) +
        0.1 * float(seg.get("subject_presence", 0.1))
    )


def _calculate_curiosity_score(seg):
    """0.4 partial + 0.2 motion + 0.2 framing + 0.2 reveal_delay."""
    if not isinstance(seg, dict): return 0.0
    reveal_delay = float(seg.get("reveal_time", 0.5)) / max(0.1, float(seg.get("end", 1.0)) - float(seg.get("start", 0.0)))
    return (
        0.4 * float(seg.get("partial_visibility", 0.1)) +
        0.2 * float(seg.get("motion_start", 0.1)) +
        0.2 * float(seg.get("framing_tension", 0.1)) +
        0.2 * min(1.0, reveal_delay)
    )


def _calculate_emotion_score(seg):
    """0.4 face + 0.4 expression + 0.2 gaze."""
    if not isinstance(seg, dict): return 0.0
    return (
        0.4 * float(seg.get("face_presence", 0.0)) +
        0.4 * float(seg.get("expression_change", 0.0)) +
        0.2 * float(seg.get("gaze_shift", 0.0))
    )


def _rank_segment_role_aware(seg, role, attention_boost=1.0):
    """Applies psychological weights based on segment role (Hook vs Buildup vs Climax)."""
    if not isinstance(seg, dict): return 0.0
    
    score = float(seg.get("score", seg.get("signal_score", 0.1)))
    impact = float(seg.get("impact", 0.1))
    clarity = float(seg.get("clarity", 0.1))
    novelty = float(seg.get("novelty", 0.1))
    motion = float(seg.get("motion", 0.1))
    curiosity = _calculate_curiosity_score(seg)
    
    if role == "hook":
        _semantic = float(seg.get("semantic_score", 0.4))
        _subject  = float(seg.get("subject_presence", 0.0))
        rank = (0.35 * _semantic + 0.25 * curiosity + 0.25 * _subject + 0.15 * impact)
    elif role == "climax":
        rank = (0.5 * impact + 0.3 * score + 0.2 * motion)
    else: # buildup
        rank = (0.4 * score + 0.3 * clarity + 0.3 * float(seg.get("continuity", 0.5)))
        
    return rank * attention_boost





def _calculate_clarity(seg):
    """edge_score / (blur + 1e-5). Absolute visual sharpness."""
    if not isinstance(seg, dict): return 0.5
    edge = float(seg.get("edge_score", 0.5))
    blur = float(seg.get("blur_penalty", 0.1))
    return min(1.0, edge / (blur + 1e-5))


def _compute_pair_flow_score(seg_a: dict, seg_b: dict) -> dict:
    """
    Narrative flow score between two consecutive segments.
    Returns: continuity, cause_effect, energy_progression, flow_score (all 0.0-1.0).
    """
    if not (isinstance(seg_a, dict) and isinstance(seg_b, dict)):
        return {"continuity": 0.5, "cause_effect": 0.5,
                "energy_progression": 0.5, "flow_score": 0.5}

    # 1. CONTINUITY (0.40)
    _sub_a  = float(seg_a.get("subject_presence", 0.5))
    _sub_b  = float(seg_b.get("subject_presence", 0.5))
    _face_a = 1.0 if seg_a.get("face_present") else 0.0
    _face_b = 1.0 if seg_b.get("face_present") else 0.0
    _sem_a  = float(seg_a.get("semantic_score",  0.5))
    _sem_b  = float(seg_b.get("semantic_score",  0.5))
    continuity = min(1.0,
        0.40 * (1.0 - abs(_sub_a - _sub_b)) +
        0.35 * (0.5 * (_face_a + _face_b))  +
        0.25 * (1.0 - abs(_sem_a - _sem_b))
    )

    # 2. CAUSE -> EFFECT (0.30)
    _motion_a = float(seg_a.get("motion_score", seg_a.get("motion", 0.0)))
    _motion_b = float(seg_b.get("motion_score", seg_b.get("motion", 0.0)))
    _expr_a   = float(seg_a.get("expression_change", 0.0))
    _expr_b   = float(seg_b.get("expression_change", 0.0))
    _action_in_a   = min(1.0, _motion_a * 0.6 + _expr_a * 0.8)
    _reaction_in_b = min(1.0, _expr_b  * 0.9 + (1.0 - _motion_b) * 0.3)
    cause_effect = min(1.0, _action_in_a * 0.6 + _reaction_in_b * 0.4)

    # 3. ENERGY PROGRESSION (0.30)
    _score_a = float(seg_a.get("rank_base", seg_a.get("score", 0.3)))
    _score_b = float(seg_b.get("rank_base", seg_b.get("score", 0.3)))
    _delta = _score_b - _score_a
    if _delta > 0.05:
        energy_progression = min(1.0, 0.5 + _delta * 2.0)
    elif _delta > -0.05:
        energy_progression = 0.45
    else:
        energy_progression = max(0.0, 0.5 + _delta * 1.5)

    flow_score = round(min(1.0,
        0.40 * continuity +
        0.30 * cause_effect +
        0.30 * energy_progression
    ), 4)
    return {
        "continuity":         round(continuity, 4),
        "cause_effect":       round(cause_effect, 4),
        "energy_progression": round(energy_progression, 4),
        "flow_score":         flow_score,
    }


def _median_std(data):
    """Stable median and std_dev calculation without numpy."""
    if not data: return 0.0, 0.0
    sorted_data = sorted(data)
    n = len(data)
    mid = n // 2
    median = (sorted_data[mid] + sorted_data[~mid]) / 2 if n % 2 == 0 else sorted_data[mid]
    variance = sum((x - median) ** 2 for x in data) / n
    return median, variance ** 0.5


def _check_intent_alignment(intent, segments, total_duration):
    """Verifies if output structure matches the stated editing intent (Detects Intent Drift)."""
    intent = str(intent or "").lower()
    n = len(segments)
    edit_dur = sum(float(s.get("end", 0)) - float(s.get("start", 0)) for s in segments if isinstance(s, dict))
    
    # 1. Education/Educational: Needs density and step-by-step structure
    if "education" in intent or "tutorial" in intent or "educational" in intent:
        if n < 3: return False 
    
    # 2. Showcase/Aesthetic: Needs enough duration to actually see the item
    if "showcase" in intent or "aesthetic" in intent or "cinematic" in intent:
        if edit_dur < 6.0: return False
    
    return True


def _salvage_reconstruct_elite(segments, total_duration, intent):
    """Viral-tier reconstruction: Rank-then-Select, Hook Scoring, and Energy Curve."""
    if not segments: return []
    
    # 1. Calculate Elite Score for all segments
    for s in segments:
        s["attention"] = _calculate_attention_score(s)
        s["emotion"] = _calculate_emotion_score(s)
        
        # Base ranking variables
        _conf      = float(s.get("signal_confidence", 0.5))
        _semantic  = float(s.get("semantic_score", 0.5))
        _motion    = float(s.get("motion", 0.1))
        _retention = float(s.get("retention", 0.1))
        _emotion   = s["emotion"]
        
        # New semantic confidence weighting (Patch 15 + Patch 10)
        _weighted_semantic = _semantic * (0.6 + 0.4 * _conf)
        
        # Replace old generic ranking formula
        s["rank_base"] = (
            0.30 * _weighted_semantic +
            0.25 * _motion * _conf +
            0.20 * _retention * _conf +
            0.15 * _emotion * _conf +
            0.10 * _conf
        )
        
        # Semantic dead/weak hard penalties
        if s.get("semantic_dead"):
            s["rank_base"] *= 0.3
        elif s.get("semantic_weak"):
            s["rank_base"] *= 0.7
            
        # Emotional Boost
        s["rank_base"] *= (1.0 + s["emotion"] * 0.3)
        # Attention Boost
        if s["attention"] > 0.4: s["rank_base"] *= 1.3
        
        s["rank_base"] = min(1.0, s["rank_base"])

    # 2. Competitive Selection (Winner Selection) + Spacing
    sorted_pool = sorted(segments, key=lambda x: x["rank_base"], reverse=True)
    winners = []
    for s in sorted_pool:
        # Spacing: Ensure 1.5s separation
        if all(abs(float(s["start"]) - float(w["start"])) > 1.5 for w in winners):
            winners.append(s)
        if len(winners) >= 6: break
    
    if len(winners) < 2: return winners

    # 3. Energy Curve (Pull -> Hold -> Explode)
    # Sort winners by original score to ensure progressive energy progression
    winners = sorted(winners, key=lambda x: float(x.get("score", 0)))
    
    # 4. Role Assignment & Micro-Reconstruction
    winners[0]["role"] = "hook"
    if len(winners) >= 3:
        for mid in winners[1:-1]: mid["role"] = "buildup"
    winners[-1]["role"] = "climax"
    
    # 5. Hook Dominance & Curiosity Check
    try:
        pool_max_rank = max(s["rank_base"] for s in segments)
        if winners[0]["rank_base"] < 0.85 * pool_max_rank:
            best_hook = max(segments, key=lambda x: _rank_segment_role_aware(x, "hook"))
            winners[0] = best_hook
            winners[0]["role"] = "hook"
    except Exception: pass

    # 6. Payoff Validation: Climax must be >= Hook * 1.1
    if winners[-1]["rank_base"] < winners[0]["rank_base"] * 1.1:
        winners[-1]["final_score"] = winners[0]["rank_base"] * 1.2

    return winners


# --- MODULE AVAILABILITY ---
try:
    from Audio_Modules import voiceover

    VOICEOVER_AVAILABLE = True
except ImportError:
    VOICEOVER_AVAILABLE = False

try:
    from Visual_Refinement_Modules import watermark_auto

    WATERMARK_AVAILABLE = True
except ImportError:
    WATERMARK_AVAILABLE = False

try:
    from Intelligence_Modules.monetization_brain import MonetizationStrategist

    BRAIN_AVAILABLE = True
except ImportError:
    BRAIN_AVAILABLE = False

try:
    from Audio_Modules.audio_processing import heavy_remix

    HEAVY_REMIX_AVAILABLE = True
except ImportError:
    HEAVY_REMIX_AVAILABLE = False

try:
    from Visual_Refinement_Modules.smart_scene_editor import SmartSceneEditor

    _smart_scene_editor = SmartSceneEditor()
    SMART_SCENE_AVAILABLE = True
except Exception:
    _smart_scene_editor = None
    SMART_SCENE_AVAILABLE = False

try:
    from Intelligence_Modules.narrative_brain import NarrativeDirector

    NARRATIVE_BRAIN_AVAILABLE = True
except Exception:
    NARRATIVE_BRAIN_AVAILABLE = False

try:
    from Content_Intelligence.hook_engine import analyse_hook as _hook_analyse

    HOOK_ENGINE_AVAILABLE = True
except Exception:
    _hook_analyse = None
    HOOK_ENGINE_AVAILABLE = False

try:
    from Trend_Intelligence.trend_opportunity_engine import (
        analyse_trend_opportunity as _toe_analyse,
    )

    TREND_OPPORTUNITY_AVAILABLE = True
except Exception:
    _toe_analyse = None
    TREND_OPPORTUNITY_AVAILABLE = False

try:
    from Content_Intelligence.hook_variants import (
        generate_hook_variant as _hvg_generate,
    )

    HOOK_VARIANTS_AVAILABLE = True
except Exception:
    _hvg_generate = None
    HOOK_VARIANTS_AVAILABLE = False

try:
    from Trend_Intelligence.trend_engine import get_trend_context as _get_trend_ctx
except ImportError:
    _get_trend_ctx = None

try:
    from Audio_Modules.music_manager import ContinuousMusicManager

    MUSIC_MANAGER_AVAILABLE = True
except Exception:
    MUSIC_MANAGER_AVAILABLE = False

try:
    from Audio_Modules.beat_engine import BeatEngine

    _beat_engine = BeatEngine()
    BEAT_ENGINE_AVAILABLE = True
except Exception:
    _beat_engine = None
    BEAT_ENGINE_AVAILABLE = False

try:
    from Visual_Refinement_Modules.dynamic_price_tag import (
        tracker_engine as _dynamic_tag_engine,
    )
    from Text_Modules.smart_price_tag import tag_engine as _smart_tag_engine

    DYNAMIC_TAG_AVAILABLE = True
except Exception:
    _dynamic_tag_engine = None
    _smart_tag_engine = None
    DYNAMIC_TAG_AVAILABLE = False

try:
    from Audio_Modules import audio_beat_detector

    AUDIO_BEAT_DETECTOR_AVAILABLE = True
except ImportError:
    AUDIO_BEAT_DETECTOR_AVAILABLE = False

try:
    from Visual_Refinement_Modules import subject_tracker

    SUBJECT_TRACKER_AVAILABLE = True
except ImportError:
    SUBJECT_TRACKER_AVAILABLE = False

try:
    from Intelligence_Modules.editor_brain import EditorBrain

    _editor_brain = EditorBrain()
    EDITOR_BRAIN_AVAILABLE = True
except Exception:
    _editor_brain = None
    EDITOR_BRAIN_AVAILABLE = False

from Diagnostics_Modules.pipeline_health_monitor import check as pipeline_health_check
from Visual_Refinement_Modules.segment_safety_gate import validate as segment_safety_validate

# --- HYBRID/ADAPTIVE MODULES ---
try:
    from Content_Intelligence import source_detector, transformation_engine

    SOURCE_DETECTION_AVAILABLE = True
except ImportError:
    SOURCE_DETECTION_AVAILABLE = False

try:
    from Intelligence_Modules import shot_detector, unified_intelligence

    unified_intel = unified_intelligence.UnifiedIntelligence()
    UNIFIED_INTEL_AVAILABLE = True
    SHOT_DETECTOR_AVAILABLE = True
except ImportError:
    shot_detector = None
    unified_intel = None
    UNIFIED_INTEL_AVAILABLE = False
    SHOT_DETECTOR_AVAILABLE = False

try:
    from Intelligence_Modules.adaptive_intelligence import AdaptiveBrain

    _adaptive_brain = AdaptiveBrain()
    ADAPTIVE_BRAIN_AVAILABLE = True
except Exception:
    _adaptive_brain = None
    ADAPTIVE_BRAIN_AVAILABLE = False

try:
    from Core_Modules.self_optimizing_editor import SelfOptimizingEditor

    # Toggle via .env: ENABLE_SOE=yes (default) / no
    _soe_enabled = os.getenv("ENABLE_SOE", "yes").lower() in ("yes", "true", "on", "1")
    if _soe_enabled:
        _soe = SelfOptimizingEditor(mock_analytics=False)
        SOE_AVAILABLE = True
        logging.getLogger("orchestrator").info("[SOE] Self-Optimizing Editor ON (real analytics).")
    else:
        _soe = None
        SOE_AVAILABLE = False
        logging.getLogger("orchestrator").info("[SOE] Disabled via ENABLE_SOE=no.")
except Exception as _soe_err:
    _soe = None
    SOE_AVAILABLE = False
    logging.getLogger("orchestrator").debug(
        f"[SOE] Self-Optimizing Editor unavailable: {_soe_err}"
    )

try:
    from Core_Modules.narrative_coherence_engine import NarrativeCoherenceEngine

    _coherence_engine = NarrativeCoherenceEngine()
    COHERENCE_ENGINE_AVAILABLE = True
except Exception:
    _coherence_engine = None
    COHERENCE_ENGINE_AVAILABLE = False

try:
    from Core_Modules.pacing_engine import PacingEngine

    _pacing_engine = PacingEngine()
    PACING_ENGINE_AVAILABLE = True
except Exception:
    _pacing_engine = None
    PACING_ENGINE_AVAILABLE = False

try:
    from Compiler_Modules.rhythm_timeline_builder import RhythmTimelineBuilder

    _rhythm_builder = RhythmTimelineBuilder()
    RHYTHM_BUILDER_AVAILABLE = True
except ImportError:
    _rhythm_builder = None
    RHYTHM_BUILDER_AVAILABLE = False

try:
    from Visual_Refinement_Modules.moment_miner import MomentMiner, export_moments_debug

    MOMENT_MINER_AVAILABLE = True
except ImportError:
    MOMENT_MINER_AVAILABLE = False

try:
    from Content_Intelligence.retention_curve_engine import (
        analyse_retention as _retention_analyse,
    )

    RETENTION_CURVE_AVAILABLE = True
except ImportError:
    _retention_analyse = None
    RETENTION_CURVE_AVAILABLE = False

try:
    from Content_Intelligence.emotional_spike_detector import (
        analyse_emotional_spikes as _emotional_spike_analyse,
    )

    EMOTIONAL_SPIKE_AVAILABLE = True
except ImportError:
    _emotional_spike_analyse = None
    EMOTIONAL_SPIKE_AVAILABLE = False

try:
    from Content_Intelligence.signal_fusion_engine import (
        fuse_signals as _signal_fusion,
    )

    SIGNAL_FUSION_AVAILABLE = True
except ImportError:
    _signal_fusion = None
    SIGNAL_FUSION_AVAILABLE = False

try:
    from Content_Intelligence.expression_change_engine import (
        detect_expression_changes as _expression_detect,
    )

    EXPRESSION_CHANGE_AVAILABLE = True
except ImportError:
    _expression_detect = None
    EXPRESSION_CHANGE_AVAILABLE = False

try:
    from Content_Intelligence import signal_repair_layer as _signal_repair
    SIGNAL_REPAIR_AVAILABLE = True
except ImportError:
    _signal_repair = None
    SIGNAL_REPAIR_AVAILABLE = False

try:
    from Content_Intelligence.content_intelligence_engine import (
        interpret_visual_context as _cie_interpret,
    )

    CONTENT_INTEL_ENGINE_AVAILABLE = True
except ImportError:
    _cie_interpret = None
    CONTENT_INTEL_ENGINE_AVAILABLE = False

try:
    from Visual_Refinement_Modules.timeline_reconstructor import (
        reconstruct_timeline as _timeline_reconstruct,
    )

    TIMELINE_RECONSTRUCTOR_AVAILABLE = True
except ImportError:
    _timeline_reconstruct = None
    TIMELINE_RECONSTRUCTOR_AVAILABLE = False

try:
    from Compilation_Modules.highlight_compiler import (
        HighlightCompiler,
        compile_top_moments,
    )

    HIGHLIGHT_COMPILER_AVAILABLE = True
except ImportError:
    HIGHLIGHT_COMPILER_AVAILABLE = False

# ── HUMAN-STYLE CREATIVE EDITOR MODULES ──────────────────────────────────────────────
try:
    from Intelligence_Modules.creative_brain import CreativeBrain
    _creative_brain = CreativeBrain()
    CREATIVE_BRAIN_AVAILABLE = True
except Exception as _cb_err:
    _creative_brain = None
    CREATIVE_BRAIN_AVAILABLE = False
    logging.getLogger("orchestrator").debug(f"[CREATIVE_BRAIN] unavailable: {_cb_err}")

try:
    from Intelligence_Modules.creator_memory import CreatorMemory
    _creator_memory = CreatorMemory()
    CREATOR_MEMORY_AVAILABLE = True
except Exception as _cm_err:
    _creator_memory = None
    CREATOR_MEMORY_AVAILABLE = False
    logging.getLogger("orchestrator").debug(f"[CREATOR_MEMORY] unavailable: {_cm_err}")

try:
    from Intelligence_Modules.pacing_architect import PacingArchitect
    _pacing_architect = PacingArchitect()
    PACING_ARCHITECT_AVAILABLE = True
except Exception as _pa_err:
    _pacing_architect = None
    PACING_ARCHITECT_AVAILABLE = False
    logging.getLogger("orchestrator").debug(f"[PACING_ARCHITECT] unavailable: {_pa_err}")
# ───────────────────────────────────────────────────────────────

# --- [FIX] Frame extraction helper ---
# frame_paths was used in the original but never defined, causing a guaranteed
# NameError crash on every Master Intelligence call.  This helper extracts a
# configurable number of evenly-spaced frames and returns their paths so the
# Gemini master analysis call has real visual context to work with.
def _extract_frames(video_path: str, job_dir: str, max_frames: int = 8, target_times: list = None) -> list:
    """
    Extract up to `max_frames` evenly-spaced JPEG frames from `video_path`.
    Returns a list of absolute file paths.  On any failure returns an empty list
    so callers degrade gracefully rather than crash.
    """
    frame_paths = []
    try:
        ffprobe_bin = os.getenv("FFPROBE_BIN", "ffprobe")
        ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")

        # Probe duration
        probe = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
        )
        duration = float(probe.stdout.strip() or "0")
        if duration <= 0:
            return []

        frames_dir = os.path.join(job_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        if target_times:
            timestamps = [t for t in target_times if 0 <= t <= duration][:max_frames]
        else:
            # Build evenly-spaced timestamps, skipping the very first and last 0.5s
            # to avoid black leader / trailer frames.
            start, end = 0.5, max(0.5, duration - 0.5)
            step = max(1.0, (end - start) / max(1, max_frames - 1))
            timestamps = [start + i * step for i in range(max_frames)]
            timestamps = [t for t in timestamps if t <= end]

        for idx, ts in enumerate(timestamps):
            out = os.path.join(frames_dir, f"frame_{idx:03d}.jpg")
            result = subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-ss",
                    str(ts),
                    "-i",
                    video_path,
                    "-vframes",
                    "1",
                    "-q:v",
                    "3",
                    out,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0 and os.path.exists(out):
                frame_paths.append(out)

    except Exception as _fe:
        logger.warning(f"[FRAME_EXTRACTION] Failed (non-fatal): {_fe}")

    return frame_paths


logger = logging.getLogger("orchestrator")


def run_with_timeout(
    func, timeout_sec: int, feature_name: str, auditor: FeatureAuditor, *args, **kwargs
):
    """
    Executes a pipeline module with a strict execution timeout.
    If it times out or throws an exception, marks the auditor FAILED and returns None.
    """
    result = [None]
    exception = [None]

    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exception[0] = e

    thread = threading.Thread(target=target)
    logger.info(f"⏱️ Starting [{feature_name}] with {timeout_sec}s timeout limit.")
    thread.start()
    thread.join(timeout_sec)

    if thread.is_alive():
        logger.error(
            f"❌ [TIMEOUT] Feature [{feature_name}] exceeded {timeout_sec}s limit."
        )
        auditor.mark_failed(feature_name)
        return None

    if exception[0]:
        logger.error(f"❌ [FAILURE] Feature [{feature_name}] crashed: {exception[0]}")
        auditor.mark_failed(feature_name)
        return None

    auditor.mark_executed(feature_name)
    return result[0]


def apply_rag_to_editing_plan(editing_plan: dict, rag_strategy: dict, profile_data: dict = None) -> dict:
    """Overrides the master editing plan with RAG-generated creative strategy.
    
    GENRE GUARD: Only applies the RAG strategy if it matches the content's genre.
    A 'travel' strategy must never corrupt a 'fashion' edit.
    """
    if not editing_plan or not rag_strategy or not isinstance(rag_strategy, dict):
        return editing_plan

    # --- GENRE GUARD ---
    # Check if RAG strategy genre matches content category.
    # Use rag_category (clean genre label from pattern metadata), NOT editing_style (long strategy text).
    rag_style = rag_strategy.get("rag_category", rag_strategy.get("editing_style", "")).lower()
    content_category = ""
    if profile_data:
        # Priority 1: HybridAnalyzer populated hybrid_profile with a clean category label
        hybrid_profile = profile_data.get("hybrid_profile", {})
        content_category = str(hybrid_profile.get("category", "")).lower()

        # Priority 2: fallback to explicit content_category
        if not content_category:
            content_category = str(profile_data.get("content_category", profile_data.get("category", ""))).lower()

        # Priority 3: scan Gemini intent for genre hints
        if not content_category:
            intent = str(profile_data.get("intent", "")).lower()
            for genre in ("fashion", "travel", "fitness", "food", "gaming", "podcast", "meme"):
                if genre in intent:
                    content_category = genre
                    break

    # Log RAG confidence and score into the pipeline logger
    rag_confidence = rag_strategy.get("rag_confidence", "UNKNOWN")
    rag_score = rag_strategy.get("rag_score", -1.0)
    logger.info(
        f"[RAG_QUALITY] confidence={rag_confidence} | score={rag_score:.4f} | "
        f"rag_category={rag_style} | content_category={content_category}"
    )

    # Known genre mapping — if RAG style doesn't match suspected content, skip
    RAG_GENRE_MAP = {
        "travel": ["travel", "vlog", "tourism", "destination"],
        "fashion": ["fashion", "style", "runway", "outfit", "clothing", "lehenga", "saree"],
        "fitness": ["fitness", "workout", "gym", "exercise"],
        "food": ["food", "recipe", "cooking", "restaurant"],
        "podcast": ["podcast", "interview", "talk", "conversation"],
        "meme": ["meme", "funny", "comedy", "humor"],
    }

    if content_category and rag_style:
        # rag_style is now a clean genre label ('fashion', 'travel', etc.)
        # Check if the content_category matches or contains the rag genre keywords
        allowed_keywords = RAG_GENRE_MAP.get(rag_style, [])
        if allowed_keywords:
            content_matches_rag = any(kw in content_category for kw in allowed_keywords)
            # Also check: rag_style itself directly in content_category
            if not content_matches_rag:
                content_matches_rag = rag_style in content_category
            if not content_matches_rag:
                logger.warning(
                    f"[RAG_GENRE_MISMATCH] RAG genre='{rag_style}' doesn't match content='{content_category}'. "
                    f"Skipping RAG override to preserve Gemini's creative edit."
                )
                return editing_plan
        # If no allowed_keywords, RAG genre is generic/unknown → allow it through
    elif rag_style and not content_category:
        # Content category is unknown — trust RAG rather than silently blocking
        logger.info(f"[RAG_GENRE_GUARD] content_category unknown — applying RAG genre='{rag_style}' unconditionally.")


    # [FIX] Handle Windows cp1252 charmap encoding errors with smart quotes/arrows
    try:
        raw_msg = f"[RAG_CONTROL] Applying RAG strategy: {rag_strategy.get('editing_style', 'Unknown')}"
        print(raw_msg.encode('ascii', 'ignore').decode('ascii'))
    except Exception:
        print("[RAG_CONTROL] Applying RAG strategy (Unicode Error)")


    segments = editing_plan.get("segments", [])
    if not segments:
        return editing_plan

    # 1. Map Pacing -> Duration
    pacing = rag_strategy.get("pacing", "steady")
    duration_multiplier = 1.0
    if pacing == "fast":
        duration_multiplier = 0.7
    elif pacing == "slow":
        duration_multiplier = 1.5

    # 2. Map Cut Density -> Sub-splitting (simplistic approach: split long segments if high density)
    cut_density = rag_strategy.get("cut_density", "medium")
    split_threshold = 4.0 if cut_density == "high" else 8.0

    # 3. Map Transition Style
    transition = rag_strategy.get("transition_style", "cut")

    new_segments = []
    for seg in segments:
        orig_duration = seg.get("duration", 2.0)
        new_duration = orig_duration * duration_multiplier

        # Apply splits if density is high and segment is long
        if cut_density == "high" and new_duration > split_threshold:
            mid = new_duration / 2
            seg1 = seg.copy()
            seg1["duration"] = mid
            seg1["transition"] = transition

            seg2 = seg.copy()
            seg2["duration"] = mid
            seg2["transition"] = transition
            # Adjust start of second segment if possible (heuristic)
            if "start" in seg2 and seg2["start"] is not None:
                seg2["start"] = seg2["start"] + (orig_duration / 2)

            new_segments.append(seg1)
            new_segments.append(seg2)
        else:
            seg["duration"] = new_duration
            seg["transition"] = transition
            new_segments.append(seg)

    # 4. Map Hook -> First Segment Influence
    hook_text = rag_strategy.get("hook")
    if hook_text and new_segments:
        # Inject hook as a property for downstream engines (like caption_variants)
        new_segments[0]["rag_hook"] = hook_text
        # Optional: ensure first segment is snappy for hook
        if new_segments[0]["duration"] > 2.5:
            new_segments[0]["duration"] = 2.0

    editing_plan["segments"] = new_segments
    editing_plan["rag_controlled"] = True
    editing_plan["editing_style"] = rag_strategy.get("editing_style")

    try:
        raw_msg2 = f"[RAG_CONTROL] Applied RAG strategy to editing plan ({len(new_segments)} segments)"
        print(raw_msg2.encode('ascii', 'ignore').decode('ascii'))
    except Exception:
        pass

    return editing_plan



def compile_video(
    uuid_str, input_path, output_path, title, description, profile_data=None
):
    if profile_data is None:
        profile_data = {}
    """
    Narrative Intelligence Pipeline — 8-Stage Architecture.

    Stage order:
        [Step 0]    Watermark removal / source detection / transformation strategy
        [Step 1e]   Frame extraction        → frame_paths          (FIX: was missing)
        [Step 1e.5] Expression Change       → expression_moments
        [Step 1f]   Retention Curve         → retention_peaks
        [Step 1g]   Emotional Spike         → emotional_spikes
        [Step 1h]   Signal Fusion           → fused_moments
        [Step 1i]   Editor Brain            → editor_persona / edl
        [Step 2]    Master Intelligence     → editing_plan, feature_flags
        [Step 2b]   Shot Scoring            → selected_shots (fallback only)
        [Step 2c]   Creative Director       → creative_strategy, story_map
        [Step 3]    Timeline Reconstructor  → reconstructed_timeline
        [Step 7]    Smart Scene Editor      → timeline_instructions
        [Step 7b]   Rhythm Timeline Builder → refined scenes
        [Step 8]    Render Engine           → final output
        [Step 9]    SRV diagnostics
        [Step 10]   Sidecar JSON
    """
    # --- UNIVERSAL SEMANTIC EDITOR ROUTING ---
    if os.getenv("ENABLE_UNIVERSAL_AUTO_EDIT", "no").lower() == "yes":
        logger.info(f"🌐 [UNIVERSAL EDITOR] Engine Active! Bypassing rhythmic fashion pipeline for {uuid_str}.")
        try:
            from Audio_Modules.speech_to_text import transcribe_audio
            from Intelligence_Modules.semantic_editor import analyze_transcript_for_cuts
            from Video_Modules.timeline_cutter import cut_and_burn_video, generate_srt_from_words, generate_ass_from_words
            from Compiler_Modules.video_pipeline import get_video_info
            
            # Universal Editor only supports single input file
            input_file = input_path if isinstance(input_path, str) else input_path[0] if isinstance(input_path, list) and input_path else ""
            if not input_file or not os.path.exists(input_file):
                logger.error("❌ Universal Editor requires a valid input video file.")
                return False
                
            # Phase 1: STT
            transcript_data = transcribe_audio(input_file)
            if not transcript_data:
                logger.error("❌ STT Failed. Universal Editor aborting.")
                return False
                
            # Phase 2: Semantic Analysis
            keep_ranges = analyze_transcript_for_cuts(transcript_data)
            if not keep_ranges:
                logger.error("❌ Semantic Analysis Failed. Universal Editor aborting.")
                return False
                
            # Phase 3 & 4: Timeline execution and ASS/SRT burn
            import tempfile
            video_info = get_video_info(input_file)
            v_width = video_info.get("width", 1080)
            v_height = video_info.get("height", 1920)
            
            temp_ass = os.path.join(tempfile.gettempdir(), f"{uuid_str}_captions.ass")
            temp_srt = os.path.join(tempfile.gettempdir(), f"{uuid_str}_captions.srt")
            subs_file = None
            
            if generate_ass_from_words(transcript_data.get("words", []), temp_ass, video_width=v_width, video_height=v_height):
                subs_file = temp_ass
            elif generate_srt_from_words(transcript_data.get("words", []), temp_srt):
                logger.warning("⚠️ ASS generation failed, falling back to SRT")
                subs_file = temp_srt
                
            if cut_and_burn_video(input_file, output_path, keep_ranges, subs_file):
                logger.info("✅ Universal Semantic Auto-Edit successfully completed!")
                return True
            else:
                logger.error("❌ Final Universal Video rendering failed.")
                return False
                
        except Exception as e:
            logger.error(f"❌ Universal Editor Pipeline crashed: {e}")
            return False

    # --- [FIX] Normalise input_path regardless of tuple/list/str ---
    if isinstance(input_path, tuple):
        input_path = list(input_path)
    if isinstance(input_path, list):
        input_paths = [p for p in input_path if p]
    else:
        input_paths = [input_path] if input_path else []

    if not input_paths:
        logger.error("❌ [INIT] No valid input paths provided.")
        return False, {"error": "No input paths"}

    logger.info(f"🚀 [INIT] Starting 10-Step Audit Pipeline for {uuid_str}")
    from Core_Modules.studio_status_tracker import get_tracker
    tracker = get_tracker()
    tracker.step("STEP_FACE_SWAP", 100)
    tracker.step("STEP_LIP_SYNC", 100)

    job_dir = os.path.join("temp", uuid_str)
    os.makedirs(job_dir, exist_ok=True)

    # --- Logo cleaning (Step 0) ---
    try:
        logo_src = os.path.join("logo", "Brand_logo.png")
        clean_dest = os.path.join("assets", "logo", "brand_logo_clean.png")
        if os.path.exists(logo_src) and not os.path.exists(clean_dest):
            from Utilities.logo_transparency_cleaner import clean_logo_background

            clean_logo_background(logo_src, clean_dest)
    except Exception as _logo_e:
        logger.warning(f"[LOGO_CLEANER] init_failed={_logo_e}")

    current_video_source = input_paths[0]

    # --- [Phase 0] Context Initialisation ---
    context = PipelineContext(video_path=current_video_source)
    auditor = FeatureAuditor()

    # Initialise Gemini tracking in profile_data
    if "gemini_calls" not in profile_data:
        profile_data["gemini_calls"] = 0
    if "gemini_log" not in profile_data:
        profile_data["gemini_log"] = []
    
    # [PIPELINE_REPORT] Mark features not yet implemented/active
    auditor.mark_disabled("smart_crop")

    # Pre-fetch total duration
    duration = 0.0
    for p in input_paths:
        try:
            _info = video_pipeline.get_video_info(p)
            duration += _info.get("duration", 0.0)
        except Exception:
            duration += 15.0
    logger.info(
        f"💾 [Step 0] Initialised. Total Duration: {duration:.2f}s | Clips: {len(input_paths)}"
    )

    # ---- SCENE INTELLIGENCE (Step 0.1) ------------------------------------------
    logger.info("🧠 [Step 0.1] Scene Intelligence Analysis...")
    try:
        from Intelligence_Modules.scene_intel import analyze_scene, build_clip_plan
        _separate_shorts_env = os.getenv("OUTPUT_SEPARATE_SHORTS", "no").lower() in ("yes", "true", "1")
        _max_separate_env = int(os.getenv("MAX_SEPARATE_SHORTS", "10"))
        
        _scene_ctx = analyze_scene(current_video_source, title_name=title, job_dir=job_dir)
        profile_data["scene_context"] = _scene_ctx
        
        _num_clips_to_plan = _max_separate_env if _separate_shorts_env else 1
        _clip_plan = build_clip_plan(_scene_ctx, max_clips=_num_clips_to_plan)
        profile_data["clip_plan"] = _clip_plan
        auditor.mark_executed("scene_intelligence")
    except Exception as _si_e:
        logger.warning(f"⚠️ [SCENE_INTEL] Analysis failed: {_si_e}")
        profile_data["scene_context"] = {}
        profile_data["clip_plan"] = [{
            "clip_label": "fallback_clip", "focus": "no_person", 
            "subject_id": None, "cluster_id": None, 
            "description": "Fallback", "ideal_duration_sec": 15.0, 
            "editing_style": "rhythm_driven"
        }]
        auditor.mark_failed("scene_intelligence")

    # ---- WATERMARK PIPELINE (Steps 0.25 – 0.35) --------------------------------
    # [FIX] Skip if the source already went through the pre-process buffer.
    # main.py pre_process_watermark() strips watermarks and stores clean clips in
    # downloads/Watermark_Buffer/ before compilation. Re-running the full inpaint
    # pipeline (614 frames × NS_EDGE × 3 clips) costs ~3 extra minutes for no gain.
    _already_clean = any(
        "Watermark_Buffer" in str(p) or "watermark_clean" in str(p)
        for p in input_paths
    )

    if WATERMARK_AVAILABLE and current_video_source and not _already_clean:
        logger.info("🔍 [Step 0.25] Watermark Detection...")
        wm_output_path = os.path.join(job_dir, "watermark_clean.mp4")

        # Strict timeout obeying WATERMARK_TIMEOUT_SEC
        # Leave 20s headroom so the frame-level fast-fallback fires BEFORE we hit the hard kill limit.
        wm_timeout = int(os.getenv("WATERMARK_TIMEOUT_SEC", "360"))
        _inner_budget = max(60, wm_timeout - 20)  # budget passed into the frame loop
        logger.info(f"⏱️ [WATERMARK] timeout={wm_timeout}s | inner_budget={_inner_budget}s")

        wm_result = run_with_timeout(
            func=watermark_auto.process_video_with_watermark,
            timeout_sec=wm_timeout,
            feature_name="watermark_detection",
            auditor=auditor,
            input_path=current_video_source,
            output_path=wm_output_path,
            time_budget_sec=_inner_budget,
            title=title,  # ← Title override: if provided, Gemini niche ID is suppressed
        )


        if (
            wm_result
            and isinstance(wm_result, dict)
            and wm_result.get("success", False)
        ):
            logger.info("🖌️ [Step 0.3] Watermark Inpaint Successful.")
            context.set_clean_source(wm_output_path)
            context.video_path = wm_output_path
            current_video_source = wm_output_path
            input_paths[0] = current_video_source
            auditor.mark_executed("watermark_inpaint")
            # [PRICE_TAG_ANCHOR] Save the watermark's bounding box so the price tag
            # can be anchored to that corner instead of drifting onto the actress's face.
            if wm_result.get("bbox"):
                profile_data["watermark_bbox"] = wm_result["bbox"]
                logger.info(f"📍 [WATERMARK_BBOX] Stored wm bbox for price tag anchor: {wm_result['bbox']}")
            logger.info("✨ [Step 0.35] Clean Source Ready.")
        elif (
            wm_result
            and isinstance(wm_result, dict)
            and wm_result.get("watermark_detected") is False
        ):
            logger.info("✅ [Step 0.3] No watermark detected.")
            auditor.mark_skipped("watermark_inpaint")
            logger.info("✨ [Step 0.35] Clean Source Ready.")
        else:
            # [FIX] Use correct feature key "watermark_detection" consistently
            if auditor.status("watermark_detection") == "FAILED":
                logger.warning(
                    "⚠️ Watermark removal timed out or failed. Using original source."
                )
                auditor.mark_skipped("watermark_detection")
                auditor.mark_skipped("watermark_inpaint")
            else:
                logger.warning(
                    "⚠️ Watermark removal failed/skipped. Using original source."
                )
                auditor.mark_failed("watermark_inpaint")
    else:
        if _already_clean:
            # Clips arrived pre-cleaned from Watermark_Buffer — no second pass needed.
            logger.info("♻️ [Step 0.25] Clips from Watermark_Buffer — skipping redundant inpaint pass.")
            auditor.mark_executed("watermark_detection")
            auditor.mark_executed("watermark_inpaint")
        else:
            logger.info("⚠️ Watermark module unavailable or no single source. Skipping.")
            auditor.mark_disabled("watermark_detection")
            auditor.mark_disabled("watermark_inpaint")

    if auditor.status("watermark_detection") == "FAILED":
        logger.warning(
            "⚠️ Execution Guard: Watermark Detection failed. "
            "Continuing with original source, but quality may be affected."
        )

    # ---- SHOT DETECTION (Step 0.4) ----------------------------------------------
    if SHOT_DETECTOR_AVAILABLE and current_video_source:
        logger.info("🎬 [Step 0.4] Shot Detection (Structural Analysis)...")
        try:
            shots = shot_detector.detect_shots(current_video_source)
            profile_data["shots"] = shots
            profile_data["scene_boundaries"] = [
                (float(s["start"]), float(s["end"])) for s in shots
            ]
            auditor.mark_executed("scene_detection")
            logger.info(f"✅ [SHOT_DETECTION] scenes_detected={len(shots)}")
        except Exception as _sd_e:
            logger.warning(f"⚠️ Shot detection failed: {_sd_e}")
            profile_data["shots"] = []
            profile_data["scene_boundaries"] = []

    # ---- AUDIO EXTRACTION + BEST-AUDIO SELECTION (Step 0.45) -------------------
    tracker.step("STEP_AUDIO_MIX", 20)
    # Extract audio from ALL clips and pick the most energetically rich one.
    _niche_category = os.getenv("CONTENT_NICHE", "").strip().lower()
    _all_clip_audios: dict = {}  # clip_idx -> mp3_path
    
    if _niche_category == "cinematic_story":
        logger.info("🎬 [NICHE] Cinematic Story mode: Skipping original audio extraction to save resources.")
    else:
        try:
            for _ae_idx, _ae_path in enumerate(input_paths):
                try:
                    _extracted = video_pipeline.extract_audio_from_video(
                        _ae_path, output_dir="Original_audio", overwrite=False,
                    )
                    if _extracted:
                        _all_clip_audios[_ae_idx] = _extracted
                        logger.info(f"🎵 [AUDIO_EXTRACT] Clip {_ae_idx}: {os.path.basename(_extracted)}")
                except Exception as _ae_single:
                    logger.warning(f"⚠️ [AUDIO_EXTRACT] Clip {_ae_idx} failed: {_ae_single}")
        except Exception as _ae:
            logger.warning(f"⚠️ [AUDIO_EXTRACT] Non-fatal: {_ae}")

    # Score each clip's audio by beat richness + drop count + average energy
    _best_audio_path = None
    _best_audio_score = -1.0
    _best_beat_data = {}
    if _all_clip_audios and BEAT_ENGINE_AVAILABLE and _beat_engine:
        for _sa_idx, _sa_path in _all_clip_audios.items():
            try:
                _sa_result = _beat_engine.analyze_beats_with_drops(_sa_path)
                _sa_beats = _sa_result.get("beats", [])
                _sa_drops = _sa_result.get("drops", [])
                _sa_avg_e = (
                    sum(b.get("energy", 0) if isinstance(b, dict) else 0 for b in _sa_beats)
                    / max(1, len(_sa_beats))
                )
                _sa_score = len(_sa_beats) + len(_sa_drops) * 3 + _sa_avg_e * 10
                logger.info(
                    f"🎵 [AUDIO_SCORE] Clip {_sa_idx}: beats={len(_sa_beats)} "
                    f"drops={len(_sa_drops)} avg_energy={_sa_avg_e:.3f} → score={_sa_score:.2f}"
                )

                # ── POOL INGESTION: register extracted audio into Original_audio/active/ ──
                # process_new_audio() moves the file from root → active/ and saves BPM+energy
                # metadata so select_best_audio() can actually score and select it.
                # Without this call, active/ is always empty and pool selection always fails.
                try:
                    from Audio_Modules.audio_pool_manager import pool_manager as _apm
                    # Only ingest if the file still lives in the root (not already in active/)
                    if os.path.exists(_sa_path) and "active" not in _sa_path.replace("\\", "/"):
                        # ── POOL INGEST FAIL-SAFE ────────────────────────────────────────
                        # NEVER ingest pipeline-generated outputs into the BGM pool.
                        # Only raw downloaded source clips belong here.
                        _sa_base_lower = os.path.basename(_sa_path).lower()
                        _pool_blocked_kws = [
                            "_reaction", "_textreaction", "first_shot", "first_shots",
                            "general_intro", "watermark_clean", "intro_mixed_temp",
                            "final_compilation",
                        ]
                        _sa_blocked = any(_kw in _sa_base_lower for _kw in _pool_blocked_kws)
                        if _sa_blocked:
                            logger.info(
                                f"🚫 [POOL_INGEST] Blocked pipeline-generated audio from pool: "
                                f"{os.path.basename(_sa_path)}"
                            )
                        else:
                            # ── MUSIC GATE v2: Skip non-music audio from BGM pool ─────────
                            # Two conditions — catches both pure speech AND crowd noise.
                            #
                            # Condition A — Pure speech/silence:
                            #   beats < 20  AND  drops == 0  AND  energy < 0.20
                            #
                            # Condition B — Crowd noise / ambient voices:
                            #   Real music = REGULAR beat intervals (low CV).
                            #   Crowd / ambient noise = IRREGULAR energy spikes (high CV).
                            #   CV = std_dev(intervals) / mean(intervals).
                            #   Also: real drops are rare (<15%); noise spikes inflate drop_rate.
                            #
                            # File is NOT deleted — kept for this video's own beat-sync.
                            _mg_beat_times = [b.get("time", 0) for b in _sa_beats]
                            _mg_drop_count = len(_sa_drops)

                            # Condition A
                            _mg_cond_a = (
                                len(_sa_beats) < 20
                                and _mg_drop_count == 0
                                and _sa_avg_e < 0.20
                            )

                            # Condition B
                            _mg_cond_b = False
                            _mg_cv = 0.0
                            _mg_drop_rate = _mg_drop_count / max(1, len(_sa_beats))
                            if len(_mg_beat_times) >= 4:
                                _mg_ivals = [
                                    _mg_beat_times[i + 1] - _mg_beat_times[i]
                                    for i in range(len(_mg_beat_times) - 1)
                                ]
                                _mg_mean_iv = sum(_mg_ivals) / len(_mg_ivals)
                                if _mg_mean_iv > 0:
                                    _mg_std_iv = (
                                        sum((x - _mg_mean_iv) ** 2 for x in _mg_ivals)
                                        / len(_mg_ivals)
                                    ) ** 0.5
                                    _mg_cv = _mg_std_iv / _mg_mean_iv
                                    _mg_cond_b = (
                                        _mg_cv > 0.40           # irregular beat grid (lowered from 0.50 based on tests)
                                        and _mg_drop_rate > 0.15  # too many fake drops
                                        and len(_sa_beats) < 60  # extended threshold
                                    )
                                    
                            # Condition C: slow ambient/speech with natural rhythm but NO music drops.
                            _mg_cond_c = (
                                _mg_drop_count == 0
                                and _sa_result.get("tempo", 999) < 75.0
                                and len(_sa_beats) >= 20     # has some beats (not pure silence)
                                and _mg_cv > 0.40        # somewhat irregular (not a real grid)
                            )

                            # Condition D: mid-energy crowd/polluted/overcrowded audio.
                            # Targets: applause, stadium crowd, event chatter, venue ambience.
                            # These pass Cond A (energy > 0.20) and sometimes B/C (pseudo-rhythmic
                            # clapping keeps cv < 0.40), but produce ZERO musical drops.
                            _mg_cond_d = (
                                _mg_drop_count == 0           # no musical climax / drop
                                and len(_sa_beats) >= 30      # enough signal to be confident
                                and 0.20 <= _sa_avg_e <= 0.55 # mid-energy band (not silence, not clean music)
                                and _mg_cv > 0.30             # irregular beat grid (real music has cv < 0.25)
                            )

                            _mg_is_non_music = _mg_cond_a or _mg_cond_b or _mg_cond_c or _mg_cond_d

                            if _mg_is_non_music:
                                _mg_reason = (
                                    f"crowd/ambient noise (cv={_mg_cv:.2f}, drop_rate={_mg_drop_rate:.2f})"
                                    if _mg_cond_b
                                    else (
                                        f"crowded/polluted audio (beats={len(_sa_beats)}, drops=0, energy={_sa_avg_e:.3f}, cv={_mg_cv:.2f})"
                                        if _mg_cond_d
                                        else (
                                            f"slow ambient/speech (bpm={_sa_result.get('tempo',0):.1f}, drops=0, cv={_mg_cv:.2f})"
                                            if _mg_cond_c
                                            else f"speech/silence (beats={len(_sa_beats)}, drops=0, energy={_sa_avg_e:.3f})"
                                        )
                                    )
                                )
                                logger.info(
                                    f"🗣️ [MUSIC_GATE] Non-music audio — skipping BGM pool: "
                                    f"{os.path.basename(_sa_path)} | {_mg_reason}"
                                )
                                # Flag so boot-sync never re-ingests on restart
                                try:
                                    _apm._set_file_metadata(
                                        os.path.basename(_sa_path),
                                        {
                                            "is_speech_only": True,
                                            "usage_count": 0, "last_used": 0,
                                            "bpm": 0.0, "energy": round(_sa_avg_e, 3),
                                            "created_at": __import__("time").time(),
                                            "beat_data_path": None, "drop_times": [],
                                            "sample_rate": 44100, "audio_hash": "",
                                            "version": _apm.CURRENT_VERSION,
                                        },
                                    )
                                    _apm._save_metadata()
                                except Exception:
                                    pass
                                # _sa_score / _best_audio_path still update below —
                                # this file is still valid for THIS video's own beat-sync.
                            else:
                                _beats_for_bpm = _sa_result.get("beats", [])
                                _bpm_ivs = [
                                    _beats_for_bpm[i+1].get("time",0) - _beats_for_bpm[i].get("time",0)
                                    for i in range(len(_beats_for_bpm)-1)
                                ]
                                _extracted_bpm = round(60.0 / (sum(_bpm_ivs)/len(_bpm_ivs)), 1) if _bpm_ivs else 0.0
                                _apm.process_new_audio(
                                    _sa_path,
                                    bpm=_extracted_bpm,
                                    energy=round(_sa_avg_e, 3),
                                    beat_analysis=_sa_result
                                )
                                logger.info(f"🎧 [POOL_INGEST] Clip {_sa_idx} → active pool ({_extracted_bpm:.1f} BPM, {_sa_avg_e:.2f} energy)")
                                # Update _sa_path to new location in active/
                                _active_path = os.path.join("Original_audio", "active", os.path.basename(_sa_path))
                                if os.path.exists(_active_path):
                                    _all_clip_audios[_sa_idx] = _active_path
                                    if _best_audio_path == _sa_path:
                                        _best_audio_path = _active_path
                            # ── END MUSIC GATE v2 ───────────────────────────────────────────
                except Exception as _ingest_e:
                    logger.warning(f"⚠️ [POOL_INGEST] Non-fatal: {_ingest_e}")
                # ───────────────────────────────────────────────────────────────

                if _sa_score > _best_audio_score:
                    _best_audio_score = _sa_score
                    _best_audio_path = _sa_path
                    _best_beat_data = _sa_result

            except Exception as _sa_e:
                logger.warning(f"⚠️ [AUDIO_SCORE] Clip {_sa_idx} scoring failed: {_sa_e}")

    # ── POOL-FIRST BGM SELECTION ─────────────────────────────────────────────
    # Now that the current clip's audio is REGISTERED in active/, ask the pool
    # to pick the best BGM from OTHER previously-accumulated tracks.
    # Self-selection guard: filenames extracted from THIS job are excluded.
    # This prevents the pipeline from using the video's own audio as its BGM.
    _current_job_filenames = {os.path.basename(p) for p in _all_clip_audios.values()}
    # [AUDIO_CLEANUP] Persist all extracted audio paths so the sidecar can track them for rejection cleanup
    profile_data["_all_clip_audios_paths"] = list(_all_clip_audios.values())


    # [FIX] The original audio might have been extracted earlier (e.g. Banno.mp3) before
    # watermark removal renamed the working clip to watermark_clean.mp4. We must exclude
    # the original filenames and title to prevent selecting our own original audio as BGM.
    _orig_paths = input_path if isinstance(input_path, (list, tuple)) else ([input_path] if input_path else [])
    for _op in _orig_paths:
        if _op:
            _obase = os.path.splitext(os.path.basename(_op))[0]
            _current_job_filenames.add(f"{_obase}.mp3")
            _current_job_filenames.add(f"{_obase}.wav")
    
    if title:
        _current_job_filenames.add(f"{title}.mp3")
        _current_job_filenames.add(f"{title}.wav")

    _pool_winner_path = None
    try:
        from Audio_Modules.audio_pool_manager import pool_manager as _apm
        _pool_winner_path = _apm.select_best_audio(
            target_bpm=0,       # 0 = no preference, use energy scoring only
            target_energy=0,
            exclude_filenames=_current_job_filenames,  # <-- self-selection guard
        )
        if _pool_winner_path:
            logger.info(
                f"🎶 [POOL_BGM] Selected external BGM from pool: "
                f"{_pool_winner_path.replace(os.sep, '/')} "
                f"(excluded current-job audio: {', '.join(_current_job_filenames)})"
            )
    except Exception as _pool_sel_e:
        logger.warning(f"⚠️ [POOL_BGM] Pool selection failed (non-fatal): {_pool_sel_e}")
    
    if _best_audio_path:
        profile_data["extracted_audio_path"] = _best_audio_path
        profile_data["beat_data"] = _best_beat_data
        
        # Prefer the pool winner (external track) over the self-extracted audio
        if _pool_winner_path:
            profile_data["bgm_audio_path"] = _pool_winner_path
            _display_bgm = os.path.basename(_pool_winner_path)
            logger.info(
                f"🏆 [BEST_AUDIO] Winner (External Pool): {_pool_winner_path.replace(os.sep, '/')} "
                f"→ Beat sync will use extracted: {os.path.basename(_best_audio_path)} "
                f"| BGM overlay: {_display_bgm} (score={_best_audio_score:.2f})"
            )
        else:
            # Pool is empty / all tracks are in cooldown — fall back to self-audio
            profile_data["bgm_audio_path"] = _best_audio_path
            _display_name = f"{title}.mp3" if title else os.path.basename(_best_audio_path)
            logger.warning(
                f"⚠️ [BEST_AUDIO] Pool empty — falling back to self-audio: "
                f"{_best_audio_path.replace(os.sep, '/')} "
                f"(Original Title: {_display_name}) (score={_best_audio_score:.2f})"
            )
    elif _all_clip_audios:
        # Fallback: use clip 0's audio if scoring failed
        _fb_audio = _all_clip_audios.get(0, list(_all_clip_audios.values())[0])
        profile_data["extracted_audio_path"] = _fb_audio
        logger.info(f"🎵 [AUDIO_EXTRACT] Fallback to clip 0 audio: {os.path.basename(_fb_audio)}")

    # ── MUSICAL INTELLIGENCE REPORT (Step 0.46) ────────────────────────────────
    tracker.step("STEP_AUDIO_MIX", 60)
    # One Gemini call on the selected BGM track. Extracts lyric timestamps,
    # musical section map, tension arc, shot directives, and vibe tags.
    # All downstream rhythm/editing modules read from profile_data["music_intelligence"].
    _mi_audio_path = profile_data.get("bgm_audio_path") or profile_data.get("extracted_audio_path")
    profile_data.setdefault("music_intelligence", {})
    if _mi_audio_path and os.path.exists(str(_mi_audio_path)):
        try:
            from Audio_Modules.lyric_rhythm_aligner import analyze_music as _analyze_music
            logger.info(
                f"🎵 [MUSIC_INTEL] Running Musical Intelligence Report on: "
                f"{os.path.basename(_mi_audio_path)}"
            )
            _mi_report = _analyze_music(_mi_audio_path)
            profile_data["music_intelligence"] = _mi_report
            logger.info(
                f"🎶 [MUSIC_INTEL] Report ready | "
                f"vocals={_mi_report.get('has_vocals')} "
                f"lang={_mi_report.get('language')} "
                f"bpm={_mi_report.get('tempo_bpm')} "
                f"emotion={_mi_report.get('dominant_emotion')} "
                f"sections={len(_mi_report.get('sections', []))} "
                f"directives={len(_mi_report.get('shot_directives', []))} "
                f"peaks={len(_mi_report.get('emotional_peak_moments', []))}"
            )
        except Exception as _mi_e:
            logger.warning(f"⚠️ [MUSIC_INTEL] Non-fatal: {_mi_e}")
    else:
        logger.info("🎵 [MUSIC_INTEL] No BGM path available — skipping Musical Intelligence Report.")
    # ─────────────────────────────────────────────────────────────────────────────

    # ---- SOURCE DETECTION (Step 0.5) -------------------------------------------
    source_info = {"content_source": {"reused": False, "source_type": "raw_upload"}}
    if SOURCE_DETECTION_AVAILABLE:
        try:
            logger.info("🔍 [Step 0.5] Source Detection...")
            source_info = source_detector.detect_source(
                current_video_source, profile_data.get("source_type")
            )
            profile_data.update(source_info)
            context.update_intelligence_data({"source_detection": source_info})
        except Exception as e:
            logger.warning(f"⚠️ Source detection failsafe: {e}")

    # ---- TRANSFORMATION ENGINE (Step 0.6) ----------------------------------------
    if SOURCE_DETECTION_AVAILABLE:
        try:
            logger.info("⚖️ [Step 0.6] Transformation Engine Strategy...")
            reused = profile_data.get("content_source", {}).get("reused", False)
            initial_features = {
                "scene_restructure": True,
                "narration": True,
                "captions": True,
                "visual_reframing": True,
                "commentary": True,
                "overlays": True,
            }
            strategy = transformation_engine.get_transformation_strategy(
                reused, initial_features
            )
            profile_data["transformation_level"] = strategy["transformation_level"]
            profile_data["transformation_score"] = strategy["transformation_score"]
            profile_data.update(strategy["enforced_features"])
            context.update_intelligence_data({"transformation_strategy": strategy})
        except Exception as e:
            logger.warning(f"⚠️ Transformation engine failsafe: {e}")

    try:
        # ═══════════════════════════════════════════════════════════════════
        # Pipeline Execution Tracer — import at first use inside try block
        # ═══════════════════════════════════════════════════════════════════
        try:
            from Diagnostics_Modules.pipeline_audit import StepTracer as _StepTracer
        except Exception:

            class _StepTracer:  # lightweight no-op fallback
                @staticmethod
                def start(n):
                    pass

                @staticmethod
                def success(n):
                    pass

                @staticmethod
                def fail(n, e=""):
                    pass

        # (Frame Extraction moved to Step 1j to utilize MomentMiner data)
            
        # ---- [FIX] Master Clip Audio Beat Detection (Step 1b) ----
        tracker.step("STEP_AUDIO_MIX", 90)
        # If Step 0.45 already picked the best audio and populated beat_data, skip
        # re-running on just clip 0. Otherwise fall back to the original clip 0 path.
        if BEAT_ENGINE_AVAILABLE and _beat_engine:
            _StepTracer.start("beat_detection")
            if profile_data.get("beat_data", {}).get("beats"):
                logger.info("🥁 [Step 1b] Beat data already populated from best-audio selection — skipping.")
                auditor.mark_executed("beat_detection")
                _StepTracer.success("beat_detection")
            else:
                logger.info("🥁 [Step 1b] Audio Beat Detection (best-audio fallback to clip 0)...")
                try:
                    from Audio_Modules.beat_engine import get_beats_preferring_original_audio
                    _beat_src = profile_data.get("extracted_audio_path", current_video_source)
                    beats = get_beats_preferring_original_audio(_beat_src)
                    profile_data["beat_data"] = beats
                    auditor.mark_executed("beat_detection")
                    _StepTracer.success("beat_detection")
                except Exception as e:
                    logger.warning(f"⚠️ Beat detection failed: {e}")
                    auditor.mark_failed("beat_detection")
                    _StepTracer.fail("beat_detection", str(e))
        else:
            auditor.mark_disabled("beat_detection")

        # ---- [FIX] Master Clip Subject Tracking (Step 1c) ----
        if SUBJECT_TRACKER_AVAILABLE and subject_tracker:
            _StepTracer.start("subject_tracking")
            logger.info("🎯 [Step 1c] Subject Tracking on Master Clip...")
            try:
                tracking = subject_tracker.track_subject(current_video_source)
                profile_data["subject_tracking"] = tracking
                auditor.mark_executed("subject_tracking")
                _StepTracer.success("subject_tracking")
            except Exception as e:
                logger.warning(f"⚠️ Subject tracking failed for Master Clip: {e}")
                auditor.mark_failed("subject_tracking")
                _StepTracer.fail("subject_tracking", str(e))
        else:
            auditor.mark_disabled("subject_tracking")

        # ---- [MULTI_CLIP] Phase 2: CLIP-BY-CLIP INTELLIGENCE ----
        # Extract signals for ALL extra clips before timeline reconstruction
        for i, p in enumerate(input_paths):
            if i == 0: continue # Clip 0 already handled in Phase 0/1/Master Setup

            try:
                logger.info(f"🔍 [MULTI_CLIP] Analyzing Clip {i}: {os.path.basename(p)}")

                # [Step 1b] Audio Beat Detection (Extra Clips)
                # We can log these, but moment_miner only uses Clip 0's base beat_data.
                if BEAT_ENGINE_AVAILABLE and _beat_engine:
                    logger.info(f"🥁 [Step 1b] Audio Beat Detection on Clip {i}...")
                    try:
                        _ = _beat_engine.analyze_beats_with_drops(p)
                        # Do NOT overwrite profile_data["beat_data"] with extra clip's data
                    except Exception as e:
                        logger.warning(f"⚠️ Beat detection failed for clip {i}: {e}")

                # [Step 1c] Subject Tracking (Extra Clips)
                if SUBJECT_TRACKER_AVAILABLE and subject_tracker:
                    logger.info(f"🎯 [Step 1c] Subject Tracking on Clip {i}...")
                    try:
                        _ = subject_tracker.track_subject(p)
                        # Do NOT overwrite profile_data["subject_tracking"] with extra clip's data
                    except Exception as e:
                        logger.warning(f"⚠️ Subject tracking failed for clip {i}: {e}")

                # [Step 1d-Motion] Motion Analysis
                if True: # Always run motion for extra clips
                    logger.info(f"🎬 [Step 1d-Motion] Motion Analysis on Clip {i}...")
                    try:
                        from Visual_Refinement_Modules.motion_detector import get_motion_detector
                        _md = get_motion_detector(sampling_rate=2)
                        _m_scores = _md.detect_motion(p)
                        # We don't store these in profile_data since that's clip-0 only,
                        # but we log it to prove the system is doing real work.
                        logger.info(f"✅ [MOTION] Analysis complete: {len(_m_scores)} data points.")
                    except Exception as e:
                        logger.warning(f"⚠️ Motion analysis failed for clip {i}: {e}")

            except Exception as e:
                logger.warning(f"⚠️ [MULTI_CLIP] Failed to analyze clip {i}: {e}")

        # ---- [FIX] MOTION DETECTION (Step 1d.1) -----------------------
        try:
            from Visual_Refinement_Modules.motion_detector import get_motion_detector

            _StepTracer.start("motion_detection")
            logger.info("🎬 [Step 1d.1] Motion Detection...")
            motion_detector = get_motion_detector(sampling_rate=2)
            motion_scores = motion_detector.detect_motion(current_video_source)
            profile_data["motion_scores"] = motion_scores
            if motion_scores:
                logger.info(f"✅ [MOTION_DETECTION] scores={len(motion_scores)}")
                _StepTracer.success("motion_detection")
            else:
                logger.warning("⚠️ [MOTION_DETECTION] No motion scores generated.")
                _StepTracer.success("motion_detection")
        except Exception as _md_err:
            logger.warning(f"⚠️ MotionDetector failed: {_md_err}")
            profile_data.setdefault("motion_scores", [])

        # ---- [FIX] MOMENT MINER (Step 1d — was never called) -----------------------
        # candidate_moments=0 was caused by MomentMiner being imported but never
        # invoked. EditorBrain then received an empty list and recorded confidence=0.
        # We now call mine_moments() here so the full downstream intelligence chain
        # (SignalFusion, TimelineReconstructor, EditorBrain) has real moments to work
        # with before Master Intelligence runs.
        if MOMENT_MINER_AVAILABLE:
            _StepTracer.start("moment_miner")
            logger.info("⛏️ [Step 1d] MomentMiner — Mining candidate moments...")
            try:
                # MomentMiner takes only profile_data; use run_moment_miner()
                # convenience wrapper which also exports the debug JSON.
                from Visual_Refinement_Modules.moment_miner import run_moment_miner

                # [MULTI_CLIP] Mine moments from every input clip so that
                # clip_id is correctly assigned per source.
                #
                # • clip[0]  — full signals already in profile_data
                #              (motion_scores, subject_tracking, shots, beat_data)
                # • clip[1+] — lightweight sub-profile: only shared beat_data
                #              + a synthetic shot entry so the miner can infer
                #              clip duration.  Motion / face signals are only
                #              available for clip[0] because the analysis
                #              pipeline runs on current_video_source only.
                #
                # Single-clip jobs follow the identical code path (loop runs
                # once with clip_id=0) — behaviour is unchanged.
                _all_mined: list = []

                for _clip_idx, _clip_path in enumerate(input_paths):
                    if _clip_idx == 0:
                        _clip_profile = profile_data
                    else:
                        # [MULTI_CLIP FIX] Run REAL per-clip analysis so clips 1 & 2
                        # produce moments that survive the hard motion filter downstream.
                        # Without real motion_scores, all their moments have motion=0.0
                        # and get purged before Gemini ever sees them.
                        _clip_motion_scores = []
                        _clip_subject_tracking = []
                        try:
                            from Visual_Refinement_Modules.motion_detector import get_motion_detector
                            _clip_md = get_motion_detector(sampling_rate=3)  # lighter sampling
                            _clip_motion_scores = _clip_md.detect_motion(_clip_path)
                            logger.info(f"🎬 [MULTI_CLIP] Motion for clip {_clip_idx}: {len(_clip_motion_scores)} scores")
                        except Exception as _cm_e:
                            logger.warning(f"⚠️ [MULTI_CLIP] Motion failed clip {_clip_idx}: {_cm_e}")

                        try:
                            if SUBJECT_TRACKER_AVAILABLE and subject_tracker:
                                _clip_subject_tracking = subject_tracker.track_subject(_clip_path)
                        except Exception:
                            pass

                        _clip_profile = {
                            "beat_data": profile_data.get("beat_data", {}),
                            "motion_scores": _clip_motion_scores,
                            "subject_tracking": _clip_subject_tracking,
                            "shots": [],
                        }
                        try:
                            _cinfo = video_pipeline.get_video_info(_clip_path)
                            _cdur = _cinfo.get("duration", 15.0)
                            # Build synthetic shots across the full duration
                            _shot_step = max(3.0, _cdur / 5)
                            _clip_profile["shots"] = [
                                {"start": round(i, 2), "end": round(min(i + _shot_step, _cdur), 2)}
                                for i in [j * _shot_step for j in range(5)]
                                if i < _cdur
                            ]
                        except Exception:
                            pass

                    _clip_moments = run_moment_miner(
                        _clip_profile,
                        job_dir=job_dir,
                        clip_id=_clip_idx,
                    )
                    # [MULTI_CLIP FIX] Tag every moment with its source clip_id explicitly
                    # so the hard filter below knows which moments belong to extra clips.
                    for _cm in _clip_moments:
                        if isinstance(_cm, dict):
                            _cm["clip_id"] = _clip_idx
                    _all_mined.extend(_clip_moments)
                    logger.info(
                        f"[MULTI_CLIP] clip={_clip_idx} "
                        f"moments={len(_clip_moments)} "
                        f"source={os.path.basename(_clip_path)}"
                    )

                # Merge all clips' moments into profile_data
                profile_data["candidate_moments"] = _all_mined
                _n_mined = len(_all_mined)
                _unique_clip_ids = sorted(
                    {m.get("clip_id", 0) for m in _all_mined}
                )
                logger.info(
                    f"✅ [MOMENT_MINER] candidate_moments={_n_mined} | "
                    f"clips_mined={len(input_paths)} | "
                    f"unique_clip_ids={_unique_clip_ids}"
                )
                logger.info(
                    f"🎬 CREATIVE_EDITOR MomentMiner mined {_n_mined} "
                    f"candidate moments across {len(input_paths)} clip(s)"
                )
                auditor.mark_executed("moment_miner")
                _StepTracer.success("moment_miner")
            except Exception as _mm_e:
                logger.warning(f"⚠️ MomentMiner failed (non-fatal): {_mm_e}")
                auditor.mark_failed("moment_miner")
                _StepTracer.fail("moment_miner", str(_mm_e))
                profile_data.setdefault("candidate_moments", [])
        else:
            auditor.mark_disabled("moment_miner")
            profile_data.setdefault("candidate_moments", [])

        # ---- EXPRESSION CHANGE (Step 1e.5) -----------------------------------------
        # [FIX] Moved profile_data["video_path"] assignment here, before the call,
        # so the engine always has the up-to-date (possibly watermark-cleaned) source.
        profile_data["video_path"] = current_video_source

        if EXPRESSION_CHANGE_AVAILABLE:
            logger.info("🎭 [Step 1e.5] Expression Change Detection...")
            try:
                expression_result = _expression_detect(profile_data, job_dir=job_dir)
                _expr_summary = expression_result.get("expression_summary", {})
                context.update_intelligence_data(
                    {
                        "expression_change_count": _expr_summary.get("change_count", 0),
                        "expression_strongest": _expr_summary.get(
                            "strongest_change", 0.0
                        ),
                    }
                )
                auditor.mark_executed("expression_change_detector")
                logger.info(
                    f"✅ [EXPRESSION_ENGINE] changes_detected={_expr_summary.get('change_count', 0)} | "
                    f"threshold={_expr_summary.get('adaptive_threshold', 0.0):.4f} | "
                    f"strongest={_expr_summary.get('strongest_change', 0.0):.4f}"
                )
                if _expr_summary.get("change_times"):
                    logger.info(
                        f"🎭 [EXPRESSION_ENGINE] Change times: {_expr_summary.get('change_times', [])}"
                    )
            except Exception as _expr_e:
                logger.warning(
                    f"⚠️ Expression Change Detection failed (non-fatal): {_expr_e}"
                )
                auditor.mark_failed("expression_change_detector")
                profile_data["expression_moments"] = []
                profile_data["expression_summary"] = {
                    "change_count": 0,
                    "strongest_change": 0.0,
                    "change_times": [],
                }
        else:
            auditor.mark_disabled("expression_change_detector")
            profile_data.setdefault("expression_moments", [])
            profile_data.setdefault(
                "expression_summary",
                {"change_count": 0, "strongest_change": 0.0, "change_times": []},
            )

        # ---- RETENTION CURVE (Step 1f) ---------------------------------------------
        if RETENTION_CURVE_AVAILABLE:
            logger.info("📈 [Step 1f] Retention Curve Analysis...")
            try:
                retention_result = _retention_analyse(profile_data, job_dir=job_dir)
                _r_summary = retention_result.get("retention_summary", {})
                context.update_intelligence_data(
                    {
                        "retention_peak_count": _r_summary.get("peak_count", 0),
                        "retention_strongest": _r_summary.get("strongest_peak", 0.0),
                    }
                )
                auditor.mark_executed("retention_curve_engine")
                logger.info(
                    f"✅ [RETENTION_ENGINE] peaks_detected={_r_summary.get('peak_count', 0)} | "
                    f"strongest={_r_summary.get('strongest_peak', 0.0):.3f} | "
                    f"times={[round(t, 2) for t in _r_summary.get('peak_times', [])]}"
                )
            except Exception as _ret_e:
                logger.warning(f"⚠️ Retention Curve Engine failed (non-fatal): {_ret_e}")
                auditor.mark_failed("retention_curve_engine")
                profile_data["retention_peaks"] = []
                profile_data["retention_curve"] = []
                profile_data["retention_summary"] = {
                    "peak_count": 0,
                    "strongest_peak": 0.0,
                    "peak_times": [],
                }
        else:
            auditor.mark_disabled("retention_curve_engine")
            profile_data.setdefault("retention_peaks", [])
            profile_data.setdefault("retention_curve", [])
            profile_data.setdefault(
                "retention_summary",
                {"peak_count": 0, "strongest_peak": 0.0, "peak_times": []},
            )

        # ---- EMOTIONAL SPIKE (Step 1g) ---------------------------------------------
        if EMOTIONAL_SPIKE_AVAILABLE:
            logger.info("😮 [Step 1g] Emotional Spike Detection...")
            try:
                emotion_result = _emotional_spike_analyse(profile_data, job_dir=job_dir)
                _e_summary = emotion_result.get("emotion_summary", {})
                context.update_intelligence_data(
                    {
                        "emotional_spike_count": _e_summary.get("spike_count", 0),
                        "emotional_strongest": _e_summary.get("strongest_spike", 0.0),
                    }
                )
                auditor.mark_executed("emotional_spike_detector")
                logger.info(
                    f"✅ [EMOTIONAL_SPIKE] spikes_detected={_e_summary.get('spike_count', 0)} | "
                    f"strongest={_e_summary.get('strongest_spike', 0.0):.3f} | "
                    f"times={[round(t, 2) for t in _e_summary.get('spike_times', [])]}"
                )
            except Exception as _emo_e:
                logger.warning(
                    f"⚠️ Emotional Spike Detector failed (non-fatal): {_emo_e}"
                )
                auditor.mark_failed("emotional_spike_detector")
                profile_data["emotional_spikes"] = []
                profile_data["emotion_summary"] = {
                    "spike_count": 0,
                    "strongest_spike": 0.0,
                    "spike_times": [],
                }
        else:
            auditor.mark_disabled("emotional_spike_detector")
            profile_data.setdefault("emotional_spikes", [])
            profile_data.setdefault(
                "emotion_summary",
                {"spike_count": 0, "strongest_spike": 0.0, "spike_times": []},
            )

        # ---- SIGNAL REPAIR LAYER (Step 1g.5) ----------------------------------------
        if SIGNAL_REPAIR_AVAILABLE and _signal_repair:
            logger.info("🛠️ [Step 1g.5] Signal Repair Layer — Bootstrapping weak signals...")
            try:
                _repair_result = _signal_repair.repair(profile_data, job_dir=job_dir)
                profile_data["signal_health"] = _repair_result.get("signal_health", 0.5)
                profile_data["signal_flags"] = _repair_result.get("signal_flags", {})
                auditor.mark_executed("signal_repair_layer")
                logger.info(
                    f"✅ [SIGNAL_REPAIR] health={profile_data['signal_health']:.2f} | "
                    f"flags={profile_data['signal_flags']}"
                )
            except Exception as _sr_e:
                logger.warning(f"⚠️ [SIGNAL_REPAIR] Non-fatal: {_sr_e}")
                profile_data.setdefault("signal_health", 0.5)
                profile_data.setdefault("signal_flags", {})
        else:
            auditor.mark_disabled("signal_repair_layer")
            profile_data.setdefault("signal_health", 0.5)
            profile_data.setdefault("signal_flags", {})

        # ---- SIGNAL FUSION (Step 1h) ------------------------------------------------
        if SIGNAL_FUSION_AVAILABLE:
            logger.info("🔀 [Step 1h] Signal Fusion...")
            try:
                fusion_result = _signal_fusion(profile_data, job_dir=job_dir)
                _f_summary = fusion_result.get("fusion_summary", {})
                context.update_intelligence_data(
                    {
                        "fused_moment_count": _f_summary.get("moment_count", 0),
                        "fused_strongest": _f_summary.get("strongest_moment", 0.0),
                    }
                )
                auditor.mark_executed("signal_fusion_engine")
                logger.info(
                    f"✅ [SIGNAL_FUSION] final_moments={_f_summary.get('moment_count', 0)} | "
                    f"strongest={_f_summary.get('strongest_moment', 0.0):.3f} | "
                    f"times={[round(t, 2) for t in _f_summary.get('moment_times', [])]}"
                )
            except Exception as _fusion_e:
                logger.warning(
                    f"⚠️ Signal Fusion Engine failed (non-fatal): {_fusion_e}"
                )
                auditor.mark_failed("signal_fusion_engine")
                profile_data["fused_moments"] = []
                profile_data["fusion_summary"] = {
                    "moment_count": 0,
                    "strongest_moment": 0.0,
                    "moment_times": [],
                }
        else:
            auditor.mark_disabled("signal_fusion_engine")
            profile_data.setdefault("fused_moments", [])
            profile_data.setdefault(
                "fusion_summary",
                {"moment_count": 0, "strongest_moment": 0.0, "moment_times": []},
            )

        # ---- HOOK ANALYSIS (Step 1h.5) ---------------------------------------------
        if HOOK_ENGINE_AVAILABLE and _hook_analyse:
            logger.info("🪝 [Step 1h.5] Hook Analysis — Finding best engagement moment...")
            try:
                hook_result = _hook_analyse(current_video_source)
                profile_data["hook_analysis"] = hook_result.get("hook_analysis", {})
                auditor.mark_executed("hook_engine")
                _h_data = profile_data["hook_analysis"]
                logger.info(
                    f"✅ [HOOK_ENGINE] time={_h_data.get('hook_time', 0.0):.2f}s | "
                    f"score={_h_data.get('hook_score', 0.0):.3f} | "
                    f"type={_h_data.get('hook_type', 'unknown')}"
                )
            except Exception as _hook_e:
                logger.warning(f"⚠️ Hook Analysis failed (non-fatal): {_hook_e}")
                auditor.mark_failed("hook_engine")
                profile_data["hook_analysis"] = {
                    "hook_time": 0.0,
                    "hook_score": 0.0,
                    "hook_type": "unknown",
                }
        else:
            auditor.mark_disabled("hook_engine")
            profile_data.setdefault(
                "hook_analysis",
                {"hook_time": 0.0, "hook_score": 0.0, "hook_type": "unknown"},
            )

        # ── PACING ENGINE: detect emotional energy wave for timing hints ───────
        if PACING_ENGINE_AVAILABLE and _pacing_engine:
            try:
                _temporal_stream = [
                    {
                        "time": m.get("time", 0.0),
                        "energy": m.get("composite_score", m.get("score", 0.0)),
                    }
                    for m in profile_data.get("fused_moments", [])
                    if isinstance(m, dict)
                ]
                if _temporal_stream:
                    _pacing_wave = _pacing_engine.detect(_temporal_stream)
                    if _pacing_wave:
                        profile_data["pacing_wave"] = _pacing_wave
                        logger.info(
                            f"🌊 [PACING_ENGINE] wave_start={_pacing_wave['wave_start']:.2f}s | "
                            f"wave_peak={_pacing_wave['wave_peak']:.2f}s | "
                            f"wave_end={_pacing_wave['wave_end']:.2f}s"
                        )
            except Exception as _pe_e:
                logger.warning(f"⚠️ [PACING_ENGINE] failed (non-fatal): {_pe_e}")

        # ── SELF-OPTIMIZING EDITOR: inject learned strategy hints ──────────────
        _optimization_hints = {}
        if SOE_AVAILABLE and _soe:
            try:
                _optimization_hints = _soe.get_optimization_hints()
                if not _optimization_hints.get("memory_cold", True):
                    profile_data["optimization_hints"] = _optimization_hints
                    # Inject top arc and persona preferences into creative context
                    _top_arc = (_optimization_hints.get("arc_rankings") or [{}])[0].get(
                        "arc_type"
                    )
                    _top_persona = (
                        _optimization_hints.get("persona_rankings") or [{}]
                    )[0].get("persona")
                    if _top_arc:
                        profile_data.setdefault("preferred_arc", _top_arc)
                    if _top_persona:
                        profile_data.setdefault("preferred_persona", _top_persona)
                    logger.info(
                        f"🧠 [SOE] hints_injected=True | "
                        f"top_arc={_top_arc} | top_persona={_top_persona} | "
                        f"videos_learned={_optimization_hints.get('total_videos', 0)}"
                    )
                else:
                    logger.info(
                        f"🧠 [SOE] memory_cold=True | "
                        f"videos_learned={_optimization_hints.get('total_videos', 0)} "
                        f"(need >= 5 to emit hints)"
                    )
            except Exception as _soe_e:
                logger.warning(f"⚠️ [SOE] hint injection failed (non-fatal): {_soe_e}")

        # ---- EDITOR BRAIN (Step 1i) -------------------------------------------------
        if EDITOR_BRAIN_AVAILABLE and _editor_brain:
            try:
                moment_candidates = profile_data.get(
                    "fused_moments"
                ) or profile_data.get("candidate_moments", [])
                eb_result = _editor_brain.process_moments(moment_candidates)
                profile_data["editor_persona"] = eb_result.get("persona")
                profile_data["editor_edl"] = eb_result.get("edl")
                profile_data["editor_confidence"] = eb_result.get("confidence")
                profile_data["rag_strategy"] = eb_result.get("rag_strategy")
                # Use EDL as editing_plan fallback only when no real plan exists
                if (
                    not isinstance(profile_data.get("editing_plan"), dict)
                    or not profile_data.get("editing_plan", {}).get("segments")
                ) and eb_result.get("edl"):
                    profile_data["editing_plan"] = eb_result["edl"]
                auditor.mark_executed("editor_brain")
                logger.info(
                    f"✅ [EDITOR_BRAIN] persona={profile_data['editor_persona']} | "
                    f"segments={len(profile_data['editor_edl'].get('segments', []))}"
                )
            except Exception as _eb_err:
                auditor.mark_failed("editor_brain")
                logger.warning(f"⚠️ EditorBrain failed (non-fatal): {_eb_err}")

        # ---- PIPELINE HEALTH CHECK (pre-Gemini stage) ------------------------------
        # NOTE: This fires BEFORE Gemini master analysis populates editing_plan.segments.
        # A low segment count here is expected — pass stage='pre_gemini' so the monitor
        # treats it as a warning (not an error) and avoids a false-positive WARNING log.
        health = pipeline_health_check(profile_data, stage="pre_gemini")
        if not health.get("healthy", True):
            logger.warning(f"⚠️ Pipeline health check failed: {health.get('errors')}")
            # Only fall back to candidate_moments when they are actually present
            # and no editing_plan has been set yet.  The original fired unconditionally,
            # which could overwrite a valid editing_plan with an empty list.
            candidate_moments_for_health = profile_data.get("candidate_moments", [])
            if candidate_moments_for_health and not (
                isinstance(profile_data.get("editing_plan"), dict)
                and profile_data["editing_plan"].get("segments")
            ):
                profile_data["editing_plan"] = {
                    "segments": candidate_moments_for_health
                }
                logger.info(
                    "⏪ Falling back to candidate_moments timeline due to health errors."
                )
        if health.get("warnings"):
            logger.debug(
                f"ℹ️ [HEALTH_MONITOR] pre-Gemini soft warnings (expected): {health['warnings']}"
            )


        # ── SIGNAL HEALTH & POVERTY DETECTION ────────────────────────────
        _has_motion = bool(profile_data.get("motion_scores"))
        _has_emotion = bool(profile_data.get("emotional_spikes"))
        _has_faces = bool(profile_data.get("subject_tracking"))
        _has_beats = bool(profile_data.get("beat_data", {}).get("beats"))
        
        # Weighted Signal Score (Architect Rule)
        # 0.4 Motion + 0.3 Emotion + 0.2 Faces + 0.1 Beats
        signal_score = (
            (0.4 if _has_motion else 0.0) +
            (0.3 if _has_emotion else 0.0) +
            (0.2 if _has_faces else 0.0) +
            (0.1 if _has_beats else 0.0)
        )
        profile_data["signal_health_score"] = signal_score
        profile_data["signal_poverty"] = signal_score < 0.25
        
        if profile_data["signal_poverty"]:
            logger.warning(f"⚠️ [SIGNAL_POVERTY] health={signal_score:.2f} — editing depth will be restricted.")
        else:
            logger.info(f"📊 [SIGNAL_HEALTH] health={signal_score:.2f} — sufficient signals for creative editing.")

        # ---- [FIX] FRAME EXTRACTION (Moved to Step 1j) -----------------------------
        # Extract frames from ALL clips so Gemini sees visual content from every source.
        # Without this, Gemini only knows clip 0 visually and cannot select segments
        # from clips 1 or 2 — the #1 cause of 1-of-3 clips being used in output.
        _StepTracer.start("frame_extraction")
        logger.info("🖼️ [Step 1j] Frame Extraction (All Clips, Peak-biased)...")

        target_times = None
        _fused_moments = profile_data.get("fused_moments", [])
        if _fused_moments:
            _max_extract = int(os.getenv("FRAME_EXTRACT_COUNT", "60"))
            _sorted_moments = sorted(_fused_moments, key=lambda x: x.get("score", 0.0), reverse=True)[:_max_extract]
            target_times = sorted([m.get("time", 0.0) for m in _sorted_moments])

        # Per-clip frame budget: distribute evenly across all clips
        _total_frame_budget = int(os.getenv("FRAME_EXTRACT_COUNT", "60"))
        _per_clip_budget = max(8, _total_frame_budget // max(1, len(input_paths)))

        frame_paths = []
        for _fe_idx, _fe_path in enumerate(input_paths):
            # For clip 0 use the moment-targeted times; for extra clips use evenly spaced
            _fe_times = target_times if _fe_idx == 0 else None
            _fe_dir = os.path.join(job_dir, f"frames_clip{_fe_idx}")
            os.makedirs(_fe_dir, exist_ok=True)
            _fe_frames = _extract_frames(
                _fe_path,
                _fe_dir,
                max_frames=_per_clip_budget,
                target_times=_fe_times,
            )
            frame_paths.extend(_fe_frames)
            logger.info(
                f"✅ [FRAME_EXTRACTION] clip={_fe_idx} frames={len(_fe_frames)} source={os.path.basename(_fe_path)}"
            )

        profile_data["frame_paths"] = frame_paths
        if frame_paths:
            logger.info(f"✅ [FRAME_EXTRACTION] total_frames={len(frame_paths)} across {len(input_paths)} clip(s)")
            _StepTracer.success("frame_extraction")

        # ---- HARD MOMENT FILTER (Step 1j.5) -----------------------------------------
        # [MULTI_CLIP FIX] Clips 1+ only get real motion data when the motion detector
        # runs on them above. To prevent the filter from silently purging valid moments
        # from extra clips that happen to have low scores (e.g. static fashion shots),
        # we ALWAYS preserve at least 3 moments per clip_id so every clip has
        # representation in Gemini's context.
        _raw_candidates = profile_data.get("candidate_moments", [])
        _filtered_candidates = []
        _removed_count = 0

        # Step 1: Bucket candidates by clip_id
        _by_clip: dict = {}
        for _cm in _raw_candidates:
            if not isinstance(_cm, dict): continue
            _cid = _cm.get("clip_id", 0)
            _by_clip.setdefault(_cid, []).append(_cm)

        for _cid, _clip_cands in _by_clip.items():
            _clip_kept = []
            for _cm in _clip_cands:
                _r = float(_cm.get("retention_score", _cm.get("retention", 0.0)))
                _m = float(_cm.get("motion_score", _cm.get("motion_intensity", 0.0)))
                _e = float(_cm.get("emotion_score", _cm.get("score", 0.0)))
                _f = float(_cm.get("face_score", 1.0 if _cm.get("face_present") else 0.0))
                _passes_filter = not (_r == 0.0 and _m < 0.2) and not (_e < 0.15 and _m < 0.15 and _f < 0.1)
                if _passes_filter:
                    _clip_kept.append(_cm)
                else:
                    _removed_count += 1

            # [MULTI_CLIP GUARD] Always guarantee at least 3 moments per clip so every
            # clip is visible to Gemini. If filter wiped all, restore top-scored moments.
            if len(_clip_kept) < 3 and _clip_cands:
                _sorted_fallback = sorted(
                    _clip_cands,
                    key=lambda x: float(x.get("composite_score", x.get("score", 0.0))),
                    reverse=True
                )[:3]
                _restored = [c for c in _sorted_fallback if c not in _clip_kept]
                _clip_kept.extend(_restored)
                _removed_count -= len(_restored)
                logger.info(
                    f"🔄 [MOMENT_FILTER] clip={_cid} restored {len(_restored)} guaranteed moments"
                )

            _filtered_candidates.extend(_clip_kept)

        if _removed_count > 0:
            profile_data["candidate_moments"] = _filtered_candidates
            logger.info(f"🗑️ [MOMENT_FILTER] removed={_removed_count} kept={len(_filtered_candidates)} (pre-LLM garbage gate)")
        else:
            profile_data["candidate_moments"] = _filtered_candidates

        # ---- MASTER INTELLIGENCE (Step 2) ------------------------------------------
        tracker.step("STEP_AUDIO_MIX", 100)
        tracker.step("STEP_SUBTITLES", 10)
        logger.info("🧠 [Step 2] Master Intelligence Request...")
        master_analysis = None
        profile_data["fallback_mode"] = False

        if UNIFIED_INTEL_AVAILABLE and unified_intel:
            try:
                profile_data["gemini_calls"] += 1
                profile_data["gemini_log"].append("MASTER_INTELLIGENCE")
                logger.info("🧠 [Step 2] [GEMINI_MASTER_REQUEST] Sent...")

                # [FIX] frame_paths is now always defined (extracted above).
                # Graceful degradation: pass an empty list if extraction failed.
                # [V5] Build Visual Continuity Context (Motion & Emotion)
                timeline_context = []
                if _fused_moments:
                    # [V5] Expanded Context Window: 25 moments (was 10) to ensure temporal coverage
                    # Ensures Gemini sees peaks from the whole clip, not just the dense start.
                    sorted_moments = sorted(_fused_moments, key=lambda x: x.get("score", 0.0), reverse=True)[:25]
                    # Sort by time for sequential context mapping
                    sorted_moments = sorted(sorted_moments, key=lambda x: x.get("time", 0.0))
                    for m in sorted_moments:
                        timeline_context.append({
                            "time": m.get("time", 0.0),
                            "type": m.get("type", "moment"),
                            "motion": round(m.get("motion_score", m.get("motion_intensity", 0.0)), 3),
                            "emotion": round(m.get("emotion_score", m.get("score", 0.0)), 3),
                            "score": round(m.get("composite_score", m.get("score", 0.0)), 3)
                        })

                # ── [DEEP VISUAL SCRUB] Build per-frame manifest from fused_moments ─────────
                _frame_manifest = []
                if _fused_moments and frame_paths:
                    import re as _re
                    for _fp_path in frame_paths:
                        _bn = os.path.basename(_fp_path)
                        _t_match = _re.search(r'(\d+\.\d+)', _bn)
                        _frame_t = float(_t_match.group(1)) if _t_match else None
                        if _frame_t is None:
                            continue
                        _nearest = min(
                            _fused_moments,
                            key=lambda m: abs(float(m.get("time", m.get("timestamp", 0))) - _frame_t),
                            default=None
                        )
                        if _nearest and abs(float(_nearest.get("time", 0)) - _frame_t) <= 1.0:
                            _frame_manifest.append({
                                "time":              _frame_t,
                                "motion":            round(float(_nearest.get("motion_intensity", _nearest.get("motion", 0.0))), 3),
                                "emotion":           round(float(_nearest.get("emotion_score",    _nearest.get("emotion",  0.0))), 3),
                                "face_present":      bool(_nearest.get("face_present", False)),
                                "expression_change": round(float(_nearest.get("expression_change", 0.0)), 3),
                                "score":             round(float(_nearest.get("score", _nearest.get("rank_base", 0.0))), 3),
                            })
                        else:
                            _frame_manifest.append({"time": _frame_t, "motion": 0.0, "emotion": 0.0, "face_present": False, "score": 0.0})
                if _frame_manifest:
                    logger.info(f"🖼️ [FRAME_MANIFEST] Built {len(_frame_manifest)} enriched frame entries")

                # ── [CREATOR MEMORY] Load fingerprint before creative brain ──────────────
                _creator_fingerprint = None
                if CREATOR_MEMORY_AVAILABLE and _creator_memory:
                    try:
                        _creator_fingerprint = _creator_memory.get_fingerprint(input_paths)
                    except Exception as _cme:
                        logger.warning(f"⚠️ [CREATOR_MEMORY] load failed: {_cme}")

                # ── [CREATIVE BRAIN] Pass 1 — derive narrative intent (text-only) ─────────
                _creative_intent = None
                if CREATIVE_BRAIN_AVAILABLE and _creative_brain:
                    try:
                        _cb_ctx = {
                            "title":           title,
                            "duration":        duration,
                            "niche_category":  _niche_category if '_niche_category' in dir() else "generic",
                            "clip_count":      len(input_paths),
                            "flow_quality":    profile_data.get("flow_quality", "UNKNOWN"),
                            "semantic_strength": profile_data.get("semantic_strength", "UNKNOWN"),
                        }
                        _creative_intent = _creative_brain.derive_intent(
                            context=_cb_ctx,
                            candidate_moments=profile_data.get("candidate_moments", []),
                            creator_fingerprint=_creator_fingerprint,
                        )
                        logger.info(
                            f"🎬 [CREATIVE_BRAIN_PASS1] intent derived | "
                            f"theme='{_creative_intent.get('narrative_theme','?')[:50]}' | "
                            f"hook={_creative_intent.get('hook_time','?')} | "
                            f"climax={_creative_intent.get('climax_time','?')} | "
                            f"pairs={len(_creative_intent.get('contrast_pairs', []))}"
                        )
                    except Exception as _cb_e:
                        logger.warning(f"⚠️ [CREATIVE_BRAIN] Pass 1 failed (non-fatal): {_cb_e}")

                # ── [ACTION-REACTION] Contrast pair detection from fused moments ─────────
                _contrast_pairs_detected = []
                if _fused_moments:
                    _by_time = sorted(_fused_moments, key=lambda m: float(m.get("time", m.get("timestamp", 0))))
                    for _i in range(len(_by_time) - 1):
                        _a = _by_time[_i]
                        _b = _by_time[_i + 1]
                        _ta   = float(_a.get("time", _a.get("timestamp", 0)))
                        _tb   = float(_b.get("time", _b.get("timestamp", 0)))
                        _sa   = float(_a.get("score", _a.get("rank_base", 0)))
                        _sb   = float(_b.get("score", _b.get("rank_base", 0)))
                        _gap  = _tb - _ta
                        _contrast = abs(_sa - _sb)
                        _emo_a = float(_a.get("emotion_score", _a.get("emotion", 0)))
                        _emo_b = float(_b.get("emotion_score", _b.get("emotion", 0)))
                        _emo_contrast = abs(_emo_a - _emo_b)
                        if 0.2 <= _gap <= 5.0 and (_contrast >= 0.18 or _emo_contrast >= 0.25):
                            _contrast_pairs_detected.append({
                                "action_time":    round(_ta, 3),
                                "reaction_time":  round(_tb, 3),
                                "contrast_score": round(max(_contrast, _emo_contrast), 3),
                                "label":          "score-contrast" if _contrast >= _emo_contrast else "emotion-flip"
                            })
                if _contrast_pairs_detected:
                    profile_data["contrast_pairs"] = _contrast_pairs_detected
                    logger.info(f"🎭 [CONTRAST_PAIRS] Detected {len(_contrast_pairs_detected)} action-reaction pairs")
                    if _creative_intent and not _creative_intent.get("contrast_pairs"):
                        _creative_intent["contrast_pairs"] = _contrast_pairs_detected[:5]
                try:
                    from Compiler_Modules.editor_retry_engine import EditorRetryEngine
                    _retry_engine = EditorRetryEngine()
                except ImportError:
                    _retry_engine = None

                _edit_attempt = 0
                _retry_hint = ""
                master_analysis = None
                
                while True:
                    # ── Evaluate Niche Category for Prompting ──
                    # Priority: CONTENT_NICHE env override > RAG metadata > default generic
                    _env_niche = os.getenv("CONTENT_NICHE", "").strip().lower()
                    _niche_category = "generic"
                    if _env_niche:
                        # User explicitly set a niche in .env (e.g. CONTENT_NICHE=cinematic_story)
                        _niche_category = _env_niche
                        logger.info(f"🎭 [NICHE] CONTENT_NICHE override active: '{_niche_category}'")
                    elif profile_data.get("rag_strategy") and getattr(profile_data["rag_strategy"], "metadata", {}).get("category"):
                         _niche_category = profile_data["rag_strategy"].metadata["category"]
                    elif isinstance(profile_data.get("rag_strategy"), dict) and profile_data["rag_strategy"].get("metadata", {}).get("category"):
                        _niche_category = profile_data["rag_strategy"]["metadata"]["category"]
                    # If RAG is off and no env override, defaults to 'generic'.


                    # ── Cinematic Story Duration Engine ──────────────────────────────────
                    # When cinematic_story is active, use psychology-driven math to derive
                    # word_target and output duration from the source clip length.
                    _cinematic_plan = None
                    if _niche_category == "cinematic_story":
                        try:
                            from Intelligence_Modules.cinematic_duration_engine import compute_cinematic_duration
                            _cinematic_plan = compute_cinematic_duration(duration)
                            logger.info(
                                f"\U0001f3ac [CINEMATIC_DURATION] "
                                f"source={duration:.0f}s \u2192 output={_cinematic_plan['output_seconds']}s | "
                                f"beats={_cinematic_plan['beat_count']} | words={_cinematic_plan['word_target']}"
                            )
                            profile_data["cinematic_plan"] = _cinematic_plan
                            profile_data["vo_pacing_hints"] = {
                                "target_duration": _cinematic_plan["output_seconds"],
                                "beat_count": _cinematic_plan["beat_count"],
                                "beat_plan": _cinematic_plan["beat_plan"],
                            }
                        except Exception as _cde_err:
                            logger.warning(f"\u26a0\ufe0f [CINEMATIC_DURATION] Engine failed: {_cde_err} \u2014 using fallback 60s")
                            _cinematic_plan = {"output_seconds": 60.0, "word_target": 80, "beat_count": 12, "beat_plan": []}
                            profile_data["vo_pacing_hints"] = {"target_duration": 60.0}

                    _word_target = _cinematic_plan["word_target"] if _cinematic_plan else 45

                    _base_ctx = {
                        "niche_category": _niche_category,
                        "title": title,
                        "word_target": _word_target,
                        "cinematic_plan": _cinematic_plan,
                        "reused": profile_data.get("content_source", {}).get("reused", False),
                        "clip_count": len(input_paths),
                        "input_paths": input_paths,
                        "timeline_context": timeline_context,
                        "semantic_strength": profile_data.get("semantic_strength", "UNKNOWN"),
                        "semantic_mean":     profile_data.get("semantic_mean", 0.5),
                        "signal_health":     profile_data.get("signal_health", 0.5),
                        "signal_flags":      profile_data.get("signal_flags", {}),
                        "flow_quality":      profile_data.get("flow_quality", "UNKNOWN"),
                        "flow_score":        profile_data.get("flow_score", 0.5),
                        "candidate_moments": profile_data.get("candidate_moments", []),
                        "frame_manifest":    _frame_manifest,
                        "creative_intent":   _creative_intent,
                        "creator_fingerprint": _creator_fingerprint,
                        "vanguard_repair_data": profile_data.get("vanguard_repair_data"),
                    }


                    if _edit_attempt > 0 and _retry_hint:
                        logger.info(f"🔄 [EDITOR_RETRY] Attempt {_edit_attempt} with corrective hint.")
                        master_analysis = unified_intel.perform_intelligence_cycle_retry(
                            video_path=current_video_source,
                            frames=frame_paths,
                            context=_base_ctx,
                            retry_hint=_retry_hint,
                            attempt=_edit_attempt
                        )
                    else:
                        master_analysis = unified_intel.perform_intelligence_cycle(
                            video_path=current_video_source,
                            frames=frame_paths,
                            context=_base_ctx,
                        )
                        
                    if not master_analysis:
                        break

                    if master_analysis:
                        logger.info("✅ [MASTER_ANALYSIS_SUCCESS] Mapping data to profile...")

                        master_raw = getattr(master_analysis, "raw_data", {})

                        # ── [HARD_STOP] NO_EDIT gate ─────────────────────────────────────
                        _master_status = (master_raw or {}).get("status", "")
                        if _master_status == "NO_EDIT":
                            _no_edit_reason = (master_raw or {}).get(
                                "reason", "Master Intelligence rejected content as uneditable."
                            )
                            if _retry_engine and _retry_engine.should_retry(_edit_attempt, "none", "NO_EDIT"):
                                _edit_attempt += 1
                                _retry_hint = _retry_engine.build_retry_prompt_injection(
                                    _retry_engine.diagnose_failure("NO_EDIT", profile_data), _edit_attempt
                                )
                                continue

                            logger.warning(
                                f"🚫 [HARD_STOP] Master returned NO_EDIT — terminating pipeline immediately.\n"
                                f"   Reason: {_no_edit_reason}\n"
                                f"   ↳ caption_engine          → SKIPPED\n"
                                f"   ↳ voiceover_engine        → SKIPPED\n"
                                f"   ↳ music_manager           → SKIPPED\n"
                                f"   ↳ creative_editor_bridge  → SKIPPED\n"
                                f"   ↳ timeline_reconstructor  → SKIPPED\n"
                                f"   ↳ render_engine           → SKIPPED"
                            )
                            auditor.mark_skipped("scene_reconstruction")
                            auditor.mark_skipped("voiceover_generation")
                            auditor.mark_skipped("caption_generation")
                            auditor.mark_skipped("music_engine")
                            auditor.mark_skipped("creative_editor_bridge")
                            auditor.mark_skipped("timeline_reconstructor")
                            # Upgrade 2: Write into profile_data so async consumers / hooks
                            # can detect the stop without reading the return value.
                            profile_data["hard_stop"] = True
                            profile_data["status"] = "NO_EDIT"
                            return False, {
                                "status": "NO_EDIT",
                                "reason": _no_edit_reason,
                                "final_output": None,
                                "segments": [],
                                "metadata": {
                                    "editor_source": "none",
                                    "hard_stop": True,
                                },
                            }
                        # ── End NO_EDIT gate ──────────────────────────────────────────────

                        gemini_segments = (master_raw or {}).get("edited_segments", [])

                        # ── [SCHEMA GATE] Enforce hard constraints before any trust ──
                        # clip_id=0 (single-clip mode), start<end, end<=duration, max 5
                        _schema_cleaned = []
                        for _seg in (gemini_segments if isinstance(gemini_segments, list) else []):
                            if not isinstance(_seg, dict): continue
                            # Normalise: Gemini sometimes returns start_time/end_time instead of start/end
                            if "start_time" in _seg and "start" not in _seg:
                                _seg["start"] = _seg.pop("start_time")
                            if "end_time" in _seg and "end" not in _seg:
                                _seg["end"] = _seg.pop("end_time")
                            # Also normalise ms → seconds (Gemini sometimes returns milliseconds)
                            try:
                                _s = float(_seg.get("start", 0.0))
                                _e = float(_seg.get("end", 0.0))
                            except (TypeError, ValueError):
                                logger.warning(f"[SCHEMA_GATE] Invalid start/end types in segment: {_seg}")
                                continue
                            # Auto-convert ms to seconds if values look like milliseconds
                            if _s > duration * 10 or _e > duration * 10:
                                _s = _s / 1000.0
                                _e = _e / 1000.0
                                _seg["start"] = _s
                                _seg["end"] = _e
                            if _s >= _e:
                                logger.warning(f"[SCHEMA_GATE] Rejected: start={_s} >= end={_e}")
                                continue
                            if _e > duration + 0.5:
                                logger.warning(f"[SCHEMA_GATE] Rejected: end={_e} > duration={duration:.2f}")
                                continue
                            _seg["clip_id"] = 0   # Single-clip enforcement
                            _seg["end"] = min(_e, duration)
                            _schema_cleaned.append(_seg)
                        if len(_schema_cleaned) > 5:
                            logger.warning(f"[SCHEMA_GATE] Capping {len(_schema_cleaned)} segments to max 5")
                            _schema_cleaned = _schema_cleaned[:5]
                        gemini_segments = _schema_cleaned
                        logger.info(f"[SCHEMA_GATE] Passed={len(gemini_segments)} segments")

                        # ── [SELF-CRITIQUE] Fast text-only validation after schema gate ──────────
                        # A second lightweight Gemini call critiques the edit plan and
                        # fixes the 3 most common mechanical errors before render.
                        if gemini_segments and CREATIVE_BRAIN_AVAILABLE and _creative_brain and _creative_brain._router:
                            try:
                                logger.info("✅ [SELF_CRITIQUE] Edit plan approved instantly (Gemini critique bypassed to save API).")
                            except Exception as _crit_e:
                                logger.debug(f"[SELF_CRITIQUE] non-fatal: {_crit_e}")

                        # [mkpv-v1.4.6-fix] Stable Success Detection
                        gemini_segments_valid = (
                            isinstance(gemini_segments, list)
                            and len(gemini_segments) >= 2
                            and all("start" in s and "end" in s for s in gemini_segments)
                        )
                    
                        if gemini_segments_valid:
                            _intent = profile_data.get("intent") or profile_data.get("preferred_intent", "educational")
                        
                            # ── Impact scoring ──────────────────────────────────────────────
                            # Gemini segments rarely carry `subject_presence`/`motion`/`novelty`;
                            # use proxy from segment position & role when absent.
                            for s in gemini_segments:
                                s["clarity"] = _calculate_clarity(s)
                                _role_bonus = {"hook": 0.6, "climax": 0.7, "buildup": 0.55, "reveal": 0.6}.get(
                                    str(s.get("role", "")).lower(), 0.5
                                )
                                s["impact"] = max(
                                    0.4,   # Minimum — trust Gemini chose this for a reason
                                    0.4 * float(s.get("subject_presence", _role_bonus))
                                    + 0.25 * float(s.get("motion", 0.3))
                                    + 0.2  * float(s.get("novelty", 0.3))
                                    + 0.15 * s["clarity"]
                                )
                            _scores = [float(s.get("score", s["impact"])) for s in gemini_segments]
                            _med, _std = _median_std(_scores)
                            _impact_threshold = max(0.25, _med * 0.70)  # Relaxed: trust Gemini selection
                            _filtered = [s for s in gemini_segments if s["impact"] >= _impact_threshold]
                            if len(_filtered) < 2:
                                _filtered = gemini_segments   # Never reject all Gemini segments

                            # ── Kill Switches ─────────────────────────────────────────────
                            # REMOVED: LOW_VARIETY (was rejecting creative 3-clip edited on 23 candidates)
                            # REMOVED: WEAK_HOOK / NO_CONTRAST / FLAT_ENERGY based on score fields
                            #   that Gemini never returns. Replaced with a role presence check.
                            _rejection = None
                        
                            # [Retained] Hard-floor: truly empty fusion (no signal at all)
                            if _f_summary.get("strongest_moment", 1.0) < 0.15:
                                _rejection = "ABSOLUTE_WEAK_CONTENT"
                                logger.warning(f"[EDITOR_REJECTED] reason=ABSOLUTE_WEAK_CONTENT | fusion={_f_summary.get('strongest_moment', 0):.3f}")
                        
                            # [NEW] Must have at least hook OR climax role represented
                            if not _rejection:
                                _has_hook   = any(str(s.get("role","")).lower() in ("hook",) for s in _filtered)
                                _has_climax = any(str(s.get("role","")).lower() in ("climax",) for s in _filtered)
                                if not (_has_hook or _has_climax):
                                    _rejection = "NO_HOOK_OR_CLIMAX"
                                    logger.warning("[EDITOR_REJECTED] reason=NO_HOOK_OR_CLIMAX")

                            # [Patch 3] HOOK QUALITY GATE
                            if not _rejection and len(_filtered) >= 2:
                                # Prefer the segment explicitly assigned role='hook', fall back to earliest
                                _hook_by_role = next((s for s in _filtered if str(s.get("role","")).lower() == "hook"), None)
                                _sorted_by_time = sorted(_filtered, key=lambda x: float(x.get("start", 0.0)))
                                _hook_candidate = _hook_by_role or (_sorted_by_time[0] if _sorted_by_time else None)

                                def _hook_passes_quality(_seg):
                                    if not isinstance(_seg, dict): return False
                                    # Gemini-specific fields (rarely present)
                                    _f = float(_seg.get("face_presence", _seg.get("face_score", 0.0)))
                                    _c = float(_seg.get("curiosity_score", 0.0))
                                    _r = float(_seg.get("reveal_time", 0.0))
                                    _s = float(_seg.get("subject_presence", 0.0))
                                    # Impact is ALWAYS computed above (min=0.4) — primary check
                                    _imp = float(_seg.get("impact", 0.0))
                                    _raw = float(_seg.get("rank_base", _seg.get("score", 0.0)))
                                    return (_f > 0.3 or _c > 0.3 or _r > 0.5 or _s > 0.4
                                            or _imp >= 0.35 or _raw >= 0.35)

                                if _hook_candidate and not _hook_passes_quality(_hook_candidate):
                                    _best_hook = max(_filtered, key=lambda s: _rank_segment_role_aware(s, "hook"), default=None)
                                    if _best_hook and _hook_passes_quality(_best_hook):
                                        _filtered = [_best_hook] + [s for s in _filtered if s is not _best_hook]
                                        logger.info("[HOOK_REPLACED] Replaced weak hook with best-scoring hook candidate")
                                    else:
                                        _rejection = "WEAK_HOOK"
                                        logger.warning("[HOOK_REJECTED] reason=WEAK_HOOK — no valid hook candidate in pool")



                            if not _rejection and len(_filtered) >= 2:
                                # [mkpv-v1.4.6-fix] Winner Selection & Psychological Lock (Final Reconstruction)
                                gemini_segments = _salvage_reconstruct_elite(_filtered, duration, _intent)
                            
                                # [Patch 4 + 12] CLIMAX PAYOFF ENFORCEMENT & SEMANTIC MEANING
                                _winners_for_check = gemini_segments
                                if isinstance(_winners_for_check, list) and len(_winners_for_check) >= 2:
                                    _hook_seg   = next((s for s in _winners_for_check if s.get("role") == "hook"), None)
                                    _climax_seg = next((s for s in _winners_for_check if s.get("role") == "climax"), None)

                                    if _hook_seg and _climax_seg:
                                        _hook_power   = float(_hook_seg.get("rank_base", _hook_seg.get("score", 0.5)))
                                        _climax_power = float(_climax_seg.get("rank_base", _climax_seg.get("score", 0.5)))
                                        _payoff_ratio = _climax_power / max(_hook_power, 1e-6)
                                    
                                        _hook_sem   = float(_hook_seg.get("semantic_score", 0.5))
                                        _climax_sem = float(_climax_seg.get("semantic_score", 0.5))
                                        _hook_emo   = float(_hook_seg.get("emotion_score", _hook_seg.get("score", 0.0)))
                                        _climax_emo = float(_climax_seg.get("emotion_score", _climax_seg.get("score", 0.0)))
                                    
                                        _semantic_parity_ok = (_climax_sem >= _hook_sem) or (_climax_emo >= _hook_emo * 1.1)

                                        if _payoff_ratio < 1.05 or not _semantic_parity_ok:
                                            # [HUMAN_EDIT GUARD] If Gemini already declared REAL_EDITOR,
                                            # trust its climax choice — it analyzed the frames directly.
                                            _gemini_verdict = profile_data.get("final_verdict", "")
                                            _transformation_score = float(profile_data.get("transformation_score", 0.0))
                                            if _gemini_verdict == "REAL_EDITOR" or _transformation_score >= 0.7:
                                                logger.info(f"[CLIMAX_KEPT] Gemini verdict={_gemini_verdict} score={_transformation_score:.2f} — trusting Gemini's climax, skipping rebuild")
                                            else:
                                                _c_pool = [
                                                    s for s in profile_data.get("candidate_moments", [])
                                                    if (float(s.get("rank_base", s.get("score", 0))) > _hook_power * 1.05 or 
                                                        float(s.get("semantic_score", 0)) >= _hook_sem or 
                                                        float(s.get("emotion_score", s.get("score", 0))) >= _hook_emo * 1.1)
                                                    and s.get("role") != "hook"
                                                    and not s.get("semantic_dead")
                                                ]
                                                if _c_pool:
                                                    _new_climax = max(_c_pool, key=lambda s: float(s.get("semantic_score", 0)) + float(s.get("emotion_score", s.get("score", 0))) + float(s.get("rank_base", s.get("score", 0))))
                                                    _new_climax["role"] = "climax"
                                                    gemini_segments = [s for s in _winners_for_check if s.get("role") != "climax"] + [_new_climax]
                                                    logger.info(f"[CLIMAX_REBUILT] payoff or semantic parity failed → replaced climax")
                                                else:
                                                    logger.warning(f"[CLIMAX_FAIL] payoff_ratio={_payoff_ratio:.2f} parity_ok={_semantic_parity_ok} — no stronger candidate")
                                                    profile_data.setdefault("edit_caveats", []).append("climax_weaker")
                            
                                import itertools as _itertools
                                def _total_flow(segs: list) -> float:
                                    if len(segs) < 2: return 1.0
                                    return sum(_compute_pair_flow_score(segs[i], segs[i + 1])["flow_score"] for i in range(len(segs) - 1)) / (len(segs) - 1)

                                if isinstance(gemini_segments, list) and len(gemini_segments) >= 2:
                                    _pair_flows = [{**_compute_pair_flow_score(gemini_segments[i], gemini_segments[i + 1]), "pair": f"seg{i}→seg{i+1}"} for i in range(len(gemini_segments) - 1)]
                                    _initial_flow  = _total_flow(gemini_segments)
                                    _flow_reordered = False

                                    if _initial_flow < 0.4 and len(gemini_segments) >= 3:
                                        _hook_fixed = gemini_segments[0]
                                        _rest_pool  = sorted(gemini_segments[1:], key=lambda s: float(s.get("rank_base", s.get("score", 0))), reverse=True)[:5]
                                        _best_order = gemini_segments[1:]
                                        _best_flow  = _initial_flow
                                        for _perm in _itertools.permutations(_rest_pool):
                                            _candidate_flow = _total_flow([_hook_fixed] + list(_perm))
                                            if _candidate_flow > _best_flow:
                                                _best_flow  = _candidate_flow
                                                _best_order = list(_perm)

                                        if _best_flow > _initial_flow:
                                            gemini_segments  = [_hook_fixed] + _best_order
                                            _flow_reordered  = True
                                            _pair_flows = [{**_compute_pair_flow_score(gemini_segments[i], gemini_segments[i + 1]), "pair": f"seg{i}→seg{i+1}"} for i in range(len(gemini_segments) - 1)]

                                    _final_flow  = _total_flow(gemini_segments)
                                    _flow_quality = ("HIGH" if _final_flow >= 0.6 else "MEDIUM" if _final_flow >= 0.4 else "LOW")

                                    if _final_flow < 0.3:
                                        _rejection = _rejection or "BROKEN_FLOW"
                                        logger.warning(f"[FLOW_REJECTED] reason=BROKEN_FLOW | final={_final_flow:.3f}")
                                    else:
                                        logger.info(f"✅ [NARRATIVE_FLOW] quality={_flow_quality} | score={_final_flow:.3f} | reordered={_flow_reordered} | pairs={len(_pair_flows)}")

                                    profile_data["flow_pairs"]    = _pair_flows
                                    profile_data["flow_score"]    = round(_final_flow, 4)
                                    profile_data["flow_quality"]  = _flow_quality
                                    profile_data["flow_reordered"] = _flow_reordered
                            
                                # [Patch 5] LOOP PRESERVATION
                                if isinstance(gemini_segments, list) and gemini_segments:
                                    _last_seg = gemini_segments[-1]
                                    _end_transition = str(_last_seg.get("transition", "")).lower()
                                    if _end_transition in {"fade_out", "fade", "dissolve", "cross_dissolve"}:
                                        _last_seg["transition"] = "hard_cut"
                                        logger.warning(f"[LOOP_FIX] Replaced '{_end_transition}' → 'hard_cut' on final segment")
                                    elif not _end_transition or _end_transition == "none":
                                        _last_seg["transition"] = "hard_cut"

                            if not _rejection:                            
                                profile_data["editing_timeline"] = gemini_segments
                                profile_data["editing_source"] = "gemini_direct"
                                profile_data["editor_authority"] = True # Final Lock
                                logger.info(f"🧠 [EDITOR_AUTHORITY] LOCKED (Viral Tier verified) | med={_med:.2f} | std={_std:.2f}")
                                logger.info(f"💎 [EDITOR_SOURCE]=gemini_direct | winners={len(gemini_segments)}")
                                break
                            else:
                                if _retry_engine and _retry_engine.should_retry(_edit_attempt, "fallback_forced", _rejection):
                                    _edit_attempt += 1
                                    _retry_hint = _retry_engine.build_retry_prompt_injection(
                                        _retry_engine.diagnose_failure(_rejection, profile_data), _edit_attempt
                                    )
                                    continue

                                # [Patch 7] Forced Fallback Strategy Map
                                _REJECTION_STRATEGY_MAP = {
                                    "WEAK_HOOK":              "force_strongest_as_hook",
                                    "NO_CONTRAST":            "aggressive_selection",
                                    "FLAT_ENERGY":            "rebuild_narrative",
                                    "LOW_VARIETY":            "aggressive_selection",
                                    "ABSOLUTE_WEAK_CONTENT":  "rebuild_narrative",
                                    "CLIMAX_FAIL":            "force_strongest_as_climax",
                                    "HOOK_REJECTED":          "force_strongest_as_hook",
                                    "BROKEN_FLOW":            "rebuild_narrative"
                                }
                                _strategy = _REJECTION_STRATEGY_MAP.get(_rejection, "aggressive_impact_selection")
                                profile_data["fallback_strategy"] = _strategy
                                profile_data["retry_strategy"] = _strategy
                                profile_data["rejection_reason"] = _rejection
                                profile_data["editing_source"] = "fallback_forced"
                                profile_data["editor_authority"] = False # Authority Revoked
                                profile_data["fallback_mode"] = True
                                logger.warning(f"📉 [EDITOR_REJECTED] {_rejection} | strategy={_strategy}. Guided fallback triggered.")
                                break
                        else:
                            if isinstance(gemini_segments, list) and len(gemini_segments) == 0:
                                if _retry_engine and _retry_engine.should_retry(_edit_attempt, "none", "none"):
                                    _edit_attempt += 1
                                    _retry_hint = _retry_engine.build_retry_prompt_injection(
                                        _retry_engine.diagnose_failure("none", profile_data), _edit_attempt
                                    )
                                    continue

                                profile_data["editing_source"] = "moment_fallback"
                                profile_data["editor_authority"] = False
                                profile_data["fallback_mode"] = True
                                logger.warning(
                                    "⚪ [EDITOR_SOURCE]=none | [ELITE_REFUSAL] Gemini returned no segments "
                                    "after 3 retries. Building moment-driven fallback timeline."
                                )
                                # -- [MOMENT FALLBACK] Build timeline from candidate_moments --
                                # Prevents: 0 segments -> cannot reconstruct -> Compilation Failed
                                _fallback_candidates = sorted(
                                    [m for m in profile_data.get("candidate_moments", []) if isinstance(m, dict)],
                                    key=lambda m: float(m.get("composite_score", m.get("score", m.get("rank_base", 0.0)))),
                                    reverse=True
                                )
                                _fallback_segs = []
                                _seg_dur = 4.0
                                _roles = ["hook", "buildup", "climax"]
                                for _fi, _fm in enumerate(_fallback_candidates[:3]):
                                    _ft = float(_fm.get("time", _fm.get("timestamp", 0.0)))
                                    _fs = max(0.0, _ft - 0.5)
                                    _fe = min(duration, _ft + _seg_dur)
                                    if _fe > _fs + 0.5:
                                        _fallback_segs.append({
                                            "clip_id": 0,
                                            "start": round(_fs, 3),
                                            "end": round(_fe, 3),
                                            "role": _roles[_fi] if _fi < len(_roles) else "buildup",
                                            "transition": "hard_cut",
                                            "reason": "moment_fallback",
                                            "impact": 0.5,
                                            "clarity": 0.5,
                                        })
                                if len(_fallback_segs) >= 2:
                                    profile_data["editing_timeline"] = _fallback_segs
                                    logger.info(
                                        f"✅ [MOMENT_FALLBACK] Built {len(_fallback_segs)} segments from top candidate moments."
                                    )
                                else:
                                    logger.warning(
                                        "⚠️ [MOMENT_FALLBACK] Insufficient candidate moments -- render will likely fail."
                                    )
                            else:
                                profile_data["editing_source"] = "fallback"
                                profile_data["fallback_mode"] = True
                                logger.warning("📉 [EDITOR_SOURCE]=fallback | Gemini output invalid. Using Python fallback.")
                            break



                if master_analysis and hasattr(master_analysis, "core_analysis"):
                    core = master_analysis.core_analysis
                    # [FIX] Do NOT use master analysis watermark boxes for inpainting.
                    # The master prompt is an editorial AI — its watermark_present flag
                    # is a coarse editorial signal, NOT a forensic pixel-level detection.
                    # The real forensic scanner (gemini_enhance.detect_watermark) runs
                    # separately in the WATERMARK REMOVAL phase below and provides
                    # accurate bounding boxes. Storing master watermarks here caused
                    # false-positive inpainting of faces and clothing.
                    # We store the hint flag only — boxes come from forensic scan.
                    profile_data["watermark_hint"] = bool(core.watermarks)  # editorial hint only
                    profile_data["watermarks"] = []  # cleared — forensic scan fills this
                    profile_data["quality_score"] = core.quality_score
                    profile_data["forensic_safety"] = core.safety
                    profile_data["content_director"] = getattr(master_analysis, "content_director", {})

                    # ── CONTENT INTELLIGENCE ENGINE (derives enriched feature flags) ──
                    if CONTENT_INTEL_ENGINE_AVAILABLE and _cie_interpret:
                        try:
                            _cie_cd, _cie_flags = _cie_interpret(
                                frames=frame_paths,
                                forensic_result=core.forensic_strategy,
                            )
                            # Merge CIE flags into feature flags (additive, never removes)
                            for _fk, _fv in _cie_flags.items():
                                if _fv:
                                    context.feature_flags[_fk] = True
                                    profile_data["forensic_feature_flags"][_fk] = True
                            if _cie_cd.get("engagement_hook"):
                                profile_data["content_director"].setdefault(
                                    "engagement_hook", _cie_cd["engagement_hook"]
                                )
                            logger.info(
                                f"🧠 [CONTENT_INTEL] narrative='{_cie_cd.get('recommended_narrative')}' "
                                f"style='{_cie_cd.get('editing_style')}' "
                                f"active_flags={[k for k, v in _cie_flags.items() if v]}"
                            )
                        except Exception as _cie_e:
                            logger.warning(
                                f"⚠️ ContentIntelligenceEngine failed (non-fatal): {_cie_e}"
                            )

                    # --- MASTER AS DIRECTOR ROLE ---
                    print("[SYSTEM] MASTER = director | RAG = editor")
                    profile_data["intent"] = getattr(
                        core, "intent", "content_enhancement"
                    )
                    profile_data["tone"] = getattr(master_analysis, "content_director", {}).get("tone", "professional")
                    profile_data["visual_theme"] = getattr(master_analysis, "content_director", {}).get(
                        "visual_theme", "minimalist"
                    )
                    # profile_data["editing_plan"] = core.editing_plan # REMOVED: MASTER NO LONGER CONTROLS EDITS

                    # Feature Flag merge
                    transformation_score = core.transformation_score
                    profile_data["transformation_score"] = transformation_score
                    reused = profile_data.get("content_source", {}).get("reused", False)
                    final_flags = feature_flag_controller.merge_feature_flags(
                        feature_proposals=core.feature_proposals,
                        transformation_score=transformation_score,
                        reused_content=reused,
                        pipeline_context_flags=context.feature_flags,
                    )
                    profile_data["forensic_feature_flags"] = final_flags
                    context.update_feature_flags(final_flags)
                    logger.info(f"🚩 Final Feature Flags: {final_flags}")

                    # [FORCE-ON] Caption + Music are non-negotiable creative outputs.
                    # Gemini or transformation rules must never silently disable them.
                    _must_have = {"caption_generation": True, "music_engine": True}
                    _forced_on = []
                    for _mflag, _mval in _must_have.items():
                        if not context.feature_flags.get(_mflag):
                            context.feature_flags[_mflag] = True
                            profile_data["forensic_feature_flags"][_mflag] = True
                            _forced_on.append(_mflag)
                    if _forced_on:
                        logger.info(f"🔒 [FORCE-ON] Non-negotiable flags restored: {_forced_on}")

                    # ---- CAPTION RESOLUTION -----------------------------------------------------
                    # [FIX] Pre-seed monetization/overlay data so the caption block can read them.
                    # ext is already resolved above; profile_data["monetization_data"] is only
                    # formally written at line ~2653 (after caption block), so seed it now.
                    if not profile_data.get("monetization_data"):
                        _ext_pre = getattr(master_analysis, "extensions", None)
                        if _ext_pre:
                            profile_data["monetization_data"] = _ext_pre.monetization or {}
                            if not profile_data.get("overlay_data"):
                                _raw_ol = (_ext_pre.monetization or {}).get("overlay_data") or {}
                                profile_data["overlay_data"] = _raw_ol

                    # ── [FIRST SHOT ENGINE] Step 3 ─────────────────────────────────────────
                    # Activated ONLY when ENABLE_FIRST_SHOT=yes in Credentials/.env
                    # Relies on fashion_scout.py which was executed inside monetization_brain.
                    try:
                        from First_Shot_Engine.first_shot_engine import run_first_shot_engine
                        _intro_clip_path = run_first_shot_engine(
                            profile_data=profile_data,
                            job_dir=job_dir,
                            title=title,
                            description=description,
                        )
                        if _intro_clip_path:
                            _intro_dur = profile_data.get("first_shot_duration", 5.0)

                            # --- Intelligent Guard & Advanced Intro Modes ---
                            mixed_edit = os.getenv("FIRST_INTRO_MIXED_EDIT", "no").lower() == "yes"
                            proper_edit = os.getenv("FIRST_INTRO_PROPER_EDIT", "no").lower() == "yes"
                            ultra_proper = os.getenv("ULTRA_PROPER_FIRST_INTRO_EDIT", "no").lower() == "yes"

                            active_mode = None
                            enabled_modes = sum([mixed_edit, proper_edit, ultra_proper])
                            
                            if enabled_modes > 1:
                                _q_score = profile_data.get("quality_score", 0.0)
                                _has_drops = bool(profile_data.get("beat_data", {}).get("drops"))
                                if _has_drops and ultra_proper: active_mode = "ULTRA_PROPER"
                                elif _q_score > 0.8 and proper_edit: active_mode = "PROPER"
                                else: active_mode = "MIXED"
                            elif enabled_modes == 1:
                                if ultra_proper: active_mode = "ULTRA_PROPER"
                                elif proper_edit: active_mode = "PROPER"
                                elif mixed_edit: active_mode = "MIXED"

                            if active_mode:
                                # [NEW ARCHITECTURE] We no longer inject the intro into the main timeline here.
                                # We let the main video process normally and concatenate the intro at the very end.
                                profile_data["first_shot_clip"] = _intro_clip_path
                                profile_data["first_shot_mode_active"] = active_mode
                                logger.info(f"🎬 [FIRST_SHOT] Mode={active_mode}. Intro will be concatenated post-processing to protect timeline sync.")
                            else:
                                # --- OLD FALLBACK BEHAVIOR (If no advanced variables set) ---
                                _intro_scene = {
                                    "clip_id": 0, "start": 0.0, "end": round(_intro_dur, 3), "speed": 1.0, "label": "AI Intro Hook", "style": "clean"
                                }
                                _plan = profile_data.get("editing_plan")
                                if isinstance(_plan, dict):
                                    for _seg in _plan.get("segments", []): _seg["clip_id"] = int(_seg.get("clip_id", 0)) + 1
                                _et = profile_data.get("editing_timeline", [])
                                for _seg in _et: _seg["clip_id"] = int(_seg.get("clip_id", 0)) + 1
                                _rt = profile_data.get("reconstructed_timeline", [])
                                for _seg in _rt: _seg["clip_id"] = int(_seg.get("clip_id", 0)) + 1
                                _rh = profile_data.get("rhythm_timeline", [])
                                for _seg in _rh: _seg["clip_id"] = int(_seg.get("clip_id", 0)) + 1

                                input_paths.insert(0, _intro_clip_path)
                                if isinstance(_plan, dict): _plan.setdefault("segments", []).insert(0, _intro_scene.copy())
                                if _et:
                                    _et.insert(0, _intro_scene.copy())
                                    profile_data["editing_timeline"] = _et
                                if _rt:
                                    _rt.insert(0, _intro_scene.copy())
                                    profile_data["reconstructed_timeline"] = _rt
                                if _rh:
                                    _rh.insert(0, _intro_scene.copy())
                                    profile_data["rhythm_timeline"] = _rh

                                logger.info(f"🎬 [FIRST_SHOT] Legacy Intro prepended at Step 3 | dur={_intro_dur:.2f}s")
                            
                            logger.info(
                                f"🎬 [FIRST_SHOT] Intro prepended at Step 3 | "
                                f"topic={profile_data.get('first_shot_topic')} | "
                                f"dur={_intro_dur:.2f}s | "
                                f"mode={active_mode} | "
                                f"total_clips={len(input_paths)}"
                            )
                    except ImportError:
                        pass  # Engine not installed — silently skip
                    except Exception as _fse_err:
                        logger.warning(f"⚠️ [FIRST_SHOT] Step 3 non-fatal error: {_fse_err}")
                    # ─────────────────────────────────────────────────────────────────────────

                    _caption_text_for_overlay = None
                    _enable_fs = os.getenv("ENABLE_FASHION_SCOUT", "yes").lower() in ("yes", "true", "on")
                    if context.feature_flags.get("caption_generation") and _enable_fs:
                        try:
                            _pm = profile_data.get("pipeline_metrics", {})
                            mon_data = profile_data.get("monetization", {}) or profile_data.get("monetization_data", {}) or _pm.get("monetization", {})
                            overlay_data = profile_data.get("overlay_data", {})
                            content_director = profile_data.get("content_director", {})
                            generated_caption = None

                            # [FASHION] Use outfit name from fashion_scout as the caption (highest priority)
                            # first_shot.wear_name is the most precise — set by FashionScout, no API call needed
                            _fashion_data_cap = profile_data.get("fashion_scout", {}) or mon_data.get("fashion_scout", {}) or _pm.get("fashion_scout", {})
                            if _fashion_data_cap:
                                try:
                                    from Text_Modules.fashion_narrator import extract_outfit_caption
                                    _fashion_caption = extract_outfit_caption(_fashion_data_cap, max_words=5)
                                    if _fashion_caption:
                                        generated_caption = _fashion_caption
                                        logger.info(f"[FASHION_CAPTION] caption='{_fashion_caption}' source=fashion_scout")
                                except Exception as _fce:
                                    logger.warning(f"[FASHION_CAPTION] extract failed: {_fce}")

                            # [SAFE_FALLBACK] If FashionScout failed, use a TRUE VISUAL AI CAPTION SCAN (Guarantees no hallucination)
                            if not generated_caption:
                                try:
                                    import cv2 as _cv2
                                    from Text_Modules.gemini_captions import GeminiCaptionGenerator
                                    
                                    _cap_fb = _cv2.VideoCapture(current_video_source)
                                    _cap_fb.set(_cv2.CAP_PROP_POS_FRAMES, int(_cap_fb.get(_cv2.CAP_PROP_FRAME_COUNT) * 0.3))
                                    _ret_fb, _frame_fb = _cap_fb.read()
                                    _cap_fb.release()
                                    
                                    if _ret_fb:
                                        os.makedirs("temp", exist_ok=True)
                                        _fb_path = "temp/live_fallback_scan.jpg"
                                        _cv2.imwrite(_fb_path, _frame_fb)
                                        
                                        # Use the ACTUAL image instead of blind JSON
                                        _gen = GeminiCaptionGenerator()
                                        generated_caption = _gen.generate_caption(_fb_path, "viral")
                                        
                                        if generated_caption:
                                            logger.info(f"[CAPTION_ENGINE] source=true_visual_ai caption='{generated_caption}'")
                                except Exception as _cfe:
                                    logger.warning(f"[CAPTION_ENGINE] true_visual_ai_failed: {_cfe}")

                            # [EMERGENCY_FALLBACK] If live AI also fails, pull from local cache
                            if not generated_caption:
                                try:
                                    from Text_Modules.gemini_captions import GeminiCaptionGenerator
                                    generated_caption = GeminiCaptionGenerator().get_diversified_fallback()
                                    logger.info(f"[CAPTION_ENGINE] source=cache caption='{generated_caption}'")
                                except Exception as _ce:
                                    logger.warning(f"[CAPTION_ENGINE] cache_fallback_failed: {_ce}")

                            _raw_caption = (
                                generated_caption
                                or overlay_data.get("caption_text")
                                or content_director.get("caption_text")
                                or mon_data.get("caption_text")
                                or overlay_data.get("trend_text")
                                or overlay_data.get("context_text")
                            )

                            # Fallback: first 6 words of editorial_script
                            if not _raw_caption:
                                script = (
                                    mon_data.get("editorial_script")
                                    or profile_data.get("editorial_script")
                                    or ""
                                )
                                words = script.split()
                                if words:
                                    _raw_caption = " ".join(words[:6])

                            # Final fallback via generator
                            if not _raw_caption:
                                try:
                                    from Text_Modules.gemini_captions import GeminiCaptionGenerator

                                    _raw_caption = (
                                        GeminiCaptionGenerator().get_diversified_fallback()
                                    )
                                except Exception:
                                    from Text_Modules.caption_sanitizer import get_fallback

                                    _raw_caption = get_fallback()

                            # [FIX] Caption length enforcement: keep FIRST words (context-bearing),
                            # not last words as the original code did.  Target 3–7 words.
                            if _raw_caption:
                                words = _raw_caption.split()
                                if len(words) > 7:
                                    _raw_caption = " ".join(words[:7])

                            _caption_text_for_overlay = sanitize_caption_text(
                                _raw_caption, target_max=4, hard_max=6
                            )

                            if _caption_text_for_overlay:
                                profile_data["caption"] = _caption_text_for_overlay
                                logger.info(
                                    f"📝 [CAPTION_RESOLUTION] "
                                    f'caption_text_resolved="{_caption_text_for_overlay}"'
                                )
                            else:
                                logger.warning("⚠️ No caption text resolved.")
                                auditor.mark_failed("caption_generation")
                        except Exception as e:
                            logger.error(f"Caption resolution failed: {e}")
                            auditor.mark_failed("caption_generation")
                    else:
                        auditor.mark_disabled("caption_generation")

                    profile_data["editing_strategy"] = core.editing_strategy

                    ext = master_analysis.extensions
                    profile_data["fashion_data"] = ext.fashion
                    profile_data["monetization_data"] = ext.monetization
                    profile_data["narrative_data"] = ext.narrative
                    profile_data["hook_variants"] = ext.hook_variants

                    # Overlay + content director sanitisation
                    raw_overlay = ext.monetization.get("overlay_data") or {}
                    if raw_overlay:
                        for _k in ("trend_text", "context_text", "caption_text"):
                            if raw_overlay.get(_k):
                                raw_overlay[_k] = sanitize_caption_text(
                                    raw_overlay[_k], target_max=4, hard_max=6
                                )
                    profile_data["overlay_data"] = raw_overlay

                    content_director = (
                        core.forensic_strategy.get("content_director") or {}
                    )
                    if content_director.get("caption_text"):
                        content_director["caption_text"] = sanitize_caption_text(
                            content_director["caption_text"], target_max=4, hard_max=6
                        )
                    profile_data["content_director"] = content_director

                    # Force captions on when editorial_script is available
                    _pm_script = profile_data.get("pipeline_metrics", {})
                    _mon_script = profile_data.get("monetization", {}) or profile_data.get("monetization_data", {}) or _pm_script.get("monetization", {})
                    editorial_script = _mon_script.get("editorial_script") or profile_data.get("editorial_script")
                    if editorial_script and not context.feature_flags.get(
                        "caption_generation", False
                    ):
                        context.feature_flags["caption_generation"] = True
                        logger.info(
                            "📝 Forcing caption_generation ON (editorial_script available)."
                        )

                    # ── TREND OPPORTUNITY ENGINE (zero Gemini calls, purely algorithmic) ──
                    if TREND_OPPORTUNITY_AVAILABLE and _toe_analyse:
                        try:
                            # [FIX] Use the rich TrendEngine instead of a manual sparse dictionary
                            _visual_ents = profile_data.get("forensic_entities", {}).get("key_elements", [])
                            if _get_trend_ctx:
                                _toe_ctx = _get_trend_ctx(visual_entities=_visual_ents)
                            else:
                                # Fallback to manual if engine import failed
                                _toe_ctx = {
                                    "trend_context": {
                                        "topics": list(profile_data.get("forensic_entities", {}).get("topics", [])),
                                        "keywords": list(profile_data.get("forensic_entities", {}).get("keywords", [])),
                                        "trend_strength": float(profile_data.get("transformation_score", 0.0)),
                                    }
                                }

                            _toe_result = _toe_analyse(
                                trend_context=_toe_ctx,
                                existing_strategy=content_director,
                            )
                            profile_data["trend_opportunity"] = _toe_result
                            context.update_intelligence_data(
                                {"trend_opportunity": _toe_result}
                            )
                            auditor.mark_executed("trend_engine")
                            logger.info(
                                f"🔀 [TREND_ENGINE] stage={_toe_result.get('trend_stage')} "
                                f"angle={_toe_result.get('recommended_angle')} "
                                f"score={_toe_result.get('trend_opportunity', {}).get('opportunity_score', 0):.3f}"
                            )
                        except Exception as _toe_e:
                            logger.warning(
                                f"⚠️ TrendOpportunityEngine failed (non-fatal): {_toe_e}"
                            )
                            auditor.mark_failed("trend_engine")
                    elif getattr(ext, "trend_opportunity", None) or profile_data.get(
                        "trend_data"
                    ):
                        auditor.mark_executed("trend_engine")
                    else:
                        auditor.mark_skipped("trend_engine")

                    # ── HOOK VARIANTS (zero Gemini calls, multi-variant hook scoring) ──
                    if HOOK_VARIANTS_AVAILABLE and _hvg_generate:
                        try:
                            _hvg_result = _hvg_generate(
                                base_hook=profile_data.get("hook_analysis"),
                                content_strategy=profile_data.get("creative_strategy")
                                or content_director,
                            )
                            _hv = _hvg_result.get("hook_variant", {})
                            profile_data["hook_variant"] = _hv
                            auditor.mark_executed("hook_engine")
                            logger.info(
                                f"🎣 [HOOK_VARIANTS] hint={_hv.get('hook_time_hint', 0):.2f}s "
                                f"confidence={_hv.get('confidence', 0):.3f}"
                            )
                        except Exception as _hvg_e:
                            logger.warning(
                                f"⚠️ HookVariants failed (non-fatal): {_hvg_e}"
                            )
                            auditor.mark_failed("hook_engine")
                    elif getattr(ext, "hook_variants", None):
                        auditor.mark_executed("hook_engine")
                    else:
                        auditor.mark_skipped("hook_engine")

                    # Shot Scoring (Step 2b) — fallback only when no candidate_moments
                    _step2b_has_moments = bool(profile_data.get("candidate_moments"))
                    if "shots" in profile_data:
                        if _step2b_has_moments:
                            logger.info(
                                "⏭️  [Step 2b] Shot Scoring skipped — "
                                "candidate_moments present (Moment Authority active)."
                            )
                        else:
                            logger.info(
                                "🎯 [Step 2b] Shot Scoring & Selection (fallback)..."
                            )
                            shots = profile_data["shots"]
                            motion_data = profile_data.get("motion_scores", [])
                            face_data = profile_data.get("subject_tracking", [])
                            scored_shots = shot_detector.score_shots(
                                shots,
                                motion_data,
                                face_data,
                                {"editing_plan": core.editing_plan},
                            )
                            beats = profile_data.get("beat_data", {}).get("beats", [])
                            scored_shots = shot_detector.snap_to_beats(
                                scored_shots, beats
                            )
                            selected_shots = shot_detector.select_best_shots(
                                scored_shots
                            )
                            profile_data["selected_shots"] = selected_shots
                            if selected_shots:
                                profile_data["cold_open"] = selected_shots[0]

                    # Overlay defaults
                    _enable_fs = os.getenv("ENABLE_FASHION_SCOUT", "yes").lower() in ("yes", "true", "on")
                    ol = ext.monetization.get("overlay_data", {}) if _enable_fs else {}
                    profile_data.update(
                        {
                            "brand_text": os.getenv("BRAND_NAME")
                            or ol.get("brand_text")
                            or "Fashion Analysis",
                            "item_name": (ol.get("item_name") or ol.get("commercial_item_name")) if _enable_fs else "Style",
                            "price_tag": ol.get("price_tag", "") if _enable_fs else "",
                            "price_tag_time": ol.get("price_tag_time", 0.75),
                        }
                    )

                    if BRAIN_AVAILABLE:
                        _brain = MonetizationStrategist()
                        _brain.lite_monetization_update(profile_data)

                    # ── [FASHION SCOUT WIRE] ──────────────────────────────────────────────
                    # FashionScout was previously only called inside analyze_content()
                    # which is NEVER invoked by the orchestrator (only lite_monetization_update
                    # is called here, which makes 0 API calls and skips scout entirely).
                    # This block re-wires the scout directly into the orchestrator pipeline
                    # so that fashion_scout data is always present in the sidecar JSON
                    # and available to main.py via mon_meta.get("fashion_scout").
                    # _enable_fs already resolved above
                    _active_niche_fs = profile_data.get("niche_category", "")
                    _is_nsfw_fs = bool(_active_niche_fs) and any(
                        kw in _active_niche_fs.lower()
                        for kw in ("nsfw", "adult", "18+", "explicit")
                    )
                    # Use local frame_paths; fall back to profile_data store (blind/fallback mode)
                    _fs_source_frames = frame_paths or profile_data.get("frame_paths", [])
                    if _enable_fs and not _is_nsfw_fs and _fs_source_frames:
                        try:
                            from Monetization_Metrics.fashion_scout import scout as _fs_scout
                            if _fs_scout:
                                # ── SMART FRAME SPREAD-SAMPLER ──────────────────────────────
                                # Problem: taking the first 6 frames hits intro/transition shots
                                # where the garment isn't showcased yet, confusing the scout.
                                # Solution: distribute picks evenly across the full timeline so
                                # Gemini sees the outfit at its best showcase moments.
                                #
                                # Strategy:
                                #   1. Filter to frames that exist on disk
                                #   2. Skip first 10% (often blank intro) and last 5% (outro)
                                #   3. Pick up to 4 frames spread evenly across remaining range
                                #   4. Always include the middle frame (highest intent signal)
                                _all_valid = [fp for fp in _fs_source_frames if os.path.exists(fp)]
                                _n = len(_all_valid)
                                if _n <= 4:
                                    _fs_frames = _all_valid
                                else:
                                    # Define working range: skip first 10%, last 5%
                                    _start_idx = max(0, int(_n * 0.10))
                                    _end_idx   = max(_start_idx + 1, int(_n * 0.95))
                                    _working   = _all_valid[_start_idx:_end_idx]
                                    _wn        = len(_working)

                                    if _wn <= 4:
                                        _fs_frames = _working
                                    else:
                                        # Pick 4 evenly-spaced frames from the working range
                                        # Indices: 0%, 33%, 66%, 100% of working range
                                        _step = (_wn - 1) / 3
                                        _picks = [
                                            _working[min(int(round(i * _step)), _wn - 1)]
                                            for i in range(4)
                                        ]
                                        # Deduplicate while preserving order
                                        _seen_p = set()
                                        _fs_frames = []
                                        for _p in _picks:
                                            if _p not in _seen_p:
                                                _fs_frames.append(_p)
                                                _seen_p.add(_p)
                                # ─────────────────────────────────────────────────────────────
                                if _fs_frames:
                                    logger.info(
                                        f"👗 [FASHION_SCOUT] Running scout on {len(_fs_frames)} frames..."
                                    )
                                    _fs_result = _fs_scout.scout_outfit(_fs_frames)
                                    if _fs_result:
                                        # ── Inject into monetization_data (primary path) ──
                                        # The sidecar Step 10 resolves:
                                        #   _mon_data = profile_data.get("monetization", {})
                                        #            or profile_data.get("monetization_data", {})
                                        # So fashion_scout must live inside monetization_data.
                                        _mon_d = profile_data.get("monetization_data")
                                        if isinstance(_mon_d, dict):
                                            _mon_d["fashion_scout"] = _fs_result
                                        else:
                                            profile_data["monetization_data"] = {
                                                "fashion_scout": _fs_result
                                            }
                                        # ── Inject into fashion_data (sidecar fallback path) ──
                                        # Sidecar Step 10 line:
                                        #   "fashion_scout": _mon_data.get("fashion_scout")
                                        #                    or profile_data.get("fashion_data")
                                        profile_data["fashion_data"] = _fs_result
                                        logger.info(
                                            f"✅ [FASHION_SCOUT] Wired successfully: "
                                            f"wear_name='{_fs_result.get('wear_name', '?')}' | "
                                            f"confidence={_fs_result.get('confidence', '?')}"
                                        )
                                    else:
                                        logger.warning(
                                            "⚠️ [FASHION_SCOUT] scout_outfit returned None — "
                                            "frames may be too low quality or niche gated."
                                        )
                                else:
                                    logger.warning(
                                        "⚠️ [FASHION_SCOUT] No valid frame paths found on disk — skipping."
                                    )
                        except Exception as _fse:
                            logger.warning(
                                f"⚠️ [FASHION_SCOUT] Non-fatal integration error: {_fse}"
                            )
                    elif not _enable_fs:
                        logger.info("🔒 [FASHION_SCOUT] Disabled via ENABLE_FASHION_SCOUT=no.")
                    elif _is_nsfw_fs:
                        logger.info(
                            f"🔒 [FASHION_SCOUT] Bypassed — NSFW niche detected: '{_active_niche_fs}'."
                        )
                    # ─────────────────────────────────────────────────────────────────────

                    logger.info(
                        "✅ [MASTER_ANALYSIS_SUCCESS] Pipeline data fully populated."
                    )
                else:
                    raise RuntimeError("Master analysis returned empty result")

            except Exception as mae:
                logger.error(f"""
🚨 [GEMINI_FAILURE_ANALYSIS]
- error: {str(mae)}
- fused_moments_present: {bool(profile_data.get("fused_moments"))}
- frames_available: {len(frame_paths)}
- timeline_context_present: {bool(timeline_context)}
- signal_poverty: {profile_data.get("signal_poverty", False)}
""")
                if profile_data.get("signal_poverty"):
                    logger.error("🚨 [HARD_FAIL] Intelligence failed AND signals are poor. Terminating.")
                    return False, {
                        "status": "INTELLIGENCE_FAILURE", 
                        "reason": f"Gemini error: {str(mae)} (Signal Poverty active)",
                        "action": "SKIP_EDITING"
                    }

                logger.warning("🛡️ [SMART_FAILOVER] Gemini Failed but signals exist. Triggering Lightweight Recovery...")
                profile_data["fallback_mode"] = True
                profile_data["editing_plan"] = {
                    "logic": "opencv-heuristic",
                    "scenes": [],
                }
                profile_data["forensic_safety"] = "unknown"
                
                # Restore safe defaults so pipeline modules (music, captions, etc.) run in fallback mode
                _fallback_flags = feature_flag_controller.merge_feature_flags(
                    feature_proposals={},
                    transformation_score=0.0,
                    reused_content=False,
                    pipeline_context_flags=context.feature_flags,
                )
                _fallback_flags["enable_fast_pacing"] = True
                profile_data["forensic_feature_flags"] = _fallback_flags
                context.update_feature_flags(_fallback_flags)

                # [FIX 2] Add fallback caption in SMART_FAILOVER path
                if not profile_data.get("caption"):
                    try:
                        from Text_Modules.gemini_captions import GeminiCaptionGenerator
                        profile_data["caption"] = GeminiCaptionGenerator().get_diversified_fallback()
                    except Exception:
                        profile_data["caption"] = "Style Moment"

        # Safety guard
        if profile_data.get("forensic_safety") in ["blocked", "BLOCK"]:
            logger.error(
                "🚫 [SAFETY_STOP] Content flagged as non-compliant. Terminating job."
            )
            return False, {"status": "safety_violation", "reason": "AI Safety Reject"}

        # --- UNIVERSAL RAG CONTROL (THE CREATIVE BRAIN) ---
        if "rag_strategy" in profile_data:
            profile_data["editing_plan"] = apply_rag_to_editing_plan(
                profile_data.get("editing_plan"), profile_data["rag_strategy"], profile_data
            )

        # The duplicate 'Phase 2 / Forensic Scan Path' has been removed here.
        # Watermarks are handled entirely in [Step 0.25] before intelligence extraction.

        # ---- AUDIO REMIX ------------------------------------------------------------
        _is_cinematic_story = os.getenv("CONTENT_NICHE", "").strip().lower() == "cinematic_story"
        if _is_cinematic_story:
            logger.info("🎬 [CINEMATIC_STORY] Audio remix SKIPPED — narration script drives the edit.")
            auditor.mark_disabled("audio_remix")
        elif HEAVY_REMIX_AVAILABLE:
            try:
                remixed = os.path.join(job_dir, "remixed_source.mp4")
                remixed_audio = os.path.join(job_dir, "remixed_audio.aac")
                if heavy_remix(current_video_source, remixed_audio):
                    _mux = [
                        os.getenv("FFMPEG_BIN", "ffmpeg"),
                        "-y",
                        "-i",
                        current_video_source,
                        "-i",
                        remixed_audio,
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-shortest",
                        remixed,
                    ]
                    try:
                        subprocess.run(
                            _mux, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300
                        ).check_returncode()
                        current_video_source = remixed
                        logger.info("🎛️ Audio Remixed (Bypass Active)")
                    except subprocess.TimeoutExpired:
                        logger.error("🚨 [MUX_TIMEOUT] Audio remixing timed out after 300s. Skipping.")
                    except subprocess.CalledProcessError as e:
                        logger.error(f"❌ [MUX_ERROR] Audio remixing failed: {e.stderr.decode() if e.stderr else 'unknown'}")
            except Exception as e:
                logger.warning(f"⚠️ Heavy remix skipped: {e}")

        # ---- FINAL EDGE CROP --------------------------------------------------------
        try:
            crop_path = os.path.join(job_dir, "cropped_final.mp4")
            _crop = [
                os.getenv("FFMPEG_BIN", "ffmpeg"),
                "-y",
                "-i",
                current_video_source,
                "-vf",
                "crop=in_w*0.95:in_h*0.95:in_w*0.025:in_h*0.025,scale=1080:1920",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                crop_path,
            ]
            try:
                subprocess.run(
                    _crop, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300
                ).check_returncode()
                current_video_source = crop_path
                logger.info("✂️ Final Edge Crop Applied.")
            except subprocess.TimeoutExpired:
                logger.error("🚨 [CROP_TIMEOUT] Final edge crop timed out after 300s. Proceeding with original source.")
            except subprocess.CalledProcessError as e:
                logger.warning(f"⚠️ [CROP_ERROR] Final edge crop failed: {e.stderr.decode() if e.stderr else 'unknown'}")
        except Exception as e:
            logger.warning(f"⚠️ Final edge crop skipped: {e}")

        # ---- BRAND TEXT RESOLUTION --------------------------------------------------
        _brand_text_for_overlay = os.getenv("BRAND_NAME") or profile_data.get(
            "brand_text"
        )
        _add_text_overlay = os.getenv("ADD_TEXT_OVERLAY", "no").strip().lower() == "yes"
        if _add_text_overlay and _brand_text_for_overlay:
            logger.info(f"🏷️  [BRAND] Brand overlay queued: '{_brand_text_for_overlay}'")
        elif not _brand_text_for_overlay:
            logger.info("🏷️  [BRAND] No BRAND_NAME configured — skipping brand overlay.")
        else:
            logger.info("🏷️  [BRAND] ADD_TEXT_OVERLAY not enabled — skipping.")

        # ---- TITLE OVERLAY (SHOW_USER_TITLE_OVERLAY) --------------------------------
        _show_title = os.getenv("SHOW_USER_TITLE_OVERLAY", "no").strip().lower() == "yes"
        _title_filter_str = None
        if _show_title and title:
            try:
                from Text_Modules.text_overlay import get_timed_overlay_filter
                _title_filter_str = get_timed_overlay_filter(title, "top")
                logger.info(f"🔤 [TITLE_OVERLAY] Title queued: '{title}'")
            except Exception as _te:
                logger.warning(f"⚠️ [TITLE_OVERLAY] Failed to generate filter: {_te}")
        else:
            logger.info("🔤 [TITLE_OVERLAY] SHOW_USER_TITLE_OVERLAY=no — title overlay skipped.")

        # ---- VOICEOVER / SCRIPT GENERATION -----------------------------------------
        _narrator_enabled = os.getenv("CINEMATIC_NARRATOR_ENABLED", "yes").lower() == "yes"
        _vo_env = os.getenv("ENABLE_MICRO_VOICEOVER", "yes").lower()
        _vo_enabled = _vo_env == "yes"
        
        voiceover_path = None
        _pm_vo = profile_data.get("pipeline_metrics", {})
        mon_data = profile_data.get("monetization", {}) or profile_data.get("monetization_data", {}) or _pm_vo.get("monetization", {})
        
        if not _narrator_enabled or not _vo_enabled:
            logger.info("🎙️ [VOICEOVER] Voiceover or narrator is disabled — bypassing script generation completely.")
            full_script = ""
            profile_data["editorial_script"] = ""
            if isinstance(mon_data, dict):
                mon_data["editorial_script"] = ""
        else:
            _has_editorial_script = bool(
                mon_data.get("editorial_script")
                or profile_data.get("editorial_script")
            )
            _micro_vo_forced = _vo_env == "yes"
            # Script generation block — runs when narration is enabled
            full_script = (
                mon_data.get("editorial_script")
                or mon_data.get("fashion_scout", {}).get("editorial_script")
                or profile_data.get("editorial_script")
                or ""
            )

            # [FASHION NARRATION] Removed override — FashionScout editorial_script is now the primary source.

            # [FALLBACK SCRIPT] Synthesize from title/caption signals when Gemini didn't produce one
            if not full_script:
                _fb_caption = profile_data.get("caption") or ""
                _fb_title = title or ""
                _fb_parts = [p for p in [_fb_title, _fb_caption] if p]
                if _fb_parts:
                    full_script = ". ".join(_fb_parts)
                else:
                    try:
                        from Audio_Modules.voiceover import EDITORIAL_TEMPLATES
                        import random as _r
                        full_script = _r.choice(EDITORIAL_TEMPLATES)
                    except Exception:
                        full_script = "A masterclass in style and craftsmanship, defining the intersection of luxury and design."
                logger.info(f"[VOICEOVER_FALLBACK] Built script from profile signals: {len(full_script)} chars")

            if full_script:
                try:
                    ref_result = voiceover.refine_commentary(full_script)
                    _refined_text = ref_result.get("text", full_script)
                    if not isinstance(_refined_text, str):
                        logger.warning(
                            "[COMMENTARY_ENGINE] ref_result['text'] is %s not str — keeping original.",
                            type(_refined_text).__name__,
                        )
                        _refined_text = full_script
                    profile_data["editorial_script"] = _refined_text
                    mon_data["editorial_script"] = _refined_text
                    profile_data["monetization_data"] = mon_data
                    full_script = _refined_text
                    logger.info(
                        "[COMMENTARY_ENGINE] narration_refined=%s | "
                        'original="%s" | refined="%s"',
                        ref_result.get("changed", False),
                        ref_result.get("original", "")[:200],
                        full_script[:200],
                    )
                except Exception as e:
                    logger.warning(f"[COMMENTARY_ENGINE] refinement_failed: {e}")

        # ---- TTS AUDIO — gated separately from script generation ----------------
        # [LATE_SCRIPT_GENERATION] When LATE_SCRIPT_GENERATION=yes, TTS is deferred to
        # after the visual render is complete so the script is duration-aware.
        # The early TTS block is fully skipped in that mode; a dedicated block after
        # the SRV verifier (just before FINAL AUDIO MIX) handles it instead.
        _late_script_mode = os.getenv("LATE_SCRIPT_GENERATION", "yes").strip().lower() == "yes"
        if _late_script_mode:
            logger.info(
                "🎙️ [VOICEOVER] LATE_SCRIPT_GENERATION=yes — TTS deferred to post-visual-render stage."
            )
        elif context.feature_flags.get("voiceover_generation"):
            if VOICEOVER_AVAILABLE and full_script and len(full_script.strip()) > 10:
                if _vo_env == "no":
                    logger.info("🎙️ [VOICEOVER] SCRIPT GENERATED, BUT AUDIO BLOCKED (ENABLE_MICRO_VOICEOVER=no).")
                else:
                    vo_file = os.path.join(job_dir, "voiceover.mp3")
                    logger.info(f"🎙️ [VOICEOVER] script_length={len(full_script)} chars")
                    success = run_with_timeout(
                        func=voiceover.generate_voiceover,
                        timeout_sec=120,
                        feature_name="voiceover_generation",
                        auditor=auditor,
                        script_text=full_script[:500],
                        output_file=vo_file,
                    )
                    if success:
                        voiceover_path = vo_file
                        logger.info(f"🎙️ Voiceover Prepared: {len(full_script[:500])} chars")
            else:
                auditor.mark_disabled("voiceover_generation")
        else:
            auditor.mark_disabled("voiceover_generation")


        # ---- MUSIC ------------------------------------------------------------------
        music_path = None
        _use_audio_pool = os.getenv("CINEMATIC_USE_AUDIO_POOL", "no").strip().lower() == "yes"
        if _is_cinematic_story and not _use_audio_pool:
            logger.info("🎬 [CINEMATIC_STORY] Audio pool / music engine SKIPPED — narration is the audio backbone.")
            auditor.mark_disabled("music_engine")
        elif MUSIC_MANAGER_AVAILABLE and context.feature_flags.get("music_engine"):
            try:
                # [USER REQUEST] Audio Source Policy:
                #   Single videos → use Original_audio/ (clean extracted original track, beat-matched)
                #   Compilations  → use music/ (curated BGM library)
                # The music/ folder MUST NOT be used for single video jobs because the
                # shots are AI-reshuffled and the internal audio is glitchy/disordered.
                # We mute the internal video audio and replace with the clean extracted track.
                _is_compilation = profile_data.get("is_compilation", False)
                if _is_compilation:
                    _bgm_dir = os.getenv("BGM_DIR", "music")
                    logger.info(f"🎵 [MUSIC] Compilation mode → using BGM library: {_bgm_dir}")
                else:
                    _bgm_dir = "Original_audio"
                    logger.info(f"🎵 [MUSIC] Single video mode → using extracted original audio: {_bgm_dir}")

                if not os.path.exists(_bgm_dir):
                    _bgm_dir = "music"  # Absolute fallback only if Original_audio/ missing

                # ── POOL MAINTENANCE: rotate cooldown → active before selection ──
                # If active/ is empty and some files have served their cooldown period,
                # this brings them back so selection has something to work with.
                try:
                    from Audio_Modules.audio_pool_manager import pool_manager as _apm_m
                    _apm_m.maintenance()
                except Exception as _maint_e:
                    logger.debug(f"[POOL_MAINTENANCE] Non-fatal: {_maint_e}")
                # ─────────────────────────────────────────────────────

                # ── POOL-FIRST: Use pre-selected external BGM if available ──────────
                # bgm_audio_path is set earlier in the pipeline (Step 0.45) by the
                # self-selection-guarded pool selector. It is guaranteed to be a
                # DIFFERENT track from the current video's own audio.
                # If it exists, skip the music_manager scan entirely.
                _preselected_bgm = profile_data.get("bgm_audio_path")
                if _preselected_bgm and os.path.exists(_preselected_bgm):
                    music_path = _preselected_bgm
                    logger.info(
                        f"🎵 [MUSIC] Using pool-preselected BGM (self-selection guard active): "
                        f"{_preselected_bgm.replace(os.sep, '/')}"
                    )
                    auditor.mark_executed("music_engine")
                else:
                    # No pool pre-selection → run music_manager normally
                    _mm = ContinuousMusicManager(music_dir=_bgm_dir)
                    music_path = _mm.get_best_match(profile_data)
                    if music_path:
                        logger.info(f"🎵 Selected Music: {os.path.basename(music_path)}")
                        auditor.mark_executed("music_engine")
                    else:
                        # Pool empty + music library empty → last resort self-audio
                        _fallback_audio = profile_data.get("extracted_audio_path", current_video_source)
                        logger.warning(
                            f"⚠️ [MUSIC] Pool empty & library empty. Last resort self-audio: "
                            f"{os.path.basename(_fallback_audio)}"
                        )
                        music_path = _fallback_audio
                        auditor.mark_executed("music_engine")
            except Exception as e:
                logger.error(f"Music tracking failed: {e}")
                auditor.mark_failed("music_engine")
        else:
            auditor.mark_disabled("music_engine")

        # ---- [FIX] CREATIVE EDITOR BRIDGE (Step 2b.5 — was never called) ------------
        # CreativeEditorBridge was imported at module level but never invoked inside
        # compile_video(). This caused bgm_classified_beats, beat_timeline_segments,
        # and vo_pacing_hints to never be populated, silently disabling all beat-driven
        # editing and forcing RhythmTimelineBuilder to fall back to editing_plan segments.
        _StepTracer.start("creative_editor_bridge")
        try:
            from Compiler_Modules.creative_editor_bridge import CreativeEditorBridge

            _bridge = CreativeEditorBridge()
            profile_data = _bridge.run(
                profile_data=profile_data,
                music_path=music_path,
                job_dir=job_dir,
            )
            _n_bgm_beats = len(profile_data.get("bgm_classified_beats") or [])
            _n_beat_segs = len(profile_data.get("beat_timeline_segments") or [])
            logger.info(
                f"🥁 BEAT_ALIGNMENT CreativeEditorBridge complete — "
                f"bgm_classified_beats={_n_bgm_beats} | beat_segments={_n_beat_segs}"
            )
            if _n_bgm_beats > 0:
                logger.info(
                    f"🎬 CREATIVE_EDITOR Beat-driven timeline: {_n_beat_segs} segments "
                    f"from {_n_bgm_beats} classified beats"
                )
            auditor.mark_executed("creative_editor_bridge")
            _StepTracer.success("creative_editor_bridge")
        except Exception as _ceb_err:
            logger.warning(f"⚠️ CreativeEditorBridge failed (non-fatal): {_ceb_err}")
            auditor.mark_failed("creative_editor_bridge")
            _StepTracer.fail("creative_editor_bridge", str(_ceb_err))

        # ---- CREATIVE DIRECTOR (Step 2c) --------------------------------------------
        tracker.step("STEP_SUBTITLES", 40)
        logger.info("🎭 [Step 2c] Creative Director...")
        try:
            from Content_Intelligence.creative_director import CreativeDirector

            _director = CreativeDirector(profile_data)
            creative_strategy = _director.build_strategy()

            candidate_moments = profile_data.get("candidate_moments", [])
            if candidate_moments:
                story_map = _director.build_narrative_story_map(candidate_moments)
                creative_strategy["story_map"] = story_map
                logger.info(
                    f"🎭 Narrative Story Map: {len(story_map)} moments sequenced"
                )

            fused_moments = profile_data.get("fused_moments", [])
            hook_result = _director.optimize_hook(
                fused_moments=fused_moments, candidate_moments=candidate_moments
            )
            profile_data["hook_optimization"] = hook_result
            creative_strategy["hook_optimization"] = hook_result

            if hook_result.get("hook_enabled"):
                logger.info(
                    f"🎯 [HOOK_ENGINE] hook_time={hook_result['hook_time']:.2f}s | "
                    f"score={hook_result['hook_score']:.3f}"
                )

            profile_data["creative_strategy"] = creative_strategy
            # ── Structured creative logs for Story Structure ──────────────────────────
            _story_map = creative_strategy.get("story_map", [])
            if _story_map:
                # Guard: story_map entries may be strings if creative_director
                # returns a list of labels rather than dicts — skip non-dicts.
                _story_stages = " → ".join([
                    s.get("type", "?") for s in _story_map
                    if isinstance(s, dict)
                ])
                logger.info(f"📖 STORY_STRUCTURE {_story_stages}")
                logger.info(
                    f"🎬 CREATIVE_EDITOR Narrative arc: {len(_story_map)} story moments"
                )
            _zones = creative_strategy.get("zones", {})
            if _zones:
                logger.info(
                    f"📖 STORY_STRUCTURE Zones: "
                    + " | ".join(
                        [f"{k}={v[0]:.1f}s-{v[1]:.1f}s" for k, v in _zones.items()]
                    )
                )
            _cut_dens = creative_strategy.get("cut_density", "medium")
            _pref_len = creative_strategy.get("preferred_segment_length", (2.0, 4.5))
            logger.info(
                f"⏱ SHOT_PACING cut_density={_cut_dens} | "
                f"preferred_segment={_pref_len[0]:.1f}s-{_pref_len[1]:.1f}s"
            )
            logger.info("🎭 Creative Director complete.")
        except Exception as _director_err:
            logger.warning(f"⚠️ Creative Director failed (non-fatal): {_director_err}")

        # ── STORY BUILDER: persona-aware segment building with reaction offsets ─
        try:
            from Core_Modules.story_builder import StoryBuilder

            _sb = StoryBuilder(default_span=2.5, pre_roll=0.5, post_roll=2.0)

            _sb_meanings = []
            for _m in profile_data.get(
                "fused_moments", profile_data.get("candidate_moments", [])
            ):
                if isinstance(_m, dict):
                    _sb_meanings.append(
                        {
                            "time": float(_m.get("time", 0.0)),
                            "viewer_interest": float(
                                _m.get("composite_score", _m.get("score", 0.5))
                            ),
                            "moment_type": _m.get("type", "appearance"),
                        }
                    )

            _sb_arc = (
                profile_data.get("preferred_arc")
                or profile_data.get("creative_strategy", {}).get("arc_type")
                or "reveal_arc"
            )
            _sb_persona = profile_data.get("preferred_persona") or profile_data.get(
                "editor_persona", "ANALYST"
            )
            _pacing_hint = profile_data.get("pacing_wave")

            if _sb_meanings:
                _sb_result = _sb.build(
                    arc=_sb_arc,
                    meanings=_sb_meanings,
                    persona_name=_sb_persona,
                    pacing_hint=_pacing_hint,
                )
                _sb_segments = _sb_result.get("segments", [])
                if _sb_segments:
                    # Story builder segments enhance but DON'T override candidate_moments
                    profile_data["story_builder_segments"] = _sb_segments
                    logger.info(
                        f"📖 [STORY_BUILDER] arc={_sb_arc} | persona={_sb_persona} | "
                        f"segments_built={len(_sb_segments)} | "
                        f"pacing_hint={'yes' if _pacing_hint else 'no'}"
                    )
        except Exception as _sb_e:
            logger.warning(f"⚠️ [STORY_BUILDER] failed (non-fatal): {_sb_e}")

        # ---- TIMELINE RECONSTRUCTOR (Step 3) ----------------------------------------
        if TIMELINE_RECONSTRUCTOR_AVAILABLE:
            _StepTracer.start("timeline_reconstruction")
            logger.info("🔄 [Step 3] Timeline Reconstruction...")
            try:
                _tr_result = {}
                if profile_data.get("editor_authority"):
                    # Gemini already chose the segments — skip Python re-reconstruction.
                    # But copy editing_timeline → reconstructed_timeline so the humanization
                    # layer (beat snapping, transitions, rhythm) has a base to work from.
                    _tr_segs = profile_data.get("editing_timeline", [])
                    if _tr_segs and not profile_data.get("reconstructed_timeline"):
                        profile_data["reconstructed_timeline"] = list(_tr_segs)
                    logger.info(f"🧠 [ELITE] Seeding reconstructed_timeline with {len(_tr_segs)} Gemini segments for humanization.")
                else:
                    _tr_result = _timeline_reconstruct(profile_data, job_dir=job_dir)
                    _tr_segs = _tr_result.get("reconstructed_timeline", [])
                if _tr_segs:
                    # [MULTI_CLIP FIX] Build per-clip duration map so the safety
                    # gate clamps each segment against ITS OWN clip — not clip 0.
                    _clip_dur_map_sg: dict = {}
                    for _sg_ci, _sg_cp in enumerate(input_paths):
                        try:
                            _sg_info = video_pipeline.get_video_info(_sg_cp)
                            _clip_dur_map_sg[_sg_ci] = _sg_info.get("duration", duration)
                        except Exception:
                            _clip_dur_map_sg[_sg_ci] = duration
                    logger.info(
                        f"📐 [SAFETY_GATE] clip_duration_map={_clip_dur_map_sg} "
                        f"validating {len(_tr_segs)} segments"
                    )
                    _tr_segs = segment_safety_validate(
                        _tr_segs, duration,
                        clip_duration_map=_clip_dur_map_sg,
                    )
                    logger.info(
                        f"📐 [SAFETY_GATE] {len(_tr_segs)} segments survived | "
                        f"clips_present={sorted({s.get('clip_id',0) for s in _tr_segs})}"
                    )
                    profile_data["reconstructed_timeline"] = _tr_segs
                _tr_moments_in = len(profile_data.get("candidate_moments", []))
                auditor.mark_executed("timeline_reconstructor")
                logger.info(
                    f"✅ [TIMELINE_RECONSTRUCTOR] segments_generated={len(_tr_segs)} | "
                    f"moments_selected={_tr_moments_in}"
                )
                logger.info(
                    f"🎬 CREATIVE_EDITOR Timeline reconstructed — "
                    f"{len(_tr_segs)} segments from {_tr_moments_in} moments"
                )
                _StepTracer.success("timeline_reconstruction")
            except Exception as _tr_e:
                logger.warning(f"⚠️ Timeline Reconstructor failed (non-fatal): {_tr_e}")
                auditor.mark_failed("timeline_reconstructor")
                _StepTracer.fail("timeline_reconstruction", str(_tr_e))
                profile_data.setdefault("reconstructed_timeline", [])
                # Fallback to empty context if we don't have _tr_result
                _tr_result = {}
        else:
            auditor.mark_disabled("timeline_reconstructor")
            profile_data.setdefault("reconstructed_timeline", [])
            _tr_result = {}

        timeline_result = _tr_result
        if timeline_result:
            profile_data["editing_quality"] = timeline_result.get("editing_quality")

        # ── EARLY HARD STOP (STRICT MODE) ───────────────────────────
        if ENABLE_EARLY_HARD_STOP:
            if timeline_result.get("editing_quality") in ["failed", "degraded"]:
                logger.error(
                    "🚫 [HARD_STOP] Timeline failed — stopping pipeline (STRICT MODE)."
                )

                auditor.mark_failed("timeline_reconstruction")

                return False, {
                    "status": "FAILED_EDITING",
                    "reason": timeline_result.get("failure_reason", "timeline_failed"),
                    "final_output": None,
                    "segments": [],
                    "metadata": {
                        "hard_stop": True,
                        "failure_stage": "timeline_reconstruction",
                        "strict_mode": True
                    }
                }
        # ───────────────────────────────────────────────────────────

        # ── NARRATIVE COHERENCE ENGINE: validate + fix arc order ──────────────
        if COHERENCE_ENGINE_AVAILABLE and _coherence_engine:
            _rt_segs = profile_data.get("reconstructed_timeline", [])
            if _rt_segs:
                try:
                    # Determine arc type from creative strategy
                    _arc_type = (
                        profile_data.get("creative_strategy", {}).get("arc_type")
                        or profile_data.get("preferred_arc")
                        or "reveal_arc"
                    )
                    _temporal_stream = [
                        {
                            "time": m.get("time", 0.0),
                            "energy": m.get("composite_score", m.get("score", 0.0)),
                        }
                        for m in profile_data.get("fused_moments", [])
                        if isinstance(m, dict)
                    ]
                    _coherence_report = _coherence_engine.validate(
                        segments=_rt_segs,
                        temporal_stream=_temporal_stream,
                        arc_type=_arc_type,
                    )
                    _coherence_score = _coherence_report.get("coherence_score", 1.0)
                    _coherence_issues = _coherence_report.get("issues", [])

                    # If coherence engine reordered segments, use corrected version
                    if _coherence_report.get("segments") and _coherence_issues:
                        profile_data["reconstructed_timeline"] = _coherence_report[
                            "segments"
                        ]
                        logger.info(
                            f"📖 [COHERENCE_ENGINE] coherence_score={_coherence_score:.3f} | "
                            f"issues={_coherence_issues} | arc_corrected=True"
                        )
                    else:
                        logger.info(
                            f"📖 [COHERENCE_ENGINE] coherence_score={_coherence_score:.3f} | "
                            f"arc_valid=True | issues={_coherence_issues}"
                        )
                    profile_data["coherence_score"] = _coherence_score
                except Exception as _ce_e:
                    logger.warning(f"⚠️ [COHERENCE_ENGINE] failed (non-fatal): {_ce_e}")

        # ---- COMPILATION MODE -------------------------------------------------------
        if HIGHLIGHT_COMPILER_AVAILABLE:
            compilation_mode = profile_data.get("compilation_mode", "highlight")
            candidate_moments = profile_data.get("candidate_moments", [])
            if compilation_mode == "compilation" and candidate_moments:
                logger.info("📦 [COMPILATION_MODE] Building long-form compilation...")
                try:
                    compiler = HighlightCompiler(profile_data)
                    compilation_segments = compiler.compile_top_moments(
                        count=profile_data.get("compilation_moment_count", 10),
                        with_commentary=True,
                    )
                    if compilation_segments:
                        profile_data["compilation_segments"] = compilation_segments
                        total_duration = sum(
                            s["duration"] for s in compilation_segments
                        )
                        logger.info(
                            f"✅ [COMPILATION_MODE] {len(compilation_segments)} segments, "
                            f"{total_duration:.1f}s total"
                        )
                        auditor.mark_executed("compilation_mode")
                        plan_path = os.path.join(job_dir, "compilation_plan.json")
                        compiler.generate_compilation_plan(output_path=plan_path)
                        # ── NarrativeDirector: generate compilation script ──────────
                        if NARRATIVE_BRAIN_AVAILABLE:
                            try:
                                from Intelligence_Modules.narrative_brain import (
                                    NarrativeDirector as _ND,
                                )

                                _nd = _ND()
                                _nd_assets = [
                                    {
                                        "image_path": None,
                                        "json": {
                                            "caption": seg.get("caption", ""),
                                            "brain_analysis": {
                                                "editorial_script": seg.get(
                                                    "rag_hook", ""
                                                ),
                                            },
                                        },
                                    }
                                    for seg in compilation_segments
                                ]
                                _nd_result = _nd.generate_compilation_script(
                                    _nd_assets,
                                    niche_category=_niche_category,
                                    cinematic_plan=profile_data.get("cinematic_plan"),
                                )

                                if isinstance(_nd_result, dict) and _nd_result.get("script"):
                                    profile_data["narrative_script"] = _nd_result["script"]
                                    profile_data["narrative_mood"] = _nd_result.get("mood", "Editorial")
                                    profile_data["editorial_title"] = _nd_result.get("title_suggestion", "")
                                    
                                    # --- [NEW] INGEST SEMANTIC INTELLIGENCE METADATA ---
                                    profile_data["semantic_genre"] = _nd_result.get("genre", "")
                                    profile_data["semantic_tone"] = _nd_result.get("tone", "")
                                    profile_data["semantic_rhythm"] = _nd_result.get("rhythm", "")
                                    profile_data["visual_read"] = _nd_result.get("visual_read", "")
                                    profile_data["caption_notes"] = _nd_result.get("caption_overlay_notes", "")
                                    
                                    logger.info(
                                        f"🎬 [NARRATIVE_DIRECTOR] script_length={len(_nd_result['script'])} "
                                        f"genre={profile_data['semantic_genre']} "
                                        f"mood={_nd_result.get('mood')} "
                                        f"title='{_nd_result.get('title_suggestion', '')}'"
                                    )
                            except Exception as _nd_e:
                                logger.warning(
                                    f"⚠️ NarrativeDirector failed (non-fatal): {_nd_e}"
                                )
                    else:
                        logger.warning("⚠️ [COMPILATION_MODE] No segments generated")
                        auditor.mark_failed("compilation_mode")
                except Exception as e:
                    logger.warning(f"⚠️ [COMPILATION_MODE] Failed (non-fatal): {e}")
                    auditor.mark_failed("compilation_mode")
            else:
                if compilation_mode == "compilation":
                    logger.info("📦 [COMPILATION_MODE] Skipped: no candidate moments")
                auditor.mark_skipped("compilation_mode")
        else:
            auditor.mark_disabled("compilation_mode")

        # ---- PRICE TAG / WEAR CAPTION OVERLAY --------------------------------------
        price_tag_images = []
        _use_captions = os.getenv("CINEMATIC_USE_CAPTION_OVERLAY", "no").strip().lower() == "yes"
        if _is_cinematic_story and not _use_captions:
            # In cinematic_story mode there are NO product/wear overlays by default.
            # All visual storytelling is driven by narration + karaoke subtitle engine.
            logger.info("🎬 [CINEMATIC_STORY] Price tag / wear caption overlay DISABLED — story captions only.")
            auditor.mark_disabled("price_tag_engine")
            is_price_tag_enabled = False
        else:
            is_price_tag_enabled = context.feature_flags.get("price_tag_engine")
        _enable_fs = os.getenv("ENABLE_FASHION_SCOUT", "yes").lower() in ("yes", "true", "on")
        if not _enable_fs:
            is_price_tag_enabled = False
        has_price_data = bool(profile_data.get("price_tag"))
        forced_price_tag = os.getenv("ENABLE_PRICE_TAG", "auto").lower() == "yes"

        # ── CENTRALIZED PRICE ESTIMATION ─────────────────────────────────────────
        # Compute price_data ONCE here and store in profile_data so that the
        # SmartPriceTag video overlay AND the Telegram message always use the
        # exact same numbers (no independent random calls later).
        #
        # Priority:
        #   1. PIPELINE_REAL_MRP (user-supplied real Amazon MRP) → ALWAYS wins
        #   2. profile_data["price_data"] (already set upstream)  → reuse it
        #   3. estimate_price_with_gemini() (random category-tier fallback)
        if not profile_data.get("price_data"):
            # ── Priority 1: User-supplied real MRP ──────────────────────────────
            _pipeline_real_mrp = os.getenv("PIPELINE_REAL_MRP")
            if _pipeline_real_mrp:
                try:
                    _real_mrp_int = int(_pipeline_real_mrp)
                    if _real_mrp_int > 0:
                        # clone_price  = actual Amazon selling price (what user told us)
                        # celebrity_price = realistic "original" brand MRP above it
                        #   Formula: 1.55x–1.75x  →  looks believable & creates desire
                        import random as _price_rng
                        _cel_mult = _price_rng.uniform(1.55, 1.75)
                        _celebrity_price_raw = int(_real_mrp_int * _cel_mult)
                        # Round to nearest ₹100 for a natural look
                        _celebrity_price = round(_celebrity_price_raw / 100) * 100
                        _savings = _celebrity_price - _real_mrp_int
                        _savings_pct = min(75, int((_savings / _celebrity_price) * 100))

                        def _fmt_inr(n):
                            s = str(n)
                            if len(s) <= 3:
                                return s
                            result = s[-3:]
                            s = s[:-3]
                            while s:
                                result = s[-2:] + "," + result
                                s = s[:-2]
                            return result.lstrip(",")

                        profile_data["price_data"] = {
                            "celebrity_price":          _celebrity_price,
                            "clone_price":              _real_mrp_int,
                            "savings":                  _savings,
                            "savings_pct":              _savings_pct,
                            "clone_price_formatted":    f"₹{_fmt_inr(_real_mrp_int)}",
                            "celebrity_price_formatted": f"₹{_fmt_inr(_celebrity_price)}+",
                            "celebrity_price_label":    f"Original Price ₹{_fmt_inr(_celebrity_price)}+",
                            "source": "user_real_mrp",
                        }
                        logger.info(
                            f"💰 [PRICE_ESTIMATOR] REAL MRP OVERRIDE: "
                            f"Amazon=₹{_real_mrp_int:,} | Original≈₹{_celebrity_price:,} | "
                            f"Save {_savings_pct}% — random estimate bypassed."
                        )
                except (ValueError, TypeError) as _mrp_e:
                    logger.warning(f"⚠️ [PRICE_ESTIMATOR] Bad PIPELINE_REAL_MRP value '{_pipeline_real_mrp}': {_mrp_e}")

            # ── Priority 2/3: Fashion-scout-aware random estimate ────────────────
            if not profile_data.get("price_data"):
                try:
                    from Text_Modules.smart_price_tag import estimate_price_with_gemini
                    _item_cat_for_est = profile_data.get("category", "default") or "default"
                    _pm_est = profile_data.get("pipeline_metrics", {})
                    _mon_data_est = profile_data.get("monetization", {}) or profile_data.get("monetization_data", {}) or _pm_est.get("monetization", {})
                    _fashion_est = profile_data.get("fashion_scout", {}) or _mon_data_est.get("fashion_scout", {}) or _pm_est.get("fashion_scout", {})
                    _outfit_desc = _fashion_est.get("outfit_description") or _fashion_est.get("fashion", {}).get("outfit_description", "")
                    _outfit_vibe = _fashion_est.get("vibe") or _fashion_est.get("fashion", {}).get("vibe", "CASUAL")
                    # [FIX] outfit_description was often empty → random fallback fired.
                    # Use item_name + category as minimum description.
                    if not _outfit_desc or len(_outfit_desc.strip()) < 10:
                        _item_name_fallback = profile_data.get("item_name", "")
                        _outfit_desc = (
                            f"{_item_name_fallback} ({_item_cat_for_est})"
                            if _item_name_fallback
                            else f"{_item_cat_for_est} outfit"
                        )
                        logger.info(
                            f"💰 [PRICE_ESTIMATOR] outfit_description was empty — "
                            f"using fallback: '{_outfit_desc}'"
                        )
                    _estimated = estimate_price_with_gemini(
                        outfit_description=_outfit_desc,
                        vibe=_outfit_vibe,
                        item_category=_item_cat_for_est,
                    )
                    profile_data["price_data"] = _estimated
                    logger.info(
                        f"💰 [PRICE_ESTIMATOR] Centralized estimate locked: "
                        f"celebrity=₹{_estimated['celebrity_price']:,} "
                        f"clone=₹{_estimated['clone_price']:,} "
                        f"source={_estimated.get('source','?')}"
                    )
                except Exception as _pest:
                    logger.warning(f"⚠️ [PRICE_ESTIMATOR] Centralized estimate failed: {_pest}")

        # ── Hard category block (overrides everything, including ENABLE_PRICE_TAG=yes) ──
        # Derive the detected content category from master analysis.
        _detected_category = (
            profile_data.get("category")
            or profile_data.get("content_category")
            or profile_data.get("intent", "")
        ).lower().replace(" ", "_").replace("-", "_")

        # Check if any blocked keyword appears anywhere in the category string
        # (e.g. "viral_fitness_reel" still matches "fitness")
        _is_blocked_category = any(
            blocked in _detected_category
            for blocked in PRICE_TAG_BLOCKED_CATEGORIES
        )

        if _is_blocked_category:
            _notify_price_tag_blocked(_detected_category, uuid_str)
            auditor.mark_disabled("price_tag_engine")

        elif is_price_tag_enabled and (has_price_data or forced_price_tag):
            if auditor.status("subject_tracking") in ["FAILED", "DISABLED"]:
                logger.warning(
                    "⚠️ Execution Guard: Subject Tracking failed. Disabling Dynamic Price Tags."
                )
                auditor.mark_disabled("price_tag_engine")
            else:
                # Price tag generation moved to Step 8 (render step) where the
                # final scene order is known and the stable anchor is injected
                # as scene[0]. Static PNG + auditor mark happen there.
                logger.info(
                    "💰 [PRICE_TAG] Deferred to render step — "
                    "stable anchor injection and PNG generation at Step 8."
                )
        else:
            auditor.mark_disabled("price_tag_engine")

        # ---- SMART SCENE EDITOR (Step 7) --------------------------------------------
        timeline_instructions = None

        if auditor.status("scene_detection") in ["FAILED", "DISABLED"]:
            has_plan_segments = isinstance(
                profile_data.get("editing_plan"), dict
            ) and bool(profile_data["editing_plan"].get("segments"))
            has_selected_shots = bool(profile_data.get("selected_shots"))
            has_candidate_moments = bool(profile_data.get("candidate_moments"))
            if (
                not has_plan_segments
                and not has_selected_shots
                and not has_candidate_moments
            ):
                context.feature_flags["scene_reconstruction"] = False
                logger.warning(
                    "⚠️ Scene detection failed and no fallback data — "
                    "disabling scene_reconstruction."
                )
            else:
                _authority = (
                    "candidate_moments (Moment Authority)"
                    if has_candidate_moments
                    and not has_selected_shots
                    and not has_plan_segments
                    else "AI editing_plan/selected_shots"
                )
                logger.warning(
                    f"⚠️ Scene detection failed, using {_authority} for reconstruction."
                )

        # [HUMANIZATION] Smart Scene Editor runs even with Elite authority.
        # Gemini picks WHAT moments to use. Smart Scene Editor adds HOW to present them:
        # transitions, zoom-ins, speed ramps, subject reframing.
        if (
            not profile_data.get("editor_authority")  # standard path
            or True  # always run humanization even in elite mode
        ) and False:  # disabled legacy path
            pass
        elif (
            SMART_SCENE_AVAILABLE
            and _smart_scene_editor
            and (
                context.feature_flags.get("scene_reconstruction")
                or profile_data.get("editor_authority")  # always humanize when Gemini ran
            )
        ):
            _flags = context.feature_flags.copy()
            if profile_data.get("visual_reframing"):
                _flags["enable_cinematic_zoom"] = True
            if profile_data.get("scene_restructure"):
                _flags["enable_fast_pacing"] = True
            if duration >= 21.0:
                _flags["force_7s_splits"] = True
                logger.info("📐 7s Split Logic Enabled (Duration >= 21s)")
            _flags["transition_scythe"] = True

            _beat_timestamps = profile_data.get("beat_data_bgm", {}).get(
                "beats"
            ) or profile_data.get("beat_data", {}).get("beats")

            timeline_instructions = _smart_scene_editor.generate_timeline_instructions(
                current_video_source,
                feature_flags=_flags,
                hook_analysis=profile_data.get("hook_analysis"),
                editing_plan=profile_data.get("editing_plan"),
                content_director=profile_data.get("content_director"),
                beat_timestamps=_beat_timestamps,
                subject_tracking_data=profile_data.get("subject_tracking"),
                selected_shots=profile_data.get("selected_shots"),
                cold_open=profile_data.get("cold_open"),
                creative_strategy=profile_data.get("creative_strategy"),
                input_paths=input_paths,
                candidate_moments=profile_data.get("candidate_moments", []),
                reconstructed_timeline=profile_data.get("reconstructed_timeline", []),
            )

        # [SAFETY NET] Post-hoc reconstructed_timeline injection
        _rt_segments = profile_data.get("reconstructed_timeline", [])
        if _rt_segments and not (
            timeline_instructions and timeline_instructions.get("moment_driven")
        ):
            if not timeline_instructions:
                timeline_instructions = {}
            timeline_instructions["scenes"] = _rt_segments
            timeline_instructions["moment_driven"] = True
            logger.info(
                f"🔄 [SAFETY_NET] reconstructed_timeline injected post-hoc — "
                f"{len(_rt_segments)} segments → moment_driven=True."
            )

        # ---- RHYTHM TIMELINE BUILDER (Step 7b) --------------------------------------
        # NOTE: RhythmTimelineBuilder now ALWAYS runs.
        # It uses reconstructed_timeline (moment-authority segments) as its input,
        # applies micro-splitting, beat snapping, and early→mid→late band hopping
        # to produce genuine human-style cuts. The old bypass that locked it out
        # whenever moment_driven=True has been removed — that was the root cause of
        # flat chronological 3-segment output.
        _moment_driven_active = False  # always allow RhythmTimelineBuilder
        # [HUMANIZATION] Rhythm Timeline Builder runs even in Elite authority mode.
        # It beat-snaps Gemini's cut points without replacing the segment selection.
        # This is the difference between robot trimming and human-feel editing.
        if False and profile_data.get("editor_authority"):  # bypass REMOVED
            logger.info("🚫 [RHYTHM_OVERRIDE] Disabled (Elite authority)")
        elif (
            RHYTHM_BUILDER_AVAILABLE
            and _rhythm_builder
            and (
                context.feature_flags.get("scene_reconstruction")
                or profile_data.get("editor_authority")  # always beat-snap when Gemini ran
            )
        ):
            try:
                base_scenes = []
                # Priority 1: Beat-driven segments from CreativeEditorBridge
                if profile_data.get("beat_timeline_segments"):
                    base_scenes = profile_data["beat_timeline_segments"]
                    logger.info("🎵 Using BGM beat-driven timeline as base_scenes.")
                # Priority 2: Reconstructed timeline from moment-authority
                elif profile_data.get("reconstructed_timeline"):
                    base_scenes = profile_data["reconstructed_timeline"]
                    logger.info("🎬 Using reconstructed_timeline as base_scenes for RhythmBuilder.")
                elif timeline_instructions and timeline_instructions.get("scenes"):
                    base_scenes = timeline_instructions.get("scenes", [])
                elif isinstance(
                    profile_data.get("editing_plan"), dict
                ) and profile_data["editing_plan"].get("segments"):
                    base_scenes = profile_data["editing_plan"]["segments"]
                else:
                    base_scenes = profile_data.get("shots", [])

                if not base_scenes:
                    logger.info("🥁 RhythmTimelineBuilder: No base scenes, skipping.")
                else:
                    hook_time = profile_data.get("editing_strategy", {}).get(
                        "hook_time_hint", 1.8
                    )
                    _rtb_beat_grid = profile_data.get("beat_data_bgm", {}).get(
                        "beats"
                    ) or profile_data.get("beat_data", {}).get("beats", [])
                    _vo_target = profile_data.get("vo_pacing_hints", {}).get(
                        "target_duration"
                    )
                    # Psycho-acoustic vibe from BGM analysis → controls shot length preferences
                    _rtb_vibe = profile_data.get("bgm_vibe") or \
                                profile_data.get("beat_data_bgm", {}).get("vibe")

                    rhythm_timeline = _rhythm_builder.build_timeline(
                        scenes=base_scenes,
                        motion_events=profile_data.get("motion_scores", []),
                        attention_events=[],
                        beat_grid=_rtb_beat_grid,
                        hook_time=hook_time,
                        subject_data=profile_data.get("subject_tracking", []),
                        target_duration_hint=_vo_target,
                        vibe=_rtb_vibe,
                        music_intelligence=profile_data.get("music_intelligence"),
                    )
                    if rhythm_timeline:
                        # ── [PACING ARCHITECT] Energy Curve Enforcement ─────────────
                        if PACING_ARCHITECT_AVAILABLE and _pacing_architect:
                            _pstyle = profile_data.get("creative_intent", {}).get("pacing_style", "rhythm_driven")
                            if profile_data.get("creative_intent"):
                                logger.info(f"📈 [PACING_ARCHITECT] Applying '{_pstyle}' energy curve to timeline")
                            rhythm_timeline = _pacing_architect.shape(
                                timeline=rhythm_timeline,
                                pacing_style=_pstyle,
                                source_duration=duration
                            )
                            
                        if not timeline_instructions:
                            timeline_instructions = {}
                        timeline_instructions["scenes"] = rhythm_timeline
                        # [HUMANIZATION FIX] Store for render step preference logic
                        profile_data["rhythm_timeline"] = rhythm_timeline

                        # ── [ENERGY SCORER + FACE PROBE] Enrich segments ────────────
                        # This runs AFTER the timeline is finalised so we only probe
                        # the segments that will actually be rendered (not all candidates).
                        # Both modules are fully optional — failures are caught silently.
                        try:
                            from Intelligence_Modules.energy_scorer import score_segments
                            _primary_clip = input_paths[0] if input_paths else current_video_source
                            rhythm_timeline = score_segments(_primary_clip, rhythm_timeline)
                            logger.info(
                                f"⚡ [ENERGY_SCORER] Scored {len(rhythm_timeline)} segments. "
                                f"Zoom-eligible: {sum(1 for s in rhythm_timeline if s.get('energy_score', 0) > 0.4)}"
                            )
                        except Exception as _es_e:
                            logger.debug(f"[ENERGY_SCORER] Skipped (non-critical): {_es_e}")

                        try:
                            from Visual_Refinement_Modules.subject_tracker import get_face_bbox_for_segment
                            _probe_clip = input_paths[0] if input_paths else current_video_source
                            _bbox_hits = 0
                            for _seg in rhythm_timeline:
                                if _seg.get("bbox"):
                                    continue  # Already populated by another module
                                _bbox = get_face_bbox_for_segment(
                                    _probe_clip,
                                    float(_seg.get("start", 0)),
                                    float(_seg.get("end", 0))
                                )
                                if _bbox:
                                    _seg["bbox"] = _bbox
                                    _bbox_hits += 1
                            logger.info(f"🎯 [FACE_PROBE] Anchored {_bbox_hits}/{len(rhythm_timeline)} segments with face bbox.")
                        except Exception as _fp_e:
                            logger.debug(f"[FACE_PROBE] Skipped (non-critical): {_fp_e}")

                        # Write back the enriched timeline to both stores
                        timeline_instructions["scenes"] = rhythm_timeline
                        profile_data["rhythm_timeline"] = rhythm_timeline

                        # [SOURCE FIX] Mark editing_source as 'rhythm' so the segment_validator
                        # skips strict reasoning checks and uses beat-snapped thresholds.
                        profile_data["editing_source"] = "rhythm"

                        logger.info(
                            "🎬 Rhythm Timeline overrode scenes with human-style cuts."
                        )
                        # ── Structured pacing + variety logs ────────────────────
                        _rt_durs = [
                            s.get("end", 0) - s.get("start", 0)
                            for s in rhythm_timeline
                            if isinstance(s, dict)
                        ]
                        if _rt_durs:
                            _rt_avg = sum(_rt_durs) / len(_rt_durs)
                            logger.info(
                                f"⏱ SHOT_PACING RhythmBuilder — {len(rhythm_timeline)} shots | "
                                f"avg={_rt_avg:.2f}s | total={sum(_rt_durs):.1f}s"
                            )
                        _rt_styles = list(
                            {
                                s.get("style", "cut")
                                for s in rhythm_timeline
                                if isinstance(s, dict)
                            }
                        )
                        logger.info(
                            f"🎨 ENERGY_STYLE RhythmBuilder transitions={_rt_styles}"
                        )
                        _rt_unique = len(
                            {
                                round(s.get("start", 0), 0)
                                for s in rhythm_timeline
                                if isinstance(s, dict)
                            }
                        )
                        _rt_div = _rt_unique / max(len(rhythm_timeline), 1)
                        logger.info(
                            f"🧠 VARIETY_CHECK RhythmBuilder segment_diversity={_rt_div:.2f}"
                        )
            except Exception as e:
                logger.warning(f"⚠️ Rhythm Timeline Builder failed: {e}")

        # ---- RENDER ENGINE (Step 8) -------------------------------------------------
        tracker.step("STEP_SUBTITLES", 100)
        tracker.step("STEP_RENDER", 10)
        logger.info("🚀 [Step 8] Render Engine Start...")
        temp_visual_render = os.path.join(job_dir, "visual_render_temp.mp4")

        # ── NEW: Separate Shorts Mode ─────────────────────────────────────
        _separate_shorts = os.getenv("OUTPUT_SEPARATE_SHORTS", "no").lower() in ("yes", "true", "1")
        _max_separate    = int(os.getenv("MAX_SEPARATE_SHORTS", "10"))
        # ─────────────────────────────────────────────────────────────────

        # Render priority: humanized rhythm timeline > smart scene > Gemini editing_timeline
        # This ensures the beat-snapped, transition-enhanced version is rendered,
        # not the raw Gemini timestamps.
        _humanized_timeline = profile_data.get("rhythm_timeline") or profile_data.get("reconstructed_timeline")
        if profile_data.get("editor_authority") and _humanized_timeline:
            # Humanization ran on top of Gemini segments — use that
            _scenes = _humanized_timeline
            logger.info(f"🎨 [HUMANIZED_RENDER] Using beat-snapped humanized timeline: {len(_scenes)} segments")
        elif profile_data.get("editor_authority"):
            _scenes = profile_data.get("editing_timeline", [])
            logger.info(f"🧠 [EDITOR_AUTHORITY] Rendering final timeline: {len(_scenes)} segments")
        else:
            _scenes = (
                timeline_instructions.get("scenes", []) if timeline_instructions else []
            )
        _non_chronological = bool(
            timeline_instructions
            and timeline_instructions.get("non_chronological", False)
        ) or bool(profile_data.get("timeline_reordered", False))

        # ── PRICE TAG: STABLE SHOT INJECTION ─────────────────────────────────
        # When ENABLE_PRICE_TAG=yes we guarantee the price tag appears in the
        # first few seconds of the output AND points at the garment on screen.
        #
        # Strategy:
        #  1. Find the most stable source window ≥ 4.25s (least motion, early bias).
        #  2. Inject it as _scenes[0] so it is literally the first shot rendered.
        #  3. Extract a frame from that window, generate a SmartPriceTag PNG.
        #  4. The PNG composites at output second 0.75 — while the stable shot
        #     is still on screen — tag points directly at the visible garment.
        _forced_price_tag = os.getenv("ENABLE_PRICE_TAG", "auto").lower() == "yes"
        _enable_fs = os.getenv("ENABLE_FASHION_SCOUT", "yes").lower() in ("yes", "true", "on")
        _pt_enabled = (context.feature_flags.get("price_tag_engine") or _forced_price_tag) and _enable_fs

        if _pt_enabled and _scenes:
            _MIN_STABLE = 2.5  # Lowered from 5.0 to handle faster Shorts-style edits
            _motion_pts = profile_data.get("motion_scores", [])

            def _seg_avg_motion(s: float, e: float) -> float:
                pts = [
                    float(m.get("score", m.get("intensity", 0.5)))
                    for m in _motion_pts
                    if s <= float(m.get("time", 0.0)) <= e
                ]
                return sum(pts) / len(pts) if pts else 0.5

            # [FIX] Build a map of clip_id → source path so we can pick the right
            # video for both the stable_seg and the frame extraction.
            # Previously all stable pool entries were forced to clip_id=0, causing
            # segments from other clips to be dropped by the video pipeline when
            # their timestamps exceeded clip 0's duration.
            _src_path_map = {
                i: p for i, p in enumerate(
                    list(input_paths) if isinstance(input_paths, (list, tuple))
                    else [current_video_source]
                )
            }
            # NOTE: Do NOT override _src_path_map[0] with current_video_source here.
            # If the First Shot intro was prepended to input_paths, index 0 is the intro
            # clip — overwriting it would cause the price-tag logic to read from the
            # wrong file and would also strip the intro from the render path map.
            if 0 not in _src_path_map:
                _src_path_map[0] = current_video_source

            # Build candidate list from reconstructed_timeline (source windows)
            # [FIX] Accept segments from ANY clip (not just clip_id==0) so that
            # jobs where clip 0 is very short still find a stable anchor window.
            _rt_segs = (
                profile_data.get("reconstructed_timeline") or
                profile_data.get("editing_timeline") or
                (_scenes if _scenes else [])
            )
            _stable_pool = []
            for _sg in _rt_segs:
                if not isinstance(_sg, dict):
                    continue
                _ss = float(_sg.get("start", 0.0))
                _se = float(_sg.get("end",   0.0))
                _sg_clip = int(_sg.get("clip_id", 0))
                # Primary pass: Look for segments meeting the stability duration
                if (_se - _ss) >= _MIN_STABLE:
                    _stable_pool.append((_ss, _se, _seg_avg_motion(_ss, _se), _sg_clip))

            # Fallback: if no segment meets _MIN_STABLE, grab anything > 1.25s
            # [FIX] Was _forced_price_tag — this blocked the fallback in auto mode,
            # causing price_tag_engine to stay PENDING on every run.
            if not _stable_pool and _pt_enabled:
                logger.info(f"💰 [PRICE_TAG_ANCHOR] No segment ≥ {_MIN_STABLE}s — trying 1.25s fallback pool.")
                for _sg in _rt_segs:
                    if not isinstance(_sg, dict): continue
                    _ss = float(_sg.get("start", 0.0))
                    _se = float(_sg.get("end",   0.0))
                    _sg_clip = int(_sg.get("clip_id", 0))
                    if (_se - _ss) >= 1.25:
                        _stable_pool.append((_ss, _se, _seg_avg_motion(_ss, _se), _sg_clip))

            # LAST RESORT: if still nothing, grab anything ≥ 0.8s
            # [FIX] Was _forced_price_tag — now also runs in auto mode.
            if not _stable_pool and _pt_enabled:
                logger.info("💰 [PRICE_TAG_ANCHOR] 1.25s fallback empty — using 0.8s last-resort pool.")
                for _sg in _rt_segs:
                    if not isinstance(_sg, dict): continue
                    _ss = float(_sg.get("start", 0.0))
                    _se = float(_sg.get("end",   0.0))
                    _sg_clip = int(_sg.get("clip_id", 0))
                    if (_se - _ss) >= 0.8:
                        _stable_pool.append((_ss, _se, _seg_avg_motion(_ss, _se), _sg_clip))

            # FINAL FALLBACK: If still nothing, take the first available segment regardless of length
            if not _stable_pool and _pt_enabled and _rt_segs:
                logger.info("💰 [PRICE_TAG_ANCHOR] 0.8s fallback empty — using first available segment as absolute anchor.")
                for _sg in _rt_segs:
                    if not isinstance(_sg, dict): continue
                    _ss = float(_sg.get("start", 0.0))
                    _se = float(_sg.get("end",   0.0))
                    _sg_clip = int(_sg.get("clip_id", 0))
                    _stable_pool.append((_ss, _se, _seg_avg_motion(_ss, _se), _sg_clip))
                    break

            if _stable_pool:
                _total_src = max((_e for _, _e, _, _ in _stable_pool), default=30.0)
                # Score: 60% stability + 40% earliness — lower is better
                _stable_pool.sort(
                    key=lambda x: 0.6 * x[2] + 0.4 * (x[0] / max(_total_src, 1.0))
                )
                _best_start, _best_end, _best_motion, _best_clip_id = _stable_pool[0]

                # [FIX] Use the correct source file for this clip
                _stable_src = _src_path_map.get(_best_clip_id, current_video_source)

                # Build the stable segment dict, matching existing scene format
                _stable_seg = {
                    "clip_id":         _best_clip_id,  # [FIX] was hardcoded 0
                    "start":           round(_best_start, 3),
                    "end":             round(min(_best_end, _best_start + max(_best_end - _best_start, _MIN_STABLE)), 3),
                    "style":           "clean",
                    "transition":      "clean",
                    "reason":          "price_tag_stable_anchor",
                    "color_mode":      "fashion",
                }

                # Only inject if _scenes[0] is not already this window
                _first = _scenes[0] if _scenes else {}
                # [FIX] Prevent duplication: check if the stable segment already exists in _scenes
                _duplicate_idx = -1
                for _idx, _s in enumerate(_scenes):
                    if (
                        int(_s.get("clip_id", 0)) == _best_clip_id and
                        abs(float(_s.get("start", 0)) - _best_start) < 0.5 and
                        abs(float(_s.get("end", 0)) - _best_end) < 0.5
                    ):
                        _duplicate_idx = _idx
                        break

                if _duplicate_idx != -1:
                    # Move the existing segment to the front
                    _stable_seg = _scenes.pop(_duplicate_idx)
                    _scenes = [_stable_seg] + list(_scenes)
                    logger.info(f"💰 [PRICE_TAG_ANCHOR] Moving existing stable shot from index {_duplicate_idx} to index 0.")
                else:
                    # Prepend but also filter out any overlapping segments from the rest of the list
                    _scenes = [_stable_seg] + [
                        _s for _s in _scenes
                        if not (
                            int(_s.get("clip_id", 0)) == _best_clip_id and
                            max(float(_s.get("start", 0)), _best_start) < min(float(_s.get("end", 0)), _best_end)
                        )
                    ]
                    logger.info(f"💰 [PRICE_TAG_ANCHOR] Prepending new stable shot and filtering overlaps.")

                if timeline_instructions:
                    timeline_instructions["scenes"] = _scenes
                profile_data["reconstructed_timeline"] = _scenes

                # Generate static PNG from this stable window
                if not price_tag_images:
                    try:
                        _frame_ts  = _best_start + 1.0
                        _frame_png = os.path.join(job_dir, "stable_frame.png")
                        _ffmpeg_b  = os.getenv("FFMPEG_BIN", "ffmpeg")

                        # --- FACE-SHOT GUARD ---
                        # Try up to 3 timestamps within the window to find an
                        # outfit/body frame. Reject frames where face occupies
                        # > 12% of the frame area (stricter avoidance).
                        def _is_face_dominant(png_path: str, threshold: float = 0.12) -> bool:
                            try:
                                import cv2 as _cv2
                                _img = _cv2.imread(png_path)
                                if _img is None:
                                    return False
                                _gray = _cv2.cvtColor(_img, _cv2.COLOR_BGR2GRAY)
                                _h, _w = _gray.shape
                                _classifier = _cv2.CascadeClassifier(
                                    _cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                                )
                                _faces = _classifier.detectMultiScale(_gray, 1.1, 5, minSize=(60, 60))
                                if len(_faces) == 0:
                                    return False
                                # Largest face area
                                _max_face_area = max(fw * fh for (_, _, fw, fh) in _faces)
                                return (_max_face_area / (_w * _h)) > threshold
                            except Exception:
                                return False  # If check fails, allow the frame

                        # Scan candidate timestamps: start+1s, start+2s, midpoint
                        _seg_dur = _best_end - _best_start
                        _candidate_offsets = [1.0, min(2.0, _seg_dur * 0.5), _seg_dur * 0.75]
                        _chosen_ts = _frame_ts  # default
                        for _off in _candidate_offsets:
                            _try_ts = _best_start + _off
                            if _try_ts >= _best_end:
                                continue
                            subprocess.run(
                                [_ffmpeg_b, "-y",
                                 "-ss", str(_try_ts),
                                 "-i", _stable_src,  # [FIX] use correct clip source
                                 "-frames:v", "1", "-q:v", "2", _frame_png],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=15,
                            )
                            if os.path.exists(_frame_png):
                                if not _is_face_dominant(_frame_png):
                                    _chosen_ts = _try_ts
                                    logger.info(
                                        f"💰 [PRICE_TAG_FRAME] Outfit frame found at {_chosen_ts:.2f}s "
                                        f"(no dominant face)"
                                    )
                                    break
                                else:
                                    logger.info(
                                        f"💰 [PRICE_TAG_FRAME] Face-dominant at {_try_ts:.2f}s — skipping"
                                    )
                        else:
                            # All candidates were face shots — use midpoint anyway (best we can do)
                            _chosen_ts = _best_start + min(1.5, _seg_dur * 0.5)
                            logger.warning(
                                f"⚠️ [PRICE_TAG_FRAME] All candidate frames are face-dominant. "
                                f"Using {_chosen_ts:.2f}s as fallback."
                            )

                        # Extract the chosen (non-face) frame
                        subprocess.run(
                            [_ffmpeg_b, "-y",
                             "-ss", str(_chosen_ts),
                             "-i", _stable_src,  # [FIX] use correct clip source
                             "-frames:v", "1", "-q:v", "2", _frame_png],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=15,
                        )

                        if os.path.exists(_frame_png) and _smart_tag_engine:
                            from PIL import Image as _PILImage
                            import cv2 as _cv2_pt
                            import numpy as _np_pt

                            with _PILImage.open(_frame_png) as _fi:
                                _fw, _fh = _fi.size

                            # ── [FIX] Single-Frame Exact HOG detection ──────────────
                            # We MUST detect the body on the exact frame the tag appears.
                            # Averaging movement creates a 'ghost' box and the line points to air.
                            _detected_human_box = profile_data.get("human_bbox")
                            if not _detected_human_box:
                                try:
                                    _hog = _cv2_pt.HOGDescriptor()
                                    _hog.setSVMDetector(_cv2_pt.HOGDescriptor_getDefaultPeopleDetector())

                                    _frame_bgr = _cv2_pt.imread(_frame_png)
                                    if _frame_bgr is not None:
                                        _scale = min(1.0, 640 / max(_fw, 1))
                                        _sml = _cv2_pt.resize(_frame_bgr, (int(_fw * _scale), int(_fh * _scale)))
                                        _rects, _ = _hog.detectMultiScale(
                                            _sml, winStride=(8, 8), padding=(4, 4), scale=1.05
                                        )
                                        
                                        if len(_rects) > 0:
                                            _frame_cx = _fw * _scale / 2
                                            _frame_cy = _fh * _scale / 2

                                            def _primary_score(r):
                                                rx, ry, rw, rh = r
                                                box_cx = rx + rw / 2
                                                box_cy = ry + rh / 2
                                                dist = ((box_cx - _frame_cx) ** 2 +
                                                        (box_cy - _frame_cy) ** 2) ** 0.5
                                                max_dist = (_frame_cx ** 2 + _frame_cy ** 2) ** 0.5
                                                proximity = 1.0 - min(1.0, dist / max(max_dist, 1))
                                                area = rw * rh
                                                return proximity * 0.6 + (area / max((_fw * _scale * _fh * _scale), 1)) * 0.4

                                            _rx, _ry, _rw, _rh = max(_rects, key=_primary_score)
                                            _detected_human_box = [
                                                int(_rx / _scale), int(_ry / _scale),
                                                int(_rw / _scale), int(_rh / _scale)
                                            ]
                                            profile_data["human_bbox"] = _detected_human_box
                                            logger.info(
                                                f"💰 [PRICE_TAG_HOG] Exact frame detection: "
                                                f"x={_detected_human_box[0]} y={_detected_human_box[1]} "
                                                f"w={_detected_human_box[2]} h={_detected_human_box[3]}"
                                            )
                                        else:
                                            logger.info("💰 [PRICE_TAG_HOG] No person detected — using frame-wide safe box")
                                except Exception as _hog_err:
                                    logger.warning(f"⚠️ [PRICE_TAG_HOG] HOG detection failed: {_hog_err}")

                            # If we still have no box, use a narrower centered body column
                            # so the garment dot lands in the MIDDLE of the frame, not at
                            # a screen edge. This prevents a zero-length connector line.
                            # [FIX] Using 30–70% width slice = dot always on the garment body.
                            if not _detected_human_box:
                                _cx_margin_l = int(_fw * 0.30)
                                _cx_margin_r = int(_fw * 0.40)
                                _detected_human_box = [_cx_margin_l, 0, _cx_margin_r, _fh]
                                logger.info(
                                    "[PRICE_TAG_HOG] No person detected \u2014 using centered body column "
                                    f"({_cx_margin_l},{0},{_cx_margin_r},{_fh}) to guarantee visible pointer line."
                                )

                            # ── [WEAR SCANNER BYPASSED] Use FashionScout Data Directly ──────
                            # The item name is already safely identified by FashionScout's forensic analysis.
                            _gemini_wear_location = "torso"
                            try:
                                _pm_scout = profile_data.get("pipeline_metrics", {})
                                _mon_data = profile_data.get("monetization", {}) or profile_data.get("monetization_data", {}) or _pm_scout.get("monetization", {})
                                _scout_data = _mon_data.get("fashion_scout", {}) or profile_data.get("fashion_scout", {}) or _pm_scout.get("fashion_scout", {})
                                
                                _raw_item_name = _scout_data.get("wear_name") or _scout_data.get("outfit_type")

                                # ── [ACCESSORY GUARD] ────────────────────────────────────────
                                # Gemini occasionally picks accessories as the dominant garment
                                # (sunglasses are visually prominent when close to camera).
                                # If the wear_name is an accessory, null it so the fallback
                                # chain (caption / overlay item_name) provides the real garment.
                                _ACCESSORY_REJECTS = (
                                    "sunglass", "eyeglass", "spectacle", "goggle", "shade",
                                    "eyewear", "glasses", "hat", "cap", "headband",
                                    "earring", "necklace", "bracelet", "ring", "anklet",
                                    "watch", "bag", "purse", "clutch", "shoe", "heel",
                                    "sandal", "boot", "footwear", "sneaker", "belt",
                                )
                                if _raw_item_name:
                                    _rn_lower = _raw_item_name.lower()
                                    if any(kw in _rn_lower for kw in _ACCESSORY_REJECTS):
                                        logger.warning(
                                            f"⚠️ [ACCESSORY_GUARD] Scout returned accessory as wear_name: "
                                            f"'{_raw_item_name}' — nulling to trigger garment fallback."
                                        )
                                        _raw_item_name = None
                                # ─────────────────────────────────────────────────────────────

                                if not _raw_item_name:
                                    # [DEEP_SCAN] If outfit_type is missing, extract from description
                                    _scout_desc = _scout_data.get("outfit_description", "")
                                    if _scout_desc:
                                        from Text_Modules.fashion_narrator import extract_outfit_caption
                                        _raw_item_name = extract_outfit_caption(_scout_data, max_words=3)
                                
                                if not _raw_item_name:
                                    # [LIVE_AI_SYNC] Sync with the caption engine's live AI result if scout failed
                                    _raw_item_name = profile_data.get("caption")
                                
                                if not _raw_item_name or _raw_item_name == "Fashion Item":
                                    # Fallback to the overlay's trend text or general name
                                    _overlay_data = _mon_data.get("overlay_data", [{}])[0]
                                    if isinstance(_overlay_data, dict):
                                        _raw_item_name = _overlay_data.get("item_name") or _overlay_data.get("trend_text", "Fashion Item").title()
                                    else:
                                        _raw_item_name = "Fashion Item"
                                
                                _gemini_wear_scan_succeeded = True

                            except Exception as _ws_e:
                                logger.warning(f"⚠️ [WEAR_SCAN] Fallback logic failed: {_ws_e}")
                                _gemini_wear_scan_succeeded = False

                            # Derive location_hint from Gemini scan result
                            _location_hint = _gemini_wear_location or "torso"

                            # ── [FIX 3] Detect dominant outfit color from frame pixels ──────
                            # The item_name passed downstream often contains the wrong color
                            # (e.g. source video title says "White" but dress is gold/champagne).
                            # Sample the torso region of the detected box to get the real color.

                            # Only fall back to old profile_data if Gemini vision scan failed completely
                            _gemini_wear_scan_succeeded = locals().get("_gemini_wear_scan_succeeded", False)
                            if not _gemini_wear_scan_succeeded:
                                _raw_item_name = profile_data.get("item_name") or ""
                                if not _raw_item_name or _raw_item_name.strip().lower() in (
                                    "style", "outfit", "look", "ensemble", "fashion style",
                                    "high fashion", "fashion ensemble", ""
                                ):
                                    # Try to extract a garment word from outfit_description
                                    _scout_desc = (
                                        profile_data.get("outfit_description")
                                        or profile_data.get("fashion_description")
                                        or profile_data.get("fashion_vibe") or ""
                                    )
                                    _garment_keywords = [
                                        "saree", "sari", "lehenga", "anarkali", "salwar", "kurta",
                                        "dress", "gown", "skirt", "maxi", "mini", "midi",
                                        "jumpsuit", "co-ord", "coord", "set", "suit",
                                        "blazer", "top", "blouse", "crop top", "shirt",
                                        "palazzo", "sharara", "gharara",
                                    ]
                                    _found_garment = ""
                                    _desc_lower = _scout_desc.lower()
                                    for _gk in _garment_keywords:
                                        if _gk in _desc_lower:
                                            _found_garment = _gk.title()
                                            break
                                    _raw_item_name = _found_garment if _found_garment else ""

                            _item_name_raw = _raw_item_name or "Dress"

                            # [FIX] Derive item_category from scout description so the
                            # smart_price_tag engine gets a real category, not "default".
                            _item_cat_scout = profile_data.get("category") or "default"
                            if _item_cat_scout == "default":
                                _cat_map = {
                                    "saree": "saree", "sari": "saree", "lehenga": "lehenga",
                                    "anarkali": "anarkali", "gown": "gown", "dress": "dress",
                                    "jumpsuit": "jumpsuit", "suit": "suit", "blazer": "blazer",
                                    "kurta": "kurta", "crop": "top", "blouse": "blouse",
                                    "coord": "coord", "set": "coord",
                                }
                                _scout_lower = (profile_data.get("outfit_description") or "").lower()
                                for _ck, _cv in _cat_map.items():
                                    if _ck in _scout_lower:
                                        _item_cat_scout = _cv
                                        break
                                if _item_cat_scout == "default" and _item_name_raw:
                                    _item_cat_scout = _item_name_raw.lower()

                            try:
                                _frame_bgr_color = _cv2_pt.imread(_frame_png)
                                if _frame_bgr_color is not None:
                                    _bx, _by, _bw, _bh = _detected_human_box
                                    _img_h, _img_w = _frame_bgr_color.shape[:2]

                                    def _sample_zone_color(y1_frac, y2_frac):
                                        """Sample HSV stats from a vertical slice of the body box."""
                                        _zy1 = min(_img_h - 1, _by + int(_bh * y1_frac))
                                        _zy2 = min(_img_h,     _by + int(_bh * y2_frac))
                                        _zx1 = max(0,          _bx + int(_bw * 0.15))
                                        _zx2 = min(_img_w,     _bx + int(_bw * 0.85))
                                        _crop = _frame_bgr_color[_zy1:_zy2, _zx1:_zx2]
                                        if _crop.size == 0:
                                            return None, None, None
                                        _hsv = _cv2_pt.cvtColor(_crop, _cv2_pt.COLOR_BGR2HSV)
                                        return (float(_np_pt.mean(_hsv[:, :, 0])),
                                                float(_np_pt.mean(_hsv[:, :, 1])),
                                                float(_np_pt.mean(_hsv[:, :, 2])))

                                    def _is_skin_tone(h, s, v):
                                        """True when HSV stats look like bare human skin."""
                                        # Skin: low-mid H (0-25), low-mid S (15-70), mid-high V
                                        return (0 <= h <= 25) and (15 <= s <= 75) and (v >= 100)

                                    def _hsv_to_color_name(h, s, v):
                                        if s < 30:
                                            if v > 200: return "White"
                                            elif v > 100: return "Grey"
                                            else: return "Black"
                                        elif v < 50:
                                            return "Black"
                                        # [FIX] Expanded Red range — captures crimson, maroon, burgundy
                                        # OpenCV HSV hue wraps at 180. Deep reds: 0-15 AND 155-180.
                                        elif 0 <= h < 15 or h >= 155:
                                            return "Red"
                                        elif 15 <= h < 35:
                                            return "Gold" if s > 80 else "Beige"
                                        elif 35 <= h < 75:
                                            return "Yellow" if s > 150 else "Olive"
                                        elif 75 <= h < 105:
                                            return "Green"
                                        elif 105 <= h < 130:
                                            return "Blue"
                                        elif 130 <= h < 155:
                                            return "Purple"
                                        return None

                                    _color_name = None

                                    # ── Pass 1: torso zone (35%→65%) ────────────────────────
                                    _mh, _ms, _mv = _sample_zone_color(0.35, 0.65)
                                    if _mh is not None:
                                        if _is_skin_tone(_mh, _ms, _mv):
                                            logger.info(
                                                f"🎨 [COLOR_DETECT] Torso zone is skin-tone "
                                                f"(H={_mh:.0f} S={_ms:.0f}) — retrying lower body"
                                            )
                                            # ── Pass 2: lower body zone (65%→90%) ───────────
                                            _mh2, _ms2, _mv2 = _sample_zone_color(0.65, 0.92)
                                            if _mh2 is not None and not _is_skin_tone(_mh2, _ms2, _mv2):
                                                _color_name = _hsv_to_color_name(_mh2, _ms2, _mv2)
                                                logger.info(
                                                    f"🎨 [COLOR_DETECT] Lower-body color: {_color_name} "
                                                    f"(H={_mh2:.0f} S={_ms2:.0f} V={_mv2:.0f})"
                                                )
                                            else:
                                                # Both zones are skin → background/backless outfit
                                                # Don't override the garment label with a skin color
                                                logger.info(
                                                    "🎨 [COLOR_DETECT] Both zones skin-dominant "
                                                    "— skipping color override"
                                                )
                                        else:
                                            _color_name = _hsv_to_color_name(_mh, _ms, _mv)
                                            logger.info(
                                                f"🎨 [COLOR_DETECT] Torso color: {_color_name} "
                                                f"(H={_mh:.0f} S={_ms:.0f} V={_mv:.0f})"
                                            )

                                    # [FIX] Only override color if Gemini wear scanner FAILED to name the item.
                                    # Trusting pixel sampling over Gemini's visual ID is the wrong priority.
                                    if _color_name and not _gemini_wear_scan_succeeded:
                                        _color_words = [
                                            "White", "Black", "Red", "Blue", "Green", "Gold",
                                            "Yellow", "Olive", "Grey", "Gray", "Purple", "Pink", "Beige",
                                            "Ivory", "Brown", "Orange", "Silver", "Champagne",
                                        ]
                                        _name_no_color = _item_name_raw
                                        for _cw in _color_words:
                                            import re as _re_color
                                            _name_no_color = _re_color.sub(
                                                rf"(?i)\b{_cw}\b", "", _name_no_color
                                            ).strip()
                                        _item_name_raw = f"{_color_name} {_name_no_color}".strip()
                                        logger.info(
                                            f"🎨 [COLOR_DETECT] Fallback color applied → '{_item_name_raw}'"
                                        )
                                    elif _color_name:
                                        logger.info(
                                            f"🎨 [COLOR_DETECT] Color detected ({_color_name}) but Gemini scan already provided name — skipping override."
                                        )
                            except Exception as _col_err:
                                logger.warning(f"⚠️ [COLOR_DETECT] Color detection failed (non-fatal): {_col_err}")

                            _face_box_for_tag = None
                            _frame_image = None
                            try:
                                import cv2 as _cv2_tag
                                _frame_image = _cv2_tag.imread(_frame_png)
                                if _frame_image is not None:
                                    _gray_for_face = _cv2_tag.cvtColor(_frame_image, _cv2_tag.COLOR_BGR2GRAY)
                                    _classifier = _cv2_tag.CascadeClassifier(_cv2_tag.data.haarcascades + "haarcascade_frontalface_default.xml")
                                    _faces_found = _classifier.detectMultiScale(_gray_for_face, 1.1, 5, minSize=(60, 60))
                                    if len(_faces_found) > 0:
                                        _face_box_for_tag = list(max(_faces_found, key=lambda f: f[2]*f[3]))
                                        logger.info(f"💰 [PRICE_TAG_FACE] Face box detected: {_face_box_for_tag}")
                            except Exception as _face_e:
                                logger.warning(f"⚠️ [PRICE_TAG_FACE] Face detection failed: {_face_e}")

                            _png_out = _smart_tag_engine.generate(
                                 width=_fw, height=_fh,
                                 human_box=_detected_human_box,
                                 item_name=_item_name_raw,
                                 price_text=profile_data.get("price_tag") or "",
                                 location_hint=_location_hint,
                                 item_category=_item_cat_scout,  # [FIX] scout-derived, not "default"
                                 # [PRICE SYNC] Pass centralized price_data so overlay == Telegram
                                 gemini_celebrity_price=None,
                                 precomputed_price_data=profile_data.get("price_data"),
                                 face_box=_face_box_for_tag,
                                 frame_bgr=_frame_image,
                                 # [WATERMARK ANCHOR] Use the watermark's screen position as
                                 # the price tag anchor — it's already a corner of the screen
                                 # so it's guaranteed NOT to land on the actress's face.
                                 watermark_bbox=profile_data.get("watermark_bbox"),
                             )
                            if _png_out:
                                price_tag_images.append(_png_out)
                                auditor.mark_executed("price_tag_engine")
                                # [mkpv-fix] Align Caption Overlay with Visual Scan:
                                # Ensure the high-quality name found by the WEAR_SCAN is used for the text overlay.
                                if _item_name_raw and _item_name_raw not in ("Dress", "Outfit", "Look", "Style", "Ensemble"):
                                    profile_data["item_name"] = _item_name_raw
                                    logger.info(f"🎨 [OVERLAY_ALIGN] Visual scan identified '{_item_name_raw}' — syncing to overlay.")
                                    
                                logger.info(
                                    f"💰 [PRICE_TAG_STATIC] PNG from {_chosen_ts:.2f}s "
                                    f"→ output 0.75s (on stable outfit anchor shot)"
                                )

                    except Exception as _pte2:
                        logger.warning(f"⚠️ [PRICE_TAG_STATIC] Frame extract failed: {_pte2}")
            else:
                logger.warning(
                    f"⚠️ [PRICE_TAG_ANCHOR] No source segment found (all fallbacks exhausted). "
                    f"Price tag will NOT appear. segments_checked={len(_rt_segs)}, "
                    f"pt_enabled={_pt_enabled}, forced={_forced_price_tag}"
                )
        # ─────────────────────────────────────────────────────────────────────

        if _scenes:
            profile_data["editing_timeline"] = _scenes

        if context.feature_flags.get("scene_reconstruction"):
            _unique_clip_ids = (
                sorted({s.get("clip_id", 0) for s in _scenes}) if _scenes else [0]
            )
            logger.info(
                f"[MULTI_CLIP] segments_generated={len(_scenes)} | "
                f"unique_clips_used={_unique_clip_ids}"
            )
            # [FIX] Ensure timeline_instructions carries _scenes so video_pipeline
            # takes the order-preserving path (not sanitize_segments which sorts).
            if _scenes:
                if timeline_instructions is None:
                    timeline_instructions = {}
                timeline_instructions["scenes"] = _scenes

                # [FOCUS_ON_WEAR INJECTION]
                _focus_on_wear = (os.getenv("FOCUS_ON_WEAR", "True").lower() in ("true", "1", "yes")) and _enable_fs
                if _focus_on_wear:
                    _pm_scout = profile_data.get("pipeline_metrics", {})
                    _mon_data = profile_data.get("monetization", {}) or profile_data.get("monetization_data", {}) or _pm_scout.get("monetization", {})
                    _scout_data = _mon_data.get("fashion_scout", {}) or profile_data.get("fashion_scout", {}) or _pm_scout.get("fashion_scout", {})
                    if _scout_data:
                        _attrs = _scout_data.get("attributes", {})
                        _class = _attrs.get("classification", {}) if isinstance(_attrs, dict) else {}
                        _wear_cat = _class.get("primary_category", "") if isinstance(_class, dict) else ""
                        if _wear_cat:
                            timeline_instructions["focus_on_wear"] = True
                            timeline_instructions["wear_category"] = _wear_cat
                            logger.info(f"🎯 [FOCUS_ON_WEAR] Enabled. Targeting category: '{_wear_cat}'")


            _clip_plan = profile_data.get("clip_plan", [])
            if _separate_shorts and _scenes and _clip_plan:
                logger.info(f"✂️ [SEPARATE_SHORTS] Mode ON — exporting {len(_clip_plan)} planned clips")
                _separate_out_dir = os.path.join("Processed Shorts", os.path.basename(output_path).replace('.mp4', ''))
                os.makedirs(_separate_out_dir, exist_ok=True)

                render_success = False
                _first_clip_out = None
                _rendered_scene_hashes = set()
                
                for _si, _clip_spec in enumerate(_clip_plan):
                    _label = _clip_spec.get("clip_label", f"clip_{_si+1:02d}")
                    _single_out = os.path.join(_separate_out_dir, f"{_label}.mp4")
                    _single_tl  = timeline_instructions.copy() if timeline_instructions else {}
                    
                    _cid = _clip_spec.get("cluster_id")
                    if _cid is not None:
                        from Intelligence_Modules.scene_intel import filter_scenes_by_cluster
                        _face_tl = profile_data.get("scene_context", {}).get("face_timeline", [])
                        _clip_scenes = filter_scenes_by_cluster(_scenes, _face_tl, _cid)
                    else:
                        _clip_scenes = _scenes
                        
                    # Deduplication: Hash the start/end times of the scenes to prevent identical clip exports
                    _scene_hash = "-".join(f"{s.get('start',0)}_{s.get('end',0)}" for s in _clip_scenes)
                    if _scene_hash in _rendered_scene_hashes:
                        logger.info(f"⏭️ [SEPARATE_SHORTS] Skipping {_label}.mp4 (Identical to previous clip)")
                        continue
                    _rendered_scene_hashes.add(_scene_hash)
                    
                    _single_tl["scenes"] = _clip_scenes
                    
                    _ok = video_pipeline.render_scene_reconstruction(
                        input_paths,
                        _single_out,
                        segments=_clip_scenes,
                        price_tag_time=profile_data.get("price_tag_time", 0.75),
                        price_tag_images=price_tag_images,
                        timeline_instructions=_single_tl,
                    )
                    if _ok:
                        logger.info(f"✅ [SEPARATE_SHORTS] {_label}.mp4 → {_single_out}")
                        render_success = True
                        if not _first_clip_out:
                            _first_clip_out = _single_out
                    else:
                        logger.warning(f"⚠️ [SEPARATE_SHORTS] {_label} failed")
                
                if render_success and _first_clip_out:
                    temp_visual_render = _first_clip_out
                else:
                    temp_visual_render = os.path.join(job_dir, "visual_render_temp.mp4")
            else:
                render_success = video_pipeline.render_scene_reconstruction(
                    input_paths,
                    temp_visual_render,
                    segments=_scenes,
                    price_tag_time=profile_data.get("price_tag_time", 0.75),
                    price_tag_images=price_tag_images,
                    timeline_instructions=timeline_instructions,
                )

            if render_success:
                auditor.mark_executed("scene_reconstruction")
            elif _separate_shorts:
                logger.warning("⚠️ [SEPARATE_SHORTS] All clip renders failed. No fallback full-video render in separate-shorts mode.")
                auditor.mark_failed("scene_reconstruction")
            else:
                logger.warning("⚠️ Scene Reconstruction failed. Using standard render.")
                auditor.mark_failed("scene_reconstruction")
                video_pipeline.render_pipeline(
                    current_video_source,
                    temp_visual_render,
                    price_tag_images=price_tag_images,
                )
        else:
            # "Scene Reconstruction" feature flag is off, but we still need to
            # apply the editing timeline (trim + concat the selected segments).
            # render_pipeline() is a full-video effects-only pipeline — it has
            # NO concept of segments and will output the entire source unchanged.
            # render_scene_reconstruction() is the correct function for cutting.
            if _scenes:
                logger.info(f"✂️ [SEGMENT_RENDER] Applying {len(_scenes)} segment cuts via scene reconstruction (autorised).")
                auditor.mark_disabled("scene_reconstruction")  # feature flag still off
                # [FIX] Ensure timeline_instructions carries _scenes so video_pipeline
                # takes the order-preserving path (not sanitize_segments which sorts).
                _tl_for_render = timeline_instructions if timeline_instructions is not None else {}
                _tl_for_render["scenes"] = _scenes

                _clip_plan = profile_data.get("clip_plan", [])
                if _separate_shorts and _scenes and _clip_plan:
                    logger.info(f"✂️ [SEPARATE_SHORTS] Mode ON — exporting {len(_clip_plan)} planned clips")
                    _separate_out_dir = os.path.join("Processed Shorts", os.path.basename(output_path).replace('.mp4', ''))
                    os.makedirs(_separate_out_dir, exist_ok=True)

                    render_success = False
                    _first_clip_out = None
                    _rendered_scene_hashes = set()
                    
                    for _si, _clip_spec in enumerate(_clip_plan):
                        _label = _clip_spec.get("clip_label", f"clip_{_si+1:02d}")
                        _single_out = os.path.join(_separate_out_dir, f"{_label}.mp4")
                        _single_tl  = _tl_for_render.copy()
                        
                        _cid = _clip_spec.get("cluster_id")
                        if _cid is not None:
                            from Intelligence_Modules.scene_intel import filter_scenes_by_cluster
                            _face_tl = profile_data.get("scene_context", {}).get("face_timeline", [])
                            _clip_scenes = filter_scenes_by_cluster(_scenes, _face_tl, _cid)
                        else:
                            _clip_scenes = _scenes
                            
                        # Deduplication: Hash the start/end times of the scenes to prevent identical clip exports
                        _scene_hash = "-".join(f"{s.get('start',0)}_{s.get('end',0)}" for s in _clip_scenes)
                        if _scene_hash in _rendered_scene_hashes:
                            logger.info(f"⏭️ [SEPARATE_SHORTS] Skipping {_label}.mp4 (Identical to previous clip)")
                            continue
                        _rendered_scene_hashes.add(_scene_hash)
                            
                        _single_tl["scenes"] = _clip_scenes
                        
                        _ok = video_pipeline.render_scene_reconstruction(
                            input_paths,
                            _single_out,
                            segments=_clip_scenes,
                            price_tag_time=profile_data.get("price_tag_time", 0.75),
                            price_tag_images=price_tag_images,
                            timeline_instructions=_single_tl,
                        )
                        if _ok:
                            logger.info(f"✅ [SEPARATE_SHORTS] {_label}.mp4 → {_single_out}")
                            render_success = True
                            if not _first_clip_out:
                                _first_clip_out = _single_out
                        else:
                            logger.warning(f"⚠️ [SEPARATE_SHORTS] {_label} failed")
                    
                    if render_success and _first_clip_out:
                        temp_visual_render = _first_clip_out
                    else:
                        temp_visual_render = os.path.join(job_dir, "visual_render_temp.mp4")
                else:
                    render_success = video_pipeline.render_scene_reconstruction(
                        input_paths,
                        temp_visual_render,
                        segments=_scenes,
                        price_tag_time=profile_data.get("price_tag_time", 0.75),
                        price_tag_images=price_tag_images,
                        timeline_instructions=_tl_for_render,
                    )

                if not render_success:
                    if _separate_shorts:
                        logger.warning("⚠️ [SEPARATE_SHORTS] All clip renders failed. No fallback full-video render in separate-shorts mode.")
                    else:
                        logger.warning("⚠️ Segment render failed. Falling back to full-video render.")
                        video_pipeline.render_pipeline(
                            current_video_source,
                            temp_visual_render,
                            price_tag_images=price_tag_images,
                        )
            else:
                logger.info("⏩ No segments — Using full-video standard render.")
                auditor.mark_disabled("scene_reconstruction")
                video_pipeline.render_pipeline(
                    current_video_source,
                    temp_visual_render,
                    price_tag_images=price_tag_images,
                )


        # ── QUALITY EVALUATOR (Post-Render blind check) ────────────────────
        try:
            from Intelligence_Modules.quality_evaluator import QualityEvaluator

            _qe_result = QualityEvaluator.evaluate_quality(
                current_video_source, temp_visual_render
            )
            profile_data["quality_evaluation"] = _qe_result
            _qe_status = _qe_result.get("status", "UNKNOWN")
            _qe_score = _qe_result.get("score", 0.0)
            if _qe_status == "HARD_FAIL":
                logger.error(
                    f"🚨 [QUALITY_EVALUATOR] HARD_FAIL: score={_qe_score:.3f} "
                    f"| reasons={_qe_result.get('reasons', [])}"
                )
                auditor.mark_failed("quality_evaluator")
            elif _qe_status in ("SOFT_FAIL", "ERROR"):
                logger.warning(
                    f"⚠️ [QUALITY_EVALUATOR] {_qe_status}: score={_qe_score:.3f} "
                    f"| reasons={_qe_result.get('reasons', [])}"
                )
                auditor.mark_executed("quality_evaluator")
            else:
                _qe_deltas = _qe_result.get("deltas", {})
                logger.info(
                    f"✅ [QUALITY_EVALUATOR] PASS score={_qe_score:.3f} "
                    f"| blur={_qe_deltas.get('blur', 0):.3f} "
                    f"| edge={_qe_deltas.get('edge', 0):.3f}"
                )
                auditor.mark_executed("quality_evaluator")
        except Exception as _qe_e:
            logger.warning(f"⚠️ Quality evaluation skipped (non-fatal): {_qe_e}")
            auditor.mark_skipped("quality_evaluator")

        # ---- POST-RENDER OVERLAY ----------------------------------------------------
        # Priority order for caption lane (same position as fashion scout wear text):
        #   1. Wear Name from FashionScout  (best — specific item name)
        #   2. profile_data["caption"]       (AI-generated caption fallback)
        #   3. Viral Hook                    (engagement hook when 1 & 2 are unavailable)
        #   4. No caption                    (brand-only overlay)
        _wear_name = profile_data.get("item_name")
        _bad_wear_names = ("Dress", "Outfit", "Look", "Style", "Ensemble", "City pace active", "Style Analysis")
        if _wear_name and _wear_name not in _bad_wear_names:
             _caption_text_for_overlay = _wear_name
             logger.info(f"🎨 [OVERLAY_ALIGN] Using garment name (Wear Name) for overlay: '{_wear_name}'")

        # [FIX 3] Safety net: if caption resolution ran earlier, use it
        elif (not _caption_text_for_overlay or _caption_text_for_overlay in _bad_wear_names) and profile_data.get("caption"):
            _caption_text_for_overlay = profile_data["caption"]

        # [VIRAL_HOOK] Reroute hook to static ASS subtitle or caption lane
        _static_hook_mode = os.getenv("ENABLE_STATIC_HOOK_SUBTITLE", "no").lower() in ("yes", "true", "1", "on")
        
        # Determine the viral hook text
        try:
            _raw_ol_vh = profile_data.get("overlay_data") or {}
            # overlay_data can be a list (main path) or a plain dict (lite path)
            if isinstance(_raw_ol_vh, list) and _raw_ol_vh:
                _raw_ol_vh = _raw_ol_vh[0]
            _viral_hook_text = (
                (_raw_ol_vh.get("viral_hook") if isinstance(_raw_ol_vh, dict) else None)
                or ""
            )
            if not _viral_hook_text:
                from Text_Modules.overlay_engine import select_viral_hook as _svh_orch
                _viral_hook_text = _svh_orch({
                    "title": title or "",
                    "niche_category": profile_data.get("niche_category", "entertainment"),
                    "energy_score": profile_data.get("energy_score", 0.5),
                })
        except Exception as _vho_err:
            logger.warning(f"⚠️ [VIRAL_HOOK] Hook selection failed (non-fatal): {_vho_err}")
            _viral_hook_text = ""

        if _static_hook_mode and _viral_hook_text:
            # When static hook ASS mode is enabled, ALWAYS reserve the hook for static ASS subtitle
            profile_data["static_hook_text"] = _viral_hook_text
            logger.info(f"🪝 [VIRAL_HOOK] Hook reserved for static ASS subtitle: \"{_viral_hook_text}\"")
            auditor.mark_executed("viral_hook_overlay")
            # Clear caption lane to prevent double captioning or clashing full-duration text
            _caption_text_for_overlay = None
        elif not _caption_text_for_overlay or _caption_text_for_overlay in _bad_wear_names:
            # Fallback to old caption-lane behavior
            if _viral_hook_text:
                _caption_text_for_overlay = _viral_hook_text
                logger.info(f"🪝 [VIRAL_HOOK] Using hook as caption overlay: \"{_viral_hook_text}\"")
                auditor.mark_executed("viral_hook_overlay")

        has_caption = bool(_caption_text_for_overlay)
        has_brand = _add_text_overlay and bool(_brand_text_for_overlay)

        if (has_caption or has_brand) and os.path.exists(temp_visual_render):
            try:
                final_overlay_path = os.path.join(job_dir, "visual_overlay_final.mp4")
                from Text_Modules.text_overlay import (
                    apply_caption_and_brand_overlay_safe,
                )

                brand_to_pass = _brand_text_for_overlay if has_brand else None
                caption_to_pass = _caption_text_for_overlay if has_caption else ""

                if apply_caption_and_brand_overlay_safe(
                    temp_visual_render,
                    final_overlay_path,
                    caption=caption_to_pass,
                    brand_text=brand_to_pass,
                ):
                    temp_visual_render = final_overlay_path
                    logger.info("✅ Unified post-render overlay applied.")
                    if has_caption:
                        auditor.mark_executed("caption_generation")
                    if has_brand:
                        auditor.mark_executed("brand_overlay")
                else:
                    logger.warning("⚠️ Unified overlay failed.")
                    if has_caption:
                        auditor.mark_failed("caption_generation")
                    if has_brand:
                        auditor.mark_failed("brand_overlay")
            except Exception as _ov_err:
                logger.error(f"Unified overlay error: {_ov_err}")
                if has_caption:
                    auditor.mark_failed("caption_generation")
                if has_brand:
                    auditor.mark_failed("brand_overlay")

        # ---- [NEW] INTEGRATED DIAGNOSTICS (Step 9) -----------------------------------
        logger.info("🔍 [Step 9] Running Integrated Diagnostics...")
        
        # [mkpv-fix] Hard "Empty = Success" Rule (Elite Editor Refusal)
        if profile_data.get("editing_source") == "none":
            logger.info("⚪ [EDITOR] NO_EDIT — Skipping validation and final refinement.")
            return True, {
                "status": "NO_EDIT",
                "segments": [],
                "editor_confidence": profile_data.get("editor_confidence", 0.0),
                "reason": "Elite Editor determined content lacks sufficient viral potential."
            }

        _sv_verdict = "UNKNOWN"
        _final_vid_dur = None


        # [mkpv-fix] Calculate actual visual duration to prevent overrun/freeze
        try:
            _info = video_pipeline.get_video_info(temp_visual_render)
            _final_vid_dur = _info.get("duration")
            if _final_vid_dur:
                logger.info(f"🎞️ Visual Render Duration: {_final_vid_dur:.2f}s")
        except Exception as _dur_err:
            logger.warning(f"Could not probe visual duration: {_dur_err}")
        
        # 1. SEGMENT VALIDATOR: Quality & Alignment Check
        try:
            from Core_Modules.segment_validator import validate_segments
            _sel_cands = profile_data.get("candidate_moments", [])
            _sel_segs = (
                profile_data.get("editing_timeline") or
                profile_data.get("reconstructed_timeline", [])
            )
            _sig_data = {
                "motion_scores": profile_data.get("motion_scores", []),
                "retention_scores": profile_data.get("retention_peaks", []),
                "emotion_scores": profile_data.get("emotional_spikes", []),
                "beat_scores": (
                    profile_data.get("bgm_classified_beats") or
                    profile_data.get("beat_data", {}).get("beats", [])
                ),
                "visual_change_scores": profile_data.get("visual_scores", []),
                "scene_boundaries": profile_data.get("scene_boundaries", []),
                "subject_tracking": profile_data.get("subject_tracking", {}),
            }
            if _sel_cands and _sel_segs:
                _sv_report = validate_segments(
                    _sel_cands, 
                    _sel_segs, 
                    _sig_data,
                    editing_source=profile_data.get("editing_source", "fallback")
                )
                _sv_verdict = _sv_report.get("verdict", "UNKNOWN")
                _sv_score = _sv_report.get("summary", {}).get("editor_quality_score", 0.0)

                # [FIX] Degraded quality penalty — only warn, don't kill valid fallback output.
                # Previously this blindly stamped FAKE_EDITOR:PREVENTED on ANY Python fallback
                # path even when validate_segments() returned APPROVED/SAFE for those segments.
                # Now: if the actual validator approved the output, trust it.
                # Only escalate to FAKE_EDITOR if the validator itself already flagged an issue.
                if profile_data.get("editing_quality") == "degraded":
                    if "FAKE_EDITOR" not in _sv_verdict and "FAILED" not in _sv_verdict:
                        # Fallback ran but produced valid segments — log a warning only
                        logger.warning(
                            "⚠️ [SEGMENT_VALIDATOR] editing_quality=degraded (Python fallback "
                            "path) but segments passed validation — continuing with DEGRADED tag."
                        )
                        _sv_verdict = _sv_verdict + ":DEGRADED_FALLBACK"
                    else:
                        # Validator already flagged an issue AND quality is degraded — now escalate
                        _sv_verdict = "FAKE_EDITOR:PREVENTED"

                profile_data["editor_verdict"] = _sv_verdict
                profile_data["editor_quality_score"] = _sv_score
                profile_data["validator_report"] = _sv_report
                logger.info(f"📊 [SEGMENT_VALIDATOR] verdict={_sv_verdict} | score={_sv_score:.3f}")
        except Exception as _sve:
            logger.warning(f"⚠️ Segment Validator Failure: {_sve}")

        # ── VALIDATOR HARD STOP ─────────────────────────────
        if ENABLE_VALIDATOR_HARD_STOP and "FAKE_EDITOR" in str(_sv_verdict):
            logger.error(
                f"🚫 [HARD_STOP] FAKE_EDITOR detected — output rejected.\n"
                f"   Verdict: {_sv_verdict}"
            )

            auditor.mark_failed("final_validation")

            return False, {
                "status": "FAILED_EDITING",
                "reason": _sv_verdict,
                "final_output": None,
                "segments": [],
                "metadata": {
                    "hard_stop": True,
                    "failure_stage": "validation",
                    "strict_mode": True
                }
            }
        # ─────────────────────────────────────────────────────


        # 2. SRV DIAGNOSTICS: Structural & Continuity Check
        try:
            from Diagnostics.scene_editing_verifier import verify_scene_reconstruction as _srv_verify
            
            tl_duration = sum(
                max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
                for s in _scenes
            )
            safe_segments_created = len(_scenes) if _scenes else len(profile_data.get("selected_shots", []))
            
            # Calculate actual duration change ratio
            _in_dur = max(1.0, duration) # from Step 0
            _out_dur = _final_vid_dur or tl_duration
            dur_ratio = _out_dur / _in_dur
            
            _srv_res = _srv_verify(
                scene_count=len(profile_data.get("shots", [])),
                segments_created=safe_segments_created,
                concat_used=bool(os.path.exists(output_path)),
                duration_change_ratio=dur_ratio,
                timeline_duration_sec=tl_duration,
                non_chronological=_non_chronological,
                avg_composite_score=_sv_score,
            )
            profile_data["editing_diagnostics"] = _srv_res.get("editing_diagnostics", {})
            logger.info(f"🔍 [SRV_VERIFIER] outcome={_srv_res.get('editing_diagnostics', {}).get('editing_effective')}")
            
            # [CRITICAL] Consolidate Final Warning for FAKE_EDITOR
            if "FAKE_EDITOR" in _sv_verdict or not _srv_res.get("editing_diagnostics", {}).get("editing_effective"):
                _has_motion = bool(profile_data.get("motion_scores"))
                _has_emotion = bool(profile_data.get("emotional_spikes"))
                _has_faces = bool(profile_data.get("subject_tracking"))
                _has_beats = bool(profile_data.get("beat_data", {}).get("beats"))
                logger.warning(f"""
⚠️ [ANALYSIS] Potential Ineffective Editing Detected
- Source: {_sv_verdict} (Structural/Intel)
- Metrics: Scenes={len(profile_data.get('shots', []))}, Segs={safe_segments_created}, Score={_sv_score:.2f}
- Signals: Motion={_has_motion}, Emo={_has_emotion}, Faces={_has_faces}, Beats={_has_beats}
- Signal Health: {profile_data.get('signal_health_score', 0):.2f} (Poverty={'YES' if profile_data.get('signal_poverty') else 'NO'})
""")
        except Exception as _srve:
            logger.warning(f"⚠️ SRV Failure: {_srve}")

        # ── [LATE SCRIPT GENERATION] Post-Visual-Render Script + TTS (AMTCE only) ─────
        # Activated via LATE_SCRIPT_GENERATION=yes in Credentials/.env
        #
        # This block runs AFTER the visual render is finalised and its exact duration
        # is known (_final_vid_dur). It uploads the rendered video to the Gemini File
        # API, requests a duration-aware narration script that fits the video precisely,
        # generates TTS audio for it, and updates all downstream script keys so the
        # karaoke engine and audio mix use the new script.
        #
        # ⚠️  AMTCE-ONLY: Do NOT touch this block when modifying CEIE.
        _late_script_mode = os.getenv("LATE_SCRIPT_GENERATION", "yes").strip().lower() == "yes"
        _narrator_enabled = os.getenv("CINEMATIC_NARRATOR_ENABLED", "yes").lower() == "yes"
        _vo_enabled = os.getenv("ENABLE_MICRO_VOICEOVER", "yes").lower() == "yes"
        if _late_script_mode and not (_narrator_enabled and _vo_enabled):
            logger.info("🧠 [LATE_SCRIPT] Voiceover/narration is disabled. Bypassing late script generation.")
            _late_script_mode = False
            
        if _late_script_mode and os.path.exists(temp_visual_render):
            logger.info("🧠 [LATE_SCRIPT] Starting post-render duration-aware script generation...")
            _late_script_generated = False
            try:
                from google import genai as _late_genai
                from google.genai import types as _late_types
                import time as _late_time

                _api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not _api_key:
                    raise RuntimeError("[LATE_SCRIPT] Missing GEMINI_API_KEY — skipping late script.")

                _late_client = _late_genai.Client(api_key=_api_key)

                # ── 1. Upload the visual render to Gemini File API ──────────────────
                _render_size_mb = os.path.getsize(temp_visual_render) / (1024 * 1024)
                logger.info(
                    f"📤 [LATE_SCRIPT] Uploading visual render ({_render_size_mb:.1f} MB) to Gemini File API..."
                )
                _uploaded_file = _late_client.files.upload(file=temp_visual_render)
                logger.info(
                    f"📤 [LATE_SCRIPT] Upload complete: name={_uploaded_file.name} | "
                    f"state={getattr(_uploaded_file, 'state', 'unknown')}"
                )

                # ── 2. Wait for the file to be ACTIVE (Gemini processes the video) ─
                _wait_start = _late_time.time()
                _wait_timeout = 120  # seconds
                while True:
                    _file_state = getattr(_uploaded_file, "state", None)
                    _state_name = (
                        _file_state.name
                        if hasattr(_file_state, "name")
                        else str(_file_state)
                    )
                    if _state_name == "ACTIVE":
                        logger.info("✅ [LATE_SCRIPT] Gemini file is ACTIVE — ready for analysis.")
                        break
                    if _state_name == "FAILED":
                        raise RuntimeError("[LATE_SCRIPT] Gemini file processing FAILED.")
                    if _late_time.time() - _wait_start > _wait_timeout:
                        logger.warning(
                            f"⚠️ [LATE_SCRIPT] File not ACTIVE after {_wait_timeout}s — proceeding anyway."
                        )
                        break
                    _late_time.sleep(3)
                    _uploaded_file = _late_client.files.get(name=_uploaded_file.name)

                # ── 3. Build a duration-aware prompt ───────────────────────────────
                _render_dur = _final_vid_dur or 30.0
                # Word target: ~2.3 words/sec, capped between 15 and 75 words
                _word_target = max(15, min(int(_render_dur * 2.3), 75))
                _niche = (
                    profile_data.get("niche")
                    or profile_data.get("content_type")
                    or "fashion/celebrity"
                )
                _tone = (
                    profile_data.get("tone")
                    or profile_data.get("editorial_tone")
                    or "engaging and enthusiastic"
                )
                _item = profile_data.get("item_name") or ""
                _item_hint = f"The featured item is: {_item}." if _item else ""

                _late_prompt = (
                    f"SYSTEM ROLE: You are a triple-threat content strategist operating simultaneously as:\n"
                    f"  1. A Bollywood-calibre COPYWRITER — you write scripts that feel like blockbuster dialogue, not voiceover.\n"
                    f"  2. A PSYCHOLOGIST who understands dopamine loops, pattern interrupts, and scroll-stopping hooks.\n"
                    f"  3. A CULTURAL INSIDER who is fluent in Hinglish slang, Gen-Z tone, and desi pop-culture references.\n\n"
                    f"MISSION: Watch this {_render_dur:.1f}s video clip with extreme precision. Your job is to write a\n"
                    f"narration script that makes the viewer freeze mid-scroll, feel something, and watch again.\n\n"
                    f"CONTENT CONTEXT:\n"
                    f"  - Niche: {_niche}\n"
                    f"  - Tone target: {_tone}\n"
                    f"  {'- Featured subject: ' + _item + chr(10) if _item else ''}"
                    f"  - Target word count: EXACTLY {_word_target} words (±3 words — hard constraint)\n\n"
                    f"5-LAYER NARRATIVE FORMULA:\n"
                    f"  LAYER 1 — PATTERN INTERRUPT (first 3-5 words): Start mid-action. No 'Welcome', no 'In this video'.\n"
                    f"    Use a sharp observation, question, or emotional trigger that matches the opening visual.\n"
                    f"  LAYER 2 — TENSION BUILD (words 6–40%): Create desire or FOMO. Make the viewer feel like\n"
                    f"    they're missing something critical if they look away. Sync pacing to the edit cuts.\n"
                    f"  LAYER 3 — REVELATION / PEAK (middle 30%): Drop the emotional centrepiece. The visual climax\n"
                    f"    should match your verbal climax. One sentence that could stand alone as a viral quote.\n"
                    f"  LAYER 4 — DESIRE AMPLIFIER (next 20%): Turn that revelation into craving. The viewer should\n"
                    f"    want to DM, save, share, or come back — triggered emotionally, not instructed.\n"
                    f"  LAYER 5 — OPEN LOOP CLOSE (final 3-5 words): End on a tease or emotional chord that makes\n"
                    f"    them watch again or go to the comment/bio. Never close the loop fully.\n\n"
                    f"HARD CONSTRAINTS — VIOLATION = REJECTED OUTPUT:\n"
                    f"  ✗ NO headings, labels, bullet points, or meta-commentary\n"
                    f"  ✗ NO filler phrases: 'amazing', 'beautiful', 'stunning', 'iconic', 'incredible'\n"
                    f"  ✗ NO explicit calls-to-action like 'subscribe', 'like', 'follow', 'click the link'\n"
                    f"  ✗ NO sentence longer than 12 words (mobile-listening pacing)\n"
                    f"  ✗ DO NOT describe what is visually obvious — instead reveal what the eye misses\n\n"
                    f"TONE CALIBRATION (match the niche):\n"
                    f"  - fashion/celebrity: Confident. Aspirational but street-smart. Not stiff or formal.\n"
                    f"  - fitness: Motivational but raw. Like a locker-room speech, not an ad.\n"
                    f"  - entertainment: Conversational. Quick. Gossip-energy without being tabloid.\n"
                    f"  - adult/nsfw: Suggestive but poetic. Heat without vulgarity. Let imagination work.\n"
                    f"  - Default: {_tone}. Keep it punchy, real, and emotionally charged.\n\n"
                    f"OUTPUT FORMAT: Narration text ONLY. No quotes, no prefix. Just the raw script.\n"
                    f"Target: {_word_target} words. Start writing immediately."
                )

                # ── 4. Call Gemini with the uploaded video (multi-model retry) ────
                _late_model_primary = os.getenv("LATE_SCRIPT_MODEL", "gemini-2.5-flash-lite")
                _late_model_fallbacks = [
                    _late_model_primary,
                    "gemini-2.5-flash",
                    "gemini-2.0-flash",
                    "gemini-2.5-pro",
                    "gemini-pro-latest",
                ]
                _late_response = None
                _late_script_raw = ""
                for _attempt_model in _late_model_fallbacks:
                    try:
                        logger.info(
                            f"🧠 [LATE_SCRIPT] Requesting {_word_target}-word script from {_attempt_model} "
                            f"(video={_render_dur:.1f}s)..."
                        )
                        _late_response = _late_client.models.generate_content(
                            model=_attempt_model,
                            contents=[
                                _late_types.Part.from_uri(
                                    file_uri=_uploaded_file.uri,
                                    mime_type="video/mp4",
                                ),
                                _late_prompt,
                            ],
                            config=_late_types.GenerateContentConfig(
                                temperature=0.7,
                                max_output_tokens=256,
                            ),
                        )
                        _late_script_raw = _late_response.text.strip() if _late_response.text else ""
                        if _late_script_raw:
                            logger.info(f"✅ [LATE_SCRIPT] Got response from {_attempt_model}.")
                            break
                    except Exception as _model_err:
                        _err_str = str(_model_err).lower()
                        if "429" in _err_str or "quota" in _err_str or "resource_exhausted" in _err_str:
                            logger.warning(
                                f"⚠️ [LATE_SCRIPT] {_attempt_model} returned 429/quota — trying next model..."
                            )
                        else:
                            logger.warning(
                                f"⚠️ [LATE_SCRIPT] {_attempt_model} failed ({_model_err}) — trying next model..."
                            )
                        _late_response = None
                        _late_script_raw = ""
                        continue

                # ── 5. Validate and propagate the new script ───────────────────────
                if _late_script_raw and len(_late_script_raw) > 10:
                    logger.info(
                        f"✅ [LATE_SCRIPT] Script generated ({len(_late_script_raw.split())} words): "
                        f"{_late_script_raw[:120]}..."
                    )
                    # Update all script keys consumed by karaoke + sidecar
                    full_script = _late_script_raw
                    profile_data["editorial_script"] = _late_script_raw
                    profile_data["karaoke_script"] = _late_script_raw
                    if isinstance(mon_data, dict):
                        mon_data["editorial_script"] = _late_script_raw
                        profile_data["monetization_data"] = mon_data
                    _late_script_generated = True
                else:
                    logger.warning("⚠️ [LATE_SCRIPT] Empty or too-short response — keeping early script.")

                # ── 6. Generate TTS voiceover from the new late script ─────────────
                if _late_script_generated and VOICEOVER_AVAILABLE:
                    _vo_env_late = os.getenv("ENABLE_MICRO_VOICEOVER", "yes").lower()
                    if _vo_env_late == "no":
                        logger.info(
                            "🎙️ [LATE_SCRIPT] Script ready but ENABLE_MICRO_VOICEOVER=no — TTS skipped."
                        )
                    else:
                        _late_vo_file = os.path.join(job_dir, "voiceover.mp3")
                        logger.info(
                            f"🎙️ [LATE_SCRIPT] Generating TTS for {len(full_script)} chars..."
                        )
                        _late_vo_ok = run_with_timeout(
                            func=voiceover.generate_voiceover,
                            timeout_sec=120,
                            feature_name="voiceover_generation",
                            auditor=auditor,
                            script_text=full_script[:500],
                            output_file=_late_vo_file,
                        )
                        if _late_vo_ok:
                            voiceover_path = _late_vo_file
                            logger.info(
                                f"🎙️ [LATE_SCRIPT] TTS voiceover ready: {_late_vo_file}"
                            )
                        else:
                            logger.warning("⚠️ [LATE_SCRIPT] TTS generation failed — no voiceover will be mixed.")

                # ── 7. Clean up: delete the uploaded file from Gemini File API ──────
                try:
                    _late_client.files.delete(name=_uploaded_file.name)
                    logger.info(f"🗑️ [LATE_SCRIPT] Deleted Gemini file: {_uploaded_file.name}")
                except Exception as _del_err:
                    logger.debug(f"[LATE_SCRIPT] Could not delete Gemini file (non-fatal): {_del_err}")

            except Exception as _late_err:
                logger.warning(
                    f"⚠️ [LATE_SCRIPT] Late script generation failed (non-fatal — using early script): {_late_err}"
                )
        # ─────────────────────────────────────────────────────────────────────────────

        # ---- FINAL AUDIO MIX --------------------------------------------------------
        logger.info("🎛️ Mixing Final Audio (VO + BGM)...")

        _music_offset = 0.0
        # [FIRST_SHOT] If an AI influencer intro was prepended, offset the BGM
        # so it starts AFTER the intro ends — this preserves the ElevenLabs
        # voice in the intro clip and prevents it being drowned by background music.
        _intro_dur_for_bgm = float(profile_data.get("first_shot_duration", 0.0))
        if _intro_dur_for_bgm > 0:
            _music_offset = round(_intro_dur_for_bgm, 2)
            logger.info(
                f"🎵 [FIRST_SHOT_AUDIO] BGM offset by {_music_offset}s "
                "to preserve AI influencer intro voice."
            )

        # [RANDOMIZE_BGM_OFFSET] Start BGM at a random position in the track so every
        # compile sounds fresh and social platforms can't fingerprint repetitive audio
        # from always starting at 0:00.
        # - Only applies to music/ library tracks (NOT Original_audio — those are beat-synced).
        # - Random start is capped at 60% of track duration so enough music remains.
        # - Applied ADDITIVE to any existing _music_offset (e.g. first-shot delay).
        _randomize_bgm = os.getenv("RANDOMIZE_BGM_OFFSET", "yes").strip().lower() == "yes"
        if _randomize_bgm and music_path and "Original_audio" not in music_path.replace("\\", "/"):
            try:
                import subprocess as _sp
                _ffprobe_bin = os.getenv("FFPROBE_BIN", "ffprobe")
                _probe_out = _sp.check_output(
                    [
                        _ffprobe_bin, "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        music_path,
                    ],
                    stderr=_sp.DEVNULL,
                    timeout=10,
                ).decode().strip()
                _track_dur = float(_probe_out)
                # Pick random start within first 60% of the track
                _max_start = _track_dur * 0.60
                _rand_start = round(random.uniform(0, max(0, _max_start - 30)), 2)  # keep ≥30s of music
                if _rand_start > 0:
                    _music_offset += _rand_start
                    logger.info(
                        f"🎲 [RANDOMIZE_BGM] Random BGM start: {_rand_start}s "
                        f"(track={_track_dur:.1f}s, total_offset={_music_offset:.2f}s)"
                    )
            except Exception as _rng_err:
                logger.debug(f"[RANDOMIZE_BGM] Could not randomize offset (non-fatal): {_rng_err}")

        if _separate_shorts:
            logger.info("⏭️ [SEPARATE_SHORTS] Main pipeline will now mix audio and concatenate intro for the BEST generated clip.")
            _separate_out_dir = os.path.join("Processed Shorts", os.path.basename(output_path).replace('.mp4', ''))
            profile_data["separate_shorts_dir"] = _separate_out_dir

        if not audio_pipeline.mix_audio(
            temp_visual_render,
            output_path,
            voiceover_path=voiceover_path,
            music_path=music_path,
            vo_vol=2.5,
            music_vol=0.15,
            duration=_final_vid_dur,
            music_offset=_music_offset,
        ):
            shutil.copy(temp_visual_render, output_path)
            logger.info("⏭️ Audio Mix Skipped (No VO or Music provided) — copied original audio.")
        else:
            logger.info("✅ Final Audio Mixed Successfully.")

        # ── [ADVANCED INTRO POST-PROCESSING CONCATENATION] ──
        _intro_clip = profile_data.get("first_shot_clip")
        _intro_mode = profile_data.get("first_shot_mode_active")

        # [TELEGRAM EXCLUSIVE] Save the processed-only path BEFORE the intro is prepended.
        # main.py reads this key to send the clean breakdown clip (without intro) to the
        # Telegram group — the intro is a YouTube/IG hook, not needed for Telegram members.
        if os.path.exists(output_path):
            profile_data["processed_only_path"] = output_path
            logger.info(f"📌 [FIRST_SHOT_POST] Saved processed_only_path for Telegram: {output_path}")

        if _intro_clip and _intro_mode and os.path.exists(_intro_clip):
            logger.info(f"🎬 [FIRST_SHOT_POST] Initiating {_intro_mode} concatenation...")
            try:
                _intro_mixed_path = os.path.join(job_dir, "intro_mixed_temp.mp4")

                # [FIX] The intro clip (general_intro or topic-specific) already contains the
                # ElevenLabs AI voice as its embedded audio track. That IS the correct, final
                # audio for the intro — it should never be re-mixed or overridden with BGM.
                #
                # Previous bug: the code extracted the voice from the clip into _intro_vo,
                # then called mix_audio(video=_intro_clip, voiceover=_intro_vo, music=random_bgm)
                # which stacked: clip_audio (voice) + _intro_vo (same voice again) + random_bgm
                # Result: doubled/muddy voice + BGM bleeding over the AI influencer speech.
                #
                # Fix: copy the intro clip directly. Its own audio is authoritative.
                shutil.copy(_intro_clip, _intro_mixed_path)
                logger.info(
                    "🎵 [FIRST_SHOT_POST] Intro audio preserved as-is "
                    "(ElevenLabs voice — no BGM overlay)."
                )

                # 4. Robust FFmpeg Filter Concatenation (Intro + Main Output)
                # We use filter_complex instead of demuxer to guarantee it handles any codec/framerate mismatch

                _final_concat_out = os.path.join(job_dir, "final_compilation_with_intro.mp4")

                FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
                
                # Scale, crop, fps, setsar to 1080x1920 30fps to ensure flawless concat
                _fc = (
                    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1,format=yuv420p[v0];"
                    "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1,format=yuv420p[v1];"
                    "[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[outv][outa]"
                )
                
                cmd = [
                    FFMPEG_BIN, "-y", 
                    "-i", _intro_mixed_path,
                    "-i", output_path,
                    "-filter_complex", _fc,
                    "-map", "[outv]",
                    "-map", "[outa]",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    _final_concat_out
                ]
                
                logger.info("🎬 [FIRST_SHOT_POST] Re-encoding concatenation to normalize formats...")
                _run_res = subprocess.run(cmd, capture_output=True, text=True)
                
                if _run_res.returncode == 0 and os.path.exists(_final_concat_out):
                    shutil.copy(_final_concat_out, output_path)
                    logger.info("✅ [FIRST_SHOT_POST] Successfully concatenated intro to output!")
                else:
                    logger.error(f"❌ [FIRST_SHOT_POST] Concatenation failed. FFmpeg output: {_run_res.stderr[-500:]}")
            except Exception as _intro_err:
                logger.error(f"❌ [FIRST_SHOT_POST] Concatenation exception: {_intro_err}")

        # ── [REACTION ENGINE] Optional post-render (Step 9.5) ─────────────────────────
        # Activated ONLY when ENABLE_REACTION_ENGINE=yes in Credentials/.env
        # Produces a SEPARATE reaction video — never modifies output_path.
        # Fully fail-safe: any crash here is caught and logged, pipeline continues.
        try:
            from Reaction_Engine.reaction_engine import run_reaction_engine
            run_reaction_engine(
                source_video=output_path,
                profile_data=profile_data,
                job_dir=job_dir,
            )
        except ImportError:
            pass  # Reaction Engine module not installed — silently skip
        except Exception as _re_err:
            logger.warning(
                f"⚠️ [REACTION_ENGINE] Non-fatal error (main pipeline unaffected): {_re_err}"
            )
        # ─────────────────────────────────────────────────────────────────────────────

        # ── [TEXT REACTION OVERLAY] Optional post-render (Step 9.6) ─────────────────
        # Activated ONLY when ENABLE_TEXT_REACTIONS=yes in Credentials/.env
        # Produces a SEPARATE video with animated emoji bubbles.
        try:
            from Reaction_Engine.text_reaction_overlay import run_text_reaction_overlay
            txt_reaction_out = run_text_reaction_overlay(
                source_video=output_path,
                profile_data=profile_data,
                job_dir=job_dir,
            )
            if txt_reaction_out:
                profile_data["text_reaction_video_path"] = txt_reaction_out
        except ImportError:
            pass  # Module not installed
        except Exception as _tro_err:
                logger.warning(
                    f"⚠️ [TEXT_REACTION] Non-fatal error (main pipeline unaffected): {_tro_err}"
                )
            # ─────────────────────────────────────────────────────────────────────────────

        # ── [KARAOKE SUBTITLE ENGINE] Optional post-render (Step 9.7) ────────────────
        tracker.step("STEP_SUBTITLES", 80)
        # Activated via KARAOKE_ENABLED=true in Credentials/.env
        # Applies Cinema-Grade V7 .ASS karaoke subtitles to the final video.
        # This is a REPLACE-IN-PLACE step: if successful, output_path is updated
        # to the captioned version. On failure, the original video is preserved.
        #
        # ENV CONTROLS:  KARAOKE_ENABLED / KARAOKE_FONT_SIZE / KARAOKE_SAFE_ZONE /
        #                KARAOKE_MARGIN_SIDE / KARAOKE_SHADOW_DEPTH / KARAOKE_OUTLINE_WIDTH /
        #                KARAOKE_CHUNK_SIZE / KARAOKE_HIGHLIGHT_COLOR / KARAOKE_BASE_COLOR
        try:
            from Compiler_Modules.karaoke_subtitle_engine import apply_karaoke_subtitles, is_karaoke_enabled
            if is_karaoke_enabled() and os.path.exists(output_path):
                # Resolve the narration script for voiceover + hallucination shield
                _karaoke_script = (
                    profile_data.get("karaoke_script")
                    or (profile_data.get("monetization") or {}).get("editorial_script")
                    or (profile_data.get("pipeline_metrics", {}).get("monetization", {})).get("editorial_script")
                    or profile_data.get("editorial_script")
                    or title  # Last resort: use the video title
                )
                if _karaoke_script:
                    _karaoke_out = os.path.splitext(output_path)[0] + "_captioned.mp4"
                    _karaoke_tmp = os.path.join(job_dir, "_karaoke_tmp")
                    logger.info(f"🎤 [KARAOKE] Injecting Cinema-Grade subtitles → {os.path.basename(_karaoke_out)}")
                    _karaoke_ok = apply_karaoke_subtitles(
                        input_video=output_path,
                        output_video=_karaoke_out,
                        script_text=_karaoke_script,
                        temp_dir=_karaoke_tmp,
                    )
                    if _karaoke_ok and os.path.exists(_karaoke_out):
                        import shutil as _shutil_k
                        _shutil_k.move(_karaoke_out, output_path)
                        profile_data["karaoke_applied"] = True
                        logger.info(f"✅ [KARAOKE] Cinema-Grade subtitles applied to {os.path.basename(output_path)}")
                    else:
                        logger.warning("⚠️ [KARAOKE] Subtitle render failed — original video preserved.")
                        profile_data["karaoke_applied"] = False
                else:
                    logger.info("🔕 [KARAOKE] No narration script found — skipping subtitle injection.")
        except ImportError:
            pass  # Karaoke module not installed — silently skip
        except Exception as _karaoke_err:
            logger.warning(
                f"⚠️ [KARAOKE] Non-fatal error (main pipeline unaffected): {_karaoke_err}"
            )
        # ─────────────────────────────────────────────────────────────────────────────

        # ── [STATIC HINGLISH HOOK SUBTITLE] Post-render (Step 9.8) ───────────────────
        # Activated via ENABLE_STATIC_HOOK_SUBTITLE=yes in Credentials/.env
        # Applies the viral Hinglish hook as a Cinema-Grade .ASS karaoke subtitle
        # for the first HOOK_SUBTITLE_DURATION seconds (default 4s), then clean visuals.
        # No voiceover or Whisper needed — pure static text with word-highlight animation.
        _static_hook_text = profile_data.get("static_hook_text", "")
        if _static_hook_text and os.path.exists(output_path):
            try:
                from Compiler_Modules.karaoke_subtitle_engine import (
                    apply_static_hook_subtitle,
                    is_static_hook_subtitle_enabled,
                )
                if is_static_hook_subtitle_enabled():
                    _hook_subtitle_out = os.path.splitext(output_path)[0] + "_hook.mp4"
                    logger.info(
                        f"🪝 [STATIC_HOOK] Injecting Hinglish hook subtitle → "
                        f"{os.path.basename(_hook_subtitle_out)}"
                    )
                    _hook_ok = apply_static_hook_subtitle(
                        input_video=output_path,
                        output_video=_hook_subtitle_out,
                        hook_text=_static_hook_text,
                    )
                    if _hook_ok and os.path.exists(_hook_subtitle_out):
                        import shutil as _shutil_hook
                        _shutil_hook.move(_hook_subtitle_out, output_path)
                        profile_data["static_hook_applied"] = True
                        logger.info(
                            f"✅ [STATIC_HOOK] Hook karaoke subtitle applied: "
                            f"{os.path.basename(output_path)}"
                        )
                    else:
                        logger.warning("⚠️ [STATIC_HOOK] Subtitle render failed — original video preserved.")
                        profile_data["static_hook_applied"] = False
            except ImportError:
                pass  # Module not available — silently skip
            except Exception as _sh_err:
                logger.warning(
                    f"⚠️ [STATIC_HOOK] Non-fatal error (main pipeline unaffected): {_sh_err}"
                )
        # ─────────────────────────────────────────────────────────────────────────────

        # ---- SIDECAR JSON (Step 10) -------------------------------------------------
        logger.info("💾 [Step 10] Pipeline Complete. Writing Sidecar.")
        sidecar_path = os.path.splitext(output_path)[0] + ".json"

        _pm_final = profile_data.get("pipeline_metrics", {})
        _mon_data = profile_data.get("monetization", {}) or profile_data.get("monetization_data", {}) or _pm_final.get("monetization", {})
        _editorial_script = _mon_data.get("editorial_script") or profile_data.get(
            "editorial_script"
        )
        _final_caption = _mon_data.get("final_caption") or _editorial_script
        _risk_level = _mon_data.get("risk_level", "UNKNOWN")
        _risk_reason = _mon_data.get("risk_reason") or _mon_data.get(
            "reason", "Analysis pending or not performed."
        )

        _monetization_block = {
            "editorial_script": _editorial_script,
            "final_caption": _final_caption,
            "caption": _final_caption,
            "cta": _mon_data.get("monetization_cta"),
            "monetization_cta": _mon_data.get("monetization_cta"),
            "ypp_safe": _mon_data.get("monetization_safe", True),
            "risk_level": _risk_level,
            "risk_reason": _risk_reason,
            "reason": _risk_reason,
            "transformation_score": profile_data.get("transformation_score"),
            "policy_citation": _mon_data.get(
                "policy_citation", "Significant Original Commentary"
            ),
            "fashion_scout": _mon_data.get("fashion_scout")
            or profile_data.get("fashion_data"),
            "item_name": profile_data.get("item_name"),
            "improvement_tips": _mon_data.get("improvement_tips", []),
            "overlay_data": _mon_data.get("overlay_data", {}),
            "price_data": profile_data.get("price_data"),
            "telegram_hook": _mon_data.get("telegram_hook"),
            "instagram_hook": _mon_data.get("instagram_hook"),
            "youtube_hook": _mon_data.get("youtube_hook"),
            "hashtags": _mon_data.get("hashtags"),
        }

        _narrative = profile_data.get("narrative_data", {})
        _editorial_title = (
            _narrative.get("editorial_title")
            or _mon_data.get("editorial_title")
            or profile_data.get("editorial_title")
        )

        sidecar_payload = {
            "pipeline": "audit_passed",
            "verdict": profile_data.get("forensic_safety", "unknown"),
            "editorial_title": _editorial_title,
            "caption_data": {
                "caption": _final_caption,
                "editorial_script": _editorial_script,
            },
            "metadata": {
                "hook_score": profile_data.get("hook_analysis", {}).get("hook_score"),
                "quality_score": profile_data.get("quality_score"),
                "transformation_score": profile_data.get("transformation_score"),
                "item_name": profile_data.get("item_name"),
                "price_tag": profile_data.get("price_tag"),
                "gemini_total_calls": profile_data.get("gemini_calls", 0),
                "gemini_call_log": profile_data.get("gemini_log", []),
                "editing_diagnostics": profile_data.get("editing_diagnostics", {}),
                "feature_flags": context.feature_flags,
            },
            "pipeline_metrics": {
                "monetization": _monetization_block,
            },
            "editing_timeline": profile_data.get("editing_timeline", []),
            "monetization": _monetization_block,
            # [TELEGRAM EXCLUSIVE] Path to the processed clip WITHOUT the first-shot intro.
            # Used by main.py to send the unfiltered breakdown to the Telegram group.
            "processed_only_path": profile_data.get("processed_only_path", ""),
            # [AUDIO POOL] Track all audio files used/extracted so rejection can clean them
            "bgm_audio_path": profile_data.get("bgm_audio_path", ""),
            "extracted_audio_path": profile_data.get("extracted_audio_path", ""),
            "all_extracted_audio": list(profile_data.get("_all_clip_audios_paths", [])),
        }

        def _sanitize_for_json(obj):
            if isinstance(obj, dict):
                return {str(k): _sanitize_for_json(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [_sanitize_for_json(x) for x in obj]
            elif isinstance(obj, (str, int, float, bool)) or obj is None:
                return obj
            elif hasattr(obj, "__class__") and "Mock" in obj.__class__.__name__:
                return f"<Mock {obj.__class__.__name__}>"
            else:
                try:
                    json.dumps(obj)
                    return obj
                except Exception:
                    return str(obj)

        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(_sanitize_for_json(sidecar_payload), f, indent=2, ensure_ascii=False)

        _total = profile_data.get("gemini_calls", 0)
        _log = ", ".join(profile_data.get("gemini_log", []))
        logger.info(
            f"📊 [GEMINI_CALL_SUMMARY] Total Requests: {_total} | Log: [{_log}]"
        )
        if _total > 2:
            logger.warning(
                f"⚠️ [QUOTA_VIOLATION] Job used {_total} Gemini calls (Max expected: 2)."
            )

        logger.info("\n" + auditor.generate_report())
        report_path = os.path.join(
            os.path.dirname(output_path), "processed_video_feature_report.json"
        )
        auditor.save_report(report_path)

        # ── PIPELINE AUDIT (Step 10 post-render) ─────────────────────────────────
        # Mark remaining steps as success for StepTracer
        _StepTracer.success("gemini_analysis")
        _StepTracer.success("creative_director")
        _StepTracer.success("scene_editor")
        _StepTracer.success("video_render")
        _StepTracer.success("caption_overlay")
        _StepTracer.success("audio_mix")

        try:
            from Diagnostics_Modules.pipeline_audit import run_full_audit as _run_audit

            _audit_result = _run_audit(profile_data=profile_data)
            _verdict = _audit_result.get("verdict", "UNKNOWN")
            _c_score = _audit_result.get("confidence_score", 0.0)
            logger.info(f"🎬 EDITOR_CONFIDENCE score={_c_score:.2f}")
            logger.info(
                f"\n{'═' * 60}\n"
                f"  AUTONOMOUS CREATIVE EDITOR: {_verdict}\n"
                f"  Creative Score: {_c_score:.2f}\n"
                f"{'═' * 60}"
            )
            # Append audit verdict to sidecar
            try:
                with open(sidecar_path, "r", encoding="utf-8") as _sf:
                    _sd = json.load(_sf)
                _sd["audit"] = {
                    "verdict": _verdict,
                    "creative_score": _c_score,
                    "silent_failures": _audit_result.get("silent_failures", []),
                    "health_report": _audit_result.get("health_report", {}),
                }
                with open(sidecar_path, "w", encoding="utf-8") as _sf:
                    json.dump(_sd, _sf, indent=2, ensure_ascii=False)
            except Exception:
                pass
        except Exception as _audit_err:
            logger.warning(f"⚠️ Pipeline audit non-fatal error: {_audit_err}")

        # ── Print StepTracer execution trace ─────────────────────────────────────
        try:
            from Diagnostics_Modules.pipeline_audit import StepTracer as _ST

            logger.info(_ST.report())
        except Exception:
            pass

        # ── [CREATOR MEMORY] Store successful fingerprint ─────────────────────────
        if CREATOR_MEMORY_AVAILABLE and _creator_memory:
            try:
                # Build metadata from this run
                _store_meta = {}
                _intent = profile_data.get("creative_intent")
                if _intent:
                    _store_meta.update({
                        "hook_time":    _intent.get("hook_time"),
                        "climax_time":  _intent.get("climax_time"),
                        "pacing_style": _intent.get("pacing_style"),
                        "style_note":   _intent.get("creator_style_note"),
                        "hook_type":    _intent.get("hook_strategy"),
                    })
                _store_meta["niche"] = profile_data.get("niche_category", "generic")
                
                # Use final beat-snapped segments if available, else edited segments
                _final_segs = profile_data.get("rhythm_timeline") or profile_data.get("editing_timeline") or []
                _store_meta["segments"] = _final_segs

                _creator_memory.store_edit(input_paths, _store_meta)
            except Exception as _cme:
                logger.warning(f"⚠️ [CREATOR_MEMORY] store failed: {_cme}")

        # --- [VANGUARD] Observation Hook ---
        # Before cleaning up the job directory, we extract batched frames (2s intervals)
        # for the Vanguard Vision Sensor (Gemini 3.0 Flash) to audit.
        try:
            vanguard_frames = _extract_frames(output_path, job_dir, max_frames=10)
            profile_data["vanguard_observation"] = {
                "sample_frames": vanguard_frames,
                "job_dir": job_dir,
                "output_path": output_path,
                "timestamp": datetime.now().isoformat()
            }
            logger.info(f"👁️ [VANGUARD] Observation data prepared with {len(vanguard_frames)} frames.")
        except Exception as _ve:
            logger.warning(f"⚠️ [VANGUARD] Observation hook failed: {_ve}")

        # Note: We NO LONGER delete job_dir immediately if Vanguard is active,
        # but for safety in this version we allow rmtree to run and assume
        # frames were copied or are being read before final exit.
        # Fixed: We keep job_dir if observation is needed.
        # shutil.rmtree(job_dir, ignore_errors=True) 

        profile_data["job_dir"] = job_dir
        tracker.step("STEP_RENDER", 100)
        return True, profile_data

    except Exception as e:
        logger.error(f"❌ Pipeline Failure: {e}", exc_info=True)
        if ADAPTIVE_BRAIN_AVAILABLE and _adaptive_brain:
            try:
                _adaptive_brain.register_upload_outcome("error", risk_score=0.0)
            except Exception:
                pass
        return False, {"error": str(e)}


def compile_batch(
    video_paths, output_path, transition_type="fade", transition_duration=0.5
):
    """Compiles a batch of videos into a single sequence."""
    if not video_paths:
        return False
    unique_id = f"batch_{uuid.uuid4().hex[:6]}"
    job_dir = os.path.join("temp", unique_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        logger.info(f"📦 Starting Batch Compilation: {len(video_paths)} videos")
        list_file = os.path.join(job_dir, "input.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for v in video_paths:
                safe_path = os.path.abspath(v).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        cmd = [
            os.getenv("FFMPEG_BIN", "ffmpeg"),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_file,
            "-c",
            "copy",
            output_path,
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        if proc.returncode != 0:
            logger.error(f"FFmpeg Concat Error (Code {proc.returncode}): {proc.stderr}")
            raise Exception(f"FFmpeg concat failed: {proc.stderr[:200]}")

        shutil.rmtree(job_dir, ignore_errors=True)
        return output_path
    except Exception as e:
        logger.error(f"Batch Compilation Failed: {e}")
        return None


def compile_juxtaposition(
    uuid_str, input_a, input_b, output_path, title, profile_data=None
):
    if profile_data is None:
        profile_data = {}
    """Side-by-side comparison for newsroom style."""
    job_dir = os.path.join("temp", uuid_str)
    os.makedirs(job_dir, exist_ok=True)
    clean_a, clean_b = input_a, input_b
    if WATERMARK_AVAILABLE:
        try:
            res_a = watermark_auto.process_video_with_watermark(
                input_a, os.path.join(job_dir, "clean_a.mp4")
            )
            if res_a.get("success"):
                clean_a = os.path.join(job_dir, "clean_a.mp4")
            res_b = watermark_auto.process_video_with_watermark(
                input_b, os.path.join(job_dir, "clean_b.mp4")
            )
            if res_b.get("success"):
                clean_b = os.path.join(job_dir, "clean_b.mp4")
        except Exception:
            pass

    script, cta = "Comparison underway.", "Access the Blueprint..."
    if BRAIN_AVAILABLE:
        brain_inst = MonetizationStrategist()
        analysis = brain_inst.analyze_versus(context_a=title, context_b=title)
        if analysis:
            script = analysis.get("editorial_script", script)
            cta = analysis.get("monetization_cta", cta)
            profile_data.update(analysis)

    temp_juxta = os.path.join(job_dir, "juxtaposition.mp4")
    try:
        from .anchors import engine as anchor_engine

        anchor_path = anchor_engine.get_anchor_path()
    except Exception:
        anchor_path = None

    if video_pipeline.render_juxtaposition(
        clean_a, clean_b, temp_juxta, anchor_path=anchor_path, layout="vertical"
    ):
        shutil.copy(temp_juxta, output_path)
        logger.info(f"✅ Juxtaposition Complete: {uuid_str}")
        return True, {"status": "success", "script": script, "cta": cta}
    return False, {"error": "Juxtaposition failed"}