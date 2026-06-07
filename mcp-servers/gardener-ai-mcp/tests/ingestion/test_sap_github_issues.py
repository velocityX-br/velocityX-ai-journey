"""Tests for ingestion/sap_github_issues.py.

Test inventory:
    TestSapGithubIssuesIngesterInit
        test_instantiation

    TestSapGithubIssuesIngesterIngest
        test_returns_documents_from_single_page
        test_document_carries_sap_github_source_origin
        test_document_carries_sap_github_repo_metadata
        test_document_url_is_html_url
        test_prs_are_filtered_out
        test_max_issues_cap_stops_early
        test_max_issues_zero_means_no_cap
        test_multiple_pages_are_fetched
        test_stops_when_page_is_empty
        test_ingest_raises_on_repo_failure
        test_comments_included_in_content
        test_comment_failure_returns_empty_comments

    TestSapGithubIssuesIngesterPageByPage
        test_yields_page_result_per_page
        test_page_result_documents_carry_sap_github_origin
        test_page_result_prs_skipped_count
        test_page_result_cumulative_totals
        test_page_result_capped_flag_set_when_limit_reached
        test_page_result_no_pages_yields_nothing
        test_page_result_raises_on_repo_failure

    TestSapGithubIssuesBatchIngester
        test_batch_ingests_multiple_repos
        test_batch_partial_failure_returns_successful_docs
        test_batch_all_fail_raises_ingestion_error
        test_batch_each_repo_respects_max_per_repo
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ingestion.base import Document, IngestionError
from ingestion.sap_github_issues import (
    PageResult,
    SapGithubIssuesBatchIngester,
    SapGithubIssuesIngester,
)

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_label(name: str) -> MagicMock:
    label = MagicMock()
    label.name = name
    return label


def _make_comment(body: str, author: str = "sap-user") -> MagicMock:
    comment = MagicMock()
    comment.body = body
    comment.user = MagicMock()
    comment.user.login = author
    return comment


def _make_issue(
    number: int = 42,
    title: str = "SAP GitHub issue title",
    body: str = "Issue body from github.tools.sap.",
    state: str = "open",
    labels: list[str] | None = None,
    html_url: str = "https://github.tools.sap/kubernetes-canary/issues-canary/issues/42",
    is_pr: bool = False,
    comments: list[MagicMock] | None = None,
) -> MagicMock:
    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.body = body
    issue.state = state
    issue.labels = [_make_label(n) for n in (labels or [])]
    issue.html_url = html_url
    issue.created_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    issue.closed_at = None
    # pull_request is None for real issues; set to a mock for PRs
    issue.pull_request = MagicMock() if is_pr else None

    _comments = comments or []
    paginated_comments = MagicMock()
    # get_comments() → paginated object; list(paginated) → comment list
    issue.get_comments = MagicMock(return_value=paginated_comments)
    paginated_comments.__iter__ = MagicMock(return_value=iter(_comments))

    return issue


def _make_paginated_pages(*pages: list[Any]) -> MagicMock:
    """Return a mock PaginatedList whose get_page() returns successive pages."""
    paginated = MagicMock()

    def _get_page(idx: int) -> list[Any]:
        return pages[idx] if idx < len(pages) else []

    paginated.get_page = MagicMock(side_effect=_get_page)
    return paginated


def _make_gh_client(repo_slug: str, paginated: MagicMock) -> MagicMock:
    """Return a PyGithub mock whose get_repo() exposes the given paginated list."""
    repo = MagicMock()
    repo.get_issues = MagicMock(return_value=paginated)

    gh = MagicMock()
    gh.get_repo = MagicMock(return_value=repo)
    return gh


# ---------------------------------------------------------------------------
# SapGithubIssuesIngester — init
# ---------------------------------------------------------------------------


class TestSapGithubIssuesIngesterInit:
    def test_instantiation(self) -> None:
        gh = MagicMock()
        ingester = SapGithubIssuesIngester(
            github_client=gh,
            repo_slug="kubernetes-canary/issues-canary",
            max_issues=200,
            batch_size=50,
        )
        assert ingester._repo_slug == "kubernetes-canary/issues-canary"
        assert ingester._max_issues == 200
        assert ingester._batch_size == 50


# ---------------------------------------------------------------------------
# SapGithubIssuesIngester — ingest behaviour
# ---------------------------------------------------------------------------


class TestSapGithubIssuesIngesterIngest:
    @pytest.mark.asyncio
    async def test_returns_documents_from_single_page(self) -> None:
        """A single page of two issues must produce two Documents."""
        issues = [_make_issue(number=1), _make_issue(number=2)]
        paginated = _make_paginated_pages(issues)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(paginated, issues)

            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=0,
                batch_size=100,
            )
            docs = await ingester.ingest()

        assert len(docs) == 2
        assert all(isinstance(d, Document) for d in docs)

    @pytest.mark.asyncio
    async def test_document_carries_sap_github_source_origin(self) -> None:
        """Every Document must have source_origin='sap_github' in metadata."""
        issues = [_make_issue(number=10)]
        paginated = _make_paginated_pages(issues)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(paginated, issues)
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
            )
            docs = await ingester.ingest()

        assert docs[0].metadata["source_origin"] == "sap_github"

    @pytest.mark.asyncio
    async def test_document_carries_sap_github_repo_metadata(self) -> None:
        """Every Document must record sap_github_repo matching the repo slug."""
        issues = [_make_issue(number=10)]
        paginated = _make_paginated_pages(issues)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(paginated, issues)
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-live/issues-live", paginated),
                repo_slug="kubernetes-live/issues-live",
            )
            docs = await ingester.ingest()

        assert docs[0].metadata["sap_github_repo"] == "kubernetes-live/issues-live"

    @pytest.mark.asyncio
    async def test_document_url_is_html_url(self) -> None:
        """Document.source and metadata['url'] must equal issue.html_url."""
        url = "https://github.tools.sap/kubernetes-canary/issues-canary/issues/99"
        issues = [_make_issue(number=99, html_url=url)]
        paginated = _make_paginated_pages(issues)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(paginated, issues)
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
            )
            docs = await ingester.ingest()

        assert docs[0].source == url
        assert docs[0].metadata["url"] == url

    @pytest.mark.asyncio
    async def test_prs_are_filtered_out(self) -> None:
        """Items with a pull_request attribute must be silently skipped."""
        real_issue = _make_issue(number=1, is_pr=False)
        pr_item = _make_issue(number=2, is_pr=True)
        paginated = _make_paginated_pages([real_issue, pr_item])

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(
                paginated, [real_issue]
            )
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=0,
            )
            docs = await ingester.ingest()

        assert len(docs) == 1
        assert docs[0].metadata["issue_number"] == 1

    @pytest.mark.asyncio
    async def test_max_issues_cap_stops_early(self) -> None:
        """max_issues=2 must stop after ingesting 2 issues even if more exist."""
        issues = [_make_issue(number=i) for i in range(1, 6)]  # 5 issues
        paginated = _make_paginated_pages(issues)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(paginated, issues)
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=2,
            )
            docs = await ingester.ingest()

        assert len(docs) == 2

    @pytest.mark.asyncio
    async def test_max_issues_zero_means_no_cap(self) -> None:
        """max_issues=0 must ingest all issues across all pages."""
        page0 = [_make_issue(number=i) for i in range(1, 4)]
        page1 = [_make_issue(number=i) for i in range(4, 7)]
        paginated = _make_paginated_pages(page0, page1)

        all_issues = page0 + page1

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(
                paginated, all_issues
            )
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=0,
            )
            docs = await ingester.ingest()

        assert len(docs) == 6

    @pytest.mark.asyncio
    async def test_multiple_pages_are_fetched(self) -> None:
        """Issues spanning two pages must all be returned."""
        page0 = [_make_issue(number=1), _make_issue(number=2)]
        page1 = [_make_issue(number=3)]
        paginated = _make_paginated_pages(page0, page1)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(
                paginated, page0 + page1
            )
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=0,
            )
            docs = await ingester.ingest()

        assert len(docs) == 3

    @pytest.mark.asyncio
    async def test_stops_when_page_is_empty(self) -> None:
        """An empty page must terminate iteration immediately."""
        paginated = _make_paginated_pages([])  # first page is already empty

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _async_to_thread_side_effect(paginated, [])
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
            )
            docs = await ingester.ingest()

        assert docs == []

    @pytest.mark.asyncio
    async def test_ingest_raises_on_repo_failure(self) -> None:
        """IngestionError must be raised when get_repo() fails."""
        gh = MagicMock()
        gh.get_repo = MagicMock(side_effect=Exception("404 Not Found"))

        with patch(
            "ingestion.sap_github_issues.asyncio.to_thread",
            side_effect=Exception("404 Not Found"),
        ):
            ingester = SapGithubIssuesIngester(
                github_client=gh,
                repo_slug="no-such-org/no-such-repo",
            )
            with pytest.raises(IngestionError, match="no-such-org/no-such-repo"):
                await ingester.ingest()

    @pytest.mark.asyncio
    async def test_comments_included_in_content(self) -> None:
        """Comment bodies must appear in the document content."""
        comment = _make_comment("This is an important SAP comment.", author="i123456")
        issue = _make_issue(number=5)
        paginated = _make_paginated_pages([issue])

        # Track which call we are on so each asyncio.to_thread invocation
        # returns the right object for the ingester's call sequence:
        #   1. get_repo  2. get_issues  3. _get_page(pg,0)
        #   4. get_comments  5. list(paginated_comments)  6. _get_page(pg,1) → []
        call_seq: list[int] = [0]

        async def _side_effect(fn, *args, **kwargs):
            call_seq[0] += 1
            n = call_seq[0]
            if n == 1:  # get_repo
                repo = MagicMock()
                repo.get_issues = MagicMock(return_value=paginated)
                return repo
            if n == 2:  # get_issues
                return paginated
            if n == 3:  # _get_page(paginated, 0) → first page
                return [issue]
            if n == 4:  # issue.get_comments()
                return MagicMock()       # paginated comments object
            if n == 5:  # list(paginated_comments)
                return [comment]         # <-- real comment returned here
            return []                    # _get_page(paginated, 1) → end

        with patch(
            "ingestion.sap_github_issues.asyncio.to_thread",
            side_effect=_side_effect,
        ):
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
            )
            docs = await ingester.ingest()

        assert "This is an important SAP comment." in docs[0].content

    @pytest.mark.asyncio
    async def test_comment_failure_returns_empty_comments(self) -> None:
        """A failure fetching comments must not abort ingestion; empty comments used."""
        issue = _make_issue(number=7)
        paginated = _make_paginated_pages([issue])

        call_count = 0

        async def _side_effect(fn, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # get_repo
                return _make_gh_client("k-c/issues-canary", paginated).get_repo.return_value
            if call_count == 2:
                # get_issues → paginated
                return paginated
            if call_count == 3:
                # _get_page(paginated, 0) → first page
                return [issue]
            if call_count == 4:
                # issue.get_comments → raise
                raise Exception("comment API error")
            # _get_page(paginated, 1) → empty (termination)
            return []

        with patch(
            "ingestion.sap_github_issues.asyncio.to_thread",
            side_effect=_side_effect,
        ):
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("k-c/issues-canary", paginated),
                repo_slug="k-c/issues-canary",
            )
            docs = await ingester.ingest()

        assert len(docs) == 1
        # Content should still contain the issue title even without comments.
        assert "SAP GitHub issue title" in docs[0].content


# ---------------------------------------------------------------------------
# SapGithubIssuesIngester — ingest_page_by_page behaviour
# ---------------------------------------------------------------------------


class TestSapGithubIssuesIngesterPageByPage:
    """Tests for the ingest_page_by_page() async generator.

    Each test drives the generator with a call-sequence mock identical to the
    one used for ingest() tests, verifying that PageResult metadata is correct
    and that the generator terminates cleanly on empty pages or when capped.
    """

    @pytest.mark.asyncio
    async def test_yields_page_result_per_page(self) -> None:
        """Two non-empty pages must produce exactly two PageResult objects."""
        page0 = [_make_issue(number=1), _make_issue(number=2)]
        page1 = [_make_issue(number=3)]
        paginated = _make_paginated_pages(page0, page1)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _make_page_by_page_side_effect(
                paginated, page0 + page1
            )
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=0,
            )
            results: list[PageResult] = []
            async for pr in ingester.ingest_page_by_page():
                results.append(pr)

        assert len(results) == 2
        assert results[0].page_index == 0
        assert results[1].page_index == 1

    @pytest.mark.asyncio
    async def test_page_result_documents_carry_sap_github_origin(self) -> None:
        """Every document in a PageResult must have source_origin='sap_github'."""
        issues = [_make_issue(number=10)]
        paginated = _make_paginated_pages(issues)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _make_page_by_page_side_effect(paginated, issues)
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=0,
            )
            page_results: list[PageResult] = []
            async for pr in ingester.ingest_page_by_page():
                page_results.append(pr)

        assert len(page_results) == 1
        for doc in page_results[0].documents:
            assert doc.metadata["source_origin"] == "sap_github"

    @pytest.mark.asyncio
    async def test_page_result_prs_skipped_count(self) -> None:
        """prs_skipped on a PageResult must count only PR items, not issues."""
        issue = _make_issue(number=1, is_pr=False)
        pr = _make_issue(number=2, is_pr=True)
        paginated = _make_paginated_pages([issue, pr])

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _make_page_by_page_side_effect(
                paginated, [issue]  # only the real issue produces a Document
            )
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=0,
            )
            results: list[PageResult] = []
            async for pr_result in ingester.ingest_page_by_page():
                results.append(pr_result)

        assert results[0].prs_skipped == 1
        assert len(results[0].documents) == 1

    @pytest.mark.asyncio
    async def test_page_result_cumulative_totals(self) -> None:
        """total_docs_so_far must accumulate correctly across pages."""
        page0 = [_make_issue(number=i) for i in range(1, 4)]   # 3 issues
        page1 = [_make_issue(number=i) for i in range(4, 7)]   # 3 issues
        paginated = _make_paginated_pages(page0, page1)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _make_page_by_page_side_effect(
                paginated, page0 + page1
            )
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=0,
            )
            results: list[PageResult] = []
            async for pr in ingester.ingest_page_by_page():
                results.append(pr)

        assert results[0].total_docs_so_far == 3
        assert results[1].total_docs_so_far == 6

    @pytest.mark.asyncio
    async def test_page_result_capped_flag_set_when_limit_reached(self) -> None:
        """The PageResult where max_issues is reached must have capped=True."""
        issues = [_make_issue(number=i) for i in range(1, 6)]  # 5 issues on one page
        paginated = _make_paginated_pages(issues)

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _make_page_by_page_side_effect(paginated, issues)
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
                max_issues=3,
            )
            results: list[PageResult] = []
            async for pr in ingester.ingest_page_by_page():
                results.append(pr)

        # The generator must stop after the capped page — only one PageResult.
        assert len(results) == 1
        assert results[0].capped is True
        assert len(results[0].documents) == 3

    @pytest.mark.asyncio
    async def test_page_result_no_pages_yields_nothing(self) -> None:
        """An empty first page must cause the generator to yield nothing."""
        paginated = _make_paginated_pages([])

        with patch("ingestion.sap_github_issues.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = _make_page_by_page_side_effect(paginated, [])
            ingester = SapGithubIssuesIngester(
                github_client=_make_gh_client("kubernetes-canary/issues-canary", paginated),
                repo_slug="kubernetes-canary/issues-canary",
            )
            results: list[PageResult] = []
            async for pr in ingester.ingest_page_by_page():
                results.append(pr)

        assert results == []

    @pytest.mark.asyncio
    async def test_page_result_raises_on_repo_failure(self) -> None:
        """IngestionError raised by get_repo must propagate out of the generator."""
        gh = MagicMock()

        with patch(
            "ingestion.sap_github_issues.asyncio.to_thread",
            side_effect=Exception("403 Forbidden"),
        ):
            ingester = SapGithubIssuesIngester(
                github_client=gh,
                repo_slug="forbidden-org/forbidden-repo",
            )
            with pytest.raises(IngestionError, match="forbidden-org/forbidden-repo"):
                async for _ in ingester.ingest_page_by_page():
                    pass


# ---------------------------------------------------------------------------
# SapGithubIssuesBatchIngester
# ---------------------------------------------------------------------------


class TestSapGithubIssuesBatchIngester:
    @pytest.mark.asyncio
    async def test_batch_ingests_multiple_repos(self) -> None:
        """Documents from all repos must be combined into a single flat list."""
        canary_doc = _make_document("kubernetes-canary/issues-canary", 1)
        live_doc = _make_document("kubernetes-live/issues-live", 2)

        with patch(
            "ingestion.sap_github_issues.SapGithubIssuesIngester.ingest",
            side_effect=[[canary_doc], [live_doc]],
        ):
            ingester = SapGithubIssuesBatchIngester(
                github_client=MagicMock(),
                repos=["kubernetes-canary/issues-canary", "kubernetes-live/issues-live"],
            )
            docs = await ingester.ingest()

        assert len(docs) == 2
        repos_seen = {d.metadata["sap_github_repo"] for d in docs}
        assert "kubernetes-canary/issues-canary" in repos_seen
        assert "kubernetes-live/issues-live" in repos_seen

    @pytest.mark.asyncio
    async def test_batch_partial_failure_returns_successful_docs(self) -> None:
        """If one repo fails but another succeeds, successful docs are returned."""
        live_doc = _make_document("kubernetes-live/issues-live", 99)

        call_count = 0

        async def _ingest_side_effect(self_inner):  # noqa: N803
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise IngestionError("repo not found", source="bad-org/bad-repo")
            return [live_doc]

        with patch.object(
            SapGithubIssuesIngester, "ingest", _ingest_side_effect
        ):
            ingester = SapGithubIssuesBatchIngester(
                github_client=MagicMock(),
                repos=["bad-org/bad-repo", "kubernetes-live/issues-live"],
            )
            docs = await ingester.ingest()

        assert len(docs) == 1
        assert docs[0].metadata["sap_github_repo"] == "kubernetes-live/issues-live"

    @pytest.mark.asyncio
    async def test_batch_all_fail_raises_ingestion_error(self) -> None:
        """If every repo fails, IngestionError must be raised."""

        async def _always_fail(self_inner):  # noqa: N803
            raise IngestionError("repo not found", source="bad/repo")

        with patch.object(SapGithubIssuesIngester, "ingest", _always_fail):
            ingester = SapGithubIssuesBatchIngester(
                github_client=MagicMock(),
                repos=["bad/repo-1", "bad/repo-2"],
            )
            with pytest.raises(IngestionError):
                await ingester.ingest()

    @pytest.mark.asyncio
    async def test_batch_each_repo_respects_max_per_repo(self) -> None:
        """max_issues_per_repo must be forwarded to each SapGithubIssuesIngester."""
        constructed_ingesters: list[SapGithubIssuesIngester] = []
        original_init = SapGithubIssuesIngester.__init__

        def _capture_init(self_inner, **kwargs):  # noqa: N803
            original_init(self_inner, **kwargs)
            constructed_ingesters.append(self_inner)

        async def _empty_ingest(self_inner):  # noqa: N803
            return []

        with patch.object(SapGithubIssuesIngester, "__init__", _capture_init), \
             patch.object(SapGithubIssuesIngester, "ingest", _empty_ingest):
            ingester = SapGithubIssuesBatchIngester(
                github_client=MagicMock(),
                repos=["kubernetes-canary/issues-canary", "kubernetes-live/issues-live"],
                max_issues_per_repo=123,
            )
            await ingester.ingest()

        assert len(constructed_ingesters) == 2
        for single_ingester in constructed_ingesters:
            assert single_ingester._max_issues == 123


# ---------------------------------------------------------------------------
# Internal test helpers
# ---------------------------------------------------------------------------


def _make_document(repo_slug: str, issue_number: int) -> Document:
    """Create a minimal Document with SAP GitHub metadata for use in batch tests."""
    url = f"https://github.tools.sap/{repo_slug}/issues/{issue_number}"
    return Document(
        content=f"# Issue {issue_number}\n\nContent.",
        metadata={
            "source_origin": "sap_github",
            "sap_github_repo": repo_slug,
            "issue_number": issue_number,
            "title": f"Issue {issue_number}",
            "state": "open",
            "labels": [],
            "url": url,
        },
        source=url,
    )


def _async_to_thread_side_effect(
    paginated: MagicMock,
    all_issues: list[MagicMock],
    comments_map: dict[int, list[MagicMock]] | None = None,
):
    """Build an asyncio.to_thread side-effect that simulates the GitHub API calls.

    Call sequence expected by SapGithubIssuesIngester.ingest():
      1. get_repo(slug)               → repo mock
      2. repo.get_issues(...)         → paginated mock
      3. _get_page(paginated, 0)      → first page
      4. issue.get_comments()         → paginated comments (per issue)
      5. list(paginated_comments)     → comment list (per issue)
      6. _get_page(paginated, 1)      → [] (end-of-pages signal)
    """
    _comments_map = comments_map or {}
    _page_call_count = 0
    _comment_phase = False
    _issues_queue = list(all_issues)  # consumed as get_comments is called

    async def _side_effect(fn, *args, **kwargs):
        nonlocal _page_call_count, _comment_phase

        fn_name = getattr(fn, "__name__", "") or getattr(fn, "__func__", fn).__name__ if hasattr(fn, "__func__") else str(fn)

        # get_repo
        if hasattr(fn, "__self__") and hasattr(fn.__self__, "get_issues"):
            return fn.__self__
        if callable(fn) and "get_repo" in str(fn):
            repo = MagicMock()
            repo.get_issues = MagicMock(return_value=paginated)
            return repo
        # get_issues
        if callable(fn) and "get_issues" in str(fn):
            return paginated
        # _get_page
        if fn.__name__ == "_get_page" if hasattr(fn, "__name__") else False:
            page = paginated.get_page(args[1] if len(args) > 1 else kwargs.get("page_index", 0))
            return page
        # get_comments
        if "get_comments" in str(fn):
            return MagicMock()
        # list(paginated_comments)
        if fn is list:
            return []
        # fallback: call fn directly (synchronous)
        return fn(*args, **kwargs)

    return _side_effect


def _make_page_by_page_side_effect(
    paginated: MagicMock,
    all_issues: list[MagicMock],
):
    """Build an asyncio.to_thread side-effect for ingest_page_by_page() tests.

    The call sequence driven by ``SapGithubIssuesIngester.ingest_page_by_page``
    is identical to ``ingest()`` so we reuse the same pattern:

      1. get_repo(slug)          → repo mock
      2. repo.get_issues(...)    → paginated mock
      3+. _get_page(pag, N)     → page N items (one call per page, then [])
      Interleaved per issue:
        4. issue.get_comments()  → paginated comments object
        5. list(paginated_cmts)  → []  (no comments in page_by_page tests)

    We use a call-counter state machine so that each ``asyncio.to_thread``
    invocation returns the right object without relying on fragile string
    inspection of function names.
    """
    # Pre-build the repo mock once so every call to get_repo returns the same object.
    repo_mock = MagicMock()
    repo_mock.get_issues = MagicMock(return_value=paginated)

    # Track global call index and per-page state.
    state: dict = {
        "call": 0,           # absolute call counter
        "page_idx": 0,       # which page _get_page should return next
        "in_comments": False,  # True between get_comments and list() calls
    }

    async def _side_effect(fn, *args, **kwargs):
        state["call"] += 1
        n = state["call"]

        if n == 1:
            # get_repo
            return repo_mock
        if n == 2:
            # get_issues
            return paginated

        # From call 3 onwards the ingester alternates between:
        #   _get_page calls and (get_comments + list) pairs per issue.
        # We detect which by inspecting fn identity / name safely.
        fn_name = getattr(fn, "__name__", None)

        if fn_name == "_get_page":
            page = paginated.get_page(state["page_idx"])
            state["page_idx"] += 1
            return page

        if fn_name == "get_comments" or (
            hasattr(fn, "__self__") and hasattr(fn.__self__, "pull_request")
        ):
            # issue.get_comments() call
            return MagicMock()

        if fn is list:
            # list(paginated_comments) — return empty comment list
            return []

        # Fallback for any remaining synchronous call
        return fn(*args, **kwargs)

    return _side_effect
