import hashlib
import json
import logging
from typing import Any

import numpy as np
import redis as redis_lib
from django.conf import settings
from django.db import connections
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

from . import embedding
from .models import KnowledgeChunk, KnowledgeDocument, RetrievalLog

logger = logging.getLogger("dbt_platform.knowledge_base")

EMBEDDING_DIM = embedding.EMBEDDING_DIM
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K_DEFAULT = 5
RAG_CACHE_TTL_SECONDS = 300  # 5 minutes

_qdrant_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(
            host=settings.QDRANT_HOST, port=settings.QDRANT_PORT
        )
    return _qdrant_client


_redis_client: redis_lib.Redis | None = None


def get_redis_client() -> redis_lib.Redis | None:
    """Return a Redis client for caching, or None if Redis is unavailable."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis_lib.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD or None,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            _redis_client.ping()
        except Exception:
            logger.warning("Redis unavailable — RAG cache disabled")
            _redis_client = False  # type: ignore[assignment]
            return None
    if _redis_client is False:
        return None
    return _redis_client


def _rag_cache_key(query: str, top_k: int) -> str:
    """Build a deterministic cache key for a RAG search query."""
    payload = f"{query}|{top_k}"
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"rag:search:{digest}"


def _rag_cache_get(query: str, top_k: int) -> list[dict[str, Any]] | None:
    """Read cached hybrid_search results from Redis."""
    client = get_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_rag_cache_key(query, top_k))
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


def _rag_cache_set(query: str, top_k: int, results: list[dict[str, Any]]) -> None:
    """Cache hybrid_search results in Redis with a 5-minute TTL."""
    client = get_redis_client()
    if client is None:
        return
    try:
        client.setex(
            _rag_cache_key(query, top_k),
            RAG_CACHE_TTL_SECONDS,
            json.dumps(results, ensure_ascii=False),
        )
    except Exception:
        pass


def extract_sections(text: str) -> list[dict[str, str]]:
    """Split text into sections by markdown headings. Falls back to first-line title or 正文."""
    import re

    heading_re = re.compile(r"^(#{1,3})\s+(.+)", re.MULTILINE)
    matches = list(heading_re.finditer(text))

    if not matches:
        first_line = text.split("\n", 1)[0].strip()
        title = first_line if first_line and len(first_line) <= 80 else "正文"
        return [{"title": title, "content": text.strip()}]

    sections = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append({
            "title": match.group(2).strip(),
            "content": text[start:end].strip(),
        })

    if matches and matches[0].start() > 0:
        preamble = text[:matches[0].start()].strip()
        if preamble:
            sections.insert(0, {"title": "概述", "content": preamble})

    return sections


def chunk_text(text: str, metadata: dict | None = None) -> list[dict[str, Any]]:
    """Split text into chunks with metadata."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " "],
    )
    docs = splitter.create_documents([text], metadatas=[metadata or {}])
    return [
        {"text": doc.page_content, "metadata": doc.metadata}
        for doc in docs
    ]


def generate_embeddings(texts: list[str]) -> np.ndarray:
    """Generate embeddings for a list of texts. Delegates to fastembed (ONNX runtime)."""
    return embedding.generate_embeddings(texts)


def ensure_qdrant_collection() -> None:
    """Create Qdrant collection if it doesn't exist."""
    client = get_qdrant_client()
    collection_name = settings.QDRANT_COLLECTION
    collections = [c.name for c in client.get_collections().collections]

    if collection_name not in collections:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=EMBEDDING_DIM,
                distance=qdrant_models.Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection: %s", collection_name)


