"""Pydantic v2 input/output models for all MCP tool handlers.

Field descriptions on every model are agent-visible documentation: they
appear verbatim in the MCP JSON schema that client agents read to
understand how to call each tool.  Keep them concise, precise, and
written from the perspective of the agent consuming the API.

Design note:
All models are pure data containers with no business logic.  They are
imported by ``sci_mcp/tools.py`` and registered automatically by FastMCP's
Pydantic schema introspection.  No model imports anything from the
retrieval or vector store layers.

This server is scoped to pure documentation RAG over two collections:
``sci_docs_operation`` (from ``cc/documentation-operation``) and
``sci_docs_customer`` (from ``cc/documentation-customer``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Collection identifiers
# ---------------------------------------------------------------------------

# The two canonical documentation collections managed by this server.
OPERATION_COLLECTION = "sci_docs_operation"
CUSTOMER_COLLECTION = "sci_docs_customer"

# Valid target collections for the low-level ``rag_retrieve`` tool.
VALID_COLLECTIONS = frozenset({OPERATION_COLLECTION, CUSTOMER_COLLECTION})


# ---------------------------------------------------------------------------
# Tool input models
# ---------------------------------------------------------------------------


class SearchOperationDocsInput(BaseModel):
    """Input schema for the ``search_operation_docs`` tool.

    Searches the ``sci_docs_operation`` Qdrant collection using semantic
    (dense-vector) similarity against SCI operation documentation content.
    """

    query: str = Field(
        description="Natural language search query for SCI operation documentation"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (1-50)",
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata filters e.g. {'content_type': 'operation'}",
    )


class SearchCustomerDocsInput(BaseModel):
    """Input schema for the ``search_customer_docs`` tool.

    Searches the ``sci_docs_customer`` Qdrant collection using semantic
    (dense-vector) similarity against SCI customer documentation content.
    """

    query: str = Field(
        description="Natural language search query for SCI customer documentation"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (1-50)",
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata filters e.g. {'content_type': 'customer'}",
    )


class SearchDocsInput(BaseModel):
    """Input schema for the ``search_docs`` tool.

    Searches BOTH documentation collections (operation + customer) using
    hybrid dense + sparse retrieval with Reciprocal Rank Fusion, returning
    a single merged ranked list.
    """

    query: str = Field(
        description="Natural language search query across all SCI documentation"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of fused results to return (1-50)",
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata filters applied to every sub-search",
    )


class RAGRetrieveInput(BaseModel):
    """Input schema for the ``rag_retrieve`` tool.

    Low-level RAG retrieval that lets the agent specify the target
    Qdrant collection explicitly.  Use the higher-level ``search_*``
    tools when the collection is known in advance.
    """

    query: str = Field(description="Natural language query for RAG retrieval")
    collection: str = Field(
        description=(
            "Qdrant collection to search: 'sci_docs_operation' or"
            " 'sci_docs_customer'"
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

    Performs hybrid retrieval across both documentation collections and
    then calls the LLM (via SAP Hyperspace) to synthesise a structured
    root cause analysis from the retrieved context.
    """

    symptom: str = Field(
        description=(
            "Description of the problem or error being investigated"
            " e.g. 'Tenant provisioning stuck in pending state'"
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
            (e.g. ``content_type``, ``repo``, ``path``, ``url``).
        collection: The Qdrant collection this result came from.
        source: Optional URL or file path of the original source document.
    """

    id: str
    content: str
    score: float
    metadata: dict[str, Any]
    collection: str
    source: str | None = None
