"""
Router Orchestra — 4-Provider Lean Architecture
================================================
Providers:
  Groq     → Captions  (ultra-fast inference)
  Mistral  → Narrative, Reasoning (high volume, uncapped)
  Cerebras → Batch / price tasks (fast throughput)
  DeepSeek → Reasoning / analysis (uncapped)
  Gemini   → Vision/Watermark ONLY (multimodal — no substitute)

Design principles:
  1. Cache-first: hash(prompt + task_type) before any network call
  2. Global budget: MAX_TOTAL_AI_CALLS=7 across ALL providers per video
  3. Dynamic scoring: success_rate - latency_penalty (not static priority)
  4. Per-provider timeouts: Groq=3s, Mistral=8s
  5. Gemini is never called for text tasks — vision quota preserved
"""

import os
import hashlib
import logging
import time
import random
import threading
import json
from typing import Optional, Dict, Any

# ── LOAD ENV EARLY ─────────────────────────────────────────────────────────────
# RouterOrchestra is instantiated at module-load time as a singleton.
# dotenv MUST be loaded before that happens so API keys are available.
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Credentials", ".env")
    if os.path.exists(_env_path):
        _load_dotenv(_env_path, override=False)  # override=False: don't clobber already-set vars
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("router_orchestra")

# ── ROUTING TABLE ─────────────────────────────────────────────────────────────
# Balanced 4-provider load to prevent quota exhaustion on any single API
ROUTING = {
    "caption":    ["groq", "cerebras", "mistral", "deepseek"],
    "narrative":  ["mistral", "cerebras", "groq", "deepseek"],
    "reasoning":  ["mistral", "groq", "cerebras", "deepseek"],
    "price":      ["cerebras", "mistral", "groq", "deepseek"],
    "analysis":   ["mistral", "cerebras", "groq", "deepseek"],
    # TEXT tasks that were WRONGLY routing to Gemini (now fixed)
    "master":     ["mistral", "groq", "cerebras", "deepseek"],  # price/fashion/verify (TEXT, not vision)
    "creative":   ["groq", "mistral", "cerebras", "deepseek"],  # overlay captions, creative text
    # Vision tasks → Gemini ONLY. Never routed through text providers.
    "vision":     ["gemini"],
    "watermark":  ["gemini"],
}

# Per-provider timeouts — read lazily at call time, not at import time
# (so dotenv has had a chance to populate os.environ first)
def _get_timeout(provider: str) -> int:
    defaults = {"groq": 5, "cerebras": 20, "mistral": 30, "deepseek": 30, "gemini": 90}
    env_key = f"{provider.upper()}_TIMEOUT"
    return int(os.getenv(env_key, defaults.get(provider, 10)))

TIMEOUTS = {
    "groq":     5,
    "cerebras": 20,
    "mistral":  30,
    "deepseek": 30,
    "gemini":   90,
}


class ProviderHealth:
    """Tracks real-time health for one provider."""
    def __init__(self, name: str):
        self.name = name
        self.success_count = 0
        self.fail_count = 0
        self.total_latency = 0.0
        self.call_count = 0
        self.banned_until_epoch = 0.0
        self.disabled = False
        self._lock = threading.Lock()

    def record_success(self, latency: float):
        with self._lock:
            self.success_count += 1
            self.call_count += 1
            self.total_latency += latency
            self.banned_until_epoch = 0.0

    def record_failure(self, ban_seconds: float = 60.0, disabled: bool = False):
        with self._lock:
            self.fail_count += 1
            self.call_count += 1
            self.banned_until_epoch = time.time() + ban_seconds
            if disabled:
                self.disabled = True

    def is_available(self) -> bool:
        if self.disabled:
            return False
        return time.time() >= self.banned_until_epoch

    def score(self) -> float:
        """
        Dynamic score: higher = prefer this provider.
        score = success_rate * 100 - avg_latency_penalty
        """
        if self.call_count == 0:
            return 50.0  # optimistic default for untried providers
        success_rate = self.success_count / max(self.call_count, 1)
        avg_latency = self.total_latency / max(self.call_count, 1)
        
        # Treatment of DeepSeek as 'Bonus' (last resort)
        bonus_penalty = 40.0 if self.name == "deepseek" else 0.0
        
        return (success_rate * 100) - (avg_latency * 2) - bonus_penalty

    def report(self) -> dict:
        banned_remain = max(0, int(self.banned_until_epoch - time.time())) if not self.is_available() else 0
        return {
            "provider": self.name,
            "score": round(self.score(), 1),
            "success": self.success_count,
            "fail": self.fail_count,
            "avg_latency": round(self.total_latency / max(self.call_count, 1), 2),
            "available": self.is_available(),
            "banned_remain": banned_remain,
            "disabled": self.disabled
        }

    def to_dict(self):
        return {
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "total_latency": self.total_latency,
            "call_count": self.call_count,
            "banned_until_epoch": self.banned_until_epoch,
            "disabled": self.disabled
        }

    def from_dict(self, data: dict):
        self.success_count = data.get("success_count", 0)
        self.fail_count = data.get("fail_count", 0)
        self.total_latency = data.get("total_latency", 0.0)
        self.call_count = data.get("call_count", 0)
        self.banned_until_epoch = data.get("banned_until_epoch", 0.0)
        self.disabled = data.get("disabled", False)



