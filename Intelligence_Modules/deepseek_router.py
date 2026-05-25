"""
DeepSeek Reasoning Router
--------------------------
Primary engine for all reasoning/planning/repair/narrative tasks in AMTCE.
DeepSeek-R1 uses chain-of-thought internally and outperforms Gemini on:
  - FFmpeg repair command generation (structured code reasoning)
  - Narrative/story scripting (long coherent chains)
  - Editing plan generation (multi-step logic)

NOTE: DeepSeek is TEXT-ONLY. It is NOT used for:
  - Watermark detection (requires image input)
  - Caption from frames (requires image input)
  - Any multimodal task (Gemini handles those)

DeepSeek API is OpenAI-compatible: uses openai SDK with a custom base_url.
"""

import os
import logging
import time
import random
from typing import Optional, Any

logger = logging.getLogger("deepseek_router")

# Task types that DeepSeek handles (text-only reasoning)
DEEPSEEK_TASK_TYPES = {
    "reasoning",    # Turn 4 repair plans, editorial decisions
    "narrative",    # Story scripts, hooks
    "price",        # Price suggestion logic
    "analysis",     # Content analysis (text-based)
    "caption",      # Caption text generation (text-based, no image)
}

# Task types that MUST go to Gemini (require multimodal/vision)
GEMINI_ONLY_TASK_TYPES = {
    "vision",       # Frame analysis with images
    "watermark",    # Watermark detection with images
    "master",       # Vision verification with images
}


class DeepSeekRouter:
    """
    Lightweight router for DeepSeek R1 API.
    Wraps the OpenAI-compatible endpoint with retry logic.
    """

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner")
        self.base_url = "https://api.deepseek.com"
        self._client = None
        self._available = None  # None = not checked yet

        if not self.api_key:
            logger.warning("⚠️ [DEEPSEEK] No DEEPSEEK_API_KEY found. DeepSeek router disabled.")

    def _get_client(self):
        """Lazy-initialize the OpenAI-compatible client."""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url
                )
                logger.info(f"🧠 [DEEPSEEK] Client initialized | model={self.model}")
            except ImportError:
                logger.error("❌ [DEEPSEEK] 'openai' package not installed. Run: pip install openai")
                self._client = None
        return self._client

    def is_available(self) -> bool:
        """Check if DeepSeek is configured and reachable."""
        return bool(self.api_key) and self._get_client() is not None

    def can_handle(self, task_type: str) -> bool:
        """Return True if this task is text-only and DeepSeek can handle it."""
        return task_type in DEEPSEEK_TASK_TYPES

    def generate(
        self,
        prompt: Any,
        task_type: str = "reasoning",
        max_retries: int = 2,
    ) -> Optional[str]:
        """
        Call DeepSeek R1 with retry logic.
        Returns the generated text or None on failure.
        """
        if not self.api_key:
            return None

        client = self._get_client()
        if not client:
            return None

        # DeepSeek only handles text — skip if prompt contains non-string parts (images)
        if not isinstance(prompt, str):
            logger.debug("[DEEPSEEK] Non-text prompt detected. Skipping (Gemini handles vision).")
            return None

        for attempt in range(max_retries + 1):
            try:
                start = time.monotonic()
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an expert video editing AI and FFmpeg engineer. "
                                "Think step by step. Be precise and actionable."
                            )
                        },
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=2048,
                    temperature=0.6,
                )
                latency = time.monotonic() - start
                result = response.choices[0].message.content

                logger.info(
                    f"✅ [DEEPSEEK] Success | task={task_type} | "
                    f"model={self.model} | latency={latency:.2f}s | chars={len(result or '')}"
                )
                return result

            except Exception as e:
                err = str(e)
                logger.warning(f"⚠️ [DEEPSEEK] Attempt {attempt+1}/{max_retries+1} failed: {err}")

                if attempt < max_retries:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    logger.info(f"⏳ [DEEPSEEK] Backing off {backoff:.1f}s...")
                    time.sleep(backoff)

        logger.error(f"❌ [DEEPSEEK] All {max_retries+1} attempts failed for task='{task_type}'")
        return None


# Global singleton
deepseek_router = DeepSeekRouter()
