"""Text chunking utilities for the ingestion layer.

Provides ``MarkdownChunker`` — uses LangChain's ``MarkdownTextSplitter`` and
is suited for Markdown documentation.

The chunker accepts the source ``Document`` directly and returns a list of
derived ``Document`` objects.  Every output chunk carries three additional
metadata fields that form the provenance envelope:

- ``chunk_index``  — zero-based position within the parent sequence
- ``total_chunks`` — total number of chunks produced from the parent
- ``parent_id``    — ``id`` of the source ``Document``
"""

from __future__ import annotations

from typing import Any

from langchain_text_splitters import MarkdownTextSplitter

from ingestion.base import Document


class MarkdownChunker:
    """Split a Markdown ``Document`` into overlapping chunks.

    Uses LangChain's ``MarkdownTextSplitter`` which is aware of Markdown
    heading structure and tries to keep sections intact.

    Attributes:
        chunk_size: Target size (in characters) for each chunk.
        chunk_overlap: Number of characters of overlap between adjacent
            chunks.  A higher value provides more context at chunk
            boundaries at the cost of storage.
    """

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200) -> None:
        """Initialise the chunker with configurable size parameters.

        Args:
            chunk_size: Target character count per chunk.  Defaults to 1000.
            chunk_overlap: Character overlap between adjacent chunks.
                Defaults to 200.
        """
        self._splitter = MarkdownTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def chunk(self, document: Document) -> list[Document]:
        """Split a single Markdown document into a list of chunk documents.

        The parent document's metadata is copied into every chunk and then
        augmented with the provenance envelope fields (``chunk_index``,
        ``total_chunks``, ``parent_id``).

        Args:
            document: The source ``Document`` to split.

        Returns:
            A list of ``Document`` objects.  Chunks whose content is empty
            or whitespace-only are skipped, since the embeddings endpoint
            rejects empty-string inputs.  A document with no non-empty
            content therefore yields an empty list.
        """
        raw_chunks: list[str] = self._splitter.split_text(document.content)

        if not raw_chunks:
            raw_chunks = [document.content]

        # Drop empty / whitespace-only chunks: the OpenAI-compatible
        # embeddings API returns HTTP 400 ("input cannot be an empty
        # string") if any batch element is blank.  Filtering here keeps
        # such chunks out of both the embedding batches and Qdrant.
        raw_chunks = [c for c in raw_chunks if c.strip()]

        total = len(raw_chunks)
        result: list[Document] = []

        for index, chunk_text in enumerate(raw_chunks):
            chunk_metadata: dict[str, Any] = {
                **document.metadata,
                "chunk_index": index,
                "total_chunks": total,
                "parent_id": document.id,
            }
            result.append(
                Document(
                    content=chunk_text,
                    metadata=chunk_metadata,
                    source=document.source,
                )
            )

        return result

    def chunk_many(self, documents: list[Document]) -> list[Document]:
        """Chunk a list of documents, returning the flattened result.

        Args:
            documents: Source documents to chunk.

        Returns:
            Flat list of all chunks across all input documents.
        """
        chunks: list[Document] = []
        for doc in documents:
            chunks.extend(self.chunk(doc))
        return chunks
