"""Tests for QdrantVectorStore.

All tests use ``pytest-mock`` (``mocker``) to inject a mock
``AsyncQdrantClient`` via constructor dependency injection.  No real Qdrant
instance is required.

Design:
- Each test creates a ``MagicMock`` whose async methods return
  pre-configured values via ``AsyncMock``.
- ``QdrantVectorStore`` is constructed with the mock client so no
  network calls are made.
- Assertions target the mock's call count and call args.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from qdrant_client.http.models import (
    Distance,
    QueryResponse,
    ScoredPoint,
    VectorParams,
)

from config.settings import Settings
from ingestion.base import Document
from vectorstore.base import SearchResult
from vectorstore.qdrant import QdrantVectorStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(*, batch_size: int = 100, dimensions: int = 1536) -> Settings:
    """Build a minimal Settings instance for vector store tests.

    ``pydantic-settings`` resolves ``validation_alias=AliasChoices`` fields
    from keyword arguments using the *alias* names, not the Python attribute
    names.  The unprefixed alias names (e.g. ``GITHUB_TOKEN``) are used here.

    Args:
        batch_size: Value to set for ``qdrant_batch_size``.
        dimensions: Value to set for ``embedding_dimensions``.

    Returns:
        A ``Settings`` instance suitable for unit tests.
    """
    return Settings(
        GITHUB_TOKEN="test-token",
        QDRANT_URL="http://localhost:6333",
        QDRANT_API_KEY="",
        QDRANT_BATCH_SIZE=batch_size,
        EMBEDDING_DIMENSIONS=dimensions,
    )


def _make_document(content: str = "hello gardener") -> Document:
    """Create a test Document with a stable UUID.

    Args:
        content: The document text.

    Returns:
        A ``Document`` with a fresh UUID.
    """
    return Document(
        id=str(uuid4()),
        content=content,
        source="https://github.com/gardener/documentation/blob/main/README.md",
        metadata={"source_type": "docs", "repo": "gardener/documentation"},
    )


def _make_vector(dims: int = 1536) -> list[float]:
    """Return a zero vector of the given dimensionality."""
    return [0.0] * dims


def _make_scored_point(doc_id: str, content: str, score: float = 0.9) -> ScoredPoint:
    """Build a ``ScoredPoint`` mimicking a Qdrant search result.

    Args:
        doc_id: Point ID.
        content: Text content stored in the payload.
        score: Similarity score.

    Returns:
        A ``ScoredPoint`` instance.
    """
    return ScoredPoint(
        id=doc_id,
        version=1,
        score=score,
        payload={"content": content, "source_type": "docs"},
    )


def _make_mock_client() -> MagicMock:
    """Return a ``MagicMock`` with async methods configured as ``AsyncMock``.

    Returns:
        A ``MagicMock`` that can stand in for ``AsyncQdrantClient`` in tests.
    """
    client = MagicMock()
    client.collection_exists = AsyncMock()
    client.create_collection = AsyncMock()
    client.create_payload_index = AsyncMock()
    client.upsert = AsyncMock()
    client.query_points = AsyncMock()
    client.delete = AsyncMock()
    client.get_collections = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestQdrantVectorStore:
    """Unit tests for QdrantVectorStore."""

    @pytest.mark.asyncio
    async def test_ensure_collection_creates_when_not_exists(self) -> None:
        """create_collection must be called with correct VectorParams when collection is absent."""
        settings = _make_settings(dimensions=1536)
        mock_client = _make_mock_client()
        mock_client.collection_exists.return_value = False

        store = QdrantVectorStore(settings=settings, client=mock_client)
        await store.ensure_collection("gardener_docs", vector_size=1536)

        mock_client.create_collection.assert_called_once()
        call_kwargs = mock_client.create_collection.call_args.kwargs
        assert call_kwargs["collection_name"] == "gardener_docs"
        vectors_config: VectorParams = call_kwargs["vectors_config"]
        assert isinstance(vectors_config, VectorParams)
        assert vectors_config.size == 1536
        assert vectors_config.distance == Distance.COSINE

    @pytest.mark.asyncio
    async def test_ensure_collection_skips_when_exists(self) -> None:
        """create_collection must NOT be called when the collection already exists."""
        settings = _make_settings(dimensions=1536)
        mock_client = _make_mock_client()
        mock_client.collection_exists.return_value = True

        store = QdrantVectorStore(settings=settings, client=mock_client)
        await store.ensure_collection("gardener_docs", vector_size=1536)

        mock_client.create_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_batches_correctly(self) -> None:
        """250 documents with batch_size=100 must produce exactly 3 upsert calls (100+100+50)."""
        settings = _make_settings(batch_size=100)
        mock_client = _make_mock_client()

        documents = [_make_document(f"doc {i}") for i in range(250)]
        vectors = [_make_vector() for _ in range(250)]

        store = QdrantVectorStore(settings=settings, client=mock_client)
        total = await store.upsert(
            collection="gardener_docs",
            documents=documents,
            vectors=vectors,
        )

        assert mock_client.upsert.call_count == 3, (
            f"Expected 3 upsert batches for 250 docs at batch_size=100, "
            f"got {mock_client.upsert.call_count}"
        )
        assert total == 250

        # Verify individual batch sizes via the points argument.
        call_args_list = mock_client.upsert.call_args_list
        batch_lengths = [len(c.kwargs["points"]) for c in call_args_list]
        assert batch_lengths == [100, 100, 50]

    @pytest.mark.asyncio
    async def test_search_returns_search_results(self) -> None:
        """search() must return SearchResult objects with the correct collection field."""
        settings = _make_settings()
        mock_client = _make_mock_client()

        point1 = _make_scored_point("id-1", "Gardener shoot cluster docs", score=0.95)
        point2 = _make_scored_point("id-2", "Seed cluster configuration", score=0.88)

        mock_client.query_points.return_value = QueryResponse(
            points=[point1, point2]
        )

        store = QdrantVectorStore(settings=settings, client=mock_client)
        results = await store.search(
            collection="gardener_docs",
            query_vector=[0.1] * 1536,
            limit=5,
        )

        assert len(results) == 2
        for result in results:
            assert isinstance(result, SearchResult)
            assert result.collection == "gardener_docs"

        assert results[0].id == "id-1"
        assert results[0].score == pytest.approx(0.95)
        assert results[0].content == "Gardener shoot cluster docs"

        assert results[1].id == "id-2"
        assert results[1].score == pytest.approx(0.88)

    @pytest.mark.asyncio
    async def test_health_check_returns_true(self) -> None:
        """health_check() must return True when get_collections() succeeds."""
        settings = _make_settings()
        mock_client = _make_mock_client()
        mock_client.get_collections.return_value = MagicMock()

        store = QdrantVectorStore(settings=settings, client=mock_client)
        result = await store.health_check()

        assert result is True
        mock_client.get_collections.assert_called_once()
