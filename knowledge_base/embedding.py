"""Embedding provider using fastembed (ONNX runtime).

Replaces SentenceTransformer (PyTorch) with fastembed's optimized ONNX backend.
Using intfloat/multilingual-e5-large — same 1024-dim vectors as BGE-M3,
multilingual (Chinese + English), excellent MTEB benchmarks.

ONNX Runtime is ~30% more memory-efficient than PyTorch for inference,
reducing per-worker memory from ~2GB to ~1.4GB.

The model is cached at MODELS_DIR/fastembed_cache/ on a persistent volume.
"""

import logging
import os
from typing import Sequence

import numpy as np
from fastembed import TextEmbedding

logger = logging.getLogger("dbt_platform.knowledge_base")

# intfloat/multilingual-e5-large: 1024 dims, multilingual, ONNX-optimized.
# Same vector dimension as BAAI/bge-m3 — drop-in replacement.
EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-large"
EMBEDDING_DIM = 1024

_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "fastembed_cache")

_embedding_model: TextEmbedding | None = None
_embedding_load_failed: bool = False


def preload_embedding_model() -> None:
    """Preload the fastembed model in a background thread at Django startup.

    Fastembed with ONNX runtime loads faster than PyTorch SentenceTransformer
    and uses ~30% less memory.
    """
    global _embedding_model, _embedding_load_failed
    if _embedding_model is not None or _embedding_load_failed:
        return
    try:
        logger.info("Preloading embedding model (fastembed): %s", EMBEDDING_MODEL_NAME)
        _embedding_model = TextEmbedding(
            model_name=EMBEDDING_MODEL_NAME, cache_dir=_MODELS_DIR
        )
        logger.info("Embedding model preloaded successfully: %s", EMBEDDING_MODEL_NAME)
    except Exception:
        _embedding_load_failed = True
        logger.exception(
            "Failed to preload embedding model %s. Semantic search disabled.",
            EMBEDDING_MODEL_NAME,
        )


def _get_model() -> TextEmbedding | None:
    """Return the fastembed model, loading it if necessary."""
    global _embedding_model, _embedding_load_failed
    if _embedding_load_failed:
        return None
    if _embedding_model is None:
        try:
            logger.info("Loading embedding model (fastembed): %s", EMBEDDING_MODEL_NAME)
            _embedding_model = TextEmbedding(
                model_name=EMBEDDING_MODEL_NAME, cache_dir=_MODELS_DIR
            )
        except Exception:
            _embedding_load_failed = True
            logger.exception(
                "Failed to load embedding model %s. Semantic search disabled.",
                EMBEDDING_MODEL_NAME,
            )
            return None
    return _embedding_model


def generate_embeddings(texts: Sequence[str]) -> np.ndarray:
    """Generate embeddings for a list of texts via fastembed (ONNX).

    Returns a (len(texts), 1024) float32 array, or zeros if model unavailable.
    """
    model = _get_model()
    if model is None:
        logger.warning("Embedding model not available, returning zeros")
        return np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
    embeddings = list(model.embed(texts))
    return np.array(embeddings, dtype=np.float32)


def embed_query(query: str) -> np.ndarray:
    """Embed a single query string via fastembed query_embed (adds instruction prefix).

    multilingual-e5-large requires "query: " prefix for asymmetric retrieval.
    """
    model = _get_model()
    if model is None:
        logger.warning("Embedding model not available for query")
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    embeddings = list(model.query_embed(query))
    return np.array(embeddings[0], dtype=np.float32)
