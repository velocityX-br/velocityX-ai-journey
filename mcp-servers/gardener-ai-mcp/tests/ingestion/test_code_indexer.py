"""Tests for ingestion/code_indexer.py — CodeIngester."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock

import pytest

from ingestion.base import Document, IngestionError
from ingestion.code_indexer import (
    CodeIngester,
    _extract_package,
    _extract_signatures,
)

# ---------------------------------------------------------------------------
# Sample Go source content
# ---------------------------------------------------------------------------

SAMPLE_GO_SOURCE = """\
package controller

import (
\t"context"
\t"fmt"
)

// ShootReconciler manages Shoot resources in the garden cluster.
type ShootReconciler struct {
\tClient client.Client
\tLogger logr.Logger
}

// Reconcile implements the reconciliation loop for Shoot resources.
func (r *ShootReconciler) Reconcile(ctx context.Context, req reconcile.Request) (reconcile.Result, error) {
\tif err := r.syncShoot(ctx, req.NamespacedName); err != nil {
\t\treturn reconcile.Result{}, fmt.Errorf("sync failed: %w", err)
\t}
\treturn reconcile.Result{}, nil
}

func (r *ShootReconciler) syncShoot(ctx context.Context, name types.NamespacedName) error {
\treturn nil
}

type ShootStatus struct {
\tObservedGeneration int64
\tLastOperation      string
}
"""

SAMPLE_GO_NO_FUNCS = """\
package constants

