"""Tests for ingestion/base.py — BaseIngester abstract contract and Document model."""

from __future__ import annotations

import pytest

from ingestion.base import BaseIngester, Document, IngestionError

# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------


class TestDocument:
    """Tests for the Document Pydantic model."""

    def test_document_requires_content_and_source(self) -> None:
        """Document must be creatable with only content and source."""
        doc = Document(content="hello", source="https://example.com")
        assert doc.content == "hello"
        assert doc.source == "https://example.com"
        assert doc.metadata == {}

    def test_document_id_auto_generated(self) -> None:
        """Each Document gets a unique UUID-style id on creation."""
        doc1 = Document(content="a", source="s1")
        doc2 = Document(content="b", source="s2")
        assert doc1.id != doc2.id
        assert len(doc1.id) == 36  # UUID format: 8-4-4-4-12

    def test_document_id_is_stable(self) -> None:
        """Document id must remain unchanged after creation."""
        doc = Document(content="x", source="s")
        original_id = doc.id
        assert doc.id == original_id

    def test_document_metadata_accepts_arbitrary_types(self) -> None:
        """Metadata dict must accept any JSON-serialisable value types."""
        doc = Document(
            content="text",
            source="s",
            metadata={
                "repo": "gardener/documentation",
                "labels": ["bug", "enhancement"],
                "issue_number": 42,
                "merged": True,
                "merged_at": None,
            },
        )
        assert doc.metadata["repo"] == "gardener/documentation"
        assert doc.metadata["labels"] == ["bug", "enhancement"]
        assert doc.metadata["issue_number"] == 42
        assert doc.metadata["merged"] is True
        assert doc.metadata["merged_at"] is None


# ---------------------------------------------------------------------------
# BaseIngester abstract enforcement
# ---------------------------------------------------------------------------


class TestBaseIngesterAbstract:
    """BaseIngester must not be directly instantiable."""

    def test_direct_instantiation_raises(self) -> None:
        """Attempting to instantiate BaseIngester directly must raise TypeError."""
        with pytest.raises(TypeError):
            BaseIngester()  # type: ignore[abstract]

    def test_subclass_without_ingest_raises(self) -> None:
        """A subclass that does not implement ingest() must also raise."""

        class IncompleteIngester(BaseIngester):
            pass  # no ingest()

        with pytest.raises(TypeError):
            IncompleteIngester()  # type: ignore[abstract]

    def test_concrete_subclass_is_instantiable(self) -> None:
        """A subclass that implements ingest() must be instantiable."""

        class ConcreteIngester(BaseIngester):
            async def ingest(self) -> list[Document]:
                return [Document(content="test", source="https://example.com")]

        ingester = ConcreteIngester()
        assert ingester is not None

    @pytest.mark.asyncio
    async def test_concrete_ingest_returns_documents(self) -> None:
        """A concrete ingest() implementation must return a list of Documents."""

        class ConcreteIngester(BaseIngester):
            async def ingest(self) -> list[Document]:
                return [
                    Document(content="doc1", source="s1"),
                    Document(content="doc2", source="s2"),
                ]

        ingester = ConcreteIngester()
        result = await ingester.ingest()

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(d, Document) for d in result)

    @pytest.mark.asyncio
    async def test_concrete_ingest_may_return_empty_list(self) -> None:
        """ingest() returning an empty list is valid."""

        class EmptyIngester(BaseIngester):
            async def ingest(self) -> list[Document]:
                return []

        result = await EmptyIngester().ingest()
        assert result == []


# ---------------------------------------------------------------------------
# IngestionError
# ---------------------------------------------------------------------------


class TestIngestionError:
    """Tests for the IngestionError helper exception."""

    def test_str_without_source(self) -> None:
        """Error without source must format cleanly."""
        err = IngestionError("something went wrong")
        assert "something went wrong" in str(err)
        assert "IngestionError" in str(err)

    def test_str_with_source(self) -> None:
        """Error with source must include the source in the string."""
        err = IngestionError("connection refused", source="gardener/documentation")
        assert "gardener/documentation" in str(err)
        assert "connection refused" in str(err)

    def test_is_exception(self) -> None:
        """IngestionError must be raiseable and catchable as Exception."""
        with pytest.raises(IngestionError, match="test error"):
            raise IngestionError("test error")
