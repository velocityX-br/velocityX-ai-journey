"""FastMCP tool definitions for the SCI AI MCP server.

All 5 tools are registered here via the ``@mcp.tool`` decorator.
Dependencies (retrievers, LLM client) reach each handler exclusively
through the ``AppContext`` stored in FastMCP's lifespan state —
no concrete implementation is imported at module level.

Tool inventory:
    search_operation_docs — semantic search over operation documentation
    search_customer_docs  — semantic search over customer documentation
    search_docs           — hybrid search across BOTH collections (RRF)
    rag_retrieve          — low-level RAG retrieval on a named collection
    root_cause_analysis   — hybrid retrieval + LLM synthesis

Context injection (FastMCP v3.x):
    FastMCP injects a ``Context`` object into any tool function that
    declares a parameter annotated with the ``Context`` type.  The
    lifespan function yields ``{"app_context": AppContext}`` which is
    accessible via ``ctx.lifespan_context["app_context"]``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from fastmcp import Context

from retrieval.semantic import SemanticRetriever
from sci_mcp.models import (
    VALID_COLLECTIONS,
    ToolSearchResult,
)
from vectorstore.base import SearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight TTL cache for tool results
# ---------------------------------------------------------------------------


class _ToolCache:
    """In-process TTL + LRU cache for MCP tool results.

    Entries expire after ``ttl_seconds`` and the cache evicts the oldest
    entry (FIFO) once ``max_size`` is reached.  Both ``ttl_seconds=0``
    and ``max_size=0`` disable caching entirely.

    This is intentionally simple — no threading locks needed because the
    MCP server is single-threaded async.
    """

    def __init__(self, ttl_seconds: int, max_size: int) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        # Ordered dict: key → (expiry_timestamp, value)
        self._store: dict[str, tuple[float, Any]] = {}

    @property
    def enabled(self) -> bool:
        """Return True when caching is active."""
        return self._ttl > 0 and self._max_size > 0

    def make_key(self, tool_name: str, **kwargs: Any) -> str:
        """Derive a stable cache key from the tool name and call arguments."""
        payload = json.dumps({"tool": tool_name, **kwargs}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key: str) -> tuple[bool, Any]:
        """Return ``(True, value)`` on a live cache hit, else ``(False, None)``."""
        if not self.enabled or key not in self._store:
            return False, None
        expiry, value = self._store[key]
        if time.monotonic() > expiry:
            del self._store[key]
            return False, None
        logger.debug("Tool cache hit for key %s", key[:8])
        return True, value

    def set(self, key: str, value: Any) -> None:
        """Store a value with the configured TTL."""
        if not self.enabled:
            return
        # FIFO eviction when at capacity.
        if len(self._store) >= self._max_size:
            self._store.pop(next(iter(self._store)))
        self._store[key] = (time.monotonic() + self._ttl, value)


# Module-level cache instance; re-configured by configure_tool_cache() at startup.
_tool_cache: _ToolCache = _ToolCache(ttl_seconds=0, max_size=0)


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


def configure_tool_cache(ttl_seconds: int, max_size: int) -> None:
    """Configure the module-level tool result cache.

    Called from the server lifespan after settings are available.
    Can be called again to reconfigure (e.g. in tests).

    Args:
        ttl_seconds: Cache entry TTL. ``0`` disables caching.
        max_size: Maximum number of cached entries. ``0`` disables caching.
    """
    global _tool_cache
    _tool_cache = _ToolCache(ttl_seconds=ttl_seconds, max_size=max_size)
    if _tool_cache.enabled:
        logger.info(
            "Tool result cache enabled: ttl=%ds max_size=%d",
            ttl_seconds,
            max_size,
        )
    else:
        logger.info("Tool result cache disabled.")


def register_tools(mcp_app: Any) -> None:
    """Register all 5 MCP tools against the given ``FastMCP`` instance.

    This function is called from ``sci_mcp/server.py`` after the ``FastMCP``
    instance is created and the lifespan is configured.  Each inner
    ``async def`` is decorated with ``@mcp_app.tool`` at call time.

    Args:
        mcp_app: The ``FastMCP`` application instance.
    """

    @mcp_app.tool
    async def search_operation_docs(
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Search SCI operation documentation using semantic (dense-vector) similarity.

        Queries the ``sci_docs_operation`` Qdrant collection.  Results are
        ranked by cosine similarity between the embedded query and stored
        document vectors.

        Args:
            query: Natural language search query for operation documentation.
            limit: Maximum number of results to return (1-50).
            filters: Optional metadata filters e.g. {'content_type': 'operation'}.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching operation documentation chunks.
        """
        cache_key = _tool_cache.make_key(
            "search_operation_docs", query=query, limit=limit, filters=filters
        )
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

        app_ctx = _get_app_context(ctx)
        results = await app_ctx.operation_retriever.retrieve(
            query=query,
            filters=filters or None,
            limit=limit,
        )
        output = [_to_tool_result(r) for r in results]
        _tool_cache.set(cache_key, output)
        return output

    @mcp_app.tool
    async def search_customer_docs(
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Search SCI customer documentation using semantic (dense-vector) similarity.

        Queries the ``sci_docs_customer`` Qdrant collection.  Results are
        ranked by cosine similarity between the embedded query and stored
        document vectors.

        Args:
            query: Natural language search query for customer documentation.
            limit: Maximum number of results to return (1-50).
            filters: Optional metadata filters e.g. {'content_type': 'customer'}.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching customer documentation chunks.
        """
        cache_key = _tool_cache.make_key(
            "search_customer_docs", query=query, limit=limit, filters=filters
        )
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

        app_ctx = _get_app_context(ctx)
        results = await app_ctx.customer_retriever.retrieve(
            query=query,
            filters=filters or None,
            limit=limit,
        )
        output = [_to_tool_result(r) for r in results]
        _tool_cache.set(cache_key, output)
        return output

    @mcp_app.tool
    async def search_docs(
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        ctx: Context = None,
    ) -> list[ToolSearchResult]:
        """Search across BOTH SCI documentation collections with hybrid RRF fusion.

        Fans out dense + sparse searches over ``sci_docs_operation`` and
        ``sci_docs_customer`` concurrently, then merges the results with
        Reciprocal Rank Fusion into a single ranked list.

        Args:
            query: Natural language search query across all SCI documentation.
            limit: Maximum number of fused results to return (1-50).
            filters: Optional metadata filters applied to every sub-search.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A single ranked list merging results from both collections.
        """
        cache_key = _tool_cache.make_key(
            "search_docs", query=query, limit=limit, filters=filters
        )
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

        app_ctx = _get_app_context(ctx)
        results = await app_ctx.hybrid_retriever.retrieve(
            query=query,
            filters=filters or None,
            limit=limit,
        )
        output = [_to_tool_result(r) for r in results]
        _tool_cache.set(cache_key, output)
        return output

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
            collection: Qdrant collection to search: 'sci_docs_operation'
                or 'sci_docs_customer'.
            limit: Maximum number of results to return (1-50).
            filters: Optional metadata filters.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ranked list of matching document chunks from the specified
            collection.

        Raises:
            ValueError: If ``collection`` is not one of the two valid
                documentation collections.
        """
        if collection not in VALID_COLLECTIONS:
            raise ValueError(
                f"Invalid collection {collection!r}. "
                f"Must be one of: {sorted(VALID_COLLECTIONS)}"
            )

        cache_key = _tool_cache.make_key(
            "rag_retrieve", query=query, collection=collection, limit=limit, filters=filters
        )
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

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
        output = [_to_tool_result(r) for r in results]
        _tool_cache.set(cache_key, output)
        return output

    @mcp_app.tool
    async def root_cause_analysis(
        symptom: str,
        context: str | None = None,
        limit: int = 5,
        ctx: Context = None,
    ) -> str:
        """Perform a structured root cause analysis for an SCI problem.

        This tool:
        1. Uses ``HybridRetriever`` (RRF fusion across both documentation
           collections) to surface relevant operation and customer docs.
        2. Formats the retrieved documents as a context block.
        3. Calls the Anthropic-compatible LLM endpoint via SAP Hyperspace
           to synthesise a structured analysis.

        The LLM response follows this structure:
        - Most likely root cause
        - Supporting evidence from retrieved documents
        - Recommended remediation steps

        Args:
            symptom: Description of the problem or error being investigated
                e.g. 'Tenant provisioning stuck in pending state'.
            context: Additional context such as error messages, logs, or
                environment details.
            limit: Number of relevant documents to retrieve per collection
                for analysis (1-20).
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A structured root cause analysis as a plain-text string from
            the LLM.
        """
        cache_key = _tool_cache.make_key(
            "root_cause_analysis", symptom=symptom, context=context, limit=limit
        )
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

        app_ctx = _get_app_context(ctx)

        # Build the combined query for hybrid retrieval.
        combined_query = symptom
        if context:
            combined_query = f"{symptom} {context}"

        # Retrieve across both collections using HybridRetriever.
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
        user_parts: list[str] = [f"Symptom: {symptom}"]
        if context:
            user_parts.append(f"Additional context:\n{context}")
        user_parts.append(f"Retrieved documents:\n\n{context_block}")
        user_message = "\n\n".join(user_parts)

        system_prompt = (
            "You are an SAP Converged Infrastructure (SCI) expert. Analyse the"
            " symptom and retrieved context to produce a structured root cause"
            " analysis with: 1) Most likely root cause, 2) Supporting evidence"
            " from retrieved documents, 3) Recommended remediation steps."
        )

        response = await app_ctx.anthropic_client.messages.create(
            model=app_ctx.settings.anthropic_model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract the text content from the first content block.
        if response.content and hasattr(response.content[0], "text"):
            result = response.content[0].text
            _tool_cache.set(cache_key, result)
            return result

        logger.warning(
            "root_cause_analysis: unexpected LLM response shape — returning empty string"
        )
        return ""
