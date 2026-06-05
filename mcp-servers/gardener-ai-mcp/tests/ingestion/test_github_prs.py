"""Tests for ingestion/github_prs.py — GitHubPRsIngester."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from ingestion.base import Document, IngestionError
from ingestion.github_prs import GitHubPRsIngester, _extract_linked_issues

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_settings(gardener_repo: str = "gardener/gardener") -> Any:
    settings = MagicMock()
    settings.github_gardener_repo = gardener_repo
    return settings


def _make_label(name: str) -> MagicMock:
    label = MagicMock()
    label.name = name
    return label


def _make_review_comment(body: str, path: str = "pkg/controller/shoot.go") -> MagicMock:
    comment = MagicMock()
    comment.body = body
    comment.path = path
    comment.user = MagicMock()
    comment.user.login = "reviewer"
    return comment


def _make_pr(
    number: int = 1,
    title: str = "Test PR",
    body: str = "Fixes #10",
    state: str = "closed",
    merged: bool = True,
    merged_at: datetime | None = None,
    labels: list[str] | None = None,
    html_url: str = "https://github.com/gardener/gardener/pull/1",
) -> MagicMock:
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.body = body
    pr.state = state
    pr.merged = merged
    pr.merged_at = merged_at or (
        datetime(2024, 3, 15, tzinfo=UTC) if merged else None
    )
    pr.labels = [_make_label(l) for l in (labels or [])]
    pr.html_url = html_url
    return pr


# ---------------------------------------------------------------------------
# Async shim
# ---------------------------------------------------------------------------


async def _async_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# _extract_linked_issues unit tests
# ---------------------------------------------------------------------------


class TestExtractLinkedIssues:
    """Tests for the _extract_linked_issues regex helper."""

    def test_bare_hash_reference(self) -> None:
        """#123 on its own must be extracted."""
        assert _extract_linked_issues("See #123 for context") == [123]

    def test_fixes_keyword(self) -> None:
        """'Fixes #123' must extract 123."""
        assert _extract_linked_issues("Fixes #123") == [123]

    def test_closes_keyword(self) -> None:
        """'Closes #456' must extract 456."""
        assert _extract_linked_issues("Closes #456") == [456]

    def test_resolves_keyword(self) -> None:
        """'Resolves #789' must extract 789."""
        assert _extract_linked_issues("Resolves #789") == [789]

    def test_multiple_references_deduped_and_sorted(self) -> None:
        """Multiple references must be deduplicated and sorted ascending."""
        body = "Fixes #123 and closes #456"
        result = _extract_linked_issues(body)
        assert result == [123, 456]

    def test_duplicate_references_deduplicated(self) -> None:
        """The same issue number referenced twice must appear once."""
        body = "#100 and #100 again"
        result = _extract_linked_issues(body)
        assert result == [100]

    def test_no_references_returns_empty_list(self) -> None:
        """Body with no issue references must return an empty list."""
        assert _extract_linked_issues("No references here") == []

    def test_none_body_returns_empty_list(self) -> None:
        """None body must return an empty list."""
        assert _extract_linked_issues(None) == []

    def test_case_insensitive_keyword(self) -> None:
        """Keywords must match regardless of case."""
        assert 42 in _extract_linked_issues("FIXES #42")
        assert 55 in _extract_linked_issues("Closes #55")

    def test_complex_body(self) -> None:
        """Real-world PR body with multiple keywords and bare refs."""
        body = (
            "This PR implements the new feature.\n"
            "Fixes #123 and closes #456.\n"
            "Related to #789.\n"
            "See also gardener/gardener#999 (external, should not match)."
        )
        result = _extract_linked_issues(body)
        # 123, 456, 789 from local refs; 999 from cross-repo ref (may or may not match)
        assert 123 in result
        assert 456 in result
        assert 789 in result


# ---------------------------------------------------------------------------
# GitHubPRsIngester tests
# ---------------------------------------------------------------------------


class TestGitHubPRsIngesterInit:
    def test_instantiation(self) -> None:
        ingester = GitHubPRsIngester(MagicMock(), _make_settings())
        assert ingester is not None


class TestGitHubPRsIngesterIngest:

    @pytest.mark.asyncio
    async def test_ingest_is_coroutine(self) -> None:
        import inspect
        ingester = GitHubPRsIngester(MagicMock(), _make_settings())
        assert inspect.iscoroutinefunction(ingester.ingest)

    @pytest.mark.asyncio
    async def test_ingest_returns_list_of_documents(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        pr = _make_pr(number=1, body="Fixes #10")
        pr.get_review_comments.return_value = []
        repo_mock.get_pulls.return_value = [pr]

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Document)

    @pytest.mark.asyncio
    async def test_document_has_pr_number(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        pr = _make_pr(number=42)
        pr.get_review_comments.return_value = []
        repo_mock.get_pulls.return_value = [pr]

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert result[0].metadata["pr_number"] == 42

    @pytest.mark.asyncio
    async def test_document_has_state(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        pr = _make_pr(state="closed")
        pr.get_review_comments.return_value = []
        repo_mock.get_pulls.return_value = [pr]

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert result[0].metadata["state"] == "closed"

    @pytest.mark.asyncio
    async def test_document_has_merged_flag(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        pr = _make_pr(merged=True)
        pr.get_review_comments.return_value = []
        repo_mock.get_pulls.return_value = [pr]

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert result[0].metadata["merged"] is True

    @pytest.mark.asyncio
    async def test_linked_issues_extracted_from_body(self, mocker: Any) -> None:
        """linked_issues must contain issue numbers from 'Fixes #123 and closes #456'."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        pr = _make_pr(number=99, body="Fixes #123 and closes #456")
        pr.get_review_comments.return_value = []
        repo_mock.get_pulls.return_value = [pr]

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        linked = result[0].metadata["linked_issues"]
        assert isinstance(linked, list)
        assert 123 in linked
        assert 456 in linked

    @pytest.mark.asyncio
    async def test_linked_issues_empty_for_no_refs(self, mocker: Any) -> None:
        """linked_issues must be an empty list when the body has no issue refs."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        pr = _make_pr(body="No issue references in this PR.")
        pr.get_review_comments.return_value = []
        repo_mock.get_pulls.return_value = [pr]

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert result[0].metadata["linked_issues"] == []

    @pytest.mark.asyncio
    async def test_review_comments_included_in_content(self, mocker: Any) -> None:
        """Review comment text must appear in the Document content."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        pr = _make_pr(body="A feature PR")
        rc = _make_review_comment("Please add a unit test here.")
        pr.get_review_comments.return_value = [rc]
        repo_mock.get_pulls.return_value = [pr]

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert "Please add a unit test here." in result[0].content

    @pytest.mark.asyncio
    async def test_raises_on_repo_failure(self, mocker: Any) -> None:
        gh = MagicMock()
        gh.get_repo.side_effect = Exception("timeout")
        settings = _make_settings()

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)

        with pytest.raises(IngestionError):
            await ingester.ingest()

    @pytest.mark.asyncio
    async def test_get_pulls_called_with_state_all(self, mocker: Any) -> None:
        """get_pulls must be called with state='all'."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock
        repo_mock.get_pulls.return_value = []

        mocker.patch(
            "ingestion.github_prs.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        await ingester.ingest()

        repo_mock.get_pulls.assert_called_once_with(state="all")
