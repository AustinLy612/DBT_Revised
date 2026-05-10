import io
import logging
from typing import Any

from celery import shared_task

from .models import KnowledgeChunk, KnowledgeDocument
from .services import (
    chunk_text,
    ensure_qdrant_collection,
    extract_sections,
    generate_embeddings,
    get_qdrant_client,
    index_chunks_to_qdrant,
)
from .storage import download_document

logger = logging.getLogger("dbt_platform.knowledge_base")


def parse_document_bytes(file_bytes: bytes, filename: str) -> str:
    """Parse document bytes into plain text."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in ("txt", "md", "markdown"):
        return file_bytes.decode("utf-8", errors="replace")

    if ext == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            texts = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(texts)
        except ImportError:
            raise RuntimeError("pypdf not installed; cannot parse PDF")

    if ext in ("docx", "doc"):
        try:
            from docx import Document
            doc = Document(io.BytesIO(file_bytes))
            texts = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(texts)
        except ImportError:
            raise RuntimeError("python-docx not installed; cannot parse DOCX")

    raise ValueError(f"Unsupported file type: .{ext}")


def run_document_pipeline(document_id: str, object_name: str, filename: str) -> int:
    """Core pipeline: download → parse → chunk → embed → index. Returns chunk count."""
    file_bytes = download_document(object_name)
    full_text = parse_document_bytes(file_bytes, filename)

    if not full_text.strip():
        raise ValueError("Document contains no extractable text")

    doc = KnowledgeDocument.objects.get(document_id=document_id)

    # Idempotency: clean up any chunks and vectors from a prior failed attempt.
    existing_chunks = list(KnowledgeChunk.objects.filter(document=doc))
    if existing_chunks:
        existing_ids = [c.chunk_id for c in existing_chunks]
        try:
            q_client = get_qdrant_client()
            from django.conf import settings
            q_client.delete(
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=existing_ids,
            )
        except Exception:
            pass
        KnowledgeChunk.objects.filter(document=doc).delete()

    doc_metadata = {
        "document_id": doc.document_id,
        "title": doc.title,
        "module": doc.module,
        "skill": doc.skill,
        "version": doc.version,
        "difficulty": doc.difficulty,
        "is_beginner_friendly": doc.is_beginner_friendly,
        "scenario_tags": doc.scenario_tags,
        "risk_flags": doc.risk_flags,
    }

    sections = extract_sections(full_text)
    chunks_data: list[dict[str, Any]] = []
    for section in sections:
        section_meta = {**doc_metadata, "section_title": section["title"]}
        section_chunks = chunk_text(section["content"], metadata=section_meta)
        chunks_data.extend(section_chunks)

    if not chunks_data:
        raise ValueError("No chunks produced from document")

    chunk_texts = [c["text"] for c in chunks_data]
    embeddings = generate_embeddings(chunk_texts)

    ensure_qdrant_collection()

    chunk_objects = []
    for cdata in chunks_data:
        chunk = KnowledgeChunk(
            document=doc,
            chunk_text=cdata["text"],
            metadata=cdata["metadata"],
        )
        chunk_objects.append(chunk)

    KnowledgeChunk.objects.bulk_create(chunk_objects)

    for chunk in chunk_objects:
        chunk.embedding_id = chunk.chunk_id
    KnowledgeChunk.objects.bulk_update(chunk_objects, fields=["embedding_id"])

    index_chunks_to_qdrant(chunk_objects, embeddings)

    return len(chunk_objects)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def process_document_async(self, document_id: str, object_name: str, filename: str):
    """Celery task: runs the document processing pipeline and updates status."""
    logger.info("Starting document processing: %s (%s)", document_id, filename)

    try:
        doc = KnowledgeDocument.objects.get(document_id=document_id)
    except KnowledgeDocument.DoesNotExist:
        logger.error("Document not found: %s", document_id)
        return

    doc.status = KnowledgeDocument.Status.PROCESSING
    doc.save(update_fields=["status"])

    try:
        chunk_count = run_document_pipeline(document_id, object_name, filename)

        doc.status = KnowledgeDocument.Status.RETRIEVABLE
        doc.error_message = ""
        doc.save(update_fields=["status", "error_message"])

        logger.info(
            "Document processed successfully: %s (%d chunks)", document_id, chunk_count
        )

    except Exception as exc:
        logger.error("Document processing failed: %s — %s", document_id, exc)
        doc.status = KnowledgeDocument.Status.FAILED
        doc.error_message = f"{type(exc).__name__}: {exc}"
        doc.save(update_fields=["status", "error_message"])
        raise self.retry(exc=exc)
