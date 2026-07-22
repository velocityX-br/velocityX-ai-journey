"""Multi-collection retrieval with Reciprocal Rank Fusion.

``HybridRetriever`` fans out one dense search per configured collection
concurrently and merges the result lists using Reciprocal Rank Fusion
(RRF).  This strategy is recommended for MCP tools that must surface
results from both documentation collections (operation + customer) in a
single ranked list.

Design notes:
- Dense search: nearest-neighbour lookup with the embedded query vector,
  delegated to ``BaseVectorStore.search``, run once per collection.
- Cross-collection fusion is the value here: independent per-collection
  rankings are merged into one list.  Native sparse-vector search
  (Qdrant >= 1.10 sparse vectors / BM42) can be added later as an extra
  ranked list per collection without changing the caller interface.
- All searches are launched with a single ``asyncio.gather`` call to
  maximise I/O parallelism — sequential awaits are explicitly avoided.
- RRF is the canonical fusion algorithm here.  It is robust to score
  incompatibility between result lists and requires no calibration.
  k=60 is the literature default that worked well across BEIR benchmarks.
- ``reciprocal_rank_fusion`` is a module-level pure function so it can
  be unit-tested without any embedder or vector store.
"""

from __future__ import annotations

import asyncio
from typing import Any

from embeddings.base import BaseEmbedder
from retrieval.base import BaseRetriever, SearchResult
from vectorstore.base import BaseVectorStore

_DEFAULT_COLLECTIONS: list[str] = [
    "sci_docs_operation",
    "sci_docs_customer",
]


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion.

    For each document ``d`` that appears in any ranked list, the RRF
    score is::

        score(d) = sum(1 / (k + rank(d, list_i)))
                   for each list_i where d appears

    where ``rank(d, list_i)`` is the 1-based position of ``d`` in
    ``list_i``.  When the same document ID appears more than once in a
    single list (which should not happen in practice), only the
    highest-ranked (lowest-index) occurrence is counted for that list.

    The returned list is a deduplicated, flat list of ``SearchResult``
    objects whose ``score`` field is set to the computed RRF score.
    Results are sorted by descending RRF score.

    Args:
        ranked_lists: A list of ranked result lists.  Each inner list
            is ordered by descending relevance (index 0 = most relevant).
            Lists may be empty; empty lists are silently ignored.
        k: Smoothing constant.  Defaults to 60 (literature standard).
            Higher values reduce the advantage of top-ranked documents.

    Returns:
        A flat, deduplicated list of ``SearchResult`` objects sorted by
        descending RRF score.  Each document appears at most once.
        The ``score`` field reflects the computed RRF score, not the
        original similarity score from any single list.
    """
    # Map document ID -> accumulated RRF score.
    rrf_scores: dict[str, float] = {}
    # Map document ID -> the SearchResult object (for reconstruction).
    # We keep the first-seen instance; the score is overwritten anyway.
    results_by_id: dict[str, SearchResult] = {}

    for ranked_list in ranked_lists:
        # Track which IDs we have already seen in THIS list so we only
        # count the highest-ranked occurrence per list.
        seen_in_list: set[str] = set()
        for rank_zero, result in enumerate(ranked_list):
            doc_id = result.id
            if doc_id in seen_in_list:
                continue
            seen_in_list.add(doc_id)

            rank_one = rank_zero + 1
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank_one)

            if doc_id not in results_by_id:
                results_by_id[doc_id] = result

    # Build the output list with updated RRF scores.
    fused: list[SearchResult] = []
    for doc_id, rrf_score in rrf_scores.items():
        original = results_by_id[doc_id]
        fused.append(
            SearchResult(
                id=original.id,
                content=original.content,
                score=rrf_score,
                metadata=original.metadata,
                collection=original.collection,
            )
        )

    fused.sort(key=lambda r: r.score, reverse=True)
    return fused


class HybridRetriever(BaseRetriever):
    """Multi-collection retriever using per-collection dense search and RRF.

    Fans out one dense (embedding-based) search per configured collection
    concurrently, then merges the result lists with
    ``reciprocal_rank_fusion`` into a single cross-collection ranking.

    The parallel fan-out is implemented with a single ``asyncio.gather``
    call, meaning the number of outstanding coroutines scales as
    ``len(collections)``.

    Attributes:
        _embedder: Embedding provider for dense query vectors.
        _vector_store: Vector store backend for all search calls.
        _collections: The ordered list of collection names to search.
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        vector_store: BaseVectorStore,
        collections: list[str] | None = None,
    ) -> None:
        """Initialise the hybrid retriever.

        Args:
            embedder: An async embedding provider used for dense search.
            vector_store: An async vector store backend.  All collections
                must be accessible via this single client.
            collections: An explicit list of collection names to search.
                Defaults to both canonical documentation collections:
                ``["sci_docs_operation", "sci_docs_customer"]``.
        """
        self._embedder = embedder
        self._vector_store = vector_store
        self._collections: list[str] = (
            collections if collections is not None else list(_DEFAULT_COLLECTIONS)
        )

    async def retrieve(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Retrieve and fuse dense results from all collections using RRF.

        Execution steps:
        1. Embed ``query`` to produce a dense vector.
        2. Build one dense-search coroutine per collection.
        3. Launch all coroutines concurrently with ``asyncio.gather``.
        4. Apply ``reciprocal_rank_fusion`` over all returned lists.
        5. Return the top ``limit`` results by fused score.

        Fusion across collections is the point of this retriever: each
        collection is searched independently and the per-collection ranked
        lists are merged into a single cross-collection ranking with RRF.
        Native sparse-vector (BM42/SPLADE) search can be added as an extra
        ranked list per collection without changing this interface.

        Args:
            query: The natural-language search query.
            filters: Optional base payload filters applied to every
                per-collection search.  ``None`` means no filtering.
            limit: Maximum number of fused results to return.  Defaults
                to 10.

        Returns:
            A flat list of at most ``limit`` ``SearchResult`` objects
            sorted by descending RRF score.

        Raises:
            EmbeddingError: If the embedder cannot encode the query.
            VectorStoreError: If any individual search call fails.
        """
        query_vector: list[float] = await self._embedder.embed_query(query)

        # One dense search per collection.  The results from every
        # collection are then merged with Reciprocal Rank Fusion, which is
        # what gives this retriever its cross-collection value: a single
        # ranked list spanning both operation and customer docs.
        coroutines = [
            self._vector_store.search(
                collection,
                query_vector,
                limit,
                filters,
            )
            for collection in self._collections
        ]

        # Launch all searches concurrently.  Any exception propagates.
        all_results: list[list[SearchResult]] = list(
            await asyncio.gather(*coroutines)
        )

        fused = reciprocal_rank_fusion(all_results)
        return fused[:limit]
