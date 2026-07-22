"""Application context (DI container) for the MCP server.

``AppContext`` is a frozen Pydantic model that holds every initialised
singleton needed by the MCP tool handlers.  It is created exactly once
inside ``build_app_context`` which is called from the FastMCP lifespan
function — never at module import time and never inside a tool handler.

Design notes:
- Frozen to prevent accidental mutation after construction.
- ``arbitrary_types_allowed=True`` is required because ``BaseEmbedder``,
  ``BaseVectorStore``, ``BaseRetriever``, and ``anthropic.AsyncAnthropic``
  are not Pydantic-native types.
- No concrete implementation is referenced here except in
  ``build_app_context``.  Tool handlers only see the abstract interfaces
  held by ``AppContext``.
- ``anthropic.AsyncAnthropic`` is constructed with the Hyperspace proxy
  URL and auth token from ``Settings``.  The SDK never calls
  ``api.anthropic.com`` directly.

This server manages two documentation collections and therefore holds
one ``SemanticRetriever`` per collection plus one ``HybridRetriever``
spanning both.
"""

from __future__ import annotations

import logging

import anthropic
from pydantic import BaseModel, ConfigDict

from config.settings import Settings
from embeddings.base import BaseEmbedder
from embeddings.openai_embedder import HyperspaceEmbedder
from retrieval.hybrid import HybridRetriever
from retrieval.semantic import SemanticRetriever
from sci_mcp.models import CUSTOMER_COLLECTION, OPERATION_COLLECTION
from vectorstore.base import BaseVectorStore
from vectorstore.qdrant import QdrantVectorStore

logger = logging.getLogger(__name__)

_ALL_COLLECTIONS: list[str] = [OPERATION_COLLECTION, CUSTOMER_COLLECTION]


class AppContext(BaseModel):
    """Frozen dependency-injection container for all MCP server singletons.

    Passed to every tool handler via FastMCP's lifespan state mechanism.
    All fields are set at server startup and remain immutable for the
    lifetime of the process.

    Attributes:
        settings: Validated application settings loaded from the environment.
        embedder: The text embedding provider (Hyperspace OpenAI-compatible).
        vector_store: The Qdrant vector store backend.
        operation_retriever: Dense-vector semantic retriever pre-configured
            for the ``sci_docs_operation`` collection.
        customer_retriever: Dense-vector semantic retriever pre-configured
            for the ``sci_docs_customer`` collection.
        hybrid_retriever: Multi-collection hybrid retriever (dense + sparse
            + RRF) spanning both documentation collections.  Required for
            ``search_docs`` and ``root_cause_analysis``.
        anthropic_client: Async Anthropic client pointed at the SAP
            Hyperspace LLM proxy.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    settings: Settings
    embedder: BaseEmbedder
    vector_store: BaseVectorStore
    operation_retriever: SemanticRetriever
    customer_retriever: SemanticRetriever
    hybrid_retriever: HybridRetriever
    anthropic_client: anthropic.AsyncAnthropic


async def build_app_context(settings: Settings) -> AppContext:
    """Construct and return a fully initialised ``AppContext``.

    Instantiation order:
    1. ``HyperspaceEmbedder`` — no I/O at construction time.
    2. ``QdrantVectorStore`` — no I/O at construction time.
    3. ``vector_store.health_check()`` — confirms Qdrant is reachable.
       A failure logs a warning but does not abort startup, allowing the
       server to start and serve non-retrieval requests even when Qdrant
       is temporarily unavailable.
    4. Two ``SemanticRetriever`` instances — one per collection.
    5. ``HybridRetriever`` — spans both collections.
    6. ``anthropic.AsyncAnthropic`` — Hyperspace LLM proxy client.

    Args:
        settings: A fully validated ``Settings`` instance.  This is the
            composition root — all singletons derive their configuration
            solely from this object.

    Returns:
        A frozen ``AppContext`` containing all initialised singletons.
    """
    embedder: BaseEmbedder = HyperspaceEmbedder(settings)

    vector_store: BaseVectorStore = QdrantVectorStore(settings)

    healthy = await vector_store.health_check()
    if not healthy:
        logger.warning(
            "Qdrant health check failed — vector store may be unreachable. "
            "Retrieval tools will fail until Qdrant becomes available."
        )
    else:
        for collection in _ALL_COLLECTIONS:
            count = await vector_store.count(collection)
            if count == 0:
                logger.warning(
                    "Collection %r is empty — run `uv run ingest-docs` "
                    "to populate it before using retrieval tools.",
                    collection,
                )

    operation_retriever = SemanticRetriever(
        embedder=embedder,
        vector_store=vector_store,
        collection=OPERATION_COLLECTION,
    )

    customer_retriever = SemanticRetriever(
        embedder=embedder,
        vector_store=vector_store,
        collection=CUSTOMER_COLLECTION,
    )

    hybrid_retriever = HybridRetriever(
        embedder=embedder,
        vector_store=vector_store,
        collections=list(_ALL_COLLECTIONS),
    )

    anthropic_client = anthropic.AsyncAnthropic(
        base_url=settings.anthropic_base_url,
        api_key=settings.anthropic_auth_token,
    )

    return AppContext(
        settings=settings,
        embedder=embedder,
        vector_store=vector_store,
        operation_retriever=operation_retriever,
        customer_retriever=customer_retriever,
        hybrid_retriever=hybrid_retriever,
        anthropic_client=anthropic_client,
    )
