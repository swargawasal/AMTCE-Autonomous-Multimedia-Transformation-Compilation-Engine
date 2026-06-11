"""
UBI — Topic Predictor
=====================
Predicts the user's next question topic using their historical topic
distribution weighted by recency, then computes the embedding distance
between the prediction and the actual topic that arrived.

Sentence-transformers model used:
  all-MiniLM-L6-v2  — lightweight (~80MB), 384-dim, cosine-distance friendly.
  The model is loaded ONCE as a module-level singleton to avoid reload cost.

MSE is updated via ubi.mse_tracker after every interaction so the tracker
can detect if predictions are improving or deteriorating.
"""

import asyncio
import hashlib
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

# Lazy-load sentence-transformers to avoid startup cost if UBI is not called
_ST_MODEL = None
_ST_LOCK  = asyncio.Lock()


async def _get_model():
    """Lazy-load the SentenceTransformer singleton (thread-safe)."""
    global _ST_MODEL
    if _ST_MODEL is None:
        async with _ST_LOCK:
            if _ST_MODEL is None:           # double-checked lock
                from sentence_transformers import SentenceTransformer
                _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _ST_MODEL


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """1 – cosine_similarity, bounded [0, 2]. 0 = identical, 2 = opposite."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 1.0
    similarity = np.dot(a, b) / (norm_a * norm_b)
    return float(1.0 - similarity)


def _predict_next_topic(
    topic_distribution: Dict[str, int],
    top_topics: List[str],
    confidence_multiplier: float = 1.0,
) -> Tuple[str, float]:
    """
    Statistical next-topic prediction.

    Strategy:
      1. Convert topic distribution to probability weights.
      2. Select the topic with highest weighted probability.
      3. Confidence = weight_of_winner / sum(all_weights) * confidence_multiplier.

    Returns:
      (predicted_topic, confidence_score 0.0–1.0)
    """
    if not topic_distribution:
        return ("general", 0.1)

    total = sum(topic_distribution.values())
    weights = {t: c / total for t, c in topic_distribution.items()}
    best   = max(weights, key=weights.get)
    conf   = min(1.0, weights[best] * confidence_multiplier)

    return (best, round(conf, 4))


async def predict(
    user_id: str,
) -> Dict[str, Any]:
    """
    Generate a topic prediction for the given user based on their current profile.

    Returns:
        {
          predicted_topic:  str,
          confidence_score: float,   # 0.0 – 1.0
          top_topics:       List[str]
        }
    """
    from ubi.pattern_learner import get_user_profile

    profile  = await get_user_profile(user_id)
    dist     = profile.get("topic_distribution", {})
    tops     = profile.get("top_topics", [])
    conf_mul = profile.get("confidence_multiplier", 1.0)

    predicted, confidence = _predict_next_topic(dist, tops, conf_mul)

    return {
        "predicted_topic":  predicted,
        "confidence_score": confidence,
        "top_topics":       tops,
    }


async def measure_and_record(
    user_id: str,
    predicted_topic: str,
    actual_topic: str,
) -> Dict[str, Any]:
    """
    Compute embedding distance between predicted and actual topic,
    then forward the error to mse_tracker for MSE update.

    Returns:
        {
          embedding_distance: float,
          mse_result:         dict from mse_tracker
        }
    """
    from ubi.mse_tracker import record_prediction_error

    model = await _get_model()

    # Encode both topics in one batch call
    embeddings = model.encode([predicted_topic, actual_topic])
    distance   = _cosine_distance(embeddings[0], embeddings[1])

    # Forward to MSE tracker
    mse_result = await record_prediction_error(user_id, distance)

    return {
        "embedding_distance": round(distance, 6),
        "mse_result":         mse_result,
    }


async def classify_topic(text: str) -> str:
    """
    Quick topic classification of raw user input.
    Uses keyword heuristics for zero-cost routing.
    Falls back to nearest-neighbour embedding if no keyword matches.

    Returns a single topic string (lowercase, max 3 words).
    """
    text_lower = text.lower()

    KEYWORD_MAP = [
        # (keywords, topic)
        (["python", "pip", "venv", "django", "flask", "fastapi"],    "python"),
        (["javascript", "typescript", "react", "vue", "node"],        "javascript"),
        (["machine learning", "ml ", "deep learning", "neural",
          "gradient", "backprop", "transformer"],                     "machine learning"),
        (["kubernetes", "docker", "ci/cd", "devops", "github action"], "devops"),
        (["sql", "database", "postgres", "mongodb", "query"],         "database"),
        (["api", "rest", "graphql", "endpoint", "http"],              "api design"),
        (["security", "auth", "oauth", "jwt", "vulnerability"],       "security"),
        (["algorithm", "big o", "complexity", "leetcode", "sort"],    "algorithms"),
        (["llm", "gpt", "gemini", "mistral", "openai", "deepseek",
          "langchain", "langgraph"],                                   "llm engineering"),
        (["math", "calculus", "linear algebra", "probability",
          "statistics"],                                               "mathematics"),
        (["ui", "ux", "css", "html", "design", "figma", "tailwind"],  "frontend"),
        (["rust", "go ", "golang", "c++", "java "],                   "systems programming"),
        (["what is", "explain", "define", "tell me about"],           "general knowledge"),
    ]

    for keywords, topic in KEYWORD_MAP:
        if any(kw in text_lower for kw in keywords):
            return topic

    # Fallback: compare against topic labels using embeddings
    topic_labels = [t for _, t in KEYWORD_MAP]
    model        = await _get_model()
    input_emb    = model.encode([text])[0]
    label_embs   = model.encode(topic_labels)

    distances = [_cosine_distance(input_emb, le) for le in label_embs]
    best_idx  = int(np.argmin(distances))
    return topic_labels[best_idx]
