"""Qdrant implementation of the vector store interface.

Manages two documentation collections (operation, customer) in a
self-hosted Qdrant instance.  All collections use HNSW indexes with cosine
distance and payload indexes on filterable fields.

Design decisions:
- Two separate collections — one per documentation source — enable
  independent HNSW tuning and clean schema evolution, and let agents scope
  searches to a single documentation set.
- HNSW parameters (m=16, ef_construct=100) balance recall and index build
  speed for documentation-scale workloads.
- Payload indexes on ``content_type`` and ``repo`` allow efficient filtered
  searches without full collection scans.
- Vector dimensions are always sourced from ``settings.embedding_dimensions``
  — never hardcoded.

API version note: qdrant-client >= 1.13 replaced ``client.search()`` with
``client.query_points()``.  This module uses ``query_points()`` throughout.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from config.settings import Settings
from ingestion.base import Document
from vectorstore.base import BaseVectorStore, SearchResult, VectorStoreError

logger = logging.getLogger(__name__)

# Payload fields that get dedicated Qdrant payload indexes for fast filtering.
_INDEXED_PAYLOAD_FIELDS: tuple[str, ...] = (
    "content_type",
    "repo",
)


class QdrantVectorStore(BaseVectorStore):
    """Qdrant-backed vector store for the SCI AI MCP project.

    Manages creation and access to the two canonical documentation
    collections.  All HNSW and index configuration is applied at
    collection-creation time; subsequent calls to ``ensure_collection`` on
    an already-existing collection are no-ops.

    Attributes:
        COLLECTIONS: The two collection names used across the project.
        _settings: Resolved application settings.
        _client: Async Qdrant client.
    """

    COLLECTIONS: ClassVar[list[str]] = [
        "sci_docs_operation",
        "sci_docs_customer",
    ]

    def __init__(
        self,
        settings: Settings,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        """Initialise the vector store.

        Args:
            settings: Application settings.  Provides ``qdrant_url``,
                ``qdrant_api_key``, ``qdrant_batch_size``, and
                ``embedding_dimensions``.
            client: An optional pre-built ``AsyncQdrantClient``.  When
                supplied, ``qdrant_url`` and ``qdrant_api_key`` from
                ``settings`` are ignored.  Useful for testing with a mock
                client.
        """
        self._settings = settings

        if client is None:
            self._client = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
            )
        else:
            self._client = client

    # ------------------------------------------------------------------
    # BaseVectorStore interface
    # ------------------------------------------------------------------

    async def ensure_collection(
        self,
        collection: str,
        vector_size: int,
    ) -> None:
        """Create the collection if it does not already exist.

        When a new collection is created this method also creates
        payload indexes on the fields listed in
        ``_INDEXED_PAYLOAD_FIELDS`` (``content_type``, ``repo``).

        Args:
            collection: The name of the collection to create or verify.
            vector_size: The number of dimensions for embedding vectors.
                Must equal ``settings.embedding_dimensions`` at call time.

        Raises:
            VectorStoreError: If collection creation or index creation fails.
        """
        try:
            exists = await self._client.collection_exists(collection)
        except Exception as exc:
            raise VectorStoreError(
                f"Failed to check collection existence: {exc}",
                collection=collection,
            ) from exc

        if exists:
            logger.debug("Collection %r already exists — skipping creation.", collection)
            return

        logger.info(
            "Creating collection %r with vector_size=%d, distance=COSINE, "
            "HNSW m=16, ef_construct=100.",
            collection,
            vector_size,
        )

        try:
            await self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=Distance.COSINE,
                ),
                hnsw_config=HnswConfigDiff(
                    m=16,
                    ef_construct=100,
                ),
            )
        except Exception as exc:
            raise VectorStoreError(
                f"Failed to create collection: {exc}",
                collection=collection,
            ) from exc

        # Create payload indexes for fast filtered search.
        for field_name in _INDEXED_PAYLOAD_FIELDS:
            try:
                await self._client.create_payload_index(
                    collection_name=collection,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
                logger.debug(
                    "Created payload index on field %r in collection %r.",
                    field_name,
                    collection,
                )
            except Exception as exc:
                # Index creation failures are logged but not fatal — the
                # collection is usable; searches will just be slower.
                logger.warning(
                    "Failed to create payload index on field %r in "
                    "collection %r: %s",
                    field_name,
                    collection,
                    exc,
                )

    async def upsert(
        self,
        collection: str,
        documents: list[Document],
        vectors: list[list[float]],
    ) -> int:
        """Upsert document vectors into the collection in batches.

        Each document is stored as a ``PointStruct`` where:
        - The point ID is ``document.id``.
        - The payload is ``document.metadata`` merged with
          ``{"content": document.content, "source": document.source}``.
        - The vector is ``vectors[i]``.

        The input is split into batches of ``settings.qdrant_batch_size``
        points (default 100) to avoid exceeding Qdrant's request size limits.

        Args:
            collection: The target collection name.
            documents: The list of documents to store.
            vectors: The list of embedding vectors, aligned with ``documents``.

        Returns:
            The total number of points upserted across all batches.

        Raises:
            ValueError: If ``documents`` and ``vectors`` have different lengths.
            VectorStoreError: If a batch upsert fails.
        """
        if len(documents) != len(vectors):
            raise ValueError(
                f"documents length ({len(documents)}) != vectors length ({len(vectors)})"
            )

        if not documents:
            return 0

        batch_size = self._settings.qdrant_batch_size
        total_upserted = 0

        for batch_start in range(0, len(documents), batch_size):
            doc_batch = documents[batch_start : batch_start + batch_size]
            vec_batch = vectors[batch_start : batch_start + batch_size]

            points = [
                PointStruct(
                    id=doc.id,
                    vector=vec,
                    payload={
                        **doc.metadata,
                        "content": doc.content,
                        "source": doc.source,
                    },
                )
                for doc, vec in zip(doc_batch, vec_batch, strict=True)
            ]

            try:
                await self._client.upsert(
                    collection_name=collection,
                    points=points,
                )
                total_upserted += len(points)
                logger.debug(
                    "Upserted batch of %d points to collection %r "
                    "(cumulative: %d).",
                    len(points),
                    collection,
                    total_upserted,
                )
            except Exception as exc:
                raise VectorStoreError(
                    f"Batch upsert failed at offset {batch_start}: {exc}",
                    collection=collection,
                ) from exc

        return total_upserted

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Search for the nearest neighbours of a query vector.

        Each key-value pair in ``filters`` is translated into a Qdrant
        ``FieldCondition`` with ``MatchValue`` (exact string/integer match).
        All conditions are combined with an implicit AND (``must`` clause).

        Args:
            collection: The collection to search.
            query_vector: The dense query vector.
            limit: Maximum number of results to return.
            filters: Optional exact-match payload filters.  E.g.
                ``{"content_type": "doc", "repo": "cc/documentation-operation"}``.

        Returns:
            A list of ``SearchResult`` objects ordered by descending score.

        Raises:
            VectorStoreError: If the search request fails.
        """
        qdrant_filter: Filter | None = None

        if filters:
            conditions = [
                FieldCondition(key=key, match=MatchValue(value=value))
                for key, value in filters.items()
            ]
            qdrant_filter = Filter(must=conditions)

        try:
            query_response = await self._client.query_points(
                collection_name=collection,
                query=query_vector,
                limit=limit,
                query_filter=qdrant_filter,
                with_payload=True,
            )
            scored_points = query_response.points
        except Exception as exc:
            raise VectorStoreError(
                f"Search failed: {exc}",
                collection=collection,
            ) from exc

        results: list[SearchResult] = []
        for point in scored_points:
            payload: dict[str, Any] = dict(point.payload or {})
            content = str(payload.pop("content", ""))
            payload.pop("source", None)  # kept in metadata at ingestion time
            results.append(
                SearchResult(
                    id=str(point.id),
                    content=content,
                    score=float(point.score),
                    metadata=payload,
                    collection=collection,
                )
            )

        return results

    async def delete(self, collection: str, ids: list[str]) -> int:
        """Delete points from the collection by their IDs.

        Args:
            collection: The collection to delete from.
            ids: The list of point IDs to remove.

        Returns:
            The number of points successfully deleted (equals ``len(ids)``
            on success).

        Raises:
            VectorStoreError: If the delete request fails.
        """
        if not ids:
            return 0

        try:
            await self._client.delete(
                collection_name=collection,
                points_selector=PointIdsList(points=ids),
            )
        except Exception as exc:
            raise VectorStoreError(
                f"Delete failed for {len(ids)} ids: {exc}",
                collection=collection,
            ) from exc

        return len(ids)

    async def health_check(self) -> bool:
        """Check whether the Qdrant instance is reachable.

        Calls ``get_collections()`` and returns ``True`` on success.  Any
        exception is caught and returns ``False`` without re-raising.

        Returns:
            ``True`` if Qdrant responded, ``False`` otherwise.
        """
        try:
            await self._client.get_collections()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Qdrant health check failed: %s", exc)
            return False

    async def count(self, collection: str) -> int:
        """Return the number of points stored in the collection.

        Args:
            collection: The collection name to count points in.

        Returns:
            The total number of indexed points, or ``0`` if the collection
            does not exist or any error occurs (never raises).
        """
        try:
            result = await self._client.count(collection_name=collection)
            return result.count
        except Exception as exc:  # noqa: BLE001
            logger.debug("count() failed for collection %r: %s", collection, exc)
            return 0
