"""Abstract base class and shared data models for the vector store layer.

All concrete vector store implementations must inherit from
``BaseVectorStore`` and implement every abstract method.  The
``SearchResult`` model is the canonical output type for all search
operations in this project.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from ingestion.base import Document


class SearchResult(BaseModel):
    """A single result returned by a vector store search operation.

    Attributes:
        id: The unique identifier of the matching document point, as
            stored in the vector store.
        content: The text content of the matching document.
        score: Cosine similarity or distance score.  Higher is more
            similar when using cosine distance.
        metadata: Arbitrary key/value metadata stored alongside the
            vector (e.g. ``source_type``, ``repo``, ``state``).
        collection: The name of the Qdrant collection this result came
            from (e.g. ``"gardener_docs"``).
    """

    id: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection: str


class BaseVectorStore(ABC):
    """Abstract interface for vector store backends.

    Implementations must be safe for concurrent async use and must never
    block the event loop with synchronous I/O.  All mutations
    (``upsert``, ``delete``) must be idempotent where possible.
    """

    @abstractmethod
    async def upsert(
        self,
        collection: str,
        documents: list[Document],
        vectors: list[list[float]],
    ) -> int:
        """Insert or update document vectors in the specified collection.

        ``documents[i]`` is stored with ``vectors[i]``.  Both lists must
        have the same length.  Existing points with matching IDs are
        overwritten.

        Args:
            collection: The name of the target collection.
            documents: The documents whose content and metadata should be
                stored as the point payload.
            vectors: The dense float vectors to store.  Must be the same
                length as ``documents``.

        Returns:
            The number of points successfully upserted.

        Raises:
            ValueError: If ``documents`` and ``vectors`` have different
                lengths.
            VectorStoreError: If the backend returns a non-recoverable
                error.
        """
        ...

    @abstractmethod
    async def search(
        self,
        collection: str,
        query_vector: list[float],
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Search for the nearest neighbours of a query vector.

        Args:
            collection: The collection to search in.
            query_vector: The dense query vector.
            limit: Maximum number of results to return.
            filters: Optional key/value pairs to filter results.  Each
                key must correspond to a payload field; the value is
                matched exactly.

        Returns:
            A list of up to ``limit`` ``SearchResult`` objects ordered by
            descending similarity score.

        Raises:
            VectorStoreError: If the backend returns a non-recoverable
                error.
        """
        ...

    @abstractmethod
    async def delete(self, collection: str, ids: list[str]) -> int:
        """Delete points from a collection by their IDs.

        Args:
            collection: The collection to delete from.
            ids: The list of point IDs to remove.

        Returns:
            The number of points successfully deleted.

        Raises:
            VectorStoreError: If the backend returns a non-recoverable
                error.
        """
        ...

    @abstractmethod
    async def ensure_collection(
        self,
        collection: str,
        vector_size: int,
    ) -> None:
        """Create the collection if it does not already exist.

        This method is idempotent: calling it on an existing collection
        must be a no-op.

        Args:
            collection: The name of the collection to create.
            vector_size: The number of dimensions for vectors in this
                collection.  Must match ``settings.embedding_dimensions``.

        Raises:
            VectorStoreError: If collection creation fails.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check whether the vector store backend is reachable.

        Returns:
            ``True`` if the backend responded successfully, ``False``
            otherwise.  Must not raise — any exception is caught
            internally and returns ``False``.
        """
        ...


class VectorStoreError(Exception):
    """Raised when a vector store operation encounters a non-recoverable error.

    Attributes:
        message: Human-readable description of the failure.
        collection: Optional name of the collection involved.
    """

    def __init__(self, message: str, collection: str = "") -> None:
        """Initialise the error with a message and optional collection name.

        Args:
            message: Human-readable description of the failure.
            collection: Optional name of the collection that caused the error.
        """
        super().__init__(message)
        self.message = message
        self.collection = collection

    def __str__(self) -> str:
        """Return a string representation including the collection if present."""
        if self.collection:
            return f"VectorStoreError(collection={self.collection!r}): {self.message}"
        return f"VectorStoreError: {self.message}"
