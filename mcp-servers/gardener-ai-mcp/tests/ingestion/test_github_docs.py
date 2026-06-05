"""Tests for ingestion/github_docs.py — GitHubDocsIngester."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ingestion.base import Document, IngestionError
from ingestion.github_docs import GitHubDocsIngester, _is_proposal_path

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_settings(
    docs_repo: str = "gardener/documentation",
    gardener_repo: str = "gardener/gardener",
) -> Any:
    """Return a minimal settings-like object."""
    settings = MagicMock()
    settings.github_docs_repo = docs_repo
    settings.github_gardener_repo = gardener_repo
    return settings


def _make_content_file(
    path: str,
    file_type: str = "file",
    sha: str = "abc123",
    html_url: str = "https://github.com/gardener/documentation/blob/main/test.md",
    encoding: str = "base64",
    content: str = "IyBIZWxsbw==",  # base64("# Hello")
) -> MagicMock:
    """Build a MagicMock that mimics a PyGithub ContentFile."""
    cf = MagicMock()
    cf.path = path
    cf.type = file_type
    cf.sha = sha
    cf.html_url = html_url
    cf.encoding = encoding
    cf.content = content
    cf.decoded_content = b"# Hello"
    return cf


# ---------------------------------------------------------------------------
# Unit tests: _is_proposal_path helper
# ---------------------------------------------------------------------------


class TestIsProposalPath:
    """Tests for the _is_proposal_path path classifier."""

    def test_proposal_directory_detected(self) -> None:
        assert _is_proposal_path("docs/proposals/001-new-feature.md") is True

    def test_proposals_plural_detected(self) -> None:
        assert _is_proposal_path("website/proposals/index.md") is True

    def test_gep_directory_detected(self) -> None:
        assert _is_proposal_path("gep/001/README.md") is True

    def test_regular_doc_not_detected(self) -> None:
        assert _is_proposal_path("website/documentation/concepts/shoot.md") is False

    def test_case_insensitive(self) -> None:
        assert _is_proposal_path("PROPOSALS/001.md") is True


# ---------------------------------------------------------------------------
# GitHubDocsIngester tests
# ---------------------------------------------------------------------------


class TestGitHubDocsIngesterInit:
    """Constructor and type contract tests."""

    def test_instantiation_with_client_and_settings(self) -> None:
        """Ingester must accept a github client and settings object."""
        gh = MagicMock()
        settings = _make_settings()
        ingester = GitHubDocsIngester(github_client=gh, settings=settings)
        assert ingester is not None


class TestGitHubDocsIngesterIngest:
    """Tests for the ingest() coroutine."""

    @pytest.mark.asyncio
    async def test_ingest_is_coroutine(self) -> None:
        """ingest() must be an awaitable coroutine function."""
        import inspect

        gh = MagicMock()
        settings = _make_settings()
        ingester = GitHubDocsIngester(github_client=gh, settings=settings)
        assert inspect.iscoroutinefunction(ingester.ingest)

    @pytest.mark.asyncio
    async def test_ingest_returns_list_of_documents(
        self, mocker: Any
    ) -> None:
        """ingest() must return a list[Document] when the API succeeds."""
        gh = MagicMock()
        settings = _make_settings()

        # Repo mock
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        # website/ directory listing → one markdown file
        md_file = _make_content_file(
            path="website/docs/shoot.md",
            html_url="https://github.com/gardener/documentation/blob/main/website/docs/shoot.md",
        )
        root_dir = _make_content_file(path="website", file_type="dir")

        # get_contents("") → [website dir] + no proposal dirs
        # get_contents("website") → [md_file]
        # get_contents("website/docs/shoot.md") → md_file (file fetch)
        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [root_dir]
            if path == "website":
                return [md_file]
            if path == "website/docs/shoot.md":
                return md_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        # Wrap to_thread so it calls the sync function synchronously in tests.
        mocker.patch(
            "ingestion.github_docs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubDocsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert isinstance(result, list)
        assert all(isinstance(d, Document) for d in result)

    @pytest.mark.asyncio
    async def test_ingest_raises_on_repo_access_failure(
        self, mocker: Any
    ) -> None:
        """ingest() must raise IngestionError when get_repo fails."""
        gh = MagicMock()
        gh.get_repo.side_effect = Exception("network error")
        settings = _make_settings()

        mocker.patch(
            "ingestion.github_docs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubDocsIngester(github_client=gh, settings=settings)

        with pytest.raises(IngestionError, match="gardener/documentation"):
            await ingester.ingest()

    @pytest.mark.asyncio
    async def test_document_metadata_has_required_fields(
        self, mocker: Any
    ) -> None:
        """Each Document must carry repo, path, sha, url, content_type."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        md_file = _make_content_file(
            path="website/index.md",
            sha="deadbeef",
            html_url="https://github.com/gardener/documentation/blob/main/website/index.md",
        )
        root_dir = _make_content_file(path="website", file_type="dir")

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [root_dir]
            if path == "website":
                return [md_file]
            if path == "website/index.md":
                return md_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.github_docs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubDocsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert len(result) >= 1
        doc = result[0]
        assert "repo" in doc.metadata
        assert "path" in doc.metadata
        assert "sha" in doc.metadata
        assert "url" in doc.metadata
        assert "content_type" in doc.metadata

    @pytest.mark.asyncio
    async def test_content_type_doc_for_website_files(
        self, mocker: Any
    ) -> None:
        """Files under website/ must have content_type='doc'."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        md_file = _make_content_file(path="website/concepts/shoot.md")
        website_dir = _make_content_file(path="website", file_type="dir")

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [website_dir]
            if path == "website":
                return [md_file]
            if path == "website/concepts/shoot.md":
                return md_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.github_docs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubDocsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert any(d.metadata["content_type"] == "doc" for d in result)

    @pytest.mark.asyncio
    async def test_content_type_proposal_for_proposal_directories(
        self, mocker: Any
    ) -> None:
        """Files inside proposal directories must have content_type='proposal'."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        proposal_file = _make_content_file(
            path="proposals/001-shoot-hibernation.md",
            html_url="https://github.com/gardener/documentation/blob/main/proposals/001.md",
        )
        proposal_dir = _make_content_file(path="proposals", file_type="dir")

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [proposal_dir]
            if path == "proposals":
                return [proposal_file]
            if path == "proposals/001-shoot-hibernation.md":
                return proposal_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.github_docs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubDocsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        # The root contains a proposal dir, not website/, so the website walk
        # may return 0 docs but the proposal walk should return 1.
        assert any(d.metadata["content_type"] == "proposal" for d in result)

    @pytest.mark.asyncio
    async def test_non_md_files_are_skipped(self, mocker: Any) -> None:
        """Only .md files must be included; .yaml, .png etc. are ignored."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        yaml_file = _make_content_file(path="website/config.yaml")
        md_file = _make_content_file(path="website/index.md")
        website_dir = _make_content_file(path="website", file_type="dir")

        def get_contents_side_effect(path: str) -> Any:
            if path == "":
                return [website_dir]
            if path == "website":
                return [yaml_file, md_file]
            if path == "website/index.md":
                return md_file
            return []

        repo_mock.get_contents.side_effect = get_contents_side_effect

        mocker.patch(
            "ingestion.github_docs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubDocsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        paths = [d.metadata["path"] for d in result]
        assert not any(p.endswith(".yaml") for p in paths)


# ---------------------------------------------------------------------------
# Async shim used by mocker.patch
# ---------------------------------------------------------------------------


async def _async_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call a synchronous function and return its result as a coroutine value."""
    return fn(*args, **kwargs)
