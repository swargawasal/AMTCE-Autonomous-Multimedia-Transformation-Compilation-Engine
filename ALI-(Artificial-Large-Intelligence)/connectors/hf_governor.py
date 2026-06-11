"""
HFGovernor — Hugging Face Model Router v1.0
============================================
Mirrors the GeminiGovernor pattern for HuggingFace Serverless Inference API.

Key features:
  - Capability-based task routing (reasoning, coding, synthesis, emotion, safety, classify)
  - Weighted scoring engine: success rate, latency, task boost, freshness, cold-start
  - Automatic ban/cooldown on 429 (per-model rate limit) with tiered durations
  - Per-task failure memory (avoids re-using a model that just failed this task)
  - Retry-aware: caller passes exclude_models to skip already-failed models
  - Multi-token pool rotation (optional: set HF_TOKEN_2, HF_TOKEN_3 in .env)
  - Singleton pattern (one governor per process lifetime)
"""

import os
import time
import random
import logging
import threading

from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hf_governor")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ══════════════════════════════════════════════════════════════════════════════
# MODEL REGISTRY
#
# Each model entry carries:
#   tier     : "large" | "medium" | "small"  — used for cost-weight calculation
#   strengths: list of task_types this model excels at → used for default boost
# ══════════════════════════════════════════════════════════════════════════════
HF_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── REASONING & GENERAL PURPOSE (70B+ class) ────────────────────────────
    "Qwen/Qwen2.5-72B-Instruct": {
        "tier": "large",
        "description": "Alibaba's SOTA 72B — top reasoning, math, multilingual",
        "strengths": ["ali_reasoning", "ali_synthesis", "ali_classify"],
    },
    "meta-llama/Llama-3.3-70B-Instruct": {
        "tier": "large",
        "description": "Meta Llama 3.3 70B — robust logic, instruction-following, agents",
        "strengths": ["ali_reasoning", "ali_synthesis", "ali_emotion"],
    },
    "mistralai/Mixtral-8x22B-Instruct-v0.1": {
        "tier": "large",
        "description": "Mistral MoE 141B — fast inference, French/EN bilingual, logic",
        "strengths": ["ali_reasoning", "ali_classify", "ali_synthesis"],
    },
    "CohereForAI/c4ai-command-r-plus": {
        "tier": "large",
        "description": "Cohere 104B — RAG, tool use, multi-step agent orchestration",
        "strengths": ["ali_reasoning", "ali_synthesis"],
    },

    # ── ELITE CODING SPECIALISTS ─────────────────────────────────────────────
    "Qwen/Qwen2.5-Coder-32B-Instruct": {
        "tier": "medium",
        "description": "King of open-source coding — beats GPT-4o on HumanEval/LiveCodeBench",
        "strengths": ["ali_coding", "ali_reasoning"],
    },
    "codellama/CodeLlama-70b-Instruct-hf": {
        "tier": "large",
        "description": "Meta CodeLlama 70B — Python, shell, multi-file code editing",
        "strengths": ["ali_coding"],
    },
    "Qwen/Qwen2.5-Coder-14B-Instruct": {
        "tier": "medium",
        "description": "Blazing fast 14B coder — ~90% of 32B at 3x speed",
        "strengths": ["ali_coding", "ali_classify"],
    },

    # ── EMOTION / NARRATIVE ──────────────────────────────────────────────────
    "mistralai/Mistral-7B-Instruct-v0.3": {
        "tier": "small",
        "description": "Mistral 7B — fast, emotion-friendly, creative generation",
        "strengths": ["ali_emotion", "ali_classify", "ali_safety"],
    },

    # ── SAFETY / CLASSIFICATION (fast, lightweight) ──────────────────────────
    "microsoft/Phi-3.5-mini-instruct": {
        "tier": "small",
        "description": "Microsoft 3.8B — huge context (128k), fast safety checks",
        "strengths": ["ali_safety", "ali_classify"],
    },
    "meta-llama/Llama-3.1-8B-Instruct": {
        "tier": "small",
        "description": "Meta 8B — fastest capable model, great all-rounder",
        "strengths": ["ali_safety", "ali_classify", "ali_emotion"],
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# TASK BOOST TABLE
#
# Per-task per-model float multiplier applied on top of the base success score.
# Higher = stronger preference for this model on this task.
# Models NOT listed get a default boost of 1.0.
# ══════════════════════════════════════════════════════════════════════════════
TASK_BOOSTS: Dict[str, Dict[str, float]] = {

    # ── REASONING ────────────────────────────────────────────────────────────
    "ali_reasoning": {
        "Qwen/Qwen2.5-72B-Instruct":               4.5,
        "meta-llama/Llama-3.3-70B-Instruct":       4.3,
        "mistralai/Mixtral-8x22B-Instruct-v0.1":   4.0,
        "CohereForAI/c4ai-command-r-plus":          3.8,
        "Qwen/Qwen2.5-Coder-32B-Instruct":          2.5,
    },

    # ── CODING ───────────────────────────────────────────────────────────────
    "ali_coding": {
        "Qwen/Qwen2.5-Coder-32B-Instruct":          5.0,
        "Qwen/Qwen2.5-Coder-14B-Instruct":          4.5,
        "codellama/CodeLlama-70b-Instruct-hf":      4.2,
        "Qwen/Qwen2.5-72B-Instruct":               3.0,
        "meta-llama/Llama-3.3-70B-Instruct":       2.8,
    },

    # ── SYNTHESIS / LONG-FORM WRITING ────────────────────────────────────────
    "ali_synthesis": {
        "meta-llama/Llama-3.3-70B-Instruct":       4.5,
        "Qwen/Qwen2.5-72B-Instruct":               4.3,
        "mistralai/Mixtral-8x22B-Instruct-v0.1":   4.0,
        "CohereForAI/c4ai-command-r-plus":          3.8,
    },

    # ── EMOTION / NARRATIVE / CREATIVE ───────────────────────────────────────
    "ali_emotion": {
        "meta-llama/Llama-3.3-70B-Instruct":       4.5,
        "mistralai/Mistral-7B-Instruct-v0.3":      4.2,
        "meta-llama/Llama-3.1-8B-Instruct":        3.5,
        "Qwen/Qwen2.5-72B-Instruct":               3.0,
    },

    # ── SAFETY / CONTENT VALIDATION ──────────────────────────────────────────
    "ali_safety": {
        "microsoft/Phi-3.5-mini-instruct":          5.0,
        "meta-llama/Llama-3.1-8B-Instruct":        4.5,
        "mistralai/Mistral-7B-Instruct-v0.3":      4.0,
        "Qwen/Qwen2.5-Coder-14B-Instruct":          3.0,
    },

    # ── CLASSIFY / ROUTING DECISIONS ─────────────────────────────────────────
    "ali_classify": {
        "microsoft/Phi-3.5-mini-instruct":          5.0,
        "meta-llama/Llama-3.1-8B-Instruct":        4.5,
        "mistralai/Mistral-7B-Instruct-v0.3":      4.2,
        "Qwen/Qwen2.5-Coder-14B-Instruct":          3.8,
        "Qwen/Qwen2.5-72B-Instruct":               3.0,
    },
}

# Cost weights — inverse multiplier (cheaper = higher multiplier when budget is tight)
TIER_COST_WEIGHTS = {
    "large":  1.0,
    "medium": 0.7,
    "small":  0.3,
}


# ══════════════════════════════════════════════════════════════════════════════
# HFGovernor — Singleton Router
# ══════════════════════════════════════════════════════════════════════════════
class HFGovernor:
    """
    Intelligent HuggingFace model router.

    Selects the best available model per task using:
      1. Task boost table (capability-aware preference)
      2. Live success rate (decaying EMA)
      3. Latency penalty
      4. Freshness bonus (avoids overloading one model)
      5. Cold-start penalty (new/untested models)
      6. Automatic ban with tiered cooldown on 429 or errors
      7. Per-task failure memory (MEMORY_TTL seconds)
      8. Multi-token pool rotation (HF_TOKEN, HF_TOKEN_2, HF_TOKEN_3...)
    """

    _instance: Optional["HFGovernor"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "HFGovernor":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self.state_lock  = threading.Lock()
        self.memory_lock = threading.Lock()

        # Governor tuning (can be overridden via env vars)
        self.DECAY       = float(os.getenv("HF_GOV_DECAY",      "0.99"))
        self.JITTER      = float(os.getenv("HF_GOV_JITTER",     "0.03"))
        self.MEMORY_TTL  = int(os.getenv("HF_GOV_MEMORY_TTL",  "120"))

        # Model state registry
        self.model_states: Dict[str, Dict[str, Any]] = {}
        self.recent_task_failures: Dict[tuple, float] = {}

        # Token pool (supports HF_TOKEN, HF_TOKEN_2, HF_TOKEN_3 ...)
        self.token_pool: List[str] = []
        self._token_index: int = 0
        self._token_lock  = threading.Lock()
        self._load_token_pool()

        # Initialize model states
        for model_id in HF_MODEL_REGISTRY:
            self.model_states[model_id] = {
                "status":               "ACTIVE",
                "ban_remaining_seconds": 0.0,
                "success_count":         5.0,  # optimistic start
                "total_calls":           5.0,
                "fail_count":            0.0,
                "429_count":             0,
                "avg_latency":           1.0,
                "last_used_at":          0.0,
                "last_time_check":       time.monotonic(),
            }

        logger.info(
            f"🧠 [HFGovernor] Initialized with {len(self.model_states)} models "
            f"and {len(self.token_pool)} token(s)."
        )

    # ── Token Pool ─────────────────────────────────────────────────────────────
    def _load_token_pool(self):
        """Load all HF_TOKEN, HF_TOKEN_2, HF_TOKEN_3... from environment."""
        base = os.getenv("HF_TOKEN", "")
        if base:
            self.token_pool.append(base)
        for i in range(2, 10):
            t = os.getenv(f"HF_TOKEN_{i}", "")
            if t:
                self.token_pool.append(t)

        if not self.token_pool:
            logger.warning("⚠️ [HFGovernor] No HF_TOKEN found in environment.")

    def get_next_token(self) -> Optional[str]:
        """Round-robin across the token pool."""
        with self._token_lock:
            if not self.token_pool:
                return None
            token = self.token_pool[self._token_index % len(self.token_pool)]
            self._token_index += 1
            return token

    # ── Ban Timer ──────────────────────────────────────────────────────────────
    def _tick_bans(self):
        """Decrement ban timers (call under state_lock)."""
        now = time.monotonic()
        for state in self.model_states.values():
            if state["status"] == "BANNED":
                elapsed = now - state.get("last_time_check", now)
                state["last_time_check"] = now
                rem = state["ban_remaining_seconds"] - elapsed
                if rem <= 0:
                    state.update({
                        "status":               "ACTIVE",
                        "ban_remaining_seconds": 0.0,
                    })
                    logger.info(f"✅ [HFGovernor] Ban expired — model re-activated.")
                else:
                    state["ban_remaining_seconds"] = rem

    # ── Ban a Model ─────────────────────────────────────────────────────────────
    def mark_model_banned(self, model_id: str, error_type: str = "429",
                          task_type: Optional[str] = None, seconds: Optional[int] = None):
        """Ban a model with tiered cooldown. Record per-task failure memory."""
        with self.state_lock:
            self._tick_bans()
            state = self.model_states.get(model_id)
            if not state:
                return

            state["fail_count"] = state.get("fail_count", 0) + 1

            if seconds is not None:
                # Manual override
                duration = seconds
            elif error_type == "429":
                state["429_count"] = state.get("429_count", 0) + 1
                count = state["429_count"]
                duration = 45 if count == 1 else (90 if count == 2 else 240)
            elif error_type == "timeout":
                duration = 30
            elif error_type == "5xx":
                duration = 60
            else:
                duration = 45

            state["status"]               = "BANNED"
            state["ban_remaining_seconds"] = float(duration)
            state["last_time_check"]       = time.monotonic()

            logger.warning(
                f"🚫 [HFGovernor] {model_id.split('/')[-1]} BANNED "
                f"for {duration}s ({error_type})"
            )

        # Record per-task failure memory
        if task_type:
            with self.memory_lock:
                self.recent_task_failures[(model_id, task_type)] = time.monotonic()

    # ── Record Success ─────────────────────────────────────────────────────────
    def record_success(self, model_id: str, latency: float):
        """Update running stats after a successful call."""
        with self.state_lock:
            state = self.model_states.get(model_id)
            if not state:
                return
            state["success_count"]  = state.get("success_count", 0) + 1
            state["total_calls"]    = state.get("total_calls",   0) + 1
            state["last_used_at"]   = time.monotonic()
            # EMA latency (α = 0.3)
            prev = state.get("avg_latency", latency)
            state["avg_latency"]    = 0.7 * prev + 0.3 * latency
            # Reset 429 streak on success
            state["429_count"]      = 0

    # ── Core Scoring Engine ────────────────────────────────────────────────────
    def get_available_model(
        self,
        task_type: str = "ali_reasoning",
        exclude_models: Optional[List[str]] = None,
        force_tier: Optional[str] = None,
    ) -> Optional[str]:
        """
        World-class weighted model selection.

        Args:
            task_type:      One of the ALI task types (ali_reasoning, ali_coding, etc.)
            exclude_models: Models to skip in this routing call (retry loop pattern)
            force_tier:     "small" | "medium" | "large" — restrict to a cost tier

        Returns:
            Model repo ID string, or None if all models are banned.
        """
        exclude_set = set(exclude_models or [])
        task_boost  = TASK_BOOSTS.get(task_type, {})
        now         = time.monotonic()

        best_model = None
        max_score  = -float("inf")

        with self.state_lock:
            self._tick_bans()

            for model_id, state in self.model_states.items():

                # 1. Skip banned models
                if state["status"] == "BANNED":
                    continue

                # 2. Skip caller-excluded models (retry loop)
                if model_id in exclude_set:
                    continue

                # 3. Skip by tier if forced
                tier = HF_MODEL_REGISTRY.get(model_id, {}).get("tier", "medium")
                if force_tier and tier != force_tier:
                    continue

                # 4. Skip models that recently failed this specific task
                with self.memory_lock:
                    fail_time = self.recent_task_failures.get((model_id, task_type))
                    if fail_time and (now - fail_time) < self.MEMORY_TTL:
                        continue

                # ── Scoring ────────────────────────────────────────────────
                total        = max(state["total_calls"], 0.001)
                success_rate = state["success_count"] / total

                # Decay EMA
                state["total_calls"]   *= self.DECAY
                state["success_count"] *= self.DECAY

                # Reliability penalty
                fail_count        = state.get("fail_count", 0)
                reliability_pen   = fail_count * 10.0

                # Latency penalty
                latency_pen       = state.get("avg_latency", 1.0) * 0.4

                # Freshness bonus
                last_used = state.get("last_used_at", 0)
                freshness = min(1.0, (now - last_used) / 300) if last_used > 0 else 1.0

                # Cost-inverse weight (cheaper models score higher when equivalent)
                cost_inv = 1.0 / TIER_COST_WEIGHTS.get(tier, 0.7)

                # Task boost
                boost = task_boost.get(model_id, 1.0)

                # Jitter (prevents thundering herd)
                jitter = random.uniform(-self.JITTER, self.JITTER)

                # Final score
                base_score = (
                    (success_rate * 50.0)
                    - reliability_pen
                    - latency_pen
                    + (freshness * 5.0)
                )
                final_score = (base_score * boost * cost_inv) + jitter

                if final_score > max_score:
                    max_score  = final_score
                    best_model = model_id

        if best_model:
            logger.info(
                f"🎯 [HFGovernor] Selected: {best_model.split('/')[-1]} "
                f"(task={task_type}, score={max_score:.2f})"
            )
            with self.state_lock:
                self.model_states[best_model]["last_used_at"] = time.monotonic()
                self.model_states[best_model]["total_calls"]  += 1

        return best_model

    # ── Diagnostics ────────────────────────────────────────────────────────────
    def status_report(self) -> Dict[str, Any]:
        """Return a snapshot of all model states for debugging."""
        with self.state_lock:
            self._tick_bans()
            report = {}
            for model_id, state in self.model_states.items():
                total    = max(state["total_calls"], 0.001)
                report[model_id] = {
                    "status":        state["status"],
                    "success_rate":  f"{state['success_count'] / total:.2%}",
                    "avg_latency":   f"{state.get('avg_latency', 0):.2f}s",
                    "429_count":     state.get("429_count", 0),
                    "ban_remaining": f"{state.get('ban_remaining_seconds', 0):.0f}s"
                                     if state["status"] == "BANNED" else "—",
                }
        return report

    def list_models_by_task(self, task_type: str) -> List[Dict[str, Any]]:
        """Return models ranked by their boost score for a given task, with metadata."""
        boosts = TASK_BOOSTS.get(task_type, {})
        result = []
        for model_id, meta in HF_MODEL_REGISTRY.items():
            boost = boosts.get(model_id, 1.0)
            result.append({
                "model":       model_id,
                "tier":        meta["tier"],
                "description": meta["description"],
                "boost":       boost,
            })
        result.sort(key=lambda x: x["boost"], reverse=True)
        return result
