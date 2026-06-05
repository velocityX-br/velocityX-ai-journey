"""Ingester for GitHub Pull Requests from the ``gardener/gardener`` repository.

Fetches all PRs (open, closed, and merged) via PyGithub and returns one
``Document`` per PR.  The document content is a structured Markdown
representation of the PR title, body, and review comments (not raw diffs).
Linked issue numbers are extracted from the PR body via regex.

All synchronous PyGithub calls are wrapped with ``asyncio.to_thread``.

Metadata schema per Document:
    repo          (str)        — repository slug
    pr_number     (int)        — PR number
    title         (str)        — PR title
    state         (str)        — ``"open"`` or ``"closed"``
    merged        (bool)       — True if the PR was merged
    merged_at     (str | None) — ISO 8601 merge timestamp, or ``None``
    labels        (list[str])  — label names attached to the PR
    linked_issues (list[int])  — issue numbers referenced in the PR body
    url           (str)        — GitHub HTML URL for the PR
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import github
from github.PullRequest import PullRequest
from github.PullRequestComment import PullRequestComment
from github.Repository import Repository

from config.settings import Settings
from ingestion.base import BaseIngester, Document, IngestionError

logger = logging.getLogger(__name__)

# Regex to extract issue references such as:
#   - #123
#   - fixes #123
#   - closes #456
#   - resolves #789
_ISSUE_REF_RE = re.compile(
    r"(?:(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+)?#(\d+)",
    re.IGNORECASE,
)


def _extract_linked_issues(body: str | None) -> list[int]:
    """Parse a PR body and extract referenced issue numbers.

    Matches patterns such as ``#123``, ``fixes #123``, ``closes #456``.

    Args:
        body: The raw PR body text.  May be ``None``.

    Returns:
        Deduplicated, sorted list of referenced issue numbers.
    """
    if not body:
        return []
    matches = _ISSUE_REF_RE.findall(body)
    return sorted({int(m) for m in matches})


def _format_pr_content(
    pr: PullRequest,
    review_comments: list[PullRequestComment],
) -> str:
    """Build a Markdown-formatted string from a PR and its review comments.

    Raw diff hunks are intentionally excluded.  Only the review comment
    bodies (discussion text) are included.

    Args:
        pr: The PyGithub ``PullRequest`` object.
        review_comments: List of review comment objects.

    Returns:
        A single string suitable for embedding.
    """
    parts: list[str] = [
        f"# PR #{pr.number}: {pr.title}",
        "",
        pr.body or "_No description provided._",
    ]

    if review_comments:
        parts.append("")
        parts.append("## Review Comments")
        for comment in review_comments:
            author = comment.user.login if comment.user else "unknown"
            parts.append(f"\n**{author}** (on `{comment.path}`):")
            parts.append(comment.body or "")

    return "\n".join(parts)


class GitHubPRsIngester(BaseIngester):
    """Ingest all GitHub Pull Requests from ``gardener/gardener``.

    Paginates through all PRs (state="all"), fetching each PR's body,
    review comments, labels, and merge metadata.  Linked issue numbers
    are extracted from the PR body via ``_extract_linked_issues``.

    All synchronous PyGithub calls are wrapped with ``asyncio.to_thread``.

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
        """Fetch all pull requests and return them as ``Document`` objects.

        Returns:
            List of ``Document`` objects, one per PR.

        Raises:
            IngestionError: If the repository cannot be accessed.
        """
        repo_slug = self._settings.github_gardener_repo
        logger.info("Starting PR ingestion from %s", repo_slug)

        try:
            repo: Repository = await asyncio.to_thread(
                self._github.get_repo, repo_slug
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to access repository {repo_slug!r}: {exc}", source=repo_slug
            ) from exc

        try:
            paginated_prs = await asyncio.to_thread(repo.get_pulls, state="all")
        except Exception as exc:
            raise IngestionError(
                f"Failed to list PRs for {repo_slug!r}: {exc}", source=repo_slug
            ) from exc

        prs: list[PullRequest] = await asyncio.to_thread(list, paginated_prs)

        logger.info(
            "Fetched %d PRs from %s; fetching review comments…", len(prs), repo_slug
        )

        documents: list[Document] = []
        for pr in prs:
            doc = await self._build_document(pr, repo_slug)
            documents.append(doc)

        logger.info(
            "PR ingestion complete: %d documents from %s", len(documents), repo_slug
        )
        return documents

    async def _build_document(self, pr: PullRequest, repo_slug: str) -> Document:
        """Build a single ``Document`` from a PR and its review comments.

        Args:
            pr: The PyGithub ``PullRequest`` object.
            repo_slug: Repository slug used in metadata.

        Returns:
            A fully populated ``Document``.
        """
        try:
            paginated_comments = await asyncio.to_thread(pr.get_review_comments)
            review_comments: list[PullRequestComment] = await asyncio.to_thread(
                list, paginated_comments
            )
        except Exception as exc:
            logger.warning(
                "Failed to fetch review comments for PR #%d: %s", pr.number, exc
            )
            review_comments = []

        content = _format_pr_content(pr, review_comments)
        linked_issues = _extract_linked_issues(pr.body)
        labels: list[str] = [label.name for label in pr.labels]

        metadata: dict[str, Any] = {
            "repo": repo_slug,
            "pr_number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "merged": pr.merged,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "labels": labels,
            "linked_issues": linked_issues,
            "url": pr.html_url,
        }

        return Document(
            content=content,
            metadata=metadata,
            source=pr.html_url,
        )
