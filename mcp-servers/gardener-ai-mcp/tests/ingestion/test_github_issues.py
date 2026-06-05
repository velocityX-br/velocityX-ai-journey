"""Tests for ingestion/github_issues.py — GitHubIssuesIngester."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from ingestion.base import Document, IngestionError
from ingestion.github_issues import GitHubIssuesIngester

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


def _make_comment(body: str, author: str = "contributor") -> MagicMock:
    comment = MagicMock()
    comment.body = body
    comment.user = MagicMock()
    comment.user.login = author
    return comment


def _make_issue(
    number: int = 1,
    title: str = "Test issue",
    body: str = "Issue body",
    state: str = "open",
    labels: list[str] | None = None,
    created_at: datetime | None = None,
    closed_at: datetime | None = None,
    html_url: str = "https://github.com/gardener/gardener/issues/1",
) -> MagicMock:
    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.body = body
    issue.state = state
    issue.labels = [_make_label(l) for l in (labels or [])]
    issue.created_at = created_at or datetime(2024, 1, 1, tzinfo=UTC)
    issue.closed_at = closed_at
    issue.html_url = html_url
    return issue


# ---------------------------------------------------------------------------
# Async shim
# ---------------------------------------------------------------------------


async def _async_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGitHubIssuesIngesterInit:
    def test_instantiation(self) -> None:
        gh = MagicMock()
        settings = _make_settings()
        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        assert ingester is not None


class TestGitHubIssuesIngesterIngest:

    @pytest.mark.asyncio
    async def test_ingest_is_coroutine(self) -> None:
        import inspect
        ingester = GitHubIssuesIngester(MagicMock(), _make_settings())
        assert inspect.iscoroutinefunction(ingester.ingest)

    @pytest.mark.asyncio
    async def test_ingest_returns_list_of_documents(self, mocker: Any) -> None:
        """ingest() must return a list[Document]."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=1, title="Shoot fails", state="open")
        comment = _make_comment("This is a bug", "alice")
        issue.get_comments.return_value = [comment]
        repo_mock.get_issues.return_value = [issue]

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert isinstance(result, list)
        assert len(result) == 1
        assert all(isinstance(d, Document) for d in result)

    @pytest.mark.asyncio
    async def test_get_issues_called_with_state_all(self, mocker: Any) -> None:
        """Pagination must use state='all' to capture open and closed issues."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock
        repo_mock.get_issues.return_value = []

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        await ingester.ingest()

        repo_mock.get_issues.assert_called_once_with(state="all")

    @pytest.mark.asyncio
    async def test_document_has_labels_field(self, mocker: Any) -> None:
        """Document metadata must contain a 'labels' list."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=42, labels=["bug", "area/shoot"])
        issue.get_comments.return_value = []
        repo_mock.get_issues.return_value = [issue]

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        doc = result[0]
        assert "labels" in doc.metadata
        assert isinstance(doc.metadata["labels"], list)
        assert "bug" in doc.metadata["labels"]
        assert "area/shoot" in doc.metadata["labels"]

    @pytest.mark.asyncio
    async def test_document_has_state_field(self, mocker: Any) -> None:
        """Document metadata must contain 'state'."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=1, state="closed")
        issue.get_comments.return_value = []
        repo_mock.get_issues.return_value = [issue]

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert result[0].metadata["state"] == "closed"

    @pytest.mark.asyncio
    async def test_document_has_created_at_field(self, mocker: Any) -> None:
        """Document metadata must contain 'created_at' as an ISO 8601 string."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        created = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        issue = _make_issue(number=1, created_at=created)
        issue.get_comments.return_value = []
        repo_mock.get_issues.return_value = [issue]

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert "created_at" in result[0].metadata
        assert "2024-06-01" in result[0].metadata["created_at"]

    @pytest.mark.asyncio
    async def test_closed_at_is_none_for_open_issue(self, mocker: Any) -> None:
        """closed_at must be None for open issues."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=1, state="open", closed_at=None)
        issue.get_comments.return_value = []
        repo_mock.get_issues.return_value = [issue]

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert result[0].metadata["closed_at"] is None

    @pytest.mark.asyncio
    async def test_comments_included_in_content(self, mocker: Any) -> None:
        """Issue content must include comment bodies."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=1, body="issue body")
        comment = _make_comment("comment content here", "bob")
        issue.get_comments.return_value = [comment]
        repo_mock.get_issues.return_value = [issue]

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert "comment content here" in result[0].content

    @pytest.mark.asyncio
    async def test_ingest_raises_on_repo_failure(self, mocker: Any) -> None:
        """IngestionError must be raised when get_repo fails."""
        gh = MagicMock()
        gh.get_repo.side_effect = Exception("rate limit")
        settings = _make_settings()

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)

        with pytest.raises(IngestionError):
            await ingester.ingest()

    @pytest.mark.asyncio
    async def test_multiple_issues_returned(self, mocker: Any) -> None:
        """ingest() must return one Document per issue."""
        gh = MagicMock()
        settings = _make_settings()

        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issues = [
            _make_issue(number=i, title=f"Issue {i}")
            for i in range(1, 6)
        ]
        for issue in issues:
            issue.get_comments.return_value = []

        repo_mock.get_issues.return_value = issues

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=lambda fn, *args, **kwargs: _async_call(fn, *args, **kwargs),
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert len(result) == 5
        issue_numbers = [d.metadata["issue_number"] for d in result]
        assert sorted(issue_numbers) == [1, 2, 3, 4, 5]