const (
\tDefaultTimeout = 30
\tMaxRetries     = 5
)
"""


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_settings(gardener_repo: str = "gardener/gardener") -> Any:
    settings = MagicMock()
    settings.github_gardener_repo = gardener_repo
    return settings


def _make_content_file(
    path: str,
    file_type: str = "file",
    sha: str = "abc123",
    html_url: str = "https://github.com/gardener/gardener/blob/main/pkg/controller/shoot.go",
    source: str = SAMPLE_GO_SOURCE,
) -> MagicMock:
    cf = MagicMock()
    cf.path = path
    cf.type = file_type
    cf.sha = sha
    cf.html_url = html_url
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    cf.encoding = "base64"
    cf.content = encoded
    cf.decoded_content = source.encode("utf-8")
    return cf


async def _async_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# _extract_package unit tests
# ---------------------------------------------------------------------------


class TestExtractPackage:
    """Tests for the _extract_package regex helper."""

    def test_extracts_package_name(self) -> None:
        assert _extract_package(SAMPLE_GO_SOURCE) == "controller"

    def test_extracts_simple_package(self) -> None:
        assert _extract_package("package main\n\nfunc main() {}") == "main"

    def test_returns_unknown_when_missing(self) -> None:
        assert _extract_package("// no package declaration") == "unknown"

    def test_constants_package(self) -> None:
        assert _extract_package(SAMPLE_GO_NO_FUNCS) == "constants"


# ---------------------------------------------------------------------------
# _extract_signatures unit tests
# ---------------------------------------------------------------------------


class TestExtractSignatures:
    """Tests for the _extract_signatures regex helper."""

    def test_extracts_func_signatures(self) -> None:
        result = _extract_signatures(SAMPLE_GO_SOURCE)
        assert "func (r *ShootReconciler) Reconcile(" in result

    def test_extracts_multiple_funcs(self) -> None:
        result = _extract_signatures(SAMPLE_GO_SOURCE)
        # Both Reconcile and syncShoot must appear.
        assert "Reconcile" in result
        assert "syncShoot" in result

    def test_extracts_type_declarations(self) -> None:
        result = _extract_signatures(SAMPLE_GO_SOURCE)
        assert "type ShootReconciler struct" in result

    def test_extracts_multiple_types(self) -> None:
        result = _extract_signatures(SAMPLE_GO_SOURCE)
        assert "ShootReconciler" in result
        assert "ShootStatus" in result

    def test_no_funcs_returns_fallback(self) -> None:
        """When no func/type lines exist, return the first 500 chars."""
        result = _extract_signatures(SAMPLE_GO_NO_FUNCS)
        # No funcs — should fall back to truncated source.
        assert "package constants" in result


# ---------------------------------------------------------------------------
# CodeIngester tests
# ---------------------------------------------------------------------------


class TestCodeIngesterInit:
    def test_instantiation(self) -> None:
        ingester = CodeIngester(MagicMock(), _make_settings())
        assert ingester is not None


class TestCodeIngesterIngest:

    @pytest.mark.asyncio
    async def test_ingest_is_coroutine(self) -> None:
        import inspect
        ingester = CodeIngester(MagicMock(), _make_settings())
        assert inspect.iscoroutinefunction(ingester.ingest)

    @pytest.mark.asyncio
    async def test_ingest_returns_list_of_documents(self, mocker: Any) -> None:
        """ingest() must return list[Document] for a repo with one Go file."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        go_file = _make_content_file(
            path="pkg/controller/shoot.go",
            html_url="https://github.com/gardener/gardener/blob/main/pkg/controller/shoot.go",
        )

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [go_file]
            if path == "pkg/controller/shoot.go":
                return go_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.code_indexer.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = CodeIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(d, Document) for d in result)

    @pytest.mark.asyncio
    async def test_document_has_language_go(self, mocker: Any) -> None:
        """Every Document must have metadata['language'] == 'go'."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        go_file = _make_content_file(path="pkg/shoot.go")

        def get_contents_side_effect(path: str) -> Any:
            if path in ("", "pkg/shoot.go"):
                return go_file if path == "pkg/shoot.go" else [go_file]
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.code_indexer.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = CodeIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert len(result) >= 1
        for doc in result:
            assert doc.metadata["language"] == "go"

    @pytest.mark.asyncio
    async def test_document_has_package_metadata(self, mocker: Any) -> None:
        """Document metadata must include a 'package' field extracted from source."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        go_file = _make_content_file(path="pkg/controller/shoot.go")

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [go_file]
            if path == "pkg/controller/shoot.go":
                return go_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.code_indexer.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = CodeIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert len(result) >= 1
        doc = result[0]
        assert "package" in doc.metadata
        # SAMPLE_GO_SOURCE has 'package controller'
        assert doc.metadata["package"] == "controller"

    @pytest.mark.asyncio
    async def test_non_go_files_are_skipped(self, mocker: Any) -> None:
        """Files that do not end in .go must not produce Documents."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        yaml_file = _make_content_file(path="config/values.yaml")
        yaml_file.path = "config/values.yaml"

        go_file = _make_content_file(path="pkg/main.go")

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [yaml_file, go_file]
            if path == "pkg/main.go":
                return go_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.code_indexer.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = CodeIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        paths = [d.metadata["path"] for d in result]
        assert not any(p.endswith(".yaml") for p in paths)
        assert any(p.endswith(".go") for p in paths)

    @pytest.mark.asyncio
    async def test_function_signature_extracted_in_content(self, mocker: Any) -> None:
        """Document content must contain at least one function signature."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        go_file = _make_content_file(
            path="pkg/controller/shoot.go",
            source=SAMPLE_GO_SOURCE,
        )

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [go_file]
            if path == "pkg/controller/shoot.go":
                return go_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.code_indexer.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = CodeIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert len(result) >= 1
        content = result[0].content
        # At least one 'func ' pattern must appear in the extracted content.
        assert "func " in content

    @pytest.mark.asyncio
    async def test_raises_on_repo_failure(self, mocker: Any) -> None:
        gh = MagicMock()
        gh.get_repo.side_effect = Exception("not found")
        settings = _make_settings()

        mocker.patch(
            "ingestion.code_indexer.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = CodeIngester(github_client=gh, settings=settings)

        with pytest.raises(IngestionError):
            await ingester.ingest()

    @pytest.mark.asyncio
    async def test_vendor_directory_skipped(self, mocker: Any) -> None:
        """The vendor/ directory must be skipped entirely."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        vendor_dir = _make_content_file(path="vendor", file_type="dir")
        vendor_dir.path = "vendor"

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [vendor_dir]
            # vendor/ content should never be fetched
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.code_indexer.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = CodeIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        # No documents from vendor.
        assert result == []

    @pytest.mark.asyncio
    async def test_document_has_repo_metadata(self, mocker: Any) -> None:
        """Document metadata must include the 'repo' field."""
        gh = MagicMock()
        settings = _make_settings(gardener_repo="gardener/gardener")

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        go_file = _make_content_file(path="main.go")

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [go_file]
            if path == "main.go":
                return go_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.code_indexer.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = CodeIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert len(result) >= 1
        assert result[0].metadata["repo"] == "gardener/gardener"
