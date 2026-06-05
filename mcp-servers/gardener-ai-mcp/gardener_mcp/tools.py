"""FastMCP tool definitions for the Gardener AI MCP server.

All 7 tools are registered here via the ``@mcp.tool`` decorator.
Dependencies (retrievers, LLM client) reach each handler exclusively
through the ``AppContext`` stored in FastMCP's lifespan state —
no concrete implementation is imported at module level (ADR-004).

Tool inventory:
    search_docs         — semantic search over Gardener documentation
    search_issues       — semantic search over GitHub issues
    search_prs          — semantic search over GitHub pull requests
    search_proposals    — semantic search over GEPs (filtered docs)
    search_code         — semantic search over Go source code
    rag_retrieve        — low-level RAG retrieval on a named collection
    root_cause_analysis — hybrid retrieval + LLM synthesis (ADR-005)

Context injection (FastMCP v3.x):
    FastMCP injects a ``Context`` object into any tool function that
    declares a parameter annotated with the ``Context`` type.  The
    lifespan function yields ``{"app_context": AppContext}`` which is
    accessible via ``ctx.lifespan_context["app_context"]``.

    This module imports ``mcp`` from ``gardener_mcp.server`` to avoid a circular
    import: ``server.py`` creates the ``FastMCP`` instance and this
    module registers decorators against it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context

from gardener_mcp.models import ToolSearchResult
from retrieval.semantic import SemanticRetriever
from vectorstore.base import SearchResult

logger = logging.getLogger(__name__)


def _to_tool_result(result: SearchResult) -> ToolSearchResult:
    """Convert a ``SearchResult`` from the retrieval layer to a ``ToolSearchResult``.

    Args:
        result: A ``SearchResult`` as returned by any retriever.

    Returns:
        A ``ToolSearchResult`` with the ``source`` field populated from
        the ``url`` metadata key when present.
    """
    return ToolSearchResult(
        id=result.id,
        content=result.content,
        score=result.score,
        metadata=result.metadata,
        collection=result.collection,
        source=result.metadata.get("url"),
    )


def _merge_filters(
    base: dict[str, Any] | None,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Merge an optional base filter dict with additional key/value pairs.

    If ``base`` is ``None`` and ``extra`` is empty, returns ``None`` so
    that retrievers receive a clean no-filter signal.

    Args:
        base: Optional caller-supplied filters.
        extra: Additional filters to merge in.  Values that are ``None``
            are excluded from the merge.

    Returns:
        A combined filter dict, or ``None`` when both inputs are empty.
    """
    merged: dict[str, Any] = dict(base) if base else {}
    for key, value in extra.items():
        if value is not None:
            merged[key] = value
    return merged if merged else {}


def _get_app_context(ctx: Context) -> Any:
    """Extract the ``AppContext`` from FastMCP's lifespan state.

    Args:
        ctx: The FastMCP ``Context`` injected by the framework.

    Returns:
        The ``AppContext`` stored under the ``"app_context"`` key.

    Raises:
        KeyError: If the lifespan did not store an ``AppContext``.
    """
    return ctx.lifespan_context["app_context"]


# ---------------------------------------------------------------------------
# Tool registration — these decorators are applied when server.py does
# ``from mcp import tools``, at which point ``mcp`` (the FastMCP instance)
# must already exist.  We defer the import via a module-level variable set
# by server.py before the import occurs.
# ---------------------------------------------------------------------------

# The FastMCP instance is injected here by server.py via ``set_mcp_instance``.
_mcp_instance: Any = None


def set_mcp_instance(instance: Any) -> None:
    """Register the FastMCP instance used for tool decoration.

    Called exactly once by ``gardener_mcp/server.py`` before importing this module.
    This breaks the circular dependency without requiring a shared global
    module that both files import.

    Args:
        instance: The ``FastMCP`` application instance.
    """
    global _mcp_instance
    _mcp_instance = instance


