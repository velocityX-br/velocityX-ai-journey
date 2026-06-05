"""Ingester for GitHub Issues from the ``gardener/gardener`` repository.

Fetches all issues (open and closed) via PyGithub and returns one
``Document`` per issue.  The document content is a structured Markdown
representation combining title, body, and all comments.  All
synchronous PyGithub calls are wrapped with ``asyncio.to_thread``.

Metadata schema per Document:
    repo        (str)         — repository slug
    issue_number (int)        — issue number
    title       (str)         — issue title
    state       (str)         — ``"open"`` or ``"closed"``
    labels      (list[str])   — label names attached to the issue
    created_at  (str | None)  — ISO 8601 creation timestamp
    closed_at   (str | None)  — ISO 8601 close timestamp, or ``None``
    url         (str)         — GitHub HTML URL for the issue
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import github
from github.Issue import Issue
from github.IssueComment import IssueComment
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


class GitHubIssuesIngester(BaseIngester):
    """Ingest all GitHub Issues from ``gardener/gardener``.

    Paginates through all open and closed issues, fetching each issue's
    body, comment thread, labels, and lifecycle timestamps.  All
    synchronous PyGithub calls are wrapped with ``asyncio.to_thread``.

    Attributes:
        github_client: An authenticated ``github.Github`` instance.
        settings: Application settings carrying repository slugs.
    """

    def __init__(self, github_client: github.Github, settings: Settings) -> None:
        """Initialise the ingester.

        Args:
            github_client: Authenticated PyGithub client.
            settings: Application settings.  The ``github_gardener_repo``
                attribute determines which repository is queried.
        """
        self._github = github_client
        self._settings = settings

    async def ingest(self) -> list[Document]:
        """Fetch all issues and return them as ``Document`` objects.

        Paginates through both open and closed issues in a single pass
        by using ``state="all"``.  Comments for each issue are fetched
        concurrently to reduce total wall-clock time.

        Returns:
            List of ``Document`` objects, one per issue.

        Raises:
            IngestionError: If the repository cannot be accessed.
        """
        repo_slug = self._settings.github_gardener_repo
        logger.info("Starting issues ingestion from %s", repo_slug)

        try:
            repo: Repository = await asyncio.to_thread(
                self._github.get_repo, repo_slug
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to access repository {repo_slug!r}: {exc}", source=repo_slug
            ) from exc

        try:
            paginated_issues = await asyncio.to_thread(
                repo.get_issues, state="all"
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to list issues for {repo_slug!r}: {exc}", source=repo_slug
            ) from exc

        documents: list[Document] = []

        # Materialise the paginated list in a thread to avoid blocking.
        issues: list[Issue] = await asyncio.to_thread(list, paginated_issues)

        logger.info("Fetched %d issues from %s; fetching comments…", len(issues), repo_slug)

        for issue in issues:
            doc = await self._build_document(issue, repo_slug)
            documents.append(doc)

        logger.info(
            "Issues ingestion complete: %d documents from %s",
            len(documents),
            repo_slug,
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
