"""Unit tests for gardener_mcp/tools.py.

All tests use a mock ``AppContext`` built with ``model_construct`` to
bypass Pydantic field validation — this avoids needing real Qdrant or
Anthropic connections.  The ``register_tools`` function is called against
a fresh ``FastMCP`` instance per test to isolate tool registrations.

Test inventory:
    test_search_docs_calls_semantic_retriever
    test_search_issues_passes_state_filter
    test_search_proposals_adds_content_type_filter
    test_search_code_passes_repo_filter
    test_root_cause_analysis_uses_hybrid_retriever
    test_root_cause_analysis_calls_anthropic
    test_rag_retrieve_uses_specified_collection
    test_tool_cache_hit_skips_retriever
    test_tool_cache_disabled_calls_retriever_every_time
    test_tool_cache_ttl_expiry
    test_cache_sap_github_content_issue_routes_to_gardener_issues
    test_cache_sap_github_content_pr_routes_to_gardener_prs
    test_cache_sap_github_content_code_uses_code_chunker
    test_cache_sap_github_content_already_existed_flag
    test_cache_sap_github_content_invalid_type_raises
    test_cache_sap_github_content_empty_content_raises
    test_cache_sap_github_content_sets_sap_github_origin_metadata
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP

from gardener_mcp.context import AppContext
from gardener_mcp.models import (
    CacheSapGithubContentResult,
    RAGRetrieveInput,
    RootCauseAnalysisInput,
    SearchCodeInput,
    SearchDocsInput,
    SearchIssuesInput,
    SearchProposalsInput,
    ToolSearchResult,
)
from gardener_mcp.tools import configure_tool_cache, register_tools
from vectorstore.base import SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_search_result(collection: str = "gardener_docs") -> SearchResult:
    """Return a minimal ``SearchResult`` for use in mock return values."""
    return SearchResult(
        id="doc-1",
        content="Example content about Shoot clusters.",
        score=0.92,
        metadata={"url": "https://gardener.cloud/docs/example"},
        collection=collection,
    )


def make_mock_context(
    retriever_results: list[SearchResult] | None = None,
    hybrid_results: list[SearchResult] | None = None,
    anthropic_model: str = "anthropic--claude-sonnet-latest",
    embed_result: list[list[float]] | None = None,
    upsert_result: int = 3,
    probe_results: list[SearchResult] | None = None,
) -> AppContext:
    """Build a fully mocked ``AppContext`` for unit testing.

    Uses ``model_construct`` to bypass Pydantic validation so that
    ``AsyncMock`` / ``MagicMock`` instances are accepted for every field.

    Args:
        retriever_results: Results the mock ``SemanticRetriever.retrieve``
            should return.  Defaults to a single canned ``SearchResult``.
        hybrid_results: Results the mock ``HybridRetriever.retrieve``
            should return.  Defaults to a single canned ``SearchResult``.
        anthropic_model: Model name to embed in mock settings.
        embed_result: Vectors returned by ``embedder.embed``.  Defaults to
            three 3-dimensional zero vectors (enough for chunked content).
        upsert_result: Integer returned by ``vector_store.upsert``.
        probe_results: Results returned by the existence-probe
            ``SemanticRetriever.retrieve`` inside
            ``cache_sap_github_content``.  Empty list by default
            (content not yet cached).

    Returns:
        A frozen ``AppContext`` whose injectable fields are all mocks.
    """
    if retriever_results is None:
        retriever_results = [_make_search_result()]
    if hybrid_results is None:
        hybrid_results = [_make_search_result()]
    if embed_result is None:
        embed_result = [[0.0, 0.0, 0.0]] * 3
    if probe_results is None:
        probe_results = []

    mock_settings = MagicMock()
    mock_settings.anthropic_model = anthropic_model
    mock_settings.embedding_dimensions = 3

    mock_embedder = MagicMock()
    mock_embedder.embed = AsyncMock(return_value=embed_result)

    mock_vector_store = MagicMock()
    mock_vector_store.upsert = AsyncMock(return_value=upsert_result)
    mock_vector_store.ensure_collection = AsyncMock(return_value=None)

    mock_semantic_retriever = MagicMock()
    mock_semantic_retriever.retrieve = AsyncMock(return_value=retriever_results)

    mock_hybrid_retriever = MagicMock()
    mock_hybrid_retriever.retrieve = AsyncMock(return_value=hybrid_results)

    # Build a mock Anthropic response with a .text attribute.
    mock_content_block = MagicMock()
    mock_content_block.text = "Root cause: Shoot stuck in Reconciling."
    mock_anthropic_response = MagicMock()
    mock_anthropic_response.content = [mock_content_block]

    mock_anthropic_messages = MagicMock()
    mock_anthropic_messages.create = AsyncMock(return_value=mock_anthropic_response)

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = mock_anthropic_messages

    ctx = AppContext.model_construct(
        settings=mock_settings,
        embedder=mock_embedder,
        vector_store=mock_vector_store,
        semantic_retriever=mock_semantic_retriever,
        hybrid_retriever=mock_hybrid_retriever,
        anthropic_client=mock_anthropic_client,
    )

    # Attach probe_results so tests can configure the existence-probe
    # behaviour without patching SemanticRetriever globally.
    ctx.__dict__["_probe_results"] = probe_results
    return ctx


async def _call_tool(tool_name: str, inp: Any, app_ctx: AppContext) -> Any:
    """Invoke a registered tool directly by calling its underlying function.

    Translates the input model to flat keyword arguments that match the
    tool function's actual signature (tools use flat params, not ``inp=``).

    Args:
        tool_name: The registered tool name (e.g. ``"search_docs"``).
        inp: The validated input model instance.
        app_ctx: The mock ``AppContext`` to inject.

    Returns:
        The raw return value of the tool function.
    """
    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    tool = await fresh_mcp.get_tool(tool_name)
    # Tools use flat parameter signatures — unpack the input model as kwargs.
    kwargs = inp.model_dump(exclude_none=False)
    kwargs["ctx"] = mock_ctx
    return await tool.fn(**kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_docs_calls_semantic_retriever() -> None:
    """search_docs must delegate to semantic_retriever with the correct query."""
    app_ctx = make_mock_context()
    inp = SearchDocsInput(query="shoot cluster")

    results = await _call_tool("search_docs", inp, app_ctx)

    app_ctx.semantic_retriever.retrieve.assert_called_once()
    call_kwargs = app_ctx.semantic_retriever.retrieve.call_args
    assert call_kwargs.kwargs.get("query") == "shoot cluster" or call_kwargs.args[0] == "shoot cluster"

    assert isinstance(results, list)
    assert all(isinstance(r, ToolSearchResult) for r in results)


@pytest.mark.asyncio
async def test_search_issues_passes_state_filter() -> None:
    """search_issues must pass state='open' as a payload filter to the retriever."""
    app_ctx = make_mock_context()

    # Patch SemanticRetriever constructor so we can capture what filters were passed.
    captured_retrieve = AsyncMock(return_value=[_make_search_result("gardener_issues")])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        instance = MagicMock()
        instance.retrieve = captured_retrieve
        MockRetriever.return_value = instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("search_issues")
        inp = SearchIssuesInput(query="DNS failure", state="open")
        await tool.fn(**inp.model_dump(exclude_none=False), ctx=mock_ctx)

    captured_retrieve.assert_called_once()
    call_kwargs = captured_retrieve.call_args
    filters_passed = call_kwargs.kwargs.get("filters") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
    assert filters_passed is not None
    assert filters_passed.get("state") == "open"


@pytest.mark.asyncio
async def test_search_proposals_adds_content_type_filter() -> None:
    """search_proposals must always pass filters={'content_type': 'proposal'}."""
    app_ctx = make_mock_context()

    captured_retrieve = AsyncMock(return_value=[_make_search_result("gardener_docs")])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        instance = MagicMock()
        instance.retrieve = captured_retrieve
        MockRetriever.return_value = instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("search_proposals")
        inp = SearchProposalsInput(query="networking proposal")
        await tool.fn(**inp.model_dump(exclude_none=False), ctx=mock_ctx)

    captured_retrieve.assert_called_once()
    call_kwargs = captured_retrieve.call_args
    filters_passed = call_kwargs.kwargs.get("filters") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
    assert filters_passed is not None
    assert filters_passed.get("content_type") == "proposal"


@pytest.mark.asyncio
async def test_search_code_passes_repo_filter() -> None:
    """search_code must pass repo='gardener/gardener' as a payload filter."""
    app_ctx = make_mock_context()

    captured_retrieve = AsyncMock(return_value=[_make_search_result("gardener_code")])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        instance = MagicMock()
        instance.retrieve = captured_retrieve
        MockRetriever.return_value = instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("search_code")
        inp = SearchCodeInput(query="reconciler loop", repo="gardener/gardener")
        await tool.fn(**inp.model_dump(exclude_none=False), ctx=mock_ctx)

    captured_retrieve.assert_called_once()
    call_kwargs = captured_retrieve.call_args
    filters_passed = call_kwargs.kwargs.get("filters") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
    assert filters_passed is not None
    assert filters_passed.get("repo") == "gardener/gardener"


@pytest.mark.asyncio
async def test_root_cause_analysis_uses_hybrid_retriever() -> None:
    """root_cause_analysis must use hybrid_retriever — not semantic_retriever."""
    app_ctx = make_mock_context()

    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    tool = await fresh_mcp.get_tool("root_cause_analysis")
    inp = RootCauseAnalysisInput(
        symptom="Shoot cluster stuck in Reconciling state",
        context="Error: network timeout",
        limit=3,
    )
    await tool.fn(**inp.model_dump(exclude_none=False), ctx=mock_ctx)

    # hybrid_retriever.retrieve MUST have been called.
    app_ctx.hybrid_retriever.retrieve.assert_called_once()

    # semantic_retriever.retrieve must NOT have been called.
    app_ctx.semantic_retriever.retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_root_cause_analysis_calls_anthropic() -> None:
    """root_cause_analysis must call anthropic_client.messages.create with the correct model."""
    app_ctx = make_mock_context(anthropic_model="anthropic--claude-sonnet-latest")

    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    tool = await fresh_mcp.get_tool("root_cause_analysis")
    inp = RootCauseAnalysisInput(symptom="DNSRecord controller crashlooping")
    result = await tool.fn(**inp.model_dump(exclude_none=False), ctx=mock_ctx)

    app_ctx.anthropic_client.messages.create.assert_called_once()
    call_kwargs = app_ctx.anthropic_client.messages.create.call_args
    model_used = call_kwargs.kwargs.get("model") or call_kwargs.args[0]
    assert model_used == "anthropic--claude-sonnet-latest"

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_rag_retrieve_uses_specified_collection() -> None:
    """rag_retrieve must create a SemanticRetriever targeted at the named collection."""
    app_ctx = make_mock_context()

    captured_retrieve = AsyncMock(return_value=[_make_search_result("gardener_code")])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        instance = MagicMock()
        instance.retrieve = captured_retrieve
        MockRetriever.return_value = instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("rag_retrieve")
        inp = RAGRetrieveInput(query="admission webhook", collection="gardener_code")
        await tool.fn(**inp.model_dump(exclude_none=False), ctx=mock_ctx)

    # The SemanticRetriever must have been constructed with the target collection.
    MockRetriever.assert_called_once()
    ctor_kwargs = MockRetriever.call_args.kwargs
    assert ctor_kwargs.get("collection") == "gardener_code"


# ---------------------------------------------------------------------------
# Tool cache tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_cache_hit_skips_retriever() -> None:
    """Second identical search_docs call must return cached result without calling retriever."""
    configure_tool_cache(ttl_seconds=60, max_size=128)
    app_ctx = make_mock_context()

    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    tool = await fresh_mcp.get_tool("search_docs")

    # First call — hits retriever.
    await tool.fn(query="certificate rotation", limit=10, filters=None, ctx=mock_ctx)
    # Second call — identical args, should hit cache.
    await tool.fn(query="certificate rotation", limit=10, filters=None, ctx=mock_ctx)

    # Retriever must have been called exactly once.
    assert app_ctx.semantic_retriever.retrieve.call_count == 1

    # Reset cache to avoid leaking state into other tests.
    configure_tool_cache(ttl_seconds=0, max_size=0)


@pytest.mark.asyncio
async def test_tool_cache_disabled_calls_retriever_every_time() -> None:
    """With cache disabled (ttl=0 / max_size=0) every call must invoke the retriever."""
    configure_tool_cache(ttl_seconds=0, max_size=0)
    app_ctx = make_mock_context()

    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    tool = await fresh_mcp.get_tool("search_docs")
    await tool.fn(query="shoot", limit=5, filters=None, ctx=mock_ctx)
    await tool.fn(query="shoot", limit=5, filters=None, ctx=mock_ctx)

    assert app_ctx.semantic_retriever.retrieve.call_count == 2


@pytest.mark.asyncio
async def test_tool_cache_ttl_expiry() -> None:
    """An entry past its TTL must be treated as a cache miss."""
    configure_tool_cache(ttl_seconds=1, max_size=128)
    app_ctx = make_mock_context()

    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    tool = await fresh_mcp.get_tool("search_docs")

    # First call populates the cache.
    await tool.fn(query="etcd backup", limit=10, filters=None, ctx=mock_ctx)

    # Advance time past the TTL by monkey-patching time.monotonic.
    original_monotonic = time.monotonic
    future_time = original_monotonic() + 2  # 2s past the 1s TTL
    with patch("gardener_mcp.tools.time") as mock_time:
        mock_time.monotonic.return_value = future_time
        # Second call — entry expired, must re-invoke retriever.
        await tool.fn(query="etcd backup", limit=10, filters=None, ctx=mock_ctx)

    assert app_ctx.semantic_retriever.retrieve.call_count == 2

    # Reset cache.
    configure_tool_cache(ttl_seconds=0, max_size=0)


# ---------------------------------------------------------------------------
# cache_sap_github_content tests
# ---------------------------------------------------------------------------
#
# Naming convention:
#   "sap_github" = content from github.tools.sap via the github-tools MCP
#   "gardener"   = public github.com/gardener/* content (batch-ingested)


def _sap_github_issue_kwargs(
    content: str = "# Issue title\n\nIssue body from github.tools.sap.",
    url: str = "https://github.tools.sap/my-org/my-repo/issues/42",
    repo: str = "my-org/my-repo",
) -> dict:
    """Return minimal valid kwargs for cache_sap_github_content (issue type)."""
    return {
        "content_type": "issue",
        "content": content,
        "sap_github_url": url,
        "sap_github_repo": repo,
        "issue_metadata": {
            "issue_number": 42,
            "repo": repo,
            "title": "Test issue",
            "state": "open",
            "labels": ["bug"],
            "url": url,
        },
        "pr_metadata": None,
        "code_metadata": None,
    }


@pytest.mark.asyncio
async def test_cache_sap_github_content_issue_routes_to_gardener_issues() -> None:
    """content_type='issue' must upsert into the gardener_issues collection."""
    # probe_results=[] → content does not yet exist
    app_ctx = make_mock_context(probe_results=[])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        probe_instance = MagicMock()
        probe_instance.retrieve = AsyncMock(return_value=[])
        MockRetriever.return_value = probe_instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("cache_sap_github_content")
        result = await tool.fn(**_sap_github_issue_kwargs(), ctx=mock_ctx)

    assert isinstance(result, CacheSapGithubContentResult)
    assert result.collection == "gardener_issues"
    assert result.chunks_upserted > 0
    assert result.sap_github_url == "https://github.tools.sap/my-org/my-repo/issues/42"

    # Qdrant upsert must have been called with the issues collection.
    upsert_call = app_ctx.vector_store.upsert.call_args
    assert upsert_call.kwargs.get("collection") == "gardener_issues"


@pytest.mark.asyncio
async def test_cache_sap_github_content_pr_routes_to_gardener_prs() -> None:
    """content_type='pr' must upsert into the gardener_prs collection."""
    app_ctx = make_mock_context(probe_results=[])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        probe_instance = MagicMock()
        probe_instance.retrieve = AsyncMock(return_value=[])
        MockRetriever.return_value = probe_instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("cache_sap_github_content")
        pr_url = "https://github.tools.sap/my-org/my-repo/pull/7"
        result = await tool.fn(
            content_type="pr",
            content="## PR title\n\nPR body from github.tools.sap.",
            sap_github_url=pr_url,
            sap_github_repo="my-org/my-repo",
            issue_metadata=None,
            pr_metadata={
                "pr_number": 7,
                "repo": "my-org/my-repo",
                "title": "Fix reconciler",
                "state": "merged",
                "url": pr_url,
                "merged_at": "2026-06-01T10:00:00Z",
            },
            code_metadata=None,
            ctx=mock_ctx,
        )

    assert result.collection == "gardener_prs"
    upsert_call = app_ctx.vector_store.upsert.call_args
    assert upsert_call.kwargs.get("collection") == "gardener_prs"


@pytest.mark.asyncio
async def test_cache_sap_github_content_code_uses_code_chunker() -> None:
    """content_type='code' must use CodeChunker (not MarkdownChunker)."""
    app_ctx = make_mock_context(probe_results=[])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever, \
         patch("gardener_mcp.tools.CodeChunker") as MockCodeChunker, \
         patch("gardener_mcp.tools.MarkdownChunker") as MockMarkdownChunker:

        probe_instance = MagicMock()
        probe_instance.retrieve = AsyncMock(return_value=[])
        MockRetriever.return_value = probe_instance

        from ingestion.base import Document
        fake_chunk = Document(
            content="package main",
            metadata={"source_origin": "sap_github"},
            source="https://github.tools.sap/my-org/my-repo/blob/main/main.go",
        )
        mock_code_chunker_instance = MagicMock()
        mock_code_chunker_instance.chunk = MagicMock(return_value=[fake_chunk])
        MockCodeChunker.return_value = mock_code_chunker_instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("cache_sap_github_content")
        code_url = "https://github.tools.sap/my-org/my-repo/blob/main/main.go"
        await tool.fn(
            content_type="code",
            content="package main\n\nfunc main() {}",
            sap_github_url=code_url,
            sap_github_repo="my-org/my-repo",
            issue_metadata=None,
            pr_metadata=None,
            code_metadata={
                "repo": "my-org/my-repo",
                "file_path": "main.go",
                "url": code_url,
                "ref": "main",
                "language": "go",
            },
            ctx=mock_ctx,
        )

    # CodeChunker must have been instantiated and used.
    MockCodeChunker.assert_called_once()
    mock_code_chunker_instance.chunk.assert_called_once()
    # MarkdownChunker must NOT have been used.
    MockMarkdownChunker.assert_not_called()


@pytest.mark.asyncio
async def test_cache_sap_github_content_already_existed_flag() -> None:
    """already_existed=True when the probe finds an existing chunk for the URL."""
    existing = SearchResult(
        id="existing-chunk-1",
        content="Previous version of this SAP GitHub issue.",
        score=0.99,
        metadata={"url": "https://github.tools.sap/my-org/my-repo/issues/42"},
        collection="gardener_issues",
    )
    app_ctx = make_mock_context(probe_results=[existing])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        # First call (probe) returns existing chunk; subsequent calls return nothing.
        probe_instance = MagicMock()
        probe_instance.retrieve = AsyncMock(return_value=[existing])
        MockRetriever.return_value = probe_instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("cache_sap_github_content")
        result = await tool.fn(**_sap_github_issue_kwargs(), ctx=mock_ctx)

    assert result.already_existed is True


@pytest.mark.asyncio
async def test_cache_sap_github_content_invalid_type_raises() -> None:
    """An unrecognised content_type must raise ValueError."""
    app_ctx = make_mock_context()

    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    tool = await fresh_mcp.get_tool("cache_sap_github_content")
    with pytest.raises(ValueError, match="Invalid content_type"):
        await tool.fn(
            content_type="unknown",
            content="Some content",
            sap_github_url="https://github.tools.sap/org/repo/issues/1",
            sap_github_repo="org/repo",
            issue_metadata=None,
            pr_metadata=None,
            code_metadata=None,
            ctx=mock_ctx,
        )


@pytest.mark.asyncio
async def test_cache_sap_github_content_empty_content_raises() -> None:
    """Empty content must raise ValueError before any embedding is attempted."""
    app_ctx = make_mock_context()

    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    tool = await fresh_mcp.get_tool("cache_sap_github_content")
    with pytest.raises(ValueError, match="content must not be empty"):
        await tool.fn(
            content_type="issue",
            content="   ",
            sap_github_url="https://github.tools.sap/org/repo/issues/1",
            sap_github_repo="org/repo",
            issue_metadata=None,
            pr_metadata=None,
            code_metadata=None,
            ctx=mock_ctx,
        )

    # embedder.embed must NOT have been called.
    app_ctx.embedder.embed.assert_not_called()


@pytest.mark.asyncio
async def test_cache_sap_github_content_sets_sap_github_origin_metadata() -> None:
    """Every chunk written to Qdrant must carry source_origin='sap_github' in metadata.

    This ensures SAP GitHub content can always be distinguished from
    public github.com/gardener/* content in search results.
    """
    app_ctx = make_mock_context(probe_results=[])

    with patch("gardener_mcp.tools.SemanticRetriever") as MockRetriever:
        probe_instance = MagicMock()
        probe_instance.retrieve = AsyncMock(return_value=[])
        MockRetriever.return_value = probe_instance

        fresh_mcp = FastMCP("test-gardener")
        register_tools(fresh_mcp)

        mock_ctx = MagicMock()
        mock_ctx.lifespan_context = {"app_context": app_ctx}

        tool = await fresh_mcp.get_tool("cache_sap_github_content")
        await tool.fn(**_sap_github_issue_kwargs(), ctx=mock_ctx)

    # Inspect documents passed to vector_store.upsert.
    upsert_call = app_ctx.vector_store.upsert.call_args
    docs_written = upsert_call.kwargs.get("documents") or upsert_call.args[1]
    assert docs_written, "No documents were passed to upsert"
    for doc in docs_written:
        assert doc.metadata.get("source_origin") == "sap_github", (
            f"Chunk missing source_origin='sap_github': {doc.metadata}"
        )