def register_tools(mcp_app: Any) -> None:
    """Register all 7 MCP tools against the given ``FastMCP`` instance.

    This function is called from ``gardener_mcp/server.py`` after the ``FastMCP``
    instance is created and the lifespan is configured.  Each inner
    ``async def`` is decorated with ``@mcp_app.tool`` at call time.

    Args:
        mcp_app: The ``FastMCP`` application instance.
    """

    @mcp_app.tool
    async def search_docs(
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Search Gardener documentation using semantic (dense-vector) similarity.

        Queries the ``gardener_docs`` Qdrant collection.  Results are
        ranked by cosine similarity between the embedded query and stored
        document vectors.

        Args:
            query: Natural language search query for Gardener documentation.
            limit: Maximum number of results to return (1-50).
            filters: Optional metadata filters e.g. {'content_type': 'doc'}.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching documentation chunks.
        """
        app_ctx = _get_app_context(ctx)
        results = await app_ctx.semantic_retriever.retrieve(
            query=query,
            filters=filters or None,
            limit=limit,
        )
        return [_to_tool_result(r) for r in results]

    @mcp_app.tool
    async def search_issues(
        query: str,
        limit: int = 10,
        state: str | None = None,
        labels: list[str] | None = None,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Search GitHub issues from the Gardener repository.

        Queries the ``gardener_issues`` Qdrant collection.  The ``state``
        and ``labels`` parameters are translated into Qdrant payload
        filters and merged with any caller-supplied ``filters``.

        Args:
            query: Natural language search query for GitHub issues.
            limit: Maximum number of results to return (1-50).
            state: Filter by issue state: 'open', 'closed', or None for all.
            labels: Filter by label names e.g. ['bug', 'help wanted'].
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching issue chunks.
        """
        app_ctx = _get_app_context(ctx)

        extra: dict[str, Any] = {}
        if state is not None:
            extra["state"] = state
        if labels is not None:
            extra["labels"] = labels

        filters = _merge_filters(None, extra) or None

        retriever = SemanticRetriever(
            embedder=app_ctx.embedder,
            vector_store=app_ctx.vector_store,
            collection="gardener_issues",
        )
        results = await retriever.retrieve(
            query=query,
            filters=filters,
            limit=limit,
        )
        return [_to_tool_result(r) for r in results]

    @mcp_app.tool
    async def search_prs(
        query: str,
        limit: int = 10,
        state: str | None = None,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Search pull requests from the Gardener repository.

        Queries the ``gardener_prs`` Qdrant collection.  The ``state``
        parameter is translated into a Qdrant payload filter.

        Args:
            query: Natural language search query for pull requests.
            limit: Maximum number of results to return (1-50).
            state: Filter by PR state: 'open', 'closed', 'merged', or None for all.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching pull request chunks.
        """
        app_ctx = _get_app_context(ctx)

        extra: dict[str, Any] = {}
        if state is not None:
            extra["state"] = state

        filters = _merge_filters(None, extra) or None

        retriever = SemanticRetriever(
            embedder=app_ctx.embedder,
            vector_store=app_ctx.vector_store,
            collection="gardener_prs",
        )
        results = await retriever.retrieve(
            query=query,
            filters=filters,
            limit=limit,
        )
        return [_to_tool_result(r) for r in results]

    @mcp_app.tool
    async def search_proposals(
        query: str,
        limit: int = 10,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Search Gardener Enhancement Proposals (GEPs).

        Queries the ``gardener_docs`` collection with a fixed
        ``content_type="proposal"`` filter to restrict results to GEP
        documents only.

        Args:
            query: Natural language search query for Gardener enhancement proposals (GEPs).
            limit: Maximum number of results to return (1-50).
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching GEP chunks.
        """
        app_ctx = _get_app_context(ctx)

        filters: dict[str, Any] = {"content_type": "proposal"}

        retriever = SemanticRetriever(
            embedder=app_ctx.embedder,
            vector_store=app_ctx.vector_store,
            collection="gardener_docs",
        )
        results = await retriever.retrieve(
            query=query,
            filters=filters,
            limit=limit,
        )
        return [_to_tool_result(r) for r in results]

    @mcp_app.tool
    async def search_code(
        query: str,
        limit: int = 10,
        repo: str | None = None,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Search Gardener Go source code.

        Queries the ``gardener_code`` Qdrant collection.  The ``repo``
        parameter restricts results to a specific repository slug.

        Args:
            query: Natural language search query for Gardener Go source code.
            limit: Maximum number of results to return (1-50).
            repo: Filter by repository name e.g. 'gardener/gardener'.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching source code chunks.
        """
        app_ctx = _get_app_context(ctx)

        extra: dict[str, Any] = {}
        if repo is not None:
            extra["repo"] = repo

        filters = _merge_filters(None, extra) or None

        retriever = SemanticRetriever(
            embedder=app_ctx.embedder,
            vector_store=app_ctx.vector_store,
            collection="gardener_code",
        )
        results = await retriever.retrieve(
            query=query,
            filters=filters,
            limit=limit,
        )
        return [_to_tool_result(r) for r in results]

    @mcp_app.tool
    async def rag_retrieve(
        query: str,
        collection: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Low-level RAG retrieval against a named Qdrant collection.

        Creates a ``SemanticRetriever`` on the fly for the specified
        collection.  Use the higher-level ``search_*`` tools when the
        target collection is known at call time.

        Args:
            query: Natural language query for RAG retrieval.
            collection: Qdrant collection to search: 'gardener_docs',
                'gardener_issues', 'gardener_prs', or 'gardener_code'.
            limit: Maximum number of results to return (1-50).
            filters: Optional metadata filters.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching document chunks from the specified
            collection.
        """
        app_ctx = _get_app_context(ctx)

        retriever = SemanticRetriever(
            embedder=app_ctx.embedder,
            vector_store=app_ctx.vector_store,
            collection=collection,
        )
        results = await retriever.retrieve(
            query=query,
            filters=filters,
            limit=limit,
        )
        return [_to_tool_result(r) for r in results]

    @mcp_app.tool
    async def root_cause_analysis(
        symptom: str,
        context: str | None = None,
        limit: int = 5,
        ctx: Context = None,
    ) -> str:
        """Perform a structured root cause analysis for a Gardener problem.

        This tool:
        1. Uses ``HybridRetriever`` (RRF fusion across all four collections)
           to surface relevant documentation, issues, PRs, and code
           (ADR-005).
        2. Formats the retrieved documents as a context block.
        3. Calls the Anthropic-compatible LLM endpoint via SAP Hyperspace
           (ADR-006) to synthesise a structured analysis.

        The LLM response follows this structure:
        - Most likely root cause
        - Supporting evidence from retrieved documents
        - Recommended remediation steps

        Args:
            symptom: Description of the problem or error being investigated
                e.g. 'Shoot cluster stuck in Reconciling state'.
            context: Additional context such as error messages, logs, or
                environment details.
            limit: Number of relevant documents to retrieve per collection
                for analysis (1-20).
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A structured root cause analysis as a plain-text string from
            the LLM.
        """
        app_ctx = _get_app_context(ctx)

        # Build the combined query for hybrid retrieval.
        combined_query = symptom
        if context:
            combined_query = f"{symptom} {context}"

        # Retrieve across all four collections using HybridRetriever (ADR-005).
        retrieved = await app_ctx.hybrid_retriever.retrieve(
            query=combined_query,
            limit=limit,
        )

        # Format retrieved documents as a numbered context block.
        context_lines: list[str] = []
        for idx, doc in enumerate(retrieved, start=1):
            source_label = doc.metadata.get("url") or doc.metadata.get("source", "unknown")
            context_lines.append(
                f"[{idx}] Collection: {doc.collection} | Source: {source_label}\n"
                f"{doc.content}"
            )
        context_block = "\n\n---\n\n".join(context_lines)

        # Build the user message.
        user_parts: list[str] = [
            f"Symptom: {symptom}",
        ]
        if context:
            user_parts.append(f"Additional context:\n{context}")
        user_parts.append(f"Retrieved documents:\n\n{context_block}")
        user_message = "\n\n".join(user_parts)

        system_prompt = (
            "You are a Gardener Kubernetes expert. Analyse the symptom and retrieved"
            " context to produce a structured root cause analysis with:"
            " 1) Most likely root cause,"
            " 2) Supporting evidence from retrieved documents,"
            " 3) Recommended remediation steps."
        )

        response = await app_ctx.anthropic_client.messages.create(
            model=app_ctx.settings.anthropic_model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract the text content from the first content block.
        if response.content and hasattr(response.content[0], "text"):
            return response.content[0].text

        logger.warning("root_cause_analysis: unexpected LLM response shape — returning empty string")
        return ""
