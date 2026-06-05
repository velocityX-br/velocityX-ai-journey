"""Text chunking utilities for the ingestion layer.

Provides two chunker classes:

- ``MarkdownChunker`` — uses LangChain's ``MarkdownTextSplitter`` and is
  suited for Markdown documentation and proposals.
- ``CodeChunker`` — uses LangChain's ``RecursiveCharacterTextSplitter`` and
  is suited for source code, issues, and PR bodies.

Both chunkers accept the source ``Document`` directly and return a list of
derived ``Document`` objects.  Every output chunk carries three additional
metadata fields that form the provenance envelope:

- ``chunk_index``  — zero-based position within the parent sequence
- ``total_chunks`` — total number of chunks produced from the parent
- ``parent_id``    — ``id`` of the source ``Document``
"""

from __future__ import annotations

from typing import Any

from langchain_text_splitters import MarkdownTextSplitter, RecursiveCharacterTextSplitter

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
            A list of ``Document`` objects.  If the document content is
            empty the list will contain exactly one chunk (the original
            content) to preserve provenance.
        """
        raw_chunks: list[str] = self._splitter.split_text(document.content)

        if not raw_chunks:
            raw_chunks = [document.content]

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


class CodeChunker:
    """Split a source-code or prose ``Document`` into overlapping chunks.

    Uses LangChain's ``RecursiveCharacterTextSplitter`` which splits on
    progressively smaller boundaries (paragraph, line, word, character)
    to minimise mid-token splits.

    Attributes:
        chunk_size: Target size (in characters) for each chunk.
        chunk_overlap: Number of characters of overlap between adjacent
            chunks.
    """

    def __init__(self, chunk_size: int = 1500, chunk_overlap: int = 300) -> None:
        """Initialise the chunker with configurable size parameters.

        Args:
            chunk_size: Target character count per chunk.  Defaults to 1500.
            chunk_overlap: Character overlap between adjacent chunks.
                Defaults to 300.
        """
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def chunk(self, document: Document) -> list[Document]:
        """Split a single document into a list of chunk documents.

        The parent document's metadata is copied into every chunk and then
        augmented with the provenance envelope fields (``chunk_index``,
        ``total_chunks``, ``parent_id``).

        Args:
            document: The source ``Document`` to split.

        Returns:
            A list of ``Document`` objects.  If the document content is
            empty the list will contain exactly one chunk to preserve
            provenance.
        """
        raw_chunks: list[str] = self._splitter.split_text(document.content)

        if not raw_chunks:
            raw_chunks = [document.content]

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
