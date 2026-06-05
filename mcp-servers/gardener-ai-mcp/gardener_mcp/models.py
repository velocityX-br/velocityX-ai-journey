"""Pydantic v2 input/output models for all MCP tool handlers.

Field descriptions on every model are agent-visible documentation: they
appear verbatim in the MCP JSON schema that client agents read to
understand how to call each tool.  Keep them concise, precise, and
written from the perspective of the agent consuming the API.

Design note (ADR-004):
All models are pure data containers with no business logic.  They are
imported by ``mcp/tools.py`` and registered automatically by FastMCP's
Pydantic schema introspection.  No model imports anything from the
retrieval or vector store layers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Tool input models
# ---------------------------------------------------------------------------


class SearchDocsInput(BaseModel):
    """Input schema for the ``search_docs`` tool.

    Searches the ``gardener_docs`` Qdrant collection using semantic
    (dense-vector) similarity against Gardener documentation content.
    """

    query: str = Field(
        description="Natural language search query for Gardener documentation"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (1-50)",
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata filters e.g. {'content_type': 'doc'}",
    )


class SearchIssuesInput(BaseModel):
    """Input schema for the ``search_issues`` tool.

    Searches the ``gardener_issues`` Qdrant collection for GitHub issues
    from the gardener/gardener repository.
    """

    query: str = Field(
        description="Natural language search query for GitHub issues"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (1-50)",
    )
    state: str | None = Field(
        default=None,
        description="Filter by issue state: 'open', 'closed', or None for all",
    )
    labels: list[str] | None = Field(
        default=None,
        description="Filter by label names e.g. ['bug', 'help wanted']",
    )


class SearchPRsInput(BaseModel):
    """Input schema for the ``search_prs`` tool.

    Searches the ``gardener_prs`` Qdrant collection for pull requests
    from the gardener/gardener repository.
    """

    query: str = Field(
        description="Natural language search query for pull requests"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (1-50)",
    )
    state: str | None = Field(
        default=None,
        description="Filter by PR state: 'open', 'closed', 'merged', or None for all",
    )


class SearchProposalsInput(BaseModel):
    """Input schema for the ``search_proposals`` tool.

    Searches the ``gardener_docs`` collection filtered to content of
    type ``"proposal"`` — Gardener Enhancement Proposals (GEPs).
    """

    query: str = Field(
        description="Natural language search query for Gardener enhancement proposals (GEPs)"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (1-50)",
    )


class SearchCodeInput(BaseModel):
    """Input schema for the ``search_code`` tool.

    Searches the ``gardener_code`` Qdrant collection for Go source code
    extracted from gardener/gardener and related repositories.
    """

    query: str = Field(
        description="Natural language search query for Gardener Go source code"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (1-50)",
    )
    repo: str | None = Field(
        default=None,
        description="Filter by repository name e.g. 'gardener/gardener'",
    )


class RAGRetrieveInput(BaseModel):
    """Input schema for the ``rag_retrieve`` tool.

    Low-level RAG retrieval that lets the agent specify the target
    Qdrant collection explicitly.  Use the higher-level ``search_*``
    tools when the collection is known in advance.
    """

    query: str = Field(
        description="Natural language query for RAG retrieval"
    )
    collection: str = Field(
        description=(
            "Qdrant collection to search: 'gardener_docs', 'gardener_issues',"
            " 'gardener_prs', or 'gardener_code'"
        )
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (1-50)",
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata filters",
    )


class RootCauseAnalysisInput(BaseModel):
    """Input schema for the ``root_cause_analysis`` tool.

    Performs hybrid retrieval across all four Qdrant collections and
    then calls the LLM (via SAP Hyperspace) to synthesise a structured
    root cause analysis from the retrieved context.
    """

    symptom: str = Field(
        description=(
            "Description of the problem or error being investigated"
            " e.g. 'Shoot cluster stuck in Reconciling state'"
        )
    )
    context: str | None = Field(
        default=None,
        description=(
            "Additional context such as error messages, logs, or environment details"
        ),
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "Number of relevant documents to retrieve per collection for analysis (1-20)"
        ),
    )


# ---------------------------------------------------------------------------
# Tool output models
# ---------------------------------------------------------------------------


class ToolSearchResult(BaseModel):
    """A single ranked search result returned by any ``search_*`` or ``rag_retrieve`` tool.

    Attributes:
        id: Unique document identifier as stored in Qdrant.
        content: The raw text content of the matching chunk.
        score: Relevance score (cosine similarity for semantic search,
            RRF score for hybrid search).  Higher is more relevant.
        metadata: Arbitrary key/value metadata attached to the chunk
            (e.g. ``source_type``, ``repo``, ``state``, ``url``).
        collection: The Qdrant collection this result came from.
        source: Optional URL or file path of the original source document.
    """

    id: str
    content: str
    score: float
    metadata: dict[str, Any]
    collection: str
    source: str | None = None
