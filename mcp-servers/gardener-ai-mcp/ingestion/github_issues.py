"""Ingester for GitHub Issues from the ``gardener/gardener`` repository.

Fetches issues (open and closed) via PyGithub and returns one ``Document``
per issue.  The document content is a structured Markdown representation
combining title, body, and all comments.  All synchronous PyGithub calls
are wrapped with ``asyncio.to_thread``.

Issues are fetched in pages (``ingestion_issues_batch_size`` per page) and
processed immediately, keeping memory usage bounded.  The total number of
issues is capped by ``ingestion_max_issues`` (default 1 000; set to 0 for
no limit).

Metadata schema per Document:
    repo         (str)         — repository slug
    issue_number (int)         — issue number
    title        (str)         — issue title
    state        (str)         — ``"open"`` or ``"closed"``
    labels       (list[str])   — label names attached to the issue
    created_at   (str | None)  — ISO 8601 creation timestamp
    closed_at    (str | None)  — ISO 8601 close timestamp, or ``None``
    url          (str)         — GitHub HTML URL for the issue
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import github
from github.Issue import Issue
from github.IssueComment import IssueComment
from github.PaginatedList import PaginatedList
from github.Repository import Repository

from config.settings import Settings
from ingestion.base import BaseIngester, Document, IngestionError

logger = logging.getLogger(__name__)


def _format_issue_content(issue: Issue, comments: list[IssueComment]) -> str:
    """Build a Markdown-formatted string from an issue and its comments.

    Args:
        issue: The PyGithub ``Issue`` object.
        comments: List of ``IssueComment`` objects for this issue.

    Returns:
        A single string suitable for embedding.
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

    PyGithub pages are 0-indexed.  This thin wrapper exists so that
    ``asyncio.to_thread`` can call it without a lambda.

    Args:
        paginated: A ``PaginatedList`` returned by any PyGithub list method.
        page_index: Zero-based page number to fetch.

    Returns:
        The list of items on that page (may be empty when past the last page).
    """
    return paginated.get_page(page_index)


class GitHubIssuesIngester(BaseIngester):
    """Ingest GitHub Issues from ``gardener/gardener`` in bounded batches.

    Issues are fetched page-by-page (``ingestion_issues_batch_size`` per
    page) so that only one page of raw GitHub objects lives in memory at a
    time.  Each page is processed (comments fetched, ``Document`` built)
    before the next page is requested.

    The total number of ingested issues is capped at
    ``settings.ingestion_max_issues``; set that value to ``0`` to remove
    the cap entirely.

    Attributes:
        github_client: An authenticated ``github.Github`` instance.
        settings: Application settings carrying repository slugs and limits.
    """

    def __init__(self, github_client: github.Github, settings: Settings) -> None:
        """Initialise the ingester.

        Args:
            github_client: Authenticated PyGithub client.
            settings: Application settings.  ``github_gardener_repo``,
                ``ingestion_max_issues``, and ``ingestion_issues_batch_size``
                are all read from here.
        """
        self._github = github_client
        self._settings = settings

    async def ingest(self) -> list[Document]:
        """Fetch issues page-by-page and return them as ``Document`` objects.

        Sorts by ``updated`` descending so the most-recently active issues
        are ingested first when a cap is in effect.  GitHub's issues endpoint
        returns both issues and PRs; pull requests are filtered out by
        checking ``issue.pull_request is None``.

        Returns:
            List of ``Document`` objects, one per issue, up to
            ``settings.ingestion_max_issues`` (or unlimited when that value
            is ``0``).

        Raises:
            IngestionError: If the repository cannot be accessed or the
                initial paginated list cannot be retrieved.
        """
        repo_slug = self._settings.github_gardener_repo
        max_issues = self._settings.ingestion_max_issues
        batch_size = self._settings.ingestion_issues_batch_size

        logger.info(
            "Starting issues ingestion from %s (max=%s, batch_size=%d)",
            repo_slug,
            max_issues if max_issues > 0 else "unlimited",
            batch_size,
        )

        try:
            repo: Repository = await asyncio.to_thread(
                self._github.get_repo, repo_slug
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to access repository {repo_slug!r}: {exc}", source=repo_slug
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
                f"Failed to list issues for {repo_slug!r}: {exc}", source=repo_slug
            ) from exc

        documents: list[Document] = []
        page_index = 0
        total_seen = 0   # items returned by the API (issues + PRs combined)
        total_prs_skipped = 0

        while True:
            page_items: list[Issue] = await asyncio.to_thread(
                _get_page, paginated_issues, page_index
            )

            if not page_items:
                # Past the last page — ingestion complete.
                break

            total_seen += len(page_items)
            issues_on_page = [i for i in page_items if i.pull_request is None]
            total_prs_skipped += len(page_items) - len(issues_on_page)

            logger.info(
                "Page %d: %d items (%d issues, %d PRs skipped) — "
                "%d documents so far",
                page_index,
                len(page_items),
                len(issues_on_page),
                len(page_items) - len(issues_on_page),
                len(documents),
            )

            for issue in issues_on_page:
                doc = await self._build_document(issue, repo_slug)
                documents.append(doc)

                if max_issues > 0 and len(documents) >= max_issues:
                    logger.info(
                        "Reached ingestion_max_issues=%d — stopping early.  "
                        "Set INGESTION_MAX_ISSUES=0 to remove the cap.",
                        max_issues,
                    )
                    logger.info(
                        "Issues ingestion complete: %d documents from %s "
                        "(%d PRs skipped, %d API items seen)",
                        len(documents),
                        repo_slug,
                        total_prs_skipped,
                        total_seen,
                    )
                    return documents

            page_index += 1

        logger.info(
            "Issues ingestion complete: %d documents from %s "
            "(%d PRs skipped, %d API items seen across %d pages)",
            len(documents),
            repo_slug,
            total_prs_skipped,
            total_seen,
            page_index,
        )
        return documents

    async def _build_document(self, issue: Issue, repo_slug: str) -> Document:
        """Build a single ``Document`` from an issue and its comment thread.

        Args:
            issue: The PyGithub ``Issue`` object.
            repo_slug: Repository slug used in metadata.

        Returns:
            A fully populated ``Document``.
        """
        try:
            paginated_comments = await asyncio.to_thread(issue.get_comments)
            comments: list[IssueComment] = await asyncio.to_thread(
                list, paginated_comments
            )
        except Exception as exc:
            logger.warning(
                "Failed to fetch comments for issue #%d: %s", issue.number, exc
            )
            comments = []

        content = _format_issue_content(issue, comments)

        labels: list[str] = [label.name for label in issue.labels]

        metadata: dict[str, Any] = {
            "repo": repo_slug,
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
