"""Ingester for Markdown documentation from the ``gardener/documentation`` repository.

Walks the GitHub Contents API recursively to locate ``.md`` files under
``website/`` directories (product documentation) and under directories
whose name contains ``proposal`` (GEP proposals).  All synchronous
PyGithub calls are wrapped with ``asyncio.to_thread`` so the ingester
does not block the event loop.

Metadata schema per Document:
    repo         (str)  — repository slug, e.g. ``"gardener/documentation"``
    path         (str)  — file path within the repository
    sha          (str)  — Git blob SHA of the file
    url          (str)  — GitHub HTML URL for the file
    content_type (str)  — ``"doc"`` or ``"proposal"``
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import github
from github.ContentFile import ContentFile
from github.Repository import Repository

from config.settings import Settings
from ingestion.base import BaseIngester, Document, IngestionError

logger = logging.getLogger(__name__)

# Top-level directories to walk.  The Contents API is walked recursively
# from these roots.
_DOC_ROOT = "website"
_PROPOSAL_INDICATORS = ("proposal", "proposals", "gep")


def _is_proposal_path(path: str) -> bool:
    """Return True if the file path looks like a GEP/proposal document.

    Args:
        path: Repository-relative file path.

    Returns:
        True when any path segment matches a known proposal indicator.
    """
    parts = path.lower().split("/")
    return any(indicator in part for part in parts for indicator in _PROPOSAL_INDICATORS)


def _decode_content(content_file: ContentFile) -> str:
    """Decode the base64-encoded content returned by the Contents API.

    The GitHub Contents API always returns file content as base64.
    ``ContentFile.decoded_content`` performs the same decode but may
    raise on very large files; this helper handles both paths gracefully.

    Args:
        content_file: A PyGithub ``ContentFile`` with ``encoding`` and
            ``content`` attributes populated.

    Returns:
        The decoded UTF-8 text.  Non-UTF-8 bytes are replaced with the
        Unicode replacement character.
    """
    if content_file.encoding == "base64" and content_file.content:
        raw: bytes = base64.b64decode(content_file.content)
        return raw.decode("utf-8", errors="replace")
    # Fall back to the PyGithub convenience property.
    decoded = content_file.decoded_content
    if isinstance(decoded, bytes):
        return decoded.decode("utf-8", errors="replace")
    return str(decoded)


class GitHubDocsIngester(BaseIngester):
    """Ingest Markdown documentation from ``gardener/documentation``.

    Walks the repository tree via the GitHub Contents API and returns one
    ``Document`` per ``.md`` file found under documentation or proposal
    directories.

    All synchronous PyGithub calls are executed in a thread pool via
    ``asyncio.to_thread`` to avoid blocking the event loop.

    Attributes:
        github_client: An authenticated ``github.Github`` instance.
        settings: Application settings carrying repository slugs.
    """

    def __init__(self, github_client: github.Github, settings: Settings) -> None:
        """Initialise the ingester with a GitHub client and settings.

        Args:
            github_client: Authenticated PyGithub client.
            settings: Application settings.  The ``github_docs_repo``
                attribute determines which repository is walked.
        """
        self._github = github_client
        self._settings = settings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest(self) -> list[Document]:
        """Walk the documentation repository and return all Markdown documents.

        Returns:
            List of ``Document`` objects, one per ``.md`` file discovered.

        Raises:
            IngestionError: If the repository cannot be accessed or an
                unexpected error occurs during traversal.
        """
        repo_slug = self._settings.github_docs_repo
        logger.info("Starting documentation ingestion from %s", repo_slug)

        try:
            repo: Repository = await asyncio.to_thread(
                self._github.get_repo, repo_slug
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to access repository {repo_slug!r}: {exc}", source=repo_slug
            ) from exc

        documents: list[Document] = []

        # Walk website/ documentation tree.
        try:
            doc_docs = await self._walk_directory(repo, _DOC_ROOT, "doc")
            documents.extend(doc_docs)
        except Exception as exc:
            logger.warning("Failed to walk %r in %s: %s", _DOC_ROOT, repo_slug, exc)

        # Walk root-level proposal directories.
        try:
            root_contents: list[ContentFile] = await asyncio.to_thread(
                repo.get_contents, ""
            )
            for item in root_contents:
                if item.type == "dir" and _is_proposal_path(item.path):
                    proposal_docs = await self._walk_directory(
                        repo, item.path, "proposal"
                    )
                    documents.extend(proposal_docs)
        except Exception as exc:
            logger.warning(
                "Failed to enumerate root contents of %s: %s", repo_slug, exc
            )

        logger.info(
            "Documentation ingestion complete: %d documents from %s",
            len(documents),
            repo_slug,
        )
        return documents

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _walk_directory(
        self, repo: Repository, path: str, content_type: str
    ) -> list[Document]:
        """Recursively walk a directory and collect Markdown files.

        Args:
            repo: The PyGithub ``Repository`` object.
            path: Repository-relative path to start walking from.
            content_type: Either ``"doc"`` or ``"proposal"``.

        Returns:
            List of ``Document`` objects for each ``.md`` file found.
        """
        documents: list[Document] = []

        try:
            contents: list[ContentFile] | ContentFile = await asyncio.to_thread(
                repo.get_contents, path
            )
        except Exception as exc:
            logger.debug("Cannot access path %r: %s", path, exc)
            return documents

        # get_contents returns a single ContentFile if the path is a file,
        # or a list when the path is a directory.
        if not isinstance(contents, list):
            contents = [contents]

        for item in contents:
            if item.type == "dir":
                sub_docs = await self._walk_directory(
                    repo,
                    item.path,
                    "proposal" if _is_proposal_path(item.path) else content_type,
                )
                documents.extend(sub_docs)
            elif item.type == "file" and item.path.endswith(".md"):
                doc = await self._fetch_document(repo, item, content_type)
                if doc is not None:
                    documents.append(doc)

        return documents

    async def _fetch_document(
        self, repo: Repository, item: ContentFile, content_type: str
    ) -> Document | None:
        """Fetch and decode a single Markdown file from the repository.

        Args:
            repo: The PyGithub ``Repository`` object.
            item: A ``ContentFile`` representing the file to fetch.
            content_type: Either ``"doc"`` or ``"proposal"``.

        Returns:
            A ``Document`` instance, or ``None`` if decoding fails.
        """
        try:
            file_content: ContentFile = await asyncio.to_thread(
                repo.get_contents, item.path
            )
            if isinstance(file_content, list):
                # Should not happen for a file path, but guard defensively.
                file_content = file_content[0]

            text = _decode_content(file_content)
        except Exception as exc:
            logger.warning("Failed to fetch %r: %s", item.path, exc)
            return None

        metadata: dict[str, Any] = {
            "repo": self._settings.github_docs_repo,
            "path": item.path,
            "sha": item.sha,
            "url": item.html_url,
            "content_type": content_type,
        }

        return Document(
            content=text,
            metadata=metadata,
            source=item.html_url or f"https://github.com/{self._settings.github_docs_repo}/blob/HEAD/{item.path}",
        )
