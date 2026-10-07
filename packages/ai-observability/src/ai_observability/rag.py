"""Child spans for embedding and Qdrant search. Vectors are never attributes."""

from __future__ import annotations

from typing import Any

from ai_observability.redact import preview
from ai_observability.spans import span


async def embed_query(embedder: Any, query: str) -> list[float]:
    """Embed ``query`` inside an ``embeddings`` span."""
    async with span("embeddings", **{"gen_ai.operation.name": "embeddings"}):
        return await embedder.embed_query(query)


async def search_collection(
    store: Any,
    collection: str,
    vector: list[float],
    limit: int,
    filters: dict[str, Any] | None,
) -> list[Any]:
    """Search one collection inside a ``qdrant.search`` span. The vector is not recorded."""
    async with span(
        "qdrant.search",
        **{
            "db.system": "qdrant",
            "ai.rag.collection": collection,
            "ai.rag.limit": limit,
        },
    ):
        return await store.search(collection, vector, limit, filters)


def top_sources(results: list[Any], n: int = 5) -> str:
    """id, score, collection, and url/source for the first ``n`` hits. No document text."""
    items: list[dict[str, Any]] = []
    for result in results[:n]:
        meta = getattr(result, "metadata", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        items.append(
            {
                "id": getattr(result, "id", ""),
                "score": getattr(result, "score", None),
                "collection": getattr(result, "collection", ""),
                "url": meta.get("url") or meta.get("source") or "",
            }
        )
    return preview(items)
