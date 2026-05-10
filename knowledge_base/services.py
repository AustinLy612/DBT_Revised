import logging
from typing import Any

import numpy as np
from django.conf import settings
from django.db import connections
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from sentence_transformers import SentenceTransformer

from .models import KnowledgeChunk, KnowledgeDocument, RetrievalLog

logger = logging.getLogger("dbt_platform.knowledge_base")

EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_DIM = 1024
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K_DEFAULT = 5

_embedding_model: SentenceTransformer | None = None
_qdrant_client: QdrantClient | None = None


_embedding_load_failed: bool = False


def get_embedding_model() -> SentenceTransformer | None:
    global _embedding_model, _embedding_load_failed
    if _embedding_load_failed:
        return None
    if _embedding_model is None:
        try:
            logger.info("Loading embedding model from cache: %s", EMBEDDING_MODEL_NAME)
            _embedding_model = SentenceTransformer(
                EMBEDDING_MODEL_NAME, local_files_only=True
            )
        except Exception:
            logger.warning(
                "Local cache miss for %s, attempting network load via HF_ENDPOINT.",
                EMBEDDING_MODEL_NAME,
            )
            try:
                _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            except Exception:
                _embedding_load_failed = True
                logger.exception(
                    "Failed to load embedding model %s. Semantic search disabled.",
                    EMBEDDING_MODEL_NAME,
                )
                return None
    return _embedding_model


def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(
            host=settings.QDRANT_HOST, port=settings.QDRANT_PORT
        )
    return _qdrant_client


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
    """Generate embeddings for a list of texts. Returns empty array if model unavailable."""
    model = get_embedding_model()
    if model is None:
        logger.warning("Embedding model not available, returning zeros")
        return np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
    return model.encode(texts, normalize_embeddings=True)


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
    """Search chunks by keyword using MongoDB $regex (Chinese-compatible)."""
    import re as _re

    ensure_mongodb_text_index()
    connection = connections["default"]
    db = connection.database
    collection = db["knowledge_chunks"]

    terms = [t for t in query.split() if t]
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
    model = get_embedding_model()
    if model is None:
        logger.warning("Semantic search skipped: embedding model not available")
        return []

    query_vector = model.encode([query], normalize_embeddings=True)[0].tolist()

    client = get_qdrant_client()
    collection_name = settings.QDRANT_COLLECTION
    ensure_qdrant_collection()

    results = client.query_points(
        collection_name=collection_name,
        query=query_vector,
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
    """Combine keyword and semantic search, deduplicate by chunk_id."""
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

    return merged[:top_k]


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
