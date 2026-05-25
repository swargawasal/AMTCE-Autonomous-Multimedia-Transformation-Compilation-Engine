"""
creative_brain.py
─────────────────────────────────────────────────────────────────────────────
TWO-PASS CREATIVE BRAIN
Pass 1 (text-only, fast, cheap): Derives narrative INTENT from raw signal data.
  → What is the story? What is the hook? What is the emotional peak?

Pass 2 (used by unified_intelligence.py): Executes the cut plan WITH that intent
  → Instead of "pick highest-scored moments" it's "execute THIS story arc"

This mirrors how human editors actually work:
  Watch → Form intent → Execute with that intent in mind.

Usage in orchestrator.py:
    from Intelligence_Modules.creative_brain import CreativeBrain
    _creative_brain = CreativeBrain()
    creative_intent = _creative_brain.derive_intent(context, candidate_moments)
    _base_ctx["creative_intent"] = creative_intent
─────────────────────────────────────────────────────────────────────────────
"""

import base64
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("creative_brain")

# ── Intent Schema ─────────────────────────────────────────────────────────────
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative_theme":   {"type": "string"},
        "emotional_arc":     {"type": "string", "enum": ["rising", "falling", "spike", "constant", "complex"]},
        "hook_strategy":     {"type": "string"},
        "hook_time":         {"type": "number"},
        "climax_time":       {"type": "number"},
        "pacing_style":      {"type": "string", "enum": ["fast_cut", "slow_build", "rhythm_driven", "story_driven", "reaction_focused"]},
        "cut_philosophy":    {"type": "string"},
        "contrast_pairs":    {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_time":    {"type": "number"},
                    "reaction_time":  {"type": "number"},
                    "contrast_score": {"type": "number"},
                    "label":          {"type": "string"}
                }
            }
        },
        "avoid_segments":    {"type": "array", "items": {"type": "number"}},
        "creator_style_note":{"type": "string"},
        "confidence":        {"type": "number"}
    },
    "required": ["narrative_theme", "hook_time", "climax_time", "pacing_style", "cut_philosophy"]
}


