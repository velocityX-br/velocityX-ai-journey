"""Abstract base class for text embedding providers.

All concrete embedders must inherit from ``BaseEmbedder`` and implement
both ``embed_documents`` and ``embed_query``.  This abstraction allows
the pipeline to swap embedding backends (Hyperspace, local models, etc.)
without changing any downstream retrieval or vector store code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseEmbedder(ABC):
    """Abstract interface for text embedding providers.

    Implementations must produce float-valued vectors whose dimensionality
    is consistent within a single instance.  Callers must not assume any
    particular vector size; the actual size is determined by the model
    configured in the concrete implementation.

    All methods are async to allow for non-blocking I/O in network-backed
    implementations.
    """

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents into dense float vectors.

        The output list is ordered to match the input list: ``result[i]``
        is the embedding for ``texts[i]``.

        Args:
            texts: A non-empty list of strings to embed.  Each string
                should be a single document or chunk — not a query.

        Returns:
            A list of float vectors, one per input text.  All vectors
            have the same dimensionality.

        Raises:
            EmbeddingError: If the embedding backend returns an error or
                the response cannot be parsed.
        """
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string into a dense float vector.

        Some providers use a different instruction prefix for query
        embeddings vs. document embeddings.  Concrete implementations
        may delegate to ``embed_documents([text])[0]`` when the provider
        makes no distinction.

        Args:
            text: The query string to embed.

        Returns:
            A single float vector of the same dimensionality as vectors
            produced by ``embed_documents``.

        Raises:
            EmbeddingError: If the embedding backend returns an error or
                the response cannot be parsed.
        """
        ...


class EmbeddingError(Exception):
    """Raised when an embedding provider returns a non-recoverable error.

    Attributes:
        message: Human-readable description of the failure.
        provider: Optional name of the provider that failed (e.g.
            ``"hyperspace-openai"``).
    """

    def __init__(self, message: str, provider: str = "") -> None:
        """Initialise the error with a message and optional provider name.

        Args:
            message: Human-readable description of the failure.
            provider: Optional identifier of the embedding provider.
        """
        super().__init__(message)
        self.message = message
        self.provider = provider

    def __str__(self) -> str:
        """Return a string representation including the provider if present."""
        if self.provider:
            return f"EmbeddingError(provider={self.provider!r}): {self.message}"
        return f"EmbeddingError: {self.message}"