class RouterOrchestra:
    """
    Central AI routing brain for AMTCE.
    Manages Groq, Mistral, and coordinates with GeminiGovernor for vision.
    """

    def __init__(self):
        self.CACHE_FILE = "logs/orchestra_cache.json"
        self.CACHE_TTL = 24 * 60 * 60  # 24 hours
        self.MAX_CACHE_ENTRIES = 500
        self._cache: Dict[str, Dict[str, Any]] = self._load_cache()
        self._global_call_count = 0
        self._global_budget = int(os.getenv("MAX_TOTAL_AI_CALLS", 7))
        self._lock = threading.Lock()

        # Provider health trackers
        self.STATE_FILE = "logs/orchestra_state.json"
        self.health: Dict[str, ProviderHealth] = {
            "groq":     ProviderHealth("groq"),
            "mistral":  ProviderHealth("mistral"),
            "cerebras": ProviderHealth("cerebras"),
            "deepseek": ProviderHealth("deepseek"),
            "gemini":   ProviderHealth("gemini"),
        }
        self._load_health_state()

        # Lazy-init clients (initialized on first use)
        self._groq_client = None
        self._mistral_client = None

        logger.info(
            f"🎼 [ORCHESTRA] Initialized | budget={self._global_budget} | "
            f"providers=DeepSeek,Cerebras,Mistral,Groq,Gemini"
        )

    def _load_cache(self) -> Dict[str, Dict]:
        try:
            if os.path.exists(self.CACHE_FILE):
                with open(self.CACHE_FILE, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}
        
    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.CACHE_FILE), exist_ok=True)
            with open(self.CACHE_FILE, "w") as f:
                json.dump(self._cache, f)
        except Exception as e:
            logger.warning(f"Failed to save orchestra cache: {e}")

    def _load_health_state(self):
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, "r") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        if k in self.health:
                            self.health[k].from_dict(v)
        except Exception as e:
            logger.warning(f"Failed to load orchestra health state: {e}")

    def _save_health_state(self):
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            state = {k: v.to_dict() for k, v in self.health.items()}
            with open(self.STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.warning(f"Failed to save orchestra health state: {e}")

    def _cache_key(self, prompt: str, task_type: str) -> str:
        # Strict hashing to prevent corruption
        raw = f"{task_type}:{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _from_cache(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._cache.get(key)
            if entry:
                # TTL Expiration Check
                if time.time() - entry.get("timestamp", 0) > self.CACHE_TTL:
                    del self._cache[key]
                    return None
                
                logger.info(f"⚡ [CACHE HIT] Saved 1 API call (Latency: 0.00s)")
                return entry.get("response")
        return None

    def _to_cache(self, key: str, value: str):
        with self._lock:
            # Enforce Max Entries (Evict Oldest)
            if len(self._cache) >= self.MAX_CACHE_ENTRIES:
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].get("timestamp", 0))
                del self._cache[oldest_key]
                
            self._cache[key] = {
                "response": value,
                "timestamp": time.time()
            }
            # Background save
            threading.Thread(target=self._save_cache, daemon=True).start()

    # ── Budget ───────────────────────────────────────────────────────────────

    def _check_and_consume_budget(self, provider: str, task_type: str) -> bool:
        """Returns True if budget allows. Gemini vision calls bypass global counter."""
        if provider == "gemini":
            return True  # Gemini budget managed by GeminiGovernor separately

        with self._lock:
            if self._global_call_count >= self._global_budget:
                logger.warning(
                    f"🚫 [ORCHESTRA] Global budget exhausted ({self._global_budget} calls). "
                    f"Skipping '{task_type}' on {provider}."
                )
                return False
            self._global_call_count += 1
            logger.info(
                f"📊 [ORCHESTRA] Call {self._global_call_count}/{self._global_budget} "
                f"| {provider} | task='{task_type}'"
            )
            return True

    def reset_video_budget(self):
        """Call at the start of each video to reset the global call counter."""
        with self._lock:
            self._global_call_count = 0
        logger.info(f"🎬 [ORCHESTRA] Budget reset for new video. Limit={self._global_budget}")

    # ── Provider Calls (Lightweight requests) ────────────────────────────────

    def _call_groq(self, prompt: str, task_type: str) -> Optional[str]:
        if self.health["groq"].disabled:
            return None
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            logger.debug("[GROQ] No API key — skipping")
            return None
            
        import requests
        url = "https://api.groq.com/openai/v1/chat/completions"
        model_env = os.getenv("GROQ_MODELS", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))
        models = [m.strip() for m in model_env.split(",") if m.strip()]
        timeout = _get_timeout("groq")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        
        for model in models:
            payload = {
                "model": model, 
                "messages": [{"role": "user", "content": prompt}], 
                "max_tokens": 1024
            }
            try:
                start = time.monotonic()
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                response.raise_for_status()
                latency = time.monotonic() - start
                result = response.json()["choices"][0]["message"]["content"]
                self.health["groq"].record_success(latency)
                self._save_health_state()
                logger.info(f"✅ [GROQ] task='{task_type}' model='{model}' | latency={latency:.2f}s")
                return result
            except requests.exceptions.RequestException as e:
                err = str(e).lower()
                response_code = getattr(e.response, "status_code", 0)
                
                if response_code == 403 or "403" in err:
                    logger.error(f"❌ [GROQ] ERROR 403: Forbidden for model '{model}'. Skipping.")
                    continue
                elif response_code == 404 or "404" in err:
                    logger.error(f"❌ [GROQ] ERROR 404: Not Found for model '{model}'. Skipping.")
                    continue
                elif response_code == 429 or "429" in err or "rate" in err:
                    logger.warning(f"⚠️ [GROQ] 429 Rate Limit. Banning for 120s.")
                    self.health["groq"].record_failure(120)
                    self._save_health_state()
                    return None
                elif "timeout" in err or "deadline" in err:
                    self.health["groq"].record_failure(30)
                    self._save_health_state()
                    return None
                else:
                    self.health["groq"].record_failure(60)
                    self._save_health_state()
                    return None

        logger.error(f"❌ [GROQ] All models exhausted or forbidden. Banning provider for 24h.")
        self.health["groq"].record_failure(86400)
        self._save_health_state()
        return None

    def _call_mistral(self, prompt: str, task_type: str) -> Optional[str]:
        if self.health["mistral"].disabled:
            return None
        api_key = os.getenv("MISTRAL_API_KEY", "")
        if not api_key:
            logger.debug("[MISTRAL] No API key — skipping")
            return None
            
        import requests
        url = "https://api.mistral.ai/v1/chat/completions"
        model_env = os.getenv("MISTRAL_MODELS", os.getenv("MISTRAL_MODEL", "mistral-large-latest"))
        models = [m.strip() for m in model_env.split(",") if m.strip()]
        timeout = _get_timeout("mistral")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        
        for model in models:
            payload = {
                "model": model, 
                "messages": [{"role": "user", "content": prompt}], 
                "max_tokens": 1024
            }
            try:
                start = time.monotonic()
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                response.raise_for_status()
                latency = time.monotonic() - start
                result = response.json()["choices"][0]["message"]["content"]
                self.health["mistral"].record_success(latency)
                self._save_health_state()
                logger.info(f"✅ [MISTRAL] task='{task_type}' model='{model}' | latency={latency:.2f}s")
                return result
            except requests.exceptions.RequestException as e:
                err = str(e).lower()
                response_code = getattr(e.response, "status_code", 0)
                
                if response_code == 403 or "403" in err:
                    continue
                elif response_code == 404 or "404" in err:
                    continue
                elif response_code == 429 or "429" in err or "rate" in err:
                    self.health["mistral"].record_failure(120)
                    self._save_health_state()
                    return None
                elif "timeout" in err or "deadline" in err:
                    self.health["mistral"].record_failure(30)
                    self._save_health_state()
                    return None
                else:
                    self.health["mistral"].record_failure(60)
                    self._save_health_state()
                    return None

        self.health["mistral"].record_failure(86400)
        self._save_health_state()
        return None

    def _call_cerebras(self, prompt: str, task_type: str) -> Optional[str]:
        if self.health["cerebras"].disabled:
            return None
        api_key = os.getenv("CEREBRAS_API_KEY", "")
        if not api_key:
            logger.debug("[CEREBRAS] No API key — skipping")
            return None
            
        import requests
        url = "https://api.cerebras.ai/v1/chat/completions"
        model_env = os.getenv("CEREBRAS_MODELS", os.getenv("CEREBRAS_MODEL", "llama-3.3-70b"))
        models = [m.strip() for m in model_env.split(",") if m.strip()]
        timeout = _get_timeout("cerebras")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        
        for model in models:
            payload = {
                "model": model, 
                "messages": [{"role": "user", "content": prompt}], 
                "max_tokens": 1024
            }
            try:
                start = time.monotonic()
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                response.raise_for_status()
                latency = time.monotonic() - start
                result = response.json()["choices"][0]["message"]["content"]
                self.health["cerebras"].record_success(latency)
                self._save_health_state()
                logger.info(f"✅ [CEREBRAS] task='{task_type}' model='{model}' | latency={latency:.2f}s")
                return result
            except requests.exceptions.RequestException as e:
                err = str(e).lower()
                response_code = getattr(e.response, "status_code", 0)
                
                if response_code == 403 or "403" in err:
                    continue
                elif response_code == 404 or "404" in err:
                    continue
                elif response_code == 429 or "429" in err or "rate" in err:
                    self.health["cerebras"].record_failure(120)
                    self._save_health_state()
                    return None
                elif "timeout" in err or "deadline" in err:
                    self.health["cerebras"].record_failure(30)
                    self._save_health_state()
                    return None
                else:
                    self.health["cerebras"].record_failure(60)
                    self._save_health_state()
                    return None

        self.health["cerebras"].record_failure(86400)
        self._save_health_state()
        return None

    def _call_deepseek(self, prompt: str, task_type: str) -> Optional[str]:
        if self.health["deepseek"].disabled:
            return None
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            logger.debug("[DEEPSEEK] No API key — skipping")
            return None
            
        import requests
        url = "https://api.deepseek.com/chat/completions"
        # Use deepseek-chat for text tasks (faster/cheaper); deepseek-reasoner for reasoning
        default_model = "deepseek-chat" if task_type not in ("reasoning",) else "deepseek-reasoner"
        model_env = os.getenv("DEEPSEEK_MODELS", os.getenv("DEEPSEEK_MODEL", default_model))
        models = [m.strip() for m in model_env.split(",") if m.strip()]
        timeout = _get_timeout("deepseek")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        
        for model in models:
            payload = {
                "model": model, 
                "messages": [{"role": "user", "content": prompt}], 
                "max_tokens": 2048
            }
            try:
                start = time.monotonic()
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                response.raise_for_status()
                latency = time.monotonic() - start
                choice = response.json()["choices"][0]["message"]
                result = choice.get("content", "")
                if not result and "reasoning_content" in choice:
                    result = choice["reasoning_content"]
                    
                self.health["deepseek"].record_success(latency)
                self._save_health_state()
                logger.info(f"✅ [DEEPSEEK] task='{task_type}' model='{model}' | latency={latency:.2f}s")
                return result
            except requests.exceptions.RequestException as e:
                err = str(e).lower()
                response_code = getattr(e.response, "status_code", 0)
                
                if response_code == 402 or "402" in err:
                    logger.error(f"❌ [DEEPSEEK] ERROR 402: Payment Required. Banning for 1 hour.")
                    self.health["deepseek"].record_failure(3600)
                    self._save_health_state()
                    return None
                elif response_code == 403 or "403" in err:
                    continue
                elif response_code == 404 or "404" in err:
                    continue
                elif response_code == 429 or "429" in err or "rate" in err:
                    self.health["deepseek"].record_failure(120)
                    self._save_health_state()
                    return None
                elif "timeout" in err or "deadline" in err:
                    self.health["deepseek"].record_failure(30)
                    self._save_health_state()
                    return None
                else:
                    self.health["deepseek"].record_failure(60)
                    self._save_health_state()
                    return None
                    
        self.health["deepseek"].record_failure(86400)
        self._save_health_state()
        return None

    # ── Main Route ───────────────────────────────────────────────────────────

    def route(
        self, 
        prompt: str, 
        task_type: str,
        visual_context: Optional[str] = None
    ) -> Optional[str]:
        """
        Main entry point. Cache → Budget → Dynamic provider selection → Fallback.
        Vision tasks return None (handled by GeminiGovernor directly).
        """
        # Vision tasks: skip orchestra, let GeminiGovernor handle
        # Master and creative are text logic tasks, they MUST be processed here
        if task_type in ("vision", "watermark"):
            return None  # Signal to caller: use Gemini

        if visual_context:
            prompt = f"[SCENE CONTEXT — extracted by vision AI]\n{visual_context}\n\n---\n{prompt}"

        # 1. Cache check (saves 30-50% calls)
        key = self._cache_key(prompt if isinstance(prompt, str) else str(prompt)[:500], task_type)
        cached = self._from_cache(key)
        if cached:
            return cached

        # 2. Get provider chain for this task type
        provider_chain = ROUTING.get(task_type, ["mistral", "groq"])

        # 3. Sort by dynamic score (best provider first)
        available = [
            p for p in provider_chain
            if self.health.get(p, ProviderHealth(p)).is_available()
        ]
        available.sort(key=lambda p: self.health.get(p, ProviderHealth(p)).score(), reverse=True)

        if not available:
            logger.warning(f"⚠️ [ORCHESTRA] All providers unavailable for '{task_type}'. Deferring to Gemini.")
            return None

        # 4. Try providers in scored order
        for provider in available:
            if not self._check_and_consume_budget(provider, task_type):
                return None  # Budget exhausted

            result = None
            if provider == "groq":
                result = self._call_groq(prompt, task_type)
            elif provider == "mistral":
                result = self._call_mistral(prompt, task_type)
            elif provider == "cerebras":
                result = self._call_cerebras(prompt, task_type)
            elif provider == "deepseek":
                result = self._call_deepseek(prompt, task_type)

            if result:
                self._to_cache(key, result)
                return result

        logger.warning(f"⚠️ [ORCHESTRA] All providers failed for '{task_type}'. Deferring to Gemini fallback.")
        return None

    # ── Health Report ────────────────────────────────────────────────────────

    def health_report(self):
        print("\n" + "═" * 50)
        print("🎼 [ROUTER ORCHESTRA] Health Report")
        print(f"   Global Budget: {self._global_call_count}/{self._global_budget} calls used")
        print(f"   Cache size: {len(self._cache)} entries")
        print("\n   Provider Status:")
        for name, h in self.health.items():
            r = h.report()
            if r["disabled"]:
                status = "🔴 PERM BANNED"
            elif r["available"]:
                status = "✅ ACTIVE"
            else:
                status = f"🚫 BANNED ({r['banned_remain']}s)"
            print(f"   - {name:10}: {status} | score={r['score']} | "
                  f"ok={r['success']} fail={r['fail']} | avg={r['avg_latency']}s")
        print("═" * 50 + "\n")


# Global singleton
orchestra = RouterOrchestra()
