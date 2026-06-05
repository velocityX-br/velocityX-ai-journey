"""Tests for ingestion/chunking.py — MarkdownChunker and CodeChunker."""

from __future__ import annotations

from ingestion.base import Document
from ingestion.chunking import CodeChunker, MarkdownChunker

# ---------------------------------------------------------------------------
# Sample content fixtures
# ---------------------------------------------------------------------------

SAMPLE_MARKDOWN = """\
# Gardener Architecture

Gardener is a Kubernetes-native system for managing shoot clusters at scale.

## Components

### Gardenlet

The gardenlet is the primary agent running in each seed cluster.
It reconciles `Shoot` resources and drives the lifecycle of shoot control planes.

### API Server

The Gardener API server extends the Kubernetes API with Gardener-specific resources
such as `Shoot`, `Seed`, `CloudProfile`, and `SecretBinding`.

## Networking

Shoot clusters communicate with their seed via a VPN tunnel established by the
`vpn-shoot` and `vpn-seed-server` components.

### DNS

Gardener manages DNS records for shoot API servers using the `dnsrecord` controller.
"""

SAMPLE_CODE = """\
package controller

import (
    "context"
    "fmt"
)

// ReconcileShoot reconciles a single Shoot resource.
func (r *ShootReconciler) ReconcileShoot(ctx context.Context, shoot *Shoot) error {
    if shoot == nil {
        return fmt.Errorf("shoot is nil")
    }
    return r.syncShoot(ctx, shoot)
}

func (r *ShootReconciler) syncShoot(ctx context.Context, shoot *Shoot) error {
    return nil
}

type ShootReconciler struct {
    Client client.Client
    Scheme *runtime.Scheme
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(content: str) -> Document:
    """Create a test Document with the given content."""
    return Document(content=content, source="https://example.com/test")


# ---------------------------------------------------------------------------
# MarkdownChunker tests
# ---------------------------------------------------------------------------


class TestMarkdownChunker:
    """Tests for MarkdownChunker."""

    def test_returns_list_of_documents(self) -> None:
        """chunk() must return a non-empty list of Document objects."""
        chunker = MarkdownChunker(chunk_size=500, chunk_overlap=50)
        doc = _make_doc(SAMPLE_MARKDOWN)
        chunks = chunker.chunk(doc)

        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        assert all(isinstance(c, Document) for c in chunks)

    def test_chunk_index_in_metadata(self) -> None:
        """Every chunk must have chunk_index in metadata."""
        chunker = MarkdownChunker(chunk_size=300, chunk_overlap=30)
        chunks = chunker.chunk(_make_doc(SAMPLE_MARKDOWN))

        for chunk in chunks:
            assert "chunk_index" in chunk.metadata

    def test_total_chunks_in_metadata(self) -> None:
        """Every chunk must have total_chunks in metadata."""
        chunker = MarkdownChunker(chunk_size=300, chunk_overlap=30)
        chunks = chunker.chunk(_make_doc(SAMPLE_MARKDOWN))

        for chunk in chunks:
            assert "total_chunks" in chunk.metadata

    def test_parent_id_in_metadata(self) -> None:
        """Every chunk must have parent_id in metadata."""
        chunker = MarkdownChunker(chunk_size=300, chunk_overlap=30)
        doc = _make_doc(SAMPLE_MARKDOWN)
        chunks = chunker.chunk(doc)

        for chunk in chunks:
            assert "parent_id" in chunk.metadata

    def test_parent_id_matches_source_document(self) -> None:
        """parent_id must equal the id of the source Document."""
        chunker = MarkdownChunker(chunk_size=300, chunk_overlap=30)
        doc = _make_doc(SAMPLE_MARKDOWN)
        chunks = chunker.chunk(doc)

        for chunk in chunks:
            assert chunk.metadata["parent_id"] == doc.id

    def test_chunk_index_is_sequential(self) -> None:
        """chunk_index values must be 0, 1, 2, … (no gaps)."""
        chunker = MarkdownChunker(chunk_size=300, chunk_overlap=30)
        chunks = chunker.chunk(_make_doc(SAMPLE_MARKDOWN))

        indices = [c.metadata["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_total_chunks_is_consistent(self) -> None:
        """total_chunks must equal the actual number of chunks produced."""
        chunker = MarkdownChunker(chunk_size=300, chunk_overlap=30)
        chunks = chunker.chunk(_make_doc(SAMPLE_MARKDOWN))

        total_reported = chunks[0].metadata["total_chunks"]
        assert total_reported == len(chunks)
        assert all(c.metadata["total_chunks"] == total_reported for c in chunks)

    def test_chunk_inherits_parent_metadata(self) -> None:
        """Chunk metadata must include all fields from the parent Document."""
        chunker = MarkdownChunker(chunk_size=300, chunk_overlap=30)
        doc = Document(
            content=SAMPLE_MARKDOWN,
            source="https://example.com",
            metadata={"repo": "gardener/documentation", "content_type": "doc"},
        )
        chunks = chunker.chunk(doc)

        for chunk in chunks:
            assert chunk.metadata["repo"] == "gardener/documentation"
            assert chunk.metadata["content_type"] == "doc"

    def test_source_is_preserved(self) -> None:
        """Chunk source must match the parent Document source."""
        chunker = MarkdownChunker()
        doc = _make_doc(SAMPLE_MARKDOWN)
        chunks = chunker.chunk(doc)

        for chunk in chunks:
            assert chunk.source == doc.source

    def test_empty_content_produces_one_chunk(self) -> None:
        """An empty document must still produce exactly one chunk."""
        chunker = MarkdownChunker()
        doc = _make_doc("")
        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
        assert chunks[0].metadata["chunk_index"] == 0
        assert chunks[0].metadata["total_chunks"] == 1
        assert chunks[0].metadata["parent_id"] == doc.id

    def test_chunk_many_flattens_results(self) -> None:
        """chunk_many() must return a flat list across multiple documents."""
        chunker = MarkdownChunker(chunk_size=300, chunk_overlap=30)
        docs = [_make_doc(SAMPLE_MARKDOWN), _make_doc(SAMPLE_MARKDOWN)]
        all_chunks = chunker.chunk_many(docs)

        assert isinstance(all_chunks, list)
        # Each document produces at least 1 chunk; two docs → at least 2.
        assert len(all_chunks) >= 2

    def test_chunk_size_affects_output_count(self) -> None:
        """A smaller chunk_size must produce more chunks than a larger one."""
        small_chunker = MarkdownChunker(chunk_size=200, chunk_overlap=20)
        large_chunker = MarkdownChunker(chunk_size=2000, chunk_overlap=200)
        doc = _make_doc(SAMPLE_MARKDOWN)

        small_chunks = small_chunker.chunk(doc)
        large_chunks = large_chunker.chunk(doc)

        assert len(small_chunks) >= len(large_chunks)


# ---------------------------------------------------------------------------
# CodeChunker tests
# ---------------------------------------------------------------------------


class TestCodeChunker:
    """Tests for CodeChunker."""

    def test_returns_list_of_documents(self) -> None:
        """chunk() must return a non-empty list of Document objects."""
        chunker = CodeChunker(chunk_size=500, chunk_overlap=50)
        doc = _make_doc(SAMPLE_CODE)
        chunks = chunker.chunk(doc)

        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        assert all(isinstance(c, Document) for c in chunks)

    def test_chunk_index_in_metadata(self) -> None:
        """Every chunk must carry chunk_index in metadata."""
        chunker = CodeChunker(chunk_size=200, chunk_overlap=20)
        chunks = chunker.chunk(_make_doc(SAMPLE_CODE))

        for chunk in chunks:
            assert "chunk_index" in chunk.metadata

    def test_total_chunks_in_metadata(self) -> None:
        """Every chunk must carry total_chunks in metadata."""
        chunker = CodeChunker(chunk_size=200, chunk_overlap=20)
        chunks = chunker.chunk(_make_doc(SAMPLE_CODE))

        for chunk in chunks:
            assert "total_chunks" in chunk.metadata

    def test_parent_id_in_metadata(self) -> None:
        """Every chunk must carry parent_id in metadata."""
        chunker = CodeChunker(chunk_size=200, chunk_overlap=20)
        doc = _make_doc(SAMPLE_CODE)
        chunks = chunker.chunk(doc)

        for chunk in chunks:
            assert "parent_id" in chunk.metadata

    def test_parent_id_matches_source_document(self) -> None:
        """parent_id must equal the id of the source Document."""
        chunker = CodeChunker(chunk_size=200, chunk_overlap=20)
        doc = _make_doc(SAMPLE_CODE)
        chunks = chunker.chunk(doc)

        for chunk in chunks:
            assert chunk.metadata["parent_id"] == doc.id

    def test_chunk_index_is_sequential(self) -> None:
        """chunk_index values must form a contiguous sequence from 0."""
        chunker = CodeChunker(chunk_size=200, chunk_overlap=20)
        chunks = chunker.chunk(_make_doc(SAMPLE_CODE))

        indices = [c.metadata["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_total_chunks_is_consistent(self) -> None:
        """total_chunks must equal the actual chunk count."""
        chunker = CodeChunker(chunk_size=200, chunk_overlap=20)
        chunks = chunker.chunk(_make_doc(SAMPLE_CODE))

        total_reported = chunks[0].metadata["total_chunks"]
        assert total_reported == len(chunks)

    def test_inherits_parent_metadata(self) -> None:
        """Chunks must carry arbitrary metadata from the parent Document."""
        chunker = CodeChunker(chunk_size=200, chunk_overlap=20)
        doc = Document(
            content=SAMPLE_CODE,
            source="https://github.com/gardener/gardener/blob/main/pkg/controller/shoot.go",
            metadata={"language": "go", "package": "controller"},
        )
        chunks = chunker.chunk(doc)

        for chunk in chunks:
            assert chunk.metadata["language"] == "go"
            assert chunk.metadata["package"] == "controller"

    def test_empty_content_produces_one_chunk(self) -> None:
        """An empty document must still produce exactly one chunk."""
        chunker = CodeChunker()
        doc = _make_doc("")
        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
        assert chunks[0].metadata["total_chunks"] == 1
        assert chunks[0].metadata["parent_id"] == doc.id

    def test_chunk_many_returns_flat_list(self) -> None:
        """chunk_many() must return all chunks from all documents in a flat list."""
        chunker = CodeChunker(chunk_size=200, chunk_overlap=20)
        docs = [_make_doc(SAMPLE_CODE), _make_doc(SAMPLE_MARKDOWN)]
        all_chunks = chunker.chunk_many(docs)

        assert isinstance(all_chunks, list)
        assert len(all_chunks) >= 2
