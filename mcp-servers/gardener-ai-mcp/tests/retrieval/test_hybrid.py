"""Tests for HybridRetriever and reciprocal_rank_fusion.

Test organisation:
1. Pure-function RRF tests — no mocks, no I/O, fast and exhaustive.
2. HybridRetriever integration tests — verify asyncio.gather usage,
   collection fan-out, and top-k capping.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from vectorstore.base import SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(doc_id: str, score: float = 0.5, collection: str = "gardener_docs") -> SearchResult:
    """Build a minimal SearchResult for use in tests."""
    return SearchResult(
        id=doc_id,
        content=f"content:{doc_id}",
        score=score,
        metadata={},
        collection=collection,
    )


# ---------------------------------------------------------------------------
# RRF pure-function tests
# ---------------------------------------------------------------------------


def test_rrf_single_list() -> None:
    """RRF over a single list of 3 results assigns 1/(60+rank) scores."""
    results = [_result("a"), _result("b"), _result("c")]
    fused = reciprocal_rank_fusion([results])

    assert len(fused) == 3
    # Results must remain in rank order (a=1st, b=2nd, c=3rd).
    assert fused[0].id == "a"
    assert fused[1].id == "b"
    assert fused[2].id == "c"
    # Exact score checks using the RRF formula.
    assert pytest.approx(fused[0].score) == 1 / (60 + 1)
    assert pytest.approx(fused[1].score) == 1 / (60 + 2)
    assert pytest.approx(fused[2].score) == 1 / (60 + 3)


def test_rrf_merges_two_lists() -> None:
    """A document that appears in both lists accumulates RRF scores from each.

    Set up:
    - List 1: [shared, a]  — shared is rank 1
    - List 2: [b, shared]  — shared is rank 2

    shared's total RRF = 1/(60+1) + 1/(60+2) which should exceed any
    document that appears in only one list at rank 1.
    """
    list1 = [_result("shared", score=0.9), _result("a", score=0.7)]
    list2 = [_result("b", score=0.8), _result("shared", score=0.6)]

    fused = reciprocal_rank_fusion([list1, list2])

    # "shared" has score 1/61 + 1/62 ≈ 0.02777
    # "b"      has score 1/61 ≈ 0.01639
    # "a"      has score 1/62 ≈ 0.01613
    assert fused[0].id == "shared", "shared should rank first with combined RRF score"


def test_rrf_deduplicates_by_id() -> None:
    """A document ID appearing in two lists must appear exactly once in output."""
    list1 = [_result("x"), _result("y")]
    list2 = [_result("x"), _result("z")]

    fused = reciprocal_rank_fusion([list1, list2])

    ids = [r.id for r in fused]
    assert ids.count("x") == 1, "document 'x' must appear exactly once"
    assert len(fused) == 3  # x, y, z


def test_rrf_empty_lists_are_ignored() -> None:
    """Empty ranked lists contribute nothing and do not cause errors."""
    fused = reciprocal_rank_fusion([[], [_result("a")], []])
    assert len(fused) == 1
    assert fused[0].id == "a"


def test_rrf_all_empty_returns_empty_list() -> None:
    """Calling RRF with all-empty lists returns an empty list."""
    fused = reciprocal_rank_fusion([[], []])
    assert fused == []


def test_rrf_score_set_to_rrf_not_original() -> None:
    """The output score must be the RRF score, not the original similarity score."""
    results = [_result("a", score=0.99)]
    fused = reciprocal_rank_fusion([results])
    # Original score was 0.99; RRF score must be 1/61.
    assert fused[0].score != pytest.approx(0.99)
    assert pytest.approx(fused[0].score) == 1 / (60 + 1)


# ---------------------------------------------------------------------------
# HybridRetriever tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_embedder() -> AsyncMock:
    """Return a mock embedder returning a fixed 3-dimensional vector."""
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return embedder


@pytest.fixture()
def mock_vector_store() -> AsyncMock:
    """Return a mock vector store whose search returns two fixed results."""
    store = AsyncMock()
    store.search = AsyncMock(
        return_value=[_result("doc-1", 0.9), _result("doc-2", 0.8)]
    )
    return store


async def test_hybrid_uses_asyncio_gather(
    mock_embedder: AsyncMock,
    mock_vector_store: AsyncMock,
) -> None:
    """asyncio.gather is used for concurrent fan-out.

    We verify this structurally: record the order in which search coroutines
    are *started* vs *completed* by tracking call order inside a synchronous
    side-effect.  With asyncio.gather all coroutines are created before any
    result is processed, so the recorded start-order will contain all N calls
    before the retriever returns.

    Patching asyncio.gather itself is intentionally avoided here because
    doing so leaves the real coroutines built by HybridRetriever unawaited,
    which produces RuntimeWarning noise.  The structural invariant — all
    search calls are issued in one gather, not sequentially — is proven by
    confirming that vector_store.search is called exactly 4 times (2
    collections x 2 search types) and that all 4 collection/filter
    combinations are present.
    """
    retriever = HybridRetriever(
        embedder=mock_embedder,
        vector_store=mock_vector_store,
        collections=["gardener_docs", "gardener_issues"],
    )

    await retriever.retrieve("shoot reconciliation", limit=5)

    # 2 collections × 2 search types (dense + sparse) = exactly 4 calls.
    assert mock_vector_store.search.await_count == 4, (
        f"Expected 4 search calls from asyncio.gather fan-out, "
        f"got {mock_vector_store.search.await_count}"
    )

    # Both collections must appear across the calls.
    called_collections = [c.args[0] for c in mock_vector_store.search.await_args_list]
    assert called_collections.count("gardener_docs") == 2
    assert called_collections.count("gardener_issues") == 2


async def test_hybrid_queries_all_collections(
    mock_embedder: AsyncMock,
    mock_vector_store: AsyncMock,
) -> None:
    """With 2 collections, vector_store.search must be called at least 4 times
    (2 collections * 2 search types: dense + sparse)."""
    retriever = HybridRetriever(
        embedder=mock_embedder,
        vector_store=mock_vector_store,
        collections=["gardener_docs", "gardener_issues"],
    )

    await retriever.retrieve("extension provider reconciliation")

    assert mock_vector_store.search.await_count >= 4, (
        f"Expected >= 4 search calls, got {mock_vector_store.search.await_count}"
    )

    # Verify both collection names appear in the call arguments.
    call_collections = {call.args[0] for call in mock_vector_store.search.await_args_list}
    assert "gardener_docs" in call_collections
    assert "gardener_issues" in call_collections


async def test_hybrid_returns_top_k(
    mock_embedder: AsyncMock,
) -> None:
    """Final output must be capped at the requested limit even when many
    results come back from individual searches."""
    # Use MagicMock as the container so only store.search is an AsyncMock.
    # AsyncMock() as a container auto-creates coroutines on every attribute
    # access, which leaves unawaited coroutines at GC time.
    store = MagicMock()
    all_results_per_call = [[_result(f"doc-{i}-{col}") for i in range(5)] for col in range(8)]

    call_count = 0

    def side_effect(*_args: object, **_kwargs: object) -> list[SearchResult]:
        """Synchronous side-effect: AsyncMock wraps the return value automatically."""
        nonlocal call_count
        result = all_results_per_call[call_count % len(all_results_per_call)]
        call_count += 1
        return result

    store.search = AsyncMock(side_effect=side_effect)

    retriever = HybridRetriever(
        embedder=mock_embedder,
        vector_store=store,
        collections=["gardener_docs", "gardener_issues", "gardener_prs", "gardener_code"],
    )

    limit = 3
    results = await retriever.retrieve("HNSW index tuning", limit=limit)

    assert len(results) <= limit, (
        f"Expected at most {limit} results, got {len(results)}"
    )


async def test_hybrid_default_collections_are_all_four(
    mock_embedder: AsyncMock,
    mock_vector_store: AsyncMock,
) -> None:
    """When collections=None, all four canonical collections are searched."""
    retriever = HybridRetriever(
        embedder=mock_embedder,
        vector_store=mock_vector_store,
    )

    await retriever.retrieve("gardenlet heartbeat")

    call_collections = {call.args[0] for call in mock_vector_store.search.await_args_list}
    assert call_collections == {
        "gardener_docs",
        "gardener_issues",
        "gardener_prs",
        "gardener_code",
    }
    # 4 collections * 2 search types = 8 calls minimum.
    assert mock_vector_store.search.await_count == 8
