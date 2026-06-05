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
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP

from gardener_mcp.context import AppContext
from gardener_mcp.models import (
    RAGRetrieveInput,
    RootCauseAnalysisInput,
    SearchCodeInput,
    SearchDocsInput,
    SearchIssuesInput,
    SearchProposalsInput,
    ToolSearchResult,
)
from gardener_mcp.tools import register_tools
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

    Returns:
        A frozen ``AppContext`` whose injectable fields are all mocks.
    """
    if retriever_results is None:
        retriever_results = [_make_search_result()]
    if hybrid_results is None:
        hybrid_results = [_make_search_result()]

    mock_settings = MagicMock()
    mock_settings.anthropic_model = anthropic_model

    mock_embedder = MagicMock()

    mock_vector_store = MagicMock()

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

    return AppContext.model_construct(
        settings=mock_settings,
        embedder=mock_embedder,
        vector_store=mock_vector_store,
        semantic_retriever=mock_semantic_retriever,
        hybrid_retriever=mock_hybrid_retriever,
        anthropic_client=mock_anthropic_client,
    )


async def _call_tool(tool_name: str, inp: Any, app_ctx: AppContext) -> Any:
    """Invoke a registered tool directly by calling its underlying function.

    FastMCP stores tools in a registry accessible via ``get_tool``.
    We invoke the tool function directly, injecting a mock ``Context``
    whose ``lifespan_context`` returns the supplied ``AppContext``.

    Args:
        tool_name: The registered tool name (e.g. ``"search_docs"``).
        inp: The validated input model instance.
        app_ctx: The mock ``AppContext`` to inject.

    Returns:
        The raw return value of the tool function.
    """
    fresh_mcp = FastMCP("test-gardener")
    register_tools(fresh_mcp)

    # Build a mock FastMCP Context whose lifespan_context contains our AppContext.
    mock_ctx = MagicMock()
    mock_ctx.lifespan_context = {"app_context": app_ctx}

    # Retrieve the tool's underlying function from the FastMCP registry and
    # call it directly, bypassing the MCP protocol layer.
    tool = await fresh_mcp.get_tool(tool_name)
    # The tool's fn is the original async def.
    return await tool.fn(inp=inp, ctx=mock_ctx)


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
        await tool.fn(inp=inp, ctx=mock_ctx)

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
        await tool.fn(inp=inp, ctx=mock_ctx)

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
        await tool.fn(inp=inp, ctx=mock_ctx)

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
    await tool.fn(inp=inp, ctx=mock_ctx)

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
    result = await tool.fn(inp=inp, ctx=mock_ctx)

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
        await tool.fn(inp=inp, ctx=mock_ctx)

    # The SemanticRetriever must have been constructed with the target collection.
    MockRetriever.assert_called_once()
    ctor_kwargs = MockRetriever.call_args.kwargs
    assert ctor_kwargs.get("collection") == "gardener_code"
