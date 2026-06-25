"""Cross-encoder reranker for improving retrieval relevance."""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Default reranker model — can be overridden via environment variable
DEFAULT_RERANKER_MODEL = os.getenv(
    "RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"
)


class RerankerService:
    """Singleton reranker service with lazy model loading.

    Uses a Cross-Encoder model to re-score (query, document) pairs
    for more accurate relevance ranking than bi-encoder cosine similarity.
    The model is downloaded from HuggingFace on first use and cached locally.
    """

    _instance: Optional["RerankerService"] = None
    _model = None
    _model_name: str = ""

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_loaded(self):
        """Load the model on first call (lazy initialization)."""
        if self._model is not None:
            return

        model_name = DEFAULT_RERANKER_MODEL
        logger.info(f"Loading reranker model: {model_name}")

        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            )

        # Set HuggingFace mirror for China users (if not already configured)
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")
        os.environ.setdefault("HF_HUB_OFFLINE", "0")

        self._model = CrossEncoder(model_name)
        self._model_name = model_name

        logger.info(f"Reranker model loaded: {self._model_name}")

    @property
    def model_name(self) -> str:
        self._ensure_loaded()
        return self._model_name

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_n: int = 5,
    ) -> list[dict]:
        """Rerank candidate documents using the cross-encoder model.

        Args:
            query: The search query.
            candidates: List of candidate dicts, each must have a "text" key.
            top_n: Number of top results to return.

        Returns:
            Reranked list of candidate dicts, each with an added "rerank_score" key.
            Ordered by descending relevance.
        """
        if not candidates:
            return []

        # If fewer candidates than top_n, just return all reranked
        top_n = min(top_n, len(candidates))

        self._ensure_loaded()

        # Build (query, text) pairs for the cross-encoder
        pairs = [(query, c["text"]) for c in candidates]

        # predict() returns an array of scores, one per pair
        scores = self._model.predict(pairs)

        # Attach scores to candidates
        scored = []
        for candidate, score in zip(candidates, scores):
            entry = dict(candidate)
            entry["rerank_score"] = float(score)
            scored.append(entry)

        # Sort by rerank score descending
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)

        return scored[:top_n]
