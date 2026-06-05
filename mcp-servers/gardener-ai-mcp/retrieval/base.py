"""Abstract base class for all retrieval strategies.

This module defines the ``BaseRetriever`` contract that every concrete
retriever must satisfy.  It re-exports ``SearchResult`` from
``vectorstore.base`` so callers only need to import from this module.

Design notes (ADR-004):
- Retrieval is decoupled from both the embedding backend and the vector
  store backend via dependency injection.  Retrievers receive concrete
  implementations at construction time and never import them by name.
- The ``retrieve`` method is the sole public API.  Callers pass an
  optional ``filters`` dict; concrete implementations translate those
  filters into whatever form the underlying vector store expects.
- ``limit`` defaults to 10 to keep token consumption predictable for MCP
  tool responses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from vectorstore.base import SearchResult

__all__ = ["BaseRetriever", "SearchResult"]


class BaseRetriever(ABC):
    """Abstract interface for retrieval strategies.

    All concrete retrievers (semantic, hybrid, …) must inherit from this
    class and implement ``retrieve``.  The contract guarantees:

    - The method is ``async`` — it must never block the event loop.
    - Results are ordered by descending relevance score.
    - The caller controls the upper bound on results via ``limit``.
    - An empty list is a valid return value (no error for zero hits).

    Concrete implementations decide how ``filters`` are translated.
    For example, ``SemanticRetriever`` forwards them as Qdrant payload
    filters, while ``HybridRetriever`` applies them to every sub-search
    before fusion.
    """

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Retrieve the most relevant documents for a query.

        Implementations must:
        1. Embed or otherwise encode ``query`` as required.
        2. Query the underlying vector store(s) — potentially in parallel.
        3. Apply any fusion or re-ranking strategy.
        4. Return at most ``limit`` results sorted by descending score.

        Args:
            query: The natural-language search query.
            filters: Optional key/value pairs to narrow the result set.
                Each key corresponds to a payload field in the vector
                store (e.g. ``{"source_type": "docs", "state": "open"}``).
                ``None`` means no filtering.
            limit: Maximum number of results to return.  Must be a
                positive integer.  Defaults to 10.

        Returns:
            A list of at most ``limit`` ``SearchResult`` objects ordered
            by descending relevance score.  May be empty if no documents
            match.

        Raises:
            EmbeddingError: If the query cannot be embedded.
            VectorStoreError: If the underlying vector store returns an
                unrecoverable error.
        """
        ...
