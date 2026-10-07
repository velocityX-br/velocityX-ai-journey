"""SemanticRetriever emits retrieve / embeddings / qdrant spans without the vector."""

from __future__ import annotations

from unittest.mock import AsyncMock

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from retrieval.semantic import SemanticRetriever
from vectorstore.base import SearchResult

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)


def _result() -> SearchResult:
    return SearchResult(
        id="doc-1",
        content="content for doc-1",
        score=0.95,
        metadata={"url": "https://example.test/doc"},
        collection="sci_docs_operation",
    )


async def test_retrieve_span_tree_omits_vectors() -> None:
    _EXPORTER.clear()
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    store = AsyncMock()
    store.search = AsyncMock(return_value=[_result()])
    retriever = SemanticRetriever(embedder=embedder, vector_store=store, collection="sci_docs_operation")

    results = await retriever.retrieve("storage latency", limit=3)

    assert results[0].id == "doc-1"
    spans = {item.name: item for item in _EXPORTER.get_finished_spans()}
    retrieve = spans["retrieve sci_docs_operation"]
    embeddings = spans["embeddings"]
    search = spans["qdrant.search"]
    assert embeddings.parent is not None and embeddings.parent.span_id == retrieve.context.span_id
    assert search.parent is not None and search.parent.span_id == retrieve.context.span_id
    assert "doc-1" in retrieve.attributes["ai.rag.top_sources"]
    blob = " ".join(str(item.attributes) for item in spans.values())
    assert "content for doc-1" not in blob
    assert "[0.1" not in blob
