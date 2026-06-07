"""Tests for ingestion/github_issues.py — GitHubIssuesIngester.

Test inventory:
    TestGitHubIssuesIngesterInit
        test_instantiation

    TestGitHubIssuesIngesterIngest
        test_ingest_is_coroutine
        test_returns_documents_from_single_page
        test_get_issues_called_with_state_all_sort_updated_desc
        test_document_has_labels_field
        test_document_has_state_field
        test_document_has_created_at_field
        test_closed_at_is_none_for_open_issue
        test_comments_included_in_content
        test_ingest_raises_on_repo_failure
        test_prs_are_filtered_out
        test_max_issues_cap_stops_early
        test_max_issues_zero_means_no_cap
        test_multiple_pages_are_fetched
        test_stops_when_page_is_empty
"""

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


def _make_settings(
    gardener_repo: str = "gardener/gardener",
    max_issues: int = 0,
    batch_size: int = 100,
) -> Any:
    settings = MagicMock()
    settings.github_gardener_repo = gardener_repo
    settings.ingestion_max_issues = max_issues
    settings.ingestion_issues_batch_size = batch_size
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
    is_pr: bool = False,
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
    # pull_request is None for real issues; non-None signals a PR returned by
    # the /issues endpoint.
    issue.pull_request = MagicMock() if is_pr else None
    issue.get_comments.return_value = []
    return issue


def _make_paginated(pages: list[list[Any]]) -> MagicMock:
    """Return a mock PaginatedList whose get_page(i) returns pages[i] or []."""
    pl = MagicMock()
    pl.get_page.side_effect = lambda i: pages[i] if i < len(pages) else []
    return pl


# ---------------------------------------------------------------------------
# Async shim — makes asyncio.to_thread calls synchronous in tests
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
    async def test_returns_documents_from_single_page(self, mocker: Any) -> None:
        """ingest() returns one Document per issue on a single page."""
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=1, title="Shoot fails", state="open")
        issue.get_comments.return_value = [_make_comment("This is a bug", "alice")]

        repo_mock.get_issues.return_value = _make_paginated([[issue]])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        result = await ingester.ingest()

        assert isinstance(result, list)
        assert len(result) == 1
        assert all(isinstance(d, Document) for d in result)

    @pytest.mark.asyncio
    async def test_get_issues_called_with_state_all_sort_updated_desc(
        self, mocker: Any
    ) -> None:
        """get_issues must use state='all', sort='updated', direction='desc'."""
        gh = MagicMock()
        settings = _make_settings()
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock
        repo_mock.get_issues.return_value = _make_paginated([])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        await GitHubIssuesIngester(github_client=gh, settings=settings).ingest()

        repo_mock.get_issues.assert_called_once_with(
            state="all", sort="updated", direction="desc"
        )

    @pytest.mark.asyncio
    async def test_document_has_labels_field(self, mocker: Any) -> None:
        """Document metadata must contain a 'labels' list."""
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=42, labels=["bug", "area/shoot"])
        repo_mock.get_issues.return_value = _make_paginated([[issue]])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert "labels" in result[0].metadata
        assert set(result[0].metadata["labels"]) == {"bug", "area/shoot"}

    @pytest.mark.asyncio
    async def test_document_has_state_field(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=1, state="closed")
        repo_mock.get_issues.return_value = _make_paginated([[issue]])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert result[0].metadata["state"] == "closed"

    @pytest.mark.asyncio
    async def test_document_has_created_at_field(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        created = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        issue = _make_issue(number=1, created_at=created)
        repo_mock.get_issues.return_value = _make_paginated([[issue]])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert "2024-06-01" in result[0].metadata["created_at"]

    @pytest.mark.asyncio
    async def test_closed_at_is_none_for_open_issue(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=1, state="open", closed_at=None)
        repo_mock.get_issues.return_value = _make_paginated([[issue]])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert result[0].metadata["closed_at"] is None

    @pytest.mark.asyncio
    async def test_comments_included_in_content(self, mocker: Any) -> None:
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        issue = _make_issue(number=1, body="issue body")
        issue.get_comments.return_value = [_make_comment("comment content here", "bob")]
        repo_mock.get_issues.return_value = _make_paginated([[issue]])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert "comment content here" in result[0].content

    @pytest.mark.asyncio
    async def test_ingest_raises_on_repo_failure(self, mocker: Any) -> None:
        gh = MagicMock()
        gh.get_repo.side_effect = Exception("rate limit")
        settings = _make_settings()

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        with pytest.raises(IngestionError):
            await GitHubIssuesIngester(github_client=gh, settings=settings).ingest()

    @pytest.mark.asyncio
    async def test_prs_are_filtered_out(self, mocker: Any) -> None:
        """Items with pull_request set must be excluded from results."""
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        real_issue = _make_issue(number=1, title="Real issue")
        pr_item = _make_issue(number=2, title="Actually a PR", is_pr=True)
        repo_mock.get_issues.return_value = _make_paginated([[real_issue, pr_item]])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert len(result) == 1
        assert result[0].metadata["issue_number"] == 1

    @pytest.mark.asyncio
    async def test_max_issues_cap_stops_early(self, mocker: Any) -> None:
        """ingestion_max_issues=3 must stop after exactly 3 documents."""
        gh = MagicMock()
        settings = _make_settings(max_issues=3)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        # Two pages of 3 issues each — only the first 3 should be ingested.
        page0 = [_make_issue(number=i) for i in range(1, 4)]
        page1 = [_make_issue(number=i) for i in range(4, 7)]
        repo_mock.get_issues.return_value = _make_paginated([page0, page1])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert len(result) == 3
        assert [d.metadata["issue_number"] for d in result] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_max_issues_zero_means_no_cap(self, mocker: Any) -> None:
        """ingestion_max_issues=0 must return all issues across all pages."""
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        page0 = [_make_issue(number=i) for i in range(1, 4)]
        page1 = [_make_issue(number=i) for i in range(4, 7)]
        repo_mock.get_issues.return_value = _make_paginated([page0, page1])

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert len(result) == 6

    @pytest.mark.asyncio
    async def test_multiple_pages_are_fetched(self, mocker: Any) -> None:
        """get_page must be called for each page until an empty page is returned."""
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        page0 = [_make_issue(number=1)]
        page1 = [_make_issue(number=2)]
        paginated = _make_paginated([page0, page1])
        repo_mock.get_issues.return_value = paginated

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert len(result) == 2
        # get_page(0), get_page(1), get_page(2) [empty — terminates]
        assert paginated.get_page.call_count == 3

    @pytest.mark.asyncio
    async def test_stops_when_page_is_empty(self, mocker: Any) -> None:
        """Ingestion must stop immediately when get_page returns an empty list."""
        gh = MagicMock()
        settings = _make_settings(max_issues=0)
        repo_mock = MagicMock()
        gh.get_repo.return_value = repo_mock

        paginated = _make_paginated([])  # first page is already empty
        repo_mock.get_issues.return_value = paginated

        mocker.patch(
            "ingestion.github_issues.asyncio.to_thread",
            side_effect=_async_call,
        )

        result = await GitHubIssuesIngester(
            github_client=gh, settings=settings
        ).ingest()

        assert result == []
        paginated.get_page.assert_called_once_with(0)
