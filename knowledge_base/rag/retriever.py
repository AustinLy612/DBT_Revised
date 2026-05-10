"""LangChain-compatible retriever wrapping DBT hybrid search.

Provides a LangChain BaseRetriever that delegates to knowledge_base.services
for keyword + semantic search with deduplication.  This lets LangChain chains
use a standard .invoke(query) → [Document, ...] interface while benefiting from
the existing MongoDB + Qdrant infrastructure.

The retriever also writes a RetrievalLog on every invocation so every RAG call
is auditable.
"""

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from ..models import RetrievalLog
from ..services import hybrid_search

logger = logging.getLogger("dbt_platform.knowledge_base.rag")


class DBTRetriever(BaseRetriever):
    """LangChain retriever backed by DBT hybrid search (keyword + semantic).

    Wraps knowledge_base.services.hybrid_search() as a LangChain BaseRetriever.
    Supports filtering by metadata fields embedded in chunk payloads.

    Usage::

        retriever = DBTRetriever(k=5)
        docs = retriever.invoke("正念是什么")

        # With user/session context for RetrievalLog:
        retriever.invoke("正念", user=request.user, session=session, use_case="teaching")
    """

    k: int = 5
    user: Any = None
    session: Any = None
    use_case: str = "teaching"

    class Meta:
        arbitrary_types_allowed = True

    def _get_relevant_documents(self, query: str, **kwargs: Any) -> list[Document]:
        """Core retrieval method. Called by BaseRetriever.invoke()."""
        top_k = kwargs.get("k", self.k)
        chunks = hybrid_search(query, top_k=top_k)

        user = kwargs.get("user", self.user)
        session = kwargs.get("session", self.session)
        use_case = kwargs.get("use_case", self.use_case)

        if user is not None and use_case in dict(RetrievalLog.UseCase.choices):
            try:
                from ..services import log_retrieval
                chunk_ids = [c["chunk_id"] for c in chunks]
                log_retrieval(
                    user=user,
                    session=session,
                    query=query,
                    retrieved_chunk_ids=chunk_ids,
                    use_case=use_case,
                )
            except Exception:
                logger.exception("Failed to write RetrievalLog (non-fatal)")

        documents = []
        for chunk in chunks:
            doc = Document(
                page_content=chunk["chunk_text"],
                metadata={
                    "chunk_id": chunk["chunk_id"],
                    "document_id": chunk.get("document_id", ""),
                    "source": chunk.get("source", "unknown"),
                    "score": chunk.get("score", 0.0),
                    **(chunk.get("metadata", {})),
                },
            )
            documents.append(doc)

        return documents

    def search_with_context(
        self,
        query: str,
        *,
        user: Any = None,
        session: Any = None,
        use_case: str = "teaching",
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return raw dict results (not LangChain Documents) for direct use
        in prompt builders that need chunk metadata."""

        k = top_k or self.k
        chunks = hybrid_search(query, top_k=k)

        _user = user or self.user
        _session = session or self.session
        _use_case = use_case or self.use_case

        if _user is not None and _use_case in dict(RetrievalLog.UseCase.choices):
            try:
                from ..services import log_retrieval
                chunk_ids = [c["chunk_id"] for c in chunks]
                log_retrieval(
                    user=_user,
                    session=_session,
                    query=query,
                    retrieved_chunk_ids=chunk_ids,
                    use_case=_use_case,
                )
            except Exception:
                logger.exception("Failed to write RetrievalLog (non-fatal)")

        return chunks


def get_retriever(
    k: int = 5,
    user: Any = None,
    session: Any = None,
    use_case: str = "teaching",
) -> DBTRetriever:
    """Factory for creating a DBTRetriever with default parameters.

    Usage::

        retriever = get_retriever(k=5, user=request.user, use_case="teaching")
        docs = retriever.invoke("正念呼吸练习")
    """
    return DBTRetriever(k=k, user=user, session=session, use_case=use_case)
