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

import hashlib
import json
import logging
import time
from typing import Any

from fastmcp import Context

from gardener_mcp.models import (
    CacheSapGithubContentResult,
    SAP_GITHUB_CONTENT_TYPES,
    ToolSearchResult,
    _SAP_GITHUB_TARGET_COLLECTIONS,
)
from ingestion.base import Document
from ingestion.chunking import CodeChunker, MarkdownChunker
from retrieval.semantic import SemanticRetriever
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

    def _make_key(self, tool_name: str, **kwargs: Any) -> str:
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


# Module-level cache instance; re-configured by register_tools() at startup.
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
    """Register all 7 MCP tools against the given ``FastMCP`` instance.

    This function is called from ``gardener_mcp/server.py`` after the ``FastMCP``
    instance is created and the lifespan is configured.  Each inner
    ``async def`` is decorated with ``@mcp_app.tool`` at call time.

    The tool-level TTL cache is initialised here from the application
    settings so that the cache size and TTL are configurable at runtime
    without touching this module.

    Args:
        mcp_app: The ``FastMCP`` application instance.
    """
    global _tool_cache

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
        cache_key = _tool_cache._make_key("search_docs", query=query, limit=limit, filters=filters)
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

        app_ctx = _get_app_context(ctx)
        results = await app_ctx.semantic_retriever.retrieve(
            query=query,
            filters=filters or None,
            limit=limit,
        )
        output = [_to_tool_result(r) for r in results]
        _tool_cache.set(cache_key, output)
        return output

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

        cache_key = _tool_cache._make_key("search_issues", query=query, limit=limit, state=state, labels=labels)
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

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
        output = [_to_tool_result(r) for r in results]
        _tool_cache.set(cache_key, output)
        return output

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

        cache_key = _tool_cache._make_key("search_prs", query=query, limit=limit, state=state)
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

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
        output = [_to_tool_result(r) for r in results]
        _tool_cache.set(cache_key, output)
        return output

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

        cache_key = _tool_cache._make_key("search_proposals", query=query, limit=limit)
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

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
        output = [_to_tool_result(r) for r in results]
        _tool_cache.set(cache_key, output)
        return output

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

        cache_key = _tool_cache._make_key("search_code", query=query, limit=limit, repo=repo)
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

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

        cache_key = _tool_cache._make_key("rag_retrieve", query=query, collection=collection, limit=limit, filters=filters)
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

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

        cache_key = _tool_cache._make_key("root_cause_analysis", symptom=symptom, context=context, limit=limit)
        hit, cached = _tool_cache.get(cache_key)
        if hit:
            return cached

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
            result = response.content[0].text
            _tool_cache.set(cache_key, result)
            return result

        logger.warning("root_cause_analysis: unexpected LLM response shape — returning empty string")
        return ""

    # ---------------------------------------------------------------------------
    # SAP GitHub → Qdrant write-through cache
    # ---------------------------------------------------------------------------

    # Collection routing: content_type → Qdrant collection name.
    # Kept here (not in models.py) so the tool owns its routing logic.
    _SAP_GITHUB_COLLECTION_MAP: dict[str, str] = {
        "issue": "gardener_issues",
        "pr": "gardener_prs",
        "code": "gardener_code",
        "doc": "gardener_docs",
    }

    @mcp_app.tool
    async def cache_sap_github_content(
        content_type: str,
        content: str,
        sap_github_url: str,
        sap_github_repo: str,
        issue_metadata: dict[str, Any] | None = None,
        pr_metadata: dict[str, Any] | None = None,
        code_metadata: dict[str, Any] | None = None,
        ctx: Context = None,
    ) -> CacheSapGithubContentResult:
        """Vectorise and cache content from github.tools.sap into Qdrant.

        Call this tool after fetching content via the github-tools MCP server
        (which targets github.tools.sap — SAP's internal GitHub Enterprise).
        The content is chunked, embedded, and upserted into the appropriate
        Qdrant collection so that future ``search_*`` calls can surface it
        semantically alongside public github.com/gardener/* content.

        **Important distinction:**
        - github.com/gardener/* content is batch-ingested offline via
          ``scripts/ingest_docs.py`` (GitHubIssuesIngester, GitHubPRsIngester).
        - github.tools.sap content arrives on-demand through this tool,
          triggered by the AI agent after calling github-tools MCP tools.

        Collection routing (automatic):
            ``content_type='issue'`` → ``gardener_issues``
            ``content_type='pr'``    → ``gardener_prs``
            ``content_type='code'``  → ``gardener_code``
            ``content_type='doc'``   → ``gardener_docs``

        Deduplication: re-caching the same ``sap_github_url`` replaces all
        existing chunks for that URL (upsert semantics — no duplicates).

        Args:
            content_type: Type of content from github.tools.sap.
                Must be one of: ``'issue'``, ``'pr'``, ``'code'``, ``'doc'``.
            content: Full text to vectorise — e.g. issue body + comments,
                PR description + diff summary, or raw file contents.
            sap_github_url: Canonical HTML URL on github.tools.sap used as
                the deduplication key.
                Example: ``'https://github.tools.sap/my-org/my-repo/issues/42'``
            sap_github_repo: Repository slug on github.tools.sap
                e.g. ``'my-org/my-repo'``.  Stored in metadata to distinguish
                SAP GitHub content from public github.com/gardener/* content.
            issue_metadata: Structured metadata when ``content_type='issue'``.
                Expected keys: ``issue_number``, ``title``, ``state``,
                ``labels``, ``created_at``, ``closed_at``.
            pr_metadata: Structured metadata when ``content_type='pr'``.
                Expected keys: ``pr_number``, ``title``, ``state``,
                ``created_at``, ``merged_at``.
            code_metadata: Structured metadata when ``content_type='code'``.
                Expected keys: ``file_path``, ``ref``, ``language``.
            ctx: FastMCP context providing access to lifespan singletons.

        Returns:
            A ``CacheSapGithubContentResult`` with the number of chunks
            upserted, the target collection, and whether the URL already
            existed in the store.

        Raises:
            ValueError: If ``content_type`` is not one of the four valid
                values, or if ``content`` is empty.
        """
        if content_type not in SAP_GITHUB_CONTENT_TYPES:
            raise ValueError(
                f"Invalid content_type {content_type!r}. "
                f"Must be one of: {sorted(SAP_GITHUB_CONTENT_TYPES)}"
            )
        if not content or not content.strip():
            raise ValueError("content must not be empty")

        app_ctx = _get_app_context(ctx)
        collection = _SAP_GITHUB_COLLECTION_MAP[content_type]

        # ------------------------------------------------------------------
        # Build the metadata payload — always tag with sap_github origin so
        # search results can be distinguished from github.com/gardener/* docs.
        # ------------------------------------------------------------------
        metadata: dict[str, Any] = {
            "source_origin": "sap_github",        # distinguishes from github.com
            "sap_github_repo": sap_github_repo,
            "url": sap_github_url,
            "content_type": content_type,
        }

        if content_type == "issue" and issue_metadata:
            metadata.update({
                "issue_number": issue_metadata.get("issue_number"),
                "title": issue_metadata.get("title"),
                "state": issue_metadata.get("state", "open"),
                "labels": issue_metadata.get("labels", []),
                "created_at": issue_metadata.get("created_at"),
                "closed_at": issue_metadata.get("closed_at"),
            })
        elif content_type == "pr" and pr_metadata:
            metadata.update({
                "pr_number": pr_metadata.get("pr_number"),
                "title": pr_metadata.get("title"),
                "state": pr_metadata.get("state", "open"),
                "created_at": pr_metadata.get("created_at"),
                "merged_at": pr_metadata.get("merged_at"),
            })
        elif content_type == "code" and code_metadata:
            metadata.update({
                "file_path": code_metadata.get("file_path"),
                "ref": code_metadata.get("ref"),
                "language": code_metadata.get("language"),
            })

        # ------------------------------------------------------------------
        # Check whether this URL already has chunks in the collection.
        # We do a tiny semantic search filtered to the exact URL as a proxy
        # for existence; if any result comes back we know it existed.
        # ------------------------------------------------------------------
        probe_retriever = SemanticRetriever(
            embedder=app_ctx.embedder,
            vector_store=app_ctx.vector_store,
            collection=collection,
        )
        probe_results = await probe_retriever.retrieve(
            query=sap_github_url,
            filters={"url": sap_github_url},
            limit=1,
        )
        already_existed = len(probe_results) > 0

        # ------------------------------------------------------------------
        # Chunk the content.
        # - Markdown/prose content (issues, PRs, docs) → MarkdownChunker
        # - Source code → CodeChunker (language-aware recursive splitting)
        # ------------------------------------------------------------------
        doc = Document(
            content=content,
            metadata=metadata,
            source=sap_github_url,
        )

        if content_type == "code":
            chunker = CodeChunker()
        else:
            chunker = MarkdownChunker()

        chunks: list[Document] = chunker.chunk(doc)

        # ------------------------------------------------------------------
        # Embed all chunks in one batch.
        # ------------------------------------------------------------------
        chunk_texts = [c.content for c in chunks]
        vectors: list[list[float]] = await app_ctx.embedder.embed(chunk_texts)

        # ------------------------------------------------------------------
        # Ensure the collection exists (idempotent), then upsert.
        # ------------------------------------------------------------------
        await app_ctx.vector_store.ensure_collection(
            collection=collection,
            vector_size=app_ctx.settings.embedding_dimensions,
        )
        upserted = await app_ctx.vector_store.upsert(
            collection=collection,
            documents=chunks,
            vectors=vectors,
        )

        logger.info(
            "cache_sap_github_content: upserted %d chunks into %r for %s "
            "(already_existed=%s)",
            upserted,
            collection,
            sap_github_url,
            already_existed,
        )

        return CacheSapGithubContentResult(
            chunks_upserted=upserted,
            collection=collection,
            sap_github_url=sap_github_url,
            already_existed=already_existed,
        )
