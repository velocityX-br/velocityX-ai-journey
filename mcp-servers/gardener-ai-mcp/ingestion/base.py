"""Abstract base classes and shared data models for the ingestion layer.

All concrete ingesters must inherit from ``BaseIngester`` and implement
the ``ingest`` coroutine.  The ``Document`` model is the canonical unit of
data that flows from ingestion through chunking and into the vector store.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    """A single unit of ingested content with associated metadata.

    Attributes:
        content: The raw text content of the document.
        metadata: Arbitrary key/value metadata attached to the document.
            Metadata keys and value types vary by ingester (docs, issues,
            PRs, code) but must always include at least ``source``.
        source: A stable identifier for the origin of this document — e.g.
            a GitHub URL, a file path, or an issue URL.  Duplicate detection
            in downstream stages keys on this field.
        id: A UUID assigned at creation time.  Stable within a single
            ingestion run; not guaranteed to be the same across runs.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str


class BaseIngester(ABC):
    """Abstract base class for all ingestion sources.

    Concrete implementations must override ``ingest`` and return a list of
    ``Document`` objects.  All network I/O must be performed asynchronously
    (or wrapped with ``asyncio.to_thread`` when the underlying library is
    synchronous).
    """

    @abstractmethod
    async def ingest(self) -> list[Document]:
        """Fetch and return documents from the upstream source.

        Returns:
            A list of ``Document`` instances.  May be empty if the source
            contains no relevant content.

        Raises:
            IngestionError: If the upstream source is unreachable or returns
                an unexpected response.
        """
        ...


class IngestionError(Exception):
    """Raised when an ingester encounters a non-recoverable error.

    Attributes:
        message: Human-readable description of the failure.
        source: The ingestion source identifier where the error occurred.
    """

    def __init__(self, message: str, source: str = "") -> None:
        """Initialise the error with a message and optional source.

        Args:
            message: Human-readable description of the failure.
            source: Optional identifier of the source that failed.
        """
        super().__init__(message)
        self.message = message
        self.source = source

    def __str__(self) -> str:
        """Return a string representation including the source if present."""
        if self.source:
            return f"IngestionError(source={self.source!r}): {self.message}"
        return f"IngestionError: {self.message}"