class CreativeBrain:
    """
    Pass 1 of the two-pass creative pipeline.
    Text-only → fast → derives editorial intent before any cut decisions are made.
    """

    # ── Niche visual playbook ─────────────────────────────────────────────────
    # Each entry tells Gemini WHAT TO LOOK FOR visually — not what numbers to pick.
    # Format:  pacing_style | HOOK visual cue | CLIMAX visual cue
    _NICHE_GUIDE: Dict[str, str] = {
        "meme":      "fast_cut    | HOOK = setup/reaction face — widest eyes or most exaggerated expression. "
                     "CLIMAX = the punchline frame itself. Cut rate: max 12 cuts/min.",
        "reaction":  "fast_cut    | HOOK = reactor's face at the moment of shock — look for mouth open, "
                     "body jolt, or hand-to-face. CLIMAX = loudest visible reaction peak.",
        "gaming":    "fast_cut    | HOOK = highest-stakes visual — health bar near zero, clutch aim, "
                     "or countdown. CLIMAX = the win/fail reveal frame.",
        "fashion":   "slow_build  | HOOK = model entrance with clean, even lighting and strong eye contact. "
                     "CLIMAX = full outfit reveal close-up where the product label or texture is sharp.",
        "beauty":    "slow_build  | HOOK = before-state face in flat, honest light. "
                     "CLIMAX = after-state close-up — skin texture or colour payoff must be pixel-sharp.",
        "fitness":   "rhythm_driven | HOOK = peak-effort moment — veins, sweat, max contraction. "
                     "CLIMAX = result reveal or the rep that proves the point.",
        "podcast":   "story_driven  | HOOK = the speaker's face delivering the most controversial line "
                     "(look for leaned-in posture, pointed gesture). CLIMAX = the counter-argument or evidence moment.",
        "travel":    "slow_build    | HOOK = wide establishing shot with dramatic sky or crowd. "
                     "CLIMAX = the hero location reveal — frame must be crisp and colour-saturated.",
        "cooking":   "story_driven  | HOOK = the finished dish in perfect light. "
                     "CLIMAX = the single technique moment that explains how it was made.",
        "tutorial":  "story_driven  | HOOK = the problem being demonstrated (broken thing, wrong state). "
                     "CLIMAX = the exact moment the solution works — screen, hands, or face confirming it.",
        "sports":    "fast_cut      | HOOK = highest-motion frame — ball in air, athlete mid-stride. "
                     "CLIMAX = impact or score frame. Avoid celebration frames as hook (save for contrast).",
        "generic":   "rhythm_driven | HOOK = best-lit, sharpest frame in first 40% with direct eye contact "
                     "or peak action. CLIMAX = highest-energy frame in last 50%.",
    }

    def __init__(self):
        try:
            from Intelligence_Modules.gemini_governor import gemini_router
            self._router = gemini_router
        except ImportError:
            self._router = None
            logger.warning("[CreativeBrain] gemini_router unavailable — will return heuristic intent")

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def derive_intent(
        self,
        context: Dict[str, Any],
        candidate_moments: List[Dict],
        creator_fingerprint: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Pass 1: Derive narrative intent from signal data.

        Priority chain (each step falls back to next on failure):
          1. Vision-Augmented Gemini  — frames + text  (CREATIVE_BRAIN_VISION=yes)
          2. Text-Only Gemini         — signal table only
          3. Heuristic Fallback       — pure Python, zero cost
        """
        if not candidate_moments:
            return self._heuristic_fallback(context)

        intent = None

        if self._router:
            # ── Pass 1-A: Vision-Augmented (frames + text) ─────────────────
            use_vision = os.getenv("CREATIVE_BRAIN_VISION", "yes").lower() == "yes"
            video_path = context.get("video_path") or context.get("input_path", "")

            if use_vision and video_path and os.path.isfile(video_path):
                try:
                    intent = self._gemini_vision_intent(
                        context, candidate_moments, creator_fingerprint, video_path
                    )
                    if intent:
                        intent["_source"] = "gemini_vision"
                        logger.info(
                            f"🎬 [CREATIVE_BRAIN] Vision intent | "
                            f"theme='{intent.get('narrative_theme', '?')}' "
                            f"| hook={intent.get('hook_time', '?')} "
                            f"| climax={intent.get('climax_time', '?')} "
                            f"| pacing={intent.get('pacing_style', '?')}"
                        )
                except Exception as e:
                    logger.warning(f"[CreativeBrain] Vision Pass failed: {e} — trying text-only")

            # ── Pass 1-B: Text-Only Gemini fallback ────────────────────────
            if not intent:
                try:
                    intent = self._gemini_intent(
                        context, candidate_moments, creator_fingerprint
                    )
                    if intent:
                        intent["_source"] = "gemini_text"
                        logger.info(
                            f"🎬 [CREATIVE_BRAIN] Text intent | "
                            f"theme='{intent.get('narrative_theme', '?')}' "
                            f"| pacing={intent.get('pacing_style', '?')}"
                        )
                except Exception as e:
                    logger.warning(f"[CreativeBrain] Text Pass failed: {e} — using heuristic")

        # ── Pass 1-C: Heuristic fallback ──────────────────────────────────
        if not intent:
            intent = self._heuristic_fallback(context, candidate_moments)

        # ── [NZT_LOOP] Optional variant selection ─────────────────────────
        if os.getenv("NZT_LOOP", "no").lower() == "yes":
            try:
                from Intelligence_Modules.nzt_simulation_loop import NZTSimulationLoop
                intent = NZTSimulationLoop().select_best(
                    base_intent=intent,
                    context=context,
                    candidate_moments=candidate_moments,
                )
            except Exception as _nzt_e:
                logger.warning(f"[NZT_LOOP] Non-fatal error (returning base intent): {_nzt_e}")

        return intent

    # ─────────────────────────────────────────────────────────────────────────
    # FRAME EXTRACTION
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_key_frames(
        self,
        video_path: str,
        moments: List[Dict],
        max_frames: int = 5,
        max_dim: int = 512,
    ) -> List[Tuple[float, bytes]]:
        """
        Extract JPEG frames at the timestamps of the highest-scored moments.
        Returns list of (timestamp, jpeg_bytes) tuples.
        """
        try:
            import cv2  # type: ignore
        except ImportError:
            logger.warning("[CreativeBrain] cv2 not available — cannot extract frames.")
            return []

        # Pick top N moments by score, spread across the video timeline
        scored = sorted(
            moments,
            key=lambda m: float(m.get("score", m.get("rank_base", 0))),
            reverse=True,
        )
        # Always include earliest and latest among top candidates for arc coverage
        top = scored[:max_frames * 2]
        by_time = sorted(top, key=lambda m: float(m.get("time", m.get("timestamp", 0))))
        # Thin down to max_frames spread evenly across the sorted list
        step = max(1, len(by_time) // max_frames)
        selected = by_time[::step][:max_frames]

        cap = cv2.VideoCapture(video_path)
        results: List[Tuple[float, bytes]] = []
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            for m in selected:
                t = float(m.get("time", m.get("timestamp", 0)))
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                # Resize so longest edge ≤ max_dim  (keeps tokens low)
                h, w = frame.shape[:2]
                scale = min(max_dim / max(h, w, 1), 1.0)
                if scale < 1.0:
                    frame = cv2.resize(
                        frame,
                        (int(w * scale), int(h * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok2:
                    results.append((t, buf.tobytes()))
        finally:
            cap.release()

        logger.info(f"[CreativeBrain] Extracted {len(results)} key frames from {os.path.basename(video_path)}")
        return results

    def _build_vision_prompt(
        self,
        context: Dict,
        candidate_moments: List[Dict],
        creator_fingerprint: Optional[Dict],
        frames: List[Tuple[float, bytes]],
    ) -> list:
        """
        Build a multimodal prompt list: [image_part, image_part, ..., text_part].
        Compatible with genai.GenerativeModel.generate_content(list).
        """
        parts: list = []

        # Label + image pairs
        for i, (t, jpg) in enumerate(frames, 1):
            b64 = base64.b64encode(jpg).decode("utf-8")
            # Gemini SDK inline_data format accepted by generate_content
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
            parts.append(f"[Frame {i} @ {t:.2f}s]")

        # Text analytical prompt
        parts.append(self._build_intent_prompt(context, candidate_moments, creator_fingerprint))
        return parts

    # ─────────────────────────────────────────────────────────────────────────
    # GEMINI VISION PASS 1 (frames + text)
    # ─────────────────────────────────────────────────────────────────────────

    def _gemini_vision_intent(
        self,
        context: Dict,
        candidate_moments: List[Dict],
        creator_fingerprint: Optional[Dict],
        video_path: str,
    ) -> Optional[Dict]:
        """
        Vision-augmented Pass 1: send actual frames + signal table to Gemini.
        Gives the model real visual context so it can make genuine editorial
        decisions (colour, energy, composition, faces) instead of guessing
        from numbers alone.
        """
        frames = self._extract_key_frames(video_path, candidate_moments)
        if not frames:
            return None  # No frames → fall through to text-only

        prompt_parts = self._build_vision_prompt(
            context, candidate_moments, creator_fingerprint, frames
        )
        start = time.time()
        try:
            raw = self._router.generate(
                task_type="vision",          # vision tier → Flash preferred
                prompt=prompt_parts,
                module_name="creative_brain_vision",
                gen_config={
                    "temperature":        0.35,
                    "max_output_tokens":  1024,
                    "response_mime_type": "application/json",
                },
            )
            elapsed = time.time() - start
            raw = raw or ""
            logger.info(
                f"[CreativeBrain] Vision Gemini call {elapsed:.1f}s | "
                f"{len(frames)} frames | {len(raw)} chars response"
            )
            data = self._parse_json(raw)
            if not data or not isinstance(data, dict):
                return None
            return self._snap_timestamps(data, candidate_moments)
        except Exception as e:
            logger.warning(f"[CreativeBrain] _gemini_vision_intent error: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # GEMINI PASS 1 (text-only fallback)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_moment_table(self, moments: List[Dict]) -> str:
        """Format candidate moments as a compact table for the prompt."""
        lines = []
        for m in moments[:25]:
            t     = float(m.get("time",             m.get("timestamp", 0.0)))
            sc    = float(m.get("score",            m.get("rank_base", 0.0)))
            mot   = float(m.get("motion_intensity", m.get("motion",    0.0)))
            emo   = float(m.get("emotion_score",    m.get("emotion",   0.0)))
            face  = "👤" if m.get("face_present") else "  "
            expr  = f" expr_Δ={m['expression_change']:.2f}" if m.get("expression_change") else ""
            lines.append(f"  @{t:6.2f}s  score={sc:.3f}  motion={mot:.2f}  emotion={emo:.2f}  {face}{expr}")
        return "\n".join(lines)

    def _build_intent_prompt(
        self,
        context: Dict,
        candidate_moments: List[Dict],
        creator_fingerprint: Optional[Dict],
    ) -> str:
        title      = context.get("title", "Unknown")
        duration   = float(context.get("duration", 0.0))
        niche      = context.get("niche_category", "generic")
        clip_count = context.get("clip_count", 1)
        flow_qual  = context.get("flow_quality", "UNKNOWN")
        sem_str    = context.get("semantic_strength", "UNKNOWN")

        moment_table = self._build_moment_table(candidate_moments)
        niche_rule   = self._NICHE_GUIDE.get(niche, self._NICHE_GUIDE["generic"])

        creator_hint = ""
        if creator_fingerprint:
            creator_hint = (
                f"\n# CREATOR FINGERPRINT (from memory)\n"
                f"  Creator: {creator_fingerprint.get('creator_id', 'unknown')}\n"
                f"  Past successful hook type: {creator_fingerprint.get('hook_type', 'unknown')}\n"
                f"  Past avg cut duration: {creator_fingerprint.get('avg_cut_duration', '?')}s\n"
                f"  Most used transitions: {creator_fingerprint.get('transitions', [])}\n"
                f"  Niche signature: {creator_fingerprint.get('style_note', 'none')}\n"
                f"  INSTRUCTION: Prefer this creator's proven patterns where appropriate."
            )

        return f"""You are a WORLD-CLASS VIRAL EDITOR and PROFIT-MAXIMIZING STRATEGIST.
You have analyzed this video's metadata and attached visual frames. Your goal is to maximize RETENTION and AFFILIATE CONVERSIONS by weaponizing aesthetics.

# VIDEO BRIEF
Title: {title}
Duration: {duration:.1f}s | Clips: {clip_count} | Niche: {niche}
Semantic strength: {sem_str} | Flow quality: {flow_qual}

# AVAILABLE MOMENTS (timestamped signal data & frames)
{moment_table}
{creator_hint}

# YOUR TASK — HIGH-CONVERTING VISUAL INTENT
Analyze the attached frames alongside the numerical signal data. 
Do NOT just pick the highest numbers. Look at the actual images and answer these questions mentally:

1. VISUAL HOOK: Which frame has the most striking "Scroll-Stop" aesthetic? Look for perfect lighting, direct eye contact, extreme action, or a "curiosity gap".
2. PREMIUM CLIMAX: Which frame shows the clearest "WTF moment" or premium fashion/product reveal? This is the climax where we will trigger affiliate pop-ups.
3. BRAND SAFETY / AESTHETICS: Are there frames that are blurry, poorly lit, or visually boring that we MUST avoid?
4. NICHE-AWARE PROFIT PACING: This is a "{niche}" video. Follow this rule exactly:
   {niche_rule}
   How should cuts be timed to build anticipation for the premium climax WITHIN this niche's visual language?

OUTPUT: Valid JSON only. No markdown fences. Follow this schema exactly:
{{
  "narrative_theme": "Describe the core visual hook and premium value prop of the video.",
  "emotional_arc": "rising|falling|spike|constant|complex",
  "hook_strategy": "Explain EXACTLY why the visual composition of the chosen hook frame stops the scroll.",
  "hook_time": <float seconds — MUST be a moment from the table/frames above>,
  "climax_time": <float seconds — MUST be a moment from the table/frames above (best product reveal)>,
  "pacing_style": "fast_cut|slow_build|rhythm_driven|story_driven|reaction_focused",
  "cut_philosophy": "Actionable editing rule (e.g., 'Hold on the 4.2s frame for price tag drop, cut fast otherwise')",
  "contrast_pairs": [
    {{"action_time": <float>, "reaction_time": <float>, "contrast_score": <0.0-1.0>, "label": "description"}}
  ],
  "avoid_segments": [<float timestamps to skip (blurry, boring, bad lighting)>],
  "creator_style_note": "Any premium aesthetic signature observed.",
  "confidence": <0.0-1.0>
}}"""

    def _gemini_intent(
        self,
        context: Dict,
        candidate_moments: List[Dict],
        creator_fingerprint: Optional[Dict],
    ) -> Optional[Dict]:
        """Call Gemini (text-only) for Pass 1 editorial intent."""
        prompt = self._build_intent_prompt(context, candidate_moments, creator_fingerprint)
        start  = time.time()

        try:
            raw = self._router.generate(
                task_type="creative",
                prompt=prompt,
                module_name="creative_brain_intent",
                gen_config={
                    "temperature":        0.4,
                    "max_output_tokens":  1024,
                    "response_mime_type": "application/json",
                },
            )
            elapsed = time.time() - start
            raw = raw or ""
            logger.info(f"[CreativeBrain] Text-only Gemini call {elapsed:.1f}s | {len(raw)} chars")

            data = self._parse_json(raw)
            if not data or not isinstance(data, dict):
                return None

            return self._snap_timestamps(data, candidate_moments)

        except Exception as e:
            logger.warning(f"[CreativeBrain] _gemini_intent error: {e}")
            return None

    def _snap_timestamps(self, data: Dict, candidate_moments: List[Dict]) -> Dict:
        """Snap hook_time / climax_time to the nearest real candidate moment."""
        moment_times = {
            round(float(m.get("time", m.get("timestamp", 0))), 1)
            for m in candidate_moments
        }
        for field in ("hook_time", "climax_time"):
            val = data.get(field)
            if val is not None and moment_times:
                nearest = min(moment_times, key=lambda t: abs(t - float(val)))
                if abs(nearest - float(val)) <= 1.5:
                    data[field] = nearest
                else:
                    data[field] = min(moment_times)
        return data

    # ─────────────────────────────────────────────────────────────────────────
    # HEURISTIC FALLBACK (no Gemini needed)
    # ─────────────────────────────────────────────────────────────────────────

    def _heuristic_fallback(
        self,
        context: Dict,
        candidate_moments: Optional[List[Dict]] = None,
    ) -> Dict:
        """Pure-Python intent derivation when Gemini is unavailable."""
        moments = candidate_moments or []
        duration = float(context.get("duration", 60.0))

        # Best hook: highest-scored moment in first 40% of clip
        early_cutoff = duration * 0.40
        early = [m for m in moments if float(m.get("time", m.get("timestamp", 0))) <= early_cutoff]
        hook_candidates = sorted(early or moments, key=lambda m: float(m.get("score", m.get("rank_base", 0))), reverse=True)
        hook_time = float(hook_candidates[0].get("time", hook_candidates[0].get("timestamp", 0))) if hook_candidates else 1.5

        # Best climax: highest-scored moment in last 50%
        late_cutoff = duration * 0.50
        late = [m for m in moments if float(m.get("time", m.get("timestamp", 0))) >= late_cutoff]
        climax_candidates = sorted(late or moments, key=lambda m: float(m.get("score", m.get("rank_base", 0))), reverse=True)
        climax_time = float(climax_candidates[0].get("time", climax_candidates[0].get("timestamp", 0))) if climax_candidates else duration * 0.75

        # Detect contrast pairs: adjacent moments with large score gap
        contrast_pairs = []
        sorted_by_time = sorted(moments, key=lambda m: float(m.get("time", m.get("timestamp", 0))))
        for i in range(len(sorted_by_time) - 1):
            a = sorted_by_time[i]
            b = sorted_by_time[i + 1]
            ta = float(a.get("time", a.get("timestamp", 0)))
            tb = float(b.get("time", b.get("timestamp", 0)))
            sa = float(a.get("score", a.get("rank_base", 0)))
            sb = float(b.get("score", b.get("rank_base", 0)))
            gap = tb - ta
            score_contrast = abs(sa - sb)
            if 0.3 <= gap <= 4.0 and score_contrast >= 0.2:
                contrast_pairs.append({
                    "action_time":    ta,
                    "reaction_time":  tb,
                    "contrast_score": round(score_contrast, 3),
                    "label":          "score-contrast pair"
                })

        # Pacing style heuristic
        flow_quality = context.get("flow_quality", "UNKNOWN")
        niche = context.get("niche_category", "generic")
        if flow_quality == "HIGH" or niche in ("meme", "reaction"):
            pacing_style = "fast_cut"
        elif niche in ("podcast", "travel"):
            pacing_style = "slow_build"
        else:
            pacing_style = "rhythm_driven"

        return {
            "narrative_theme":    "Highlight the strongest visual moments in an engaging sequence",
            "emotional_arc":      "rising",
            "hook_strategy":      "Open on highest-energy early moment to stop the scroll",
            "hook_time":          hook_time,
            "climax_time":        climax_time,
            "pacing_style":       pacing_style,
            "cut_philosophy":     "Lead with energy, build tension, deliver payoff at climax",
            "contrast_pairs":     contrast_pairs[:5],
            "avoid_segments":     [],
            "creator_style_note": "",
            "confidence":         0.55,
            "_source":            "heuristic_fallback"
        }

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(raw: str) -> Optional[Dict]:
        """Extract and parse JSON from Gemini response. Handles markdown fences."""
        raw = raw.strip()
        # Strip markdown code fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        # Find first { … } block
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass
        return None
