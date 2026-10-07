"""Dense-vector semantic retrieval strategy.

A ``SemanticRetriever`` encodes the query with a single call to the
embedder, then issues one nearest-neighbour search per invocation.  It
is the simplest retrieval strategy and the recommended starting point
for single-collection use cases.

Design notes:
- No Settings dependency — the caller injects a pre-built ``BaseEmbedder``
  and ``BaseVectorStore``, keeping the retriever fully portable across
  test and production environments.
- The collection name is fixed at construction time.  If you need to
  search across multiple collections, use ``HybridRetriever`` instead.
"""

from __future__ import annotations

from typing import Any

from ai_observability import embed_query, preview, search_collection, span, top_sources

from embeddings.base import BaseEmbedder
from retrieval.base import BaseRetriever, SearchResult
from vectorstore.base import BaseVectorStore


class SemanticRetriever(BaseRetriever):
    """Dense-vector semantic retriever backed by a single Qdrant collection.

    Encodes the query with the injected embedder, then delegates the
    nearest-neighbour search to the injected vector store.  All
    filtering and limit constraints are forwarded without modification.

    Attributes:
        _embedder: The embedding provider used to encode queries.
        _vector_store: The vector store backend to search.
        _collection: The name of the target collection.
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
        collection: str,
    ) -> None:
        """Initialise the semantic retriever.

        Args:
            embedder: An async embedding provider.  Must implement
                ``BaseEmbedder.embed_query``.
            vector_store: An async vector store backend.  Must implement
                ``BaseVectorStore.search``.
            collection: The name of the Qdrant collection to search
                (e.g. ``"sci_docs_operation"``).
        """
        self._embedder = embedder
        self._vector_store = vector_store
        self._collection = collection

    async def retrieve(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Retrieve the most semantically similar documents for a query.

        Embeds ``query`` using the injected embedder, then searches the
        configured collection for the nearest ``limit`` neighbours.  Any
        ``filters`` are forwarded directly to the vector store as payload
        filters (exact-match key/value conditions combined with AND).

        Args:
            query: The natural-language search query to embed and search.
            filters: Optional exact-match payload filters.  E.g.
                ``{"content_type": "doc"}``.  ``None`` means no filtering.
            limit: Maximum number of results to return.  Defaults to 10.

        Returns:
            A list of at most ``limit`` ``SearchResult`` objects ordered
            by descending cosine similarity score.

        Raises:
            EmbeddingError: If the embedder cannot encode the query.
            VectorStoreError: If the vector store search fails.
        """
        async with span(
            f"retrieve {self._collection}",
            **{
                "gen_ai.operation.name": "retrieve",
                "ai.rag.collection": self._collection,
                "ai.rag.limit": limit,
                "ai.query": preview(query),
            },
        ) as current:
            query_vector: list[float] = await embed_query(self._embedder, query)
            results: list[SearchResult] = await search_collection(
                self._vector_store,
                self._collection,
                query_vector,
                limit,
                filters,
            )
            if current.is_recording():
                current.set_attribute("ai.rag.result_count", len(results))
                current.set_attribute("ai.rag.top_sources", top_sources(results))
            return results
