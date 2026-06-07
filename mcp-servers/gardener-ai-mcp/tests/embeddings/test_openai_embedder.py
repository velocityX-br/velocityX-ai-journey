"""Tests for HyperspaceEmbedder.

These tests mock the HTTP layer via ``respx`` so no real network calls
are made.  The ``openai.AsyncOpenAI`` client is constructed inside each
test with ``base_url="http://test"`` and ``api_key="test"`` and then
passed to ``HyperspaceEmbedder`` via dependency injection.

ADR note: ``respx.mock(base_url="http://test")`` intercepts all httpx
requests to that origin.  The openai SDK's default retry logic is
disabled (``max_retries=0``) so that tenacity (our own retry layer) is
the only retry mechanism under test.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import openai
import pytest
import respx

from config.settings import Settings
from embeddings.openai_embedder import HyperspaceEmbedder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(cache_size: int = 0) -> Settings:
    """Return a minimal Settings instance suitable for unit tests.

    ``pydantic-settings`` resolves ``validation_alias=AliasChoices`` fields
    from keyword arguments using the *alias* names, not the Python attribute
    names.  The unprefixed alias names (e.g. ``GITHUB_TOKEN``) are used here
    to keep the helper concise.

    Args:
        cache_size: Value for ``EMBEDDING_CACHE_SIZE``.  Defaults to ``0``
            (disabled) so existing tests are unaffected.
    """
    return Settings(
        GITHUB_TOKEN="test-token",
        HYPERSPACE_OPENAI_BASE_URL="http://test/v1",
        ANTHROPIC_AUTH_TOKEN="test-key",
        EMBEDDING_MODEL="text-embedding-3-small",
        EMBEDDING_DIMENSIONS=3,
        EMBEDDING_CACHE_SIZE=cache_size,
    )


def _make_embedding_response(
    vectors: list[list[float]],
    model: str = "text-embedding-3-small",
) -> bytes:
    """Serialise a mock OpenAI embeddings API response body.

    Args:
        vectors: One vector per input text.
        model: Model name to include in the response.

    Returns:
        JSON-encoded response bytes.
    """
    data: list[dict[str, Any]] = [
        {"object": "embedding", "index": i, "embedding": vec}
        for i, vec in enumerate(vectors)
    ]
    body = {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {"prompt_tokens": len(vectors) * 3, "total_tokens": len(vectors) * 3},
    }
    return json.dumps(body).encode()


def _ok_response(vectors: list[list[float]], model: str = "text-embedding-3-small") -> httpx.Response:
    """Build a 200 httpx.Response with the given embedding vectors."""
    return httpx.Response(
        200,
        content=_make_embedding_response(vectors, model),
        headers={"content-type": "application/json"},
    )


def _rate_limit_response() -> httpx.Response:
    """Build a 429 httpx.Response that the openai SDK converts to RateLimitError."""
    body = json.dumps({
        "error": {
            "message": "Rate limit exceeded",
            "type": "requests",
            "code": "rate_limit_exceeded",
        }
    }).encode()
    return httpx.Response(
        429,
        content=body,
        headers={"content-type": "application/json"},
    )


def _make_embedder(settings: Settings) -> HyperspaceEmbedder:
    """Construct a HyperspaceEmbedder whose client targets http://test/v1.

    The client has ``max_retries=0`` so that the openai SDK does not
    add its own retry loop on top of tenacity.

    Args:
        settings: Application settings (from ``_make_settings()``).

    Returns:
        A ``HyperspaceEmbedder`` ready to be called inside a
        ``respx.mock(base_url="http://test")`` block.
    """
    client = openai.AsyncOpenAI(
        base_url="http://test/v1",
        api_key="test",
        max_retries=0,
    )
    return HyperspaceEmbedder(settings=settings, client=client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHyperspaceEmbedder:
    """Unit tests for HyperspaceEmbedder."""

    @pytest.mark.asyncio
    async def test_embed_query_returns_vector(self) -> None:
        """embed_query must return a list[float] of the configured dimension."""
        settings = _make_settings()
        embedder = _make_embedder(settings)
        expected_vector = [0.1, 0.2, 0.3]

        async with respx.mock(base_url="http://test") as mock:
            mock.post("/v1/embeddings").mock(
                return_value=_ok_response([expected_vector])
            )
            result = await embedder.embed_query("What is Gardener?")

        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)
        assert len(result) == settings.embedding_dimensions
        assert result == pytest.approx(expected_vector)

    @pytest.mark.asyncio
    async def test_embed_documents_batches_at_2048(self) -> None:
        """2049 texts must produce exactly two HTTP calls: one of 2048 and one of 1."""
        settings = _make_settings()
        embedder = _make_embedder(settings)

        # 2049 single-word texts, each embedding returns a [0.0, 0.0, 0.0] vector.
        texts = [f"word{i}" for i in range(2049)]

        async with respx.mock(base_url="http://test") as mock:
            # Register the route; side_effect accepts a list of responses.
            route = mock.post("/v1/embeddings")
            route.side_effect = [
                # First call: 2048 texts -> 2048 vectors
                _ok_response([[0.0, 0.0, 0.0]] * 2048),
                # Second call: 1 text -> 1 vector
                _ok_response([[1.0, 1.0, 1.0]]),
            ]

            result = await embedder.embed_documents(texts)

        assert route.call_count == 2, (
            f"Expected 2 HTTP calls for 2049 texts (2048 + 1), got {route.call_count}"
        )
        assert len(result) == 2049

    @pytest.mark.asyncio
    async def test_retry_on_429(self) -> None:
        """A 429 on the first attempt must trigger a tenacity retry and succeed on the second."""
        settings = _make_settings()
        embedder = _make_embedder(settings)
        expected_vector = [0.5, 0.6, 0.7]

        # Patch wait_exponential to sleep(0) so the test does not actually wait.
        with patch("embeddings.openai_embedder.wait_exponential", return_value=lambda *_: 0):
            async with respx.mock(base_url="http://test") as mock:
                route = mock.post("/v1/embeddings")
                route.side_effect = [
                    _rate_limit_response(),  # attempt 1: 429
                    _ok_response([expected_vector]),  # attempt 2: 200
                ]

                result = await embedder.embed_query("shoot cluster crash")

        assert route.call_count == 2
        assert result == pytest.approx(expected_vector)

    @pytest.mark.asyncio
    async def test_uses_model_from_settings(self) -> None:
        """The request body sent to the API must include the model from settings."""
        settings = _make_settings()
        embedder = _make_embedder(settings)
        captured_requests: list[httpx.Request] = []

        async def _capture(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return _ok_response([[0.1, 0.2, 0.3]])

        async with respx.mock(base_url="http://test") as mock:
            mock.post("/v1/embeddings").mock(side_effect=_capture)
            await embedder.embed_query("gardener shoot reconciliation")

        assert len(captured_requests) == 1
        body = json.loads(captured_requests[0].content)
        assert body["model"] == "text-embedding-3-small"


class TestHyperspaceEmbedderCache:
    """Unit tests for embed_query in-process caching."""

    @pytest.mark.asyncio
    async def test_cache_hit_avoids_second_http_call(self) -> None:
        """Second embed_query call with the same text must not hit the HTTP API."""
        settings = _make_settings(cache_size=256)
        embedder = _make_embedder(settings)
        expected_vector = [0.1, 0.2, 0.3]

        async with respx.mock(base_url="http://test") as mock:
            route = mock.post("/v1/embeddings").mock(
                return_value=_ok_response([expected_vector])
            )
            first = await embedder.embed_query("certificate rotation")
            second = await embedder.embed_query("certificate rotation")

        # Only one HTTP call despite two embed_query calls.
        assert route.call_count == 1
        assert first == pytest.approx(expected_vector)
        assert second == pytest.approx(expected_vector)

    @pytest.mark.asyncio
    async def test_different_queries_each_call_api(self) -> None:
        """Two distinct queries must each trigger one HTTP call."""
        settings = _make_settings(cache_size=256)
        embedder = _make_embedder(settings)

        async with respx.mock(base_url="http://test") as mock:
            route = mock.post("/v1/embeddings")
            route.side_effect = [
                _ok_response([[0.1, 0.2, 0.3]]),
                _ok_response([[0.4, 0.5, 0.6]]),
            ]
            v1 = await embedder.embed_query("shoot cluster")
            v2 = await embedder.embed_query("etcd backup")

        assert route.call_count == 2
        assert v1 == pytest.approx([0.1, 0.2, 0.3])
        assert v2 == pytest.approx([0.4, 0.5, 0.6])

    @pytest.mark.asyncio
    async def test_cache_disabled_when_size_zero(self) -> None:
        """With cache_size=0 every call must go to the HTTP API."""
        settings = _make_settings(cache_size=0)
        embedder = _make_embedder(settings)
        vector = [0.1, 0.2, 0.3]

        async with respx.mock(base_url="http://test") as mock:
            route = mock.post("/v1/embeddings").mock(
                return_value=_ok_response([vector])
            )
            await embedder.embed_query("gardener shoot")
            await embedder.embed_query("gardener shoot")

        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_cache_fifo_eviction(self) -> None:
        """After the cache fills up the oldest entry is evicted."""
        settings = _make_settings(cache_size=2)
        embedder = _make_embedder(settings)

        async with respx.mock(base_url="http://test") as mock:
            route = mock.post("/v1/embeddings")
            route.side_effect = [
                _ok_response([[0.1, 0.1, 0.1]]),  # query A — fills slot 1
                _ok_response([[0.2, 0.2, 0.2]]),  # query B — fills slot 2
                _ok_response([[0.3, 0.3, 0.3]]),  # query C — evicts A, fills slot 2
                _ok_response([[0.4, 0.4, 0.4]]),  # query A (re-embed after eviction)
            ]
            await embedder.embed_query("alpha")   # call 1
            await embedder.embed_query("beta")    # call 2
            await embedder.embed_query("gamma")   # call 3 — evicts "alpha"
            result = await embedder.embed_query("alpha")  # call 4 — cache miss

        # All four queries triggered HTTP calls (alpha was evicted).
        assert route.call_count == 4
        assert result == pytest.approx([0.4, 0.4, 0.4])
