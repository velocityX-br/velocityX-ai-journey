"""Ingester for GitHub Issues from repositories on github.tools.sap.

Fetches issues (open and closed) from one or more SAP GitHub Enterprise
repositories via PyGithub and returns one ``Document`` per issue.  The
document content is identical in structure to ``GitHubIssuesIngester`` but
carries ``source_origin="sap_github"`` in every document's metadata so that
search results can always be distinguished from public github.com/gardener/*
content.

Supports ingesting multiple repositories in a single run via the
``SapGithubIssuesBatchIngester`` wrapper, which calls
``SapGithubIssuesIngester`` per repository and concatenates results.

Typical usage::

    gh = github.Github(
        login_or_token=settings.github_token,
        base_url="https://github.tools.sap/api/v3",
    )
    ingester = SapGithubIssuesBatchIngester(
        github_client=gh,
        repos=["kubernetes-canary/issues-canary", "kubernetes-live/issues-live"],
        max_issues_per_repo=500,
        batch_size=100,
    )
    documents = await ingester.ingest()

Metadata schema per Document (mirrors GitHubIssuesIngester + SAP fields):
    source_origin  (str)         — always ``"sap_github"``
    sap_github_repo (str)        — repository slug on github.tools.sap
    issue_number   (int)         — issue number
    title          (str)         — issue title
    state          (str)         — ``"open"`` or ``"closed"``
    labels         (list[str])   — label names attached to the issue
    created_at     (str | None)  — ISO 8601 creation timestamp
    closed_at      (str | None)  — ISO 8601 close timestamp, or ``None``
    url            (str)         — HTML URL of the issue on github.tools.sap
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import github
from github.Issue import Issue
from github.IssueComment import IssueComment
from github.PaginatedList import PaginatedList
from github.Repository import Repository

from ingestion.base import BaseIngester, Document, IngestionError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Page result container
# ---------------------------------------------------------------------------


@dataclass
class PageResult:
    """Metadata for a single page yielded by ``SapGithubIssuesIngester.ingest_page_by_page``.

    Attributes:
        page_index: Zero-based page number (0, 1, 2, …).
        documents: ``Document`` objects produced from this page.
        api_items_on_page: Total items returned by the API on this page
            (issues + PRs combined).
        prs_skipped: Number of pull-request items filtered out on this page.
        total_docs_so_far: Cumulative documents produced across all pages
            including this one.
        total_api_items_so_far: Cumulative API items seen across all pages
            including this one.
        capped: ``True`` when the ``max_issues`` cap was hit on this page and
            the generator has stopped early.
    """

    page_index: int
    documents: list[Document]
    api_items_on_page: int
    prs_skipped: int
    total_docs_so_far: int
    total_api_items_so_far: int
    capped: bool = field(default=False)


# ---------------------------------------------------------------------------
# Formatting helpers (same structure as GitHubIssuesIngester)
# ---------------------------------------------------------------------------


def _format_sap_github_issue_content(
    issue: Issue, comments: list[IssueComment]
) -> str:
    """Build a Markdown-formatted string from a SAP GitHub issue and its comments.

    Args:
        issue: The PyGithub ``Issue`` object from github.tools.sap.
        comments: List of ``IssueComment`` objects for this issue.

    Returns:
        A single Markdown string suitable for embedding.
    """
    parts: list[str] = [
        f"# {issue.title}",
        "",
        issue.body or "_No description provided._",
    ]

    if comments:
        parts.append("")
        parts.append("## Comments")
        for comment in comments:
            author = comment.user.login if comment.user else "unknown"
            parts.append(f"\n**{author}:**")
            parts.append(comment.body or "")

    return "\n".join(parts)


def _get_page(paginated: PaginatedList, page_index: int) -> list[Any]:
    """Return a single page from a PyGithub ``PaginatedList``.

    Args:
        paginated: A ``PaginatedList`` returned by any PyGithub list method.
        page_index: Zero-based page number to fetch.

    Returns:
        The list of items on that page (empty list when past the last page).
    """
    return paginated.get_page(page_index)


# ---------------------------------------------------------------------------
# Single-repo ingester
# ---------------------------------------------------------------------------


class SapGithubIssuesIngester(BaseIngester):
    """Ingest GitHub Issues from a single repository on github.tools.sap.

    Issues are fetched page-by-page (``batch_size`` per page) so that only
    one page of raw GitHub objects lives in memory at a time.  Each page is
    processed (comments fetched, ``Document`` built) before the next page is
    requested.

    Pull requests are filtered out — the PyGithub ``issues`` endpoint returns
    both issue types; only items where ``issue.pull_request is None`` are kept.

    All ``Document`` objects produced carry ``source_origin="sap_github"`` in
    their metadata so that search results can always be distinguished from
    public github.com/gardener/* content stored in the same Qdrant collection.

    Attributes:
        github_client: An authenticated ``github.Github`` instance pointed at
            ``https://github.tools.sap/api/v3``.
        repo_slug: Repository slug on github.tools.sap e.g.
            ``"kubernetes-canary/issues-canary"``.
        max_issues: Maximum number of issues to ingest (``0`` = unlimited).
        batch_size: Number of issues per GitHub API page.
    """

    def __init__(
        self,
        github_client: github.Github,
        repo_slug: str,
        max_issues: int = 500,
        batch_size: int = 100,
    ) -> None:
        """Initialise the ingester for a single SAP GitHub repository.

        Args:
            github_client: Authenticated PyGithub client targeting
                ``https://github.tools.sap/api/v3``.
            repo_slug: Repository slug e.g. ``"kubernetes-canary/issues-canary"``.
            max_issues: Hard cap on the number of issues to ingest.  Issues are
                fetched sorted by ``updated`` descending so the most-recently
                active issues are ingested first.  ``0`` removes the cap (not
                recommended for large repos — will exhaust API rate limits).
            batch_size: Number of issues fetched per GitHub API page.
        """
        self._github = github_client
        self._repo_slug = repo_slug
        self._max_issues = max_issues
        self._batch_size = batch_size

    async def ingest(self) -> list[Document]:
        """Fetch issues page-by-page and return them as ``Document`` objects.

        Sorts by ``updated`` descending so the most-recently active issues are
        ingested first when a cap is in effect.  Pull requests returned by the
        GitHub issues endpoint are filtered out.

        Returns:
            List of ``Document`` objects, one per issue, up to ``max_issues``
            (or unlimited when ``max_issues=0``).

        Raises:
            IngestionError: If the repository cannot be accessed or the
                initial paginated list cannot be retrieved.
        """
        logger.info(
            "Starting SAP GitHub issues ingestion from %s "
            "(max=%s, batch_size=%d)",
            self._repo_slug,
            self._max_issues if self._max_issues > 0 else "unlimited",
            self._batch_size,
        )

        try:
            repo: Repository = await asyncio.to_thread(
                self._github.get_repo, self._repo_slug
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to access SAP GitHub repository {self._repo_slug!r}: {exc}",
                source=self._repo_slug,
            ) from exc

        try:
            paginated_issues: PaginatedList = await asyncio.to_thread(
                repo.get_issues,
                state="all",
                sort="updated",
                direction="desc",
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to list issues for SAP GitHub repo {self._repo_slug!r}: {exc}",
                source=self._repo_slug,
            ) from exc

        documents: list[Document] = []
        page_index = 0
        total_seen = 0
        total_prs_skipped = 0

        while True:
            page_items: list[Issue] = await asyncio.to_thread(
                _get_page, paginated_issues, page_index
            )

            if not page_items:
                break

            total_seen += len(page_items)
            issues_on_page = [i for i in page_items if i.pull_request is None]
            total_prs_skipped += len(page_items) - len(issues_on_page)

            logger.info(
                "[%s] Page %d: %d items (%d issues, %d PRs skipped) — "
                "%d documents so far",
                self._repo_slug,
                page_index,
                len(page_items),
                len(issues_on_page),
                len(page_items) - len(issues_on_page),
                len(documents),
            )

            for issue in issues_on_page:
                doc = await self._build_document(issue)
                documents.append(doc)

                if self._max_issues > 0 and len(documents) >= self._max_issues:
                    logger.info(
                        "[%s] Reached max_issues=%d — stopping early.",
                        self._repo_slug,
                        self._max_issues,
                    )
                    _log_completion(
                        self._repo_slug, len(documents), total_prs_skipped, total_seen
                    )
                    return documents

            page_index += 1

        _log_completion(
            self._repo_slug, len(documents), total_prs_skipped, total_seen, page_index
        )
        return documents

    async def ingest_page_by_page(
        self,
    ) -> AsyncGenerator[PageResult, None]:
        """Yield one page of ``Document`` objects at a time.

        This is the memory-efficient alternative to :meth:`ingest`.  Instead
        of accumulating all documents in memory, the caller receives one
        :class:`PageResult` per GitHub API page and can immediately chunk,
        embed, and upsert that page before the next one is fetched.

        Sorting, PR filtering, and the ``max_issues`` cap behave identically
        to :meth:`ingest`.

        Yields:
            :class:`PageResult` for each non-empty page, in ascending page
            order (page 0, 1, 2, …).  The ``capped`` field is ``True`` on the
            last ``PageResult`` when the ``max_issues`` limit caused early
            termination.

        Raises:
            :exc:`IngestionError`: If the repository cannot be accessed or the
                initial paginated list cannot be retrieved (raised before the
                first yield).
        """
        logger.info(
            "Starting SAP GitHub page-by-page ingestion from %s "
            "(max=%s, batch_size=%d)",
            self._repo_slug,
            self._max_issues if self._max_issues > 0 else "unlimited",
            self._batch_size,
        )

        try:
            repo: Repository = await asyncio.to_thread(
                self._github.get_repo, self._repo_slug
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to access SAP GitHub repository {self._repo_slug!r}: {exc}",
                source=self._repo_slug,
            ) from exc

        try:
            paginated_issues: PaginatedList = await asyncio.to_thread(
                repo.get_issues,
                state="all",
                sort="updated",
                direction="desc",
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to list issues for SAP GitHub repo {self._repo_slug!r}: {exc}",
                source=self._repo_slug,
            ) from exc

        page_index = 0
        total_docs = 0
        total_api_items = 0
        total_prs_skipped = 0

        while True:
            page_items: list[Issue] = await asyncio.to_thread(
                _get_page, paginated_issues, page_index
            )

            if not page_items:
                break

            prs_on_page = sum(1 for i in page_items if i.pull_request is not None)
            issues_on_page = [i for i in page_items if i.pull_request is None]
            total_api_items += len(page_items)
            total_prs_skipped += prs_on_page

            logger.info(
                "[%s] Page %d: %d items (%d issues, %d PRs skipped) — "
                "%d documents so far",
                self._repo_slug,
                page_index,
                len(page_items),
                len(issues_on_page),
                prs_on_page,
                total_docs,
            )

            page_docs: list[Document] = []
            capped = False

            for issue in issues_on_page:
                doc = await self._build_document(issue)
                page_docs.append(doc)
                total_docs += 1

                if self._max_issues > 0 and total_docs >= self._max_issues:
                    logger.info(
                        "[%s] Reached max_issues=%d — stopping early.",
                        self._repo_slug,
                        self._max_issues,
                    )
                    capped = True
                    break

            yield PageResult(
                page_index=page_index,
                documents=page_docs,
                api_items_on_page=len(page_items),
                prs_skipped=prs_on_page,
                total_docs_so_far=total_docs,
                total_api_items_so_far=total_api_items,
                capped=capped,
            )

            if capped:
                _log_completion(
                    self._repo_slug, total_docs, total_prs_skipped, total_api_items
                )
                return

            page_index += 1

        _log_completion(
            self._repo_slug, total_docs, total_prs_skipped, total_api_items, page_index
        )

    async def _build_document(self, issue: Issue) -> Document:
        """Build a single ``Document`` from a SAP GitHub issue.

        Fetches the issue's comment thread and combines everything into a
        single Markdown string.  Metadata always includes
        ``source_origin="sap_github"`` to distinguish from public
        github.com/gardener/* content.

        Args:
            issue: The PyGithub ``Issue`` object from github.tools.sap.

        Returns:
            A fully populated ``Document`` with SAP GitHub provenance metadata.
        """
        try:
            paginated_comments = await asyncio.to_thread(issue.get_comments)
            comments: list[IssueComment] = await asyncio.to_thread(
                list, paginated_comments
            )
        except Exception as exc:
            logger.warning(
                "[%s] Failed to fetch comments for issue #%d: %s",
                self._repo_slug,
                issue.number,
                exc,
            )
            comments = []

        content = _format_sap_github_issue_content(issue, comments)
        labels: list[str] = [label.name for label in issue.labels]

        metadata: dict[str, Any] = {
            # SAP GitHub origin marker — used by search tools to distinguish
            # this content from public github.com/gardener/* content.
            "source_origin": "sap_github",
            "sap_github_repo": self._repo_slug,
            # Fields parallel to GitHubIssuesIngester for query consistency.
            "issue_number": issue.number,
            "title": issue.title,
            "state": issue.state,
            "labels": labels,
            "created_at": issue.created_at.isoformat() if issue.created_at else None,
            "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
            "url": issue.html_url,
        }

        return Document(
            content=content,
            metadata=metadata,
            source=issue.html_url,
        )


# ---------------------------------------------------------------------------
# Multi-repo batch ingester
# ---------------------------------------------------------------------------


class SapGithubIssuesBatchIngester(BaseIngester):
    """Ingest issues from multiple repositories on github.tools.sap.

    Wraps ``SapGithubIssuesIngester`` and runs it sequentially for each
    repository in ``repos``.  All documents from all repositories are
    returned in a single flat list, ordered by repository then by issue
    (most-recently updated first within each repository).

    Attributes:
        github_client: Authenticated PyGithub client targeting
            ``https://github.tools.sap/api/v3``.
        repos: List of repository slugs to ingest e.g.
            ``["kubernetes-canary/issues-canary", "kubernetes-live/issues-live"]``.
        max_issues_per_repo: Hard cap applied independently to each repository.
            ``0`` removes the cap for all repos.
        batch_size: GitHub API page size passed to each per-repo ingester.
    """

    def __init__(
        self,
        github_client: github.Github,
        repos: list[str],
        max_issues_per_repo: int = 500,
        batch_size: int = 100,
    ) -> None:
        """Initialise the batch ingester.

        Args:
            github_client: Authenticated PyGithub client targeting
                ``https://github.tools.sap/api/v3``.
            repos: Repository slugs on github.tools.sap to ingest.
            max_issues_per_repo: Cap per repository.  ``0`` = unlimited.
            batch_size: GitHub API page size per repository.
        """
        self._github = github_client
        self._repos = repos
        self._max_issues_per_repo = max_issues_per_repo
        self._batch_size = batch_size

    async def ingest(self) -> list[Document]:
        """Ingest all repositories sequentially and return a flat document list.

        Returns:
            All ``Document`` objects from all configured repositories, with
            ``source_origin="sap_github"`` in every document's metadata.

        Raises:
            IngestionError: If any single repository fails to be accessed.
                Remaining repositories are still attempted.
        """
        all_documents: list[Document] = []
        errors: list[str] = []

        for repo_slug in self._repos:
            ingester = SapGithubIssuesIngester(
                github_client=self._github,
                repo_slug=repo_slug,
                max_issues=self._max_issues_per_repo,
                batch_size=self._batch_size,
            )
            try:
                docs = await ingester.ingest()
                all_documents.extend(docs)
                logger.info(
                    "SapGithubIssuesBatchIngester: %s → %d documents",
                    repo_slug,
                    len(docs),
                )
            except IngestionError as exc:
                logger.error(
                    "SapGithubIssuesBatchIngester: failed to ingest %s — %s",
                    repo_slug,
                    exc,
                )
                errors.append(str(exc))

        if errors and not all_documents:
            raise IngestionError(
                f"All {len(errors)} SAP GitHub repo(s) failed to ingest: "
                + "; ".join(errors),
                source=", ".join(self._repos),
            )

        logger.info(
            "SapGithubIssuesBatchIngester: total %d documents from %d repo(s)"
            " (%d error(s))",
            len(all_documents),
            len(self._repos),
            len(errors),
        )
        return all_documents


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log_completion(
    repo_slug: str,
    n_docs: int,
    n_prs_skipped: int,
    n_api_items: int,
    n_pages: int | None = None,
) -> None:
    """Log a completion message for a single-repo ingestion run."""
    if n_pages is not None:
        logger.info(
            "[%s] Ingestion complete: %d documents (%d PRs skipped, "
            "%d API items across %d pages)",
            repo_slug,
            n_docs,
            n_prs_skipped,
            n_api_items,
            n_pages,
        )
    else:
        logger.info(
            "[%s] Ingestion complete: %d documents (%d PRs skipped, "
            "%d API items seen)",
            repo_slug,
            n_docs,
            n_prs_skipped,
            n_api_items,
        )
