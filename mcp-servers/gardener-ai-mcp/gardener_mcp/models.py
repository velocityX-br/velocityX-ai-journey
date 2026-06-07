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
# SAP GitHub cache tool models
# ---------------------------------------------------------------------------

# Valid Qdrant target collections for SAP GitHub content.
# These are the same four collections used by the read-side search tools,
# but the naming makes explicit which GitHub origin is being written to.
_SAP_GITHUB_TARGET_COLLECTIONS = frozenset(
    {"gardener_docs", "gardener_issues", "gardener_prs", "gardener_code"}
)

# Content types as returned by github-tools MCP (github.tools.sap).
# Kept explicit so agents know what values are valid without guessing.
SAP_GITHUB_CONTENT_TYPES = frozenset({"issue", "pr", "code", "doc"})


class SapGithubIssueMetadata(BaseModel):
    """Metadata for a GitHub issue fetched from github.tools.sap via github-tools MCP.

    All fields mirror the payload returned by github-tools ``issue_read``
    and ``list_issues`` operations.  Optional fields may be absent when
    the github-tools MCP omits them.
    """

    issue_number: int = Field(
        description="Issue number on github.tools.sap (e.g. 42)"
    )
    repo: str = Field(
        description="Repository slug on github.tools.sap e.g. 'my-org/my-repo'"
    )
    title: str = Field(
        description="Issue title as returned by github-tools MCP"
    )
    state: str = Field(
        default="open",
        description="Issue state: 'open' or 'closed'",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Label names attached to the issue",
    )
    url: str = Field(
        description="HTML URL of the issue on github.tools.sap"
    )
    created_at: str | None = Field(
        default=None,
        description="ISO 8601 creation timestamp",
    )
    closed_at: str | None = Field(
        default=None,
        description="ISO 8601 close timestamp, or None if still open",
    )


class SapGithubPRMetadata(BaseModel):
    """Metadata for a pull request fetched from github.tools.sap via github-tools MCP."""

    pr_number: int = Field(
        description="Pull request number on github.tools.sap"
    )
    repo: str = Field(
        description="Repository slug on github.tools.sap e.g. 'my-org/my-repo'"
    )
    title: str = Field(
        description="PR title as returned by github-tools MCP"
    )
    state: str = Field(
        default="open",
        description="PR state: 'open', 'closed', or 'merged'",
    )
    url: str = Field(
        description="HTML URL of the PR on github.tools.sap"
    )
    created_at: str | None = Field(
        default=None,
        description="ISO 8601 creation timestamp",
    )
    merged_at: str | None = Field(
        default=None,
        description="ISO 8601 merge timestamp, or None if not merged",
    )


class SapGithubCodeMetadata(BaseModel):
    """Metadata for a source-code file fetched from github.tools.sap via github-tools MCP."""

    repo: str = Field(
        description="Repository slug on github.tools.sap e.g. 'my-org/my-repo'"
    )
    file_path: str = Field(
        description="File path within the repository e.g. 'pkg/client/client.go'"
    )
    url: str = Field(
        description="HTML URL of the file on github.tools.sap"
    )
    ref: str | None = Field(
        default=None,
        description="Git ref (branch, tag, or commit SHA) this file was read from",
    )
    language: str | None = Field(
        default=None,
        description="Programming language detected for this file e.g. 'go', 'python'",
    )


class CacheSapGithubContentInput(BaseModel):
    """Input schema for the ``cache_sap_github_content`` tool.

    Vectorises content retrieved from github.tools.sap (via the github-tools
    MCP server) and upserts it into the appropriate Qdrant collection so that
    future ``search_*`` calls can surface it semantically.

    The caller is responsible for:
    - Fetching the raw content from github-tools first.
    - Choosing the correct ``content_type`` to route to the right collection.
    - Providing the typed metadata block that matches ``content_type``.

    Deduplication is by ``sap_github_url``: re-caching the same URL replaces
    the existing vectors (upsert semantics).

    Collection routing:
        ``issue`` → ``gardener_issues``
        ``pr``    → ``gardener_prs``
        ``code``  → ``gardener_code``
        ``doc``   → ``gardener_docs``
    """

    content_type: str = Field(
        description=(
            "Type of content from github.tools.sap: "
            "'issue', 'pr', 'code', or 'doc'"
        )
    )
    content: str = Field(
        description=(
            "Full text content to vectorise — e.g. issue body + comments, "
            "PR description + diff summary, or file contents"
        )
    )
    sap_github_url: str = Field(
        description=(
            "Canonical HTML URL of the item on github.tools.sap — used as the "
            "deduplication key.  Example: "
            "'https://github.tools.sap/my-org/my-repo/issues/42'"
        )
    )
    sap_github_repo: str = Field(
        description=(
            "Repository slug on github.tools.sap e.g. 'my-org/my-repo'.  "
            "Stored in metadata to distinguish SAP GitHub content from "
            "public github.com/gardener/* content."
        )
    )
    issue_metadata: SapGithubIssueMetadata | None = Field(
        default=None,
        description="Required when content_type='issue'. Omit for other types.",
    )
    pr_metadata: SapGithubPRMetadata | None = Field(
        default=None,
        description="Required when content_type='pr'. Omit for other types.",
    )
    code_metadata: SapGithubCodeMetadata | None = Field(
        default=None,
        description="Required when content_type='code'. Omit for other types.",
    )


class CacheSapGithubContentResult(BaseModel):
    """Result returned by the ``cache_sap_github_content`` tool.

    Attributes:
        chunks_upserted: Number of vector chunks written to Qdrant.
        collection: The Qdrant collection that was written to.
        sap_github_url: The deduplication key used for this upsert.
        already_existed: True when at least one chunk with the same
            ``sap_github_url`` was already present in the collection
            before this call (i.e. the content was refreshed, not new).
    """

    chunks_upserted: int
    collection: str
    sap_github_url: str
    already_existed: bool


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