def index_chunks_to_qdrant(chunks: list[KnowledgeChunk], embeddings: np.ndarray) -> None:
    """Index chunk embeddings into Qdrant."""
    client = get_qdrant_client()
    collection_name = settings.QDRANT_COLLECTION
    ensure_qdrant_collection()

    points = []
    for i, chunk in enumerate(chunks):
        points.append(
            qdrant_models.PointStruct(
                id=chunk.chunk_id,
                vector=embeddings[i].tolist(),
                payload={
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "chunk_text": chunk.chunk_text,
                    "metadata": chunk.metadata,
                },
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    logger.info("Indexed %d chunks to Qdrant", len(chunks))


def ensure_mongodb_text_index() -> None:
    """Create MongoDB text index on knowledge_chunks if it doesn't exist."""
    connection = connections["default"]
    db = connection.database
    collection = db["knowledge_chunks"]

    existing = collection.index_information()
    has_text_index = any(
        idx.get("key") == {"_fts": "text", "_ftsx": 1} or "chunk_text_text" in name
        for name, idx in existing.items()
    )

    if not has_text_index:
        collection.create_index([("chunk_text", "text")], name="chunk_text_text")
        logger.info("Created MongoDB text index on knowledge_chunks.chunk_text")


def keyword_search(query: str, top_k: int = TOP_K_DEFAULT) -> list[dict[str, Any]]:
    """Search chunks by keyword using MongoDB $text index.

    Falls back to $regex when $text returns no results (e.g. short
    single-character queries that don't match any text-index tokens).
    """
    ensure_mongodb_text_index()
    connection = connections["default"]
    db = connection.database
    collection = db["knowledge_chunks"]

    terms = [t for t in query.split() if t]
    if not terms:
        return []

    search_query = " ".join(terms)

    # Use $text search — leverages the chunk_text_text index for fast lookup
    try:
        cursor = collection.find(
            {"$text": {"$search": search_query}},
            {"score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(top_k)

        results = []
        for doc in cursor:
            results.append({
                "chunk_id": doc["chunk_id"],
                "document_id": doc["document_id"],
                "chunk_text": doc.get("chunk_text", ""),
                "score": round(doc.get("score", 0), 4),
                "source": "keyword",
            })

        if results:
            return results
    except Exception:
        logger.exception("MongoDB $text search failed, falling back to $regex")

    # Fallback: $regex for queries that $text can't match
    return _keyword_search_regex(terms, collection, top_k)


def _keyword_search_regex(
    terms: list[str], collection, top_k: int
) -> list[dict[str, Any]]:
    """Fallback keyword search using $regex when $text returns no results."""
    import re as _re

    if len(terms) == 1:
        regex = _re.escape(terms[0])
    else:
        regex_parts = [f"(?=.*{_re.escape(term)})" for term in terms]
        regex = f"^{''.join(regex_parts)}.*$"

    cursor = collection.find(
        {"chunk_text": {"$regex": regex, "$options": "i"}}
    ).limit(top_k)

    results = []
    for doc in cursor:
        text = doc["chunk_text"]
        relevance = sum(1 for t in terms if t.lower() in text.lower()) / max(len(terms), 1)
        results.append({
            "chunk_id": doc["chunk_id"],
            "document_id": doc["document_id"],
            "chunk_text": text,
            "score": relevance,
            "source": "keyword",
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def semantic_search(query: str, top_k: int = TOP_K_DEFAULT) -> list[dict[str, Any]]:
    """Search chunks by semantic similarity using Qdrant.
    Falls back to empty results if the embedding model is unavailable."""
    query_vector = embedding.embed_query(query)
    if not query_vector.any():
        logger.warning("Semantic search skipped: embedding model not available")
        return []

    client = get_qdrant_client()
    collection_name = settings.QDRANT_COLLECTION
    ensure_qdrant_collection()

    results = client.query_points(
        collection_name=collection_name,
        query=query_vector.tolist(),
        limit=top_k,
    )

    return [
        {
            "chunk_id": hit.payload["chunk_id"],
            "document_id": hit.payload["document_id"],
            "chunk_text": hit.payload["chunk_text"],
            "score": float(hit.score),
            "source": "semantic",
        }
        for hit in results.points
    ]


def hybrid_search(query: str, top_k: int = TOP_K_DEFAULT) -> list[dict[str, Any]]:
    """Combine keyword and semantic search, deduplicate by chunk_id.

    Results are cached in Redis for 5 minutes to eliminate redundant
    MongoDB + Qdrant lookups when the same skill is queried repeatedly
    within a teaching session.
    """
    cached = _rag_cache_get(query, top_k)
    if cached is not None:
        return cached

    kw_results = keyword_search(query, top_k)
    try:
        sem_results = semantic_search(query, top_k)
    except Exception:
        logger.exception("Semantic search failed in hybrid_search; using keyword-only results")
        sem_results = []

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []

    for r in kw_results:
        if r["chunk_id"] not in seen:
            seen.add(r["chunk_id"])
            merged.append(r)

    for r in sem_results:
        if r["chunk_id"] not in seen:
            seen.add(r["chunk_id"])
            r["source"] = "hybrid"
            merged.append(r)

    merged = merged[:top_k]
    _rag_cache_set(query, top_k, merged)
    return merged


def log_retrieval(
    user,
    session,
    query: str,
    retrieved_chunk_ids: list[str],
    use_case: str,
) -> RetrievalLog:
    """Write a RetrievalLog entry."""
    log = RetrievalLog.objects.create(
        user=user,
        session=session,
        query=query,
        retrieved_chunk_ids=retrieved_chunk_ids,
        use_case=use_case,
    )
    return log
