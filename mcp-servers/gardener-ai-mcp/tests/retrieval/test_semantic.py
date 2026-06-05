"""Tests for SemanticRetriever.

Verifies that SemanticRetriever correctly orchestrates the embedder
and vector store: embedding is called with the right query string,
the resulting vector is forwarded to the store, and all optional
parameters (filters, limit) pass through without modification.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from retrieval.semantic import SemanticRetriever
from vectorstore.base import SearchResult


def _make_result(doc_id: str, score: float = 0.9) -> SearchResult:
    """Build a minimal SearchResult for use in test fixtures."""
    return SearchResult(
        id=doc_id,
        content=f"content for {doc_id}",
        score=score,
        metadata={"source_type": "docs"},
        collection="gardener_docs",
    )


@pytest.fixture()
def mock_embedder() -> AsyncMock:
    """Return a mock BaseEmbedder whose embed_query returns a fixed vector."""
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return embedder


@pytest.fixture()
def mock_vector_store() -> AsyncMock:
    """Return a mock BaseVectorStore whose search returns two fixed results."""
    store = AsyncMock()
    store.search = AsyncMock(
        return_value=[_make_result("doc-1", 0.95), _make_result("doc-2", 0.80)]
    )
    return store


@pytest.fixture()
def retriever(mock_embedder: AsyncMock, mock_vector_store: AsyncMock) -> SemanticRetriever:
    """Return a SemanticRetriever wired to the mock embedder and store."""
    return SemanticRetriever(
        embedder=mock_embedder,
        vector_store=mock_vector_store,
        collection="gardener_docs",
    )


# ---------------------------------------------------------------------------
# test_retrieve_embeds_query_and_searches
# ---------------------------------------------------------------------------


async def test_retrieve_embeds_query_and_searches(
    retriever: SemanticRetriever,
    mock_embedder: AsyncMock,
    mock_vector_store: AsyncMock,
) -> None:
    """embed_query is called with the query; search is called with the
    resulting vector and the configured collection; results are returned
    unchanged."""
    results = await retriever.retrieve("how does Gardener manage shoot clusters?")

    # Embedder must be called with the exact query string.
    mock_embedder.embed_query.assert_awaited_once_with(
        "how does Gardener manage shoot clusters?"
    )

    # Vector store must be called with the vector returned by embed_query.
    mock_vector_store.search.assert_awaited_once_with(
        "gardener_docs",
        [0.1, 0.2, 0.3],
        10,
        None,
    )

    # The results from the store are returned without modification.
    assert len(results) == 2
    assert results[0].id == "doc-1"
    assert results[1].id == "doc-2"


# ---------------------------------------------------------------------------
# test_retrieve_passes_filters
# ---------------------------------------------------------------------------


async def test_retrieve_passes_filters(
    retriever: SemanticRetriever,
    mock_vector_store: AsyncMock,
) -> None:
    """Caller-supplied filters are forwarded verbatim to vector_store.search."""
    filters = {"state": "open", "repo": "gardener/gardener"}
    await retriever.retrieve("DNS shoot cluster", filters=filters)

    _args, _kwargs = mock_vector_store.search.await_args
    # Fourth positional argument is filters.
    assert _args[3] == filters


# ---------------------------------------------------------------------------
# test_retrieve_passes_limit
# ---------------------------------------------------------------------------


async def test_retrieve_passes_limit(
    retriever: SemanticRetriever,
    mock_vector_store: AsyncMock,
) -> None:
    """A custom limit value is forwarded verbatim to vector_store.search."""
    await retriever.retrieve("seed cluster networking", limit=5)

    _args, _kwargs = mock_vector_store.search.await_args
    # Third positional argument is limit.
    assert _args[2] == 5
