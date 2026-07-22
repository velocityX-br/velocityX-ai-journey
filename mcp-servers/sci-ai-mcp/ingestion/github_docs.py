"""Ingester for Markdown documentation from an SCI documentation repository.

Walks the GitHub (Enterprise) Contents API recursively from the
repository root, collecting every ``.md`` file.  Unlike the reference
``gardener/documentation`` layout, the SAP SCI documentation repositories
(``cc/documentation-operation`` and ``cc/documentation-customer``) are not
assumed to follow a ``website/`` convention — the entire tree is walked.

All synchronous PyGithub calls are wrapped with ``asyncio.to_thread`` so
the ingester never blocks the event loop.

The ingester is parameterised by ``(repo_slug, content_type)`` so a single
class serves both documentation collections: instantiate one ingester per
repo with the appropriate ``content_type`` label (``"operation"`` or
``"customer"``).

Metadata schema per Document:
    repo         (str)  — repository slug, e.g. ``"cc/documentation-operation"``
    path         (str)  — file path within the repository
    sha          (str)  — Git blob SHA of the file
    url          (str)  — GitHub HTML URL for the file
    content_type (str)  — ``"operation"`` or ``"customer"``
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import github
from github.ContentFile import ContentFile
from github.Repository import Repository

from ingestion.base import BaseIngester, Document, IngestionError

logger = logging.getLogger(__name__)


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
    """Ingest Markdown documentation from a single SCI documentation repo.

    Walks the repository tree via the GitHub Contents API and returns one
    ``Document`` per ``.md`` file found anywhere in the tree.

    All synchronous PyGithub calls are executed in a thread pool via
    ``asyncio.to_thread`` to avoid blocking the event loop.

    Attributes:
        _github: An authenticated ``github.Github`` instance (pointed at
            the SAP GitHub Enterprise base URL via ``build_github_client``).
        _repo_slug: The repository slug to walk, e.g.
            ``"cc/documentation-operation"``.
        _content_type: The metadata label applied to every document from
            this repo (``"operation"`` or ``"customer"``).
    """

    def __init__(
        self,
        github_client: github.Github,
        repo_slug: str,
        content_type: str,
    ) -> None:
        """Initialise the ingester for one documentation repository.

        Args:
            github_client: Authenticated PyGithub client.  For SAP
                Enterprise, build this via ``config.settings.build_github_client``
                so the correct ``base_url`` is applied.
            repo_slug: The repository to walk, e.g.
                ``"cc/documentation-operation"``.
            content_type: The metadata label for every document from this
                repo — typically ``"operation"`` or ``"customer"``.
        """
        self._github = github_client
        self._repo_slug = repo_slug
        self._content_type = content_type

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest(self) -> list[Document]:
        """Walk the documentation repository and return all Markdown documents.

        Returns:
            List of ``Document`` objects, one per ``.md`` file discovered
            anywhere in the repository tree.

        Raises:
            IngestionError: If the repository cannot be accessed.
        """
        logger.info("Starting documentation ingestion from %s", self._repo_slug)

        try:
            repo: Repository = await asyncio.to_thread(
                self._github.get_repo, self._repo_slug
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to access repository {self._repo_slug!r}: {exc}",
                source=self._repo_slug,
            ) from exc

        documents: list[Document] = await self._walk_directory(repo, "")

        logger.info(
            "Documentation ingestion complete: %d documents from %s",
            len(documents),
            self._repo_slug,
        )
        return documents

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _walk_directory(self, repo: Repository, path: str) -> list[Document]:
        """Recursively walk a directory and collect Markdown files.

        Args:
            repo: The PyGithub ``Repository`` object.
            path: Repository-relative path to start walking from.  Use
                ``""`` for the repository root.

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
                sub_docs = await self._walk_directory(repo, item.path)
                documents.extend(sub_docs)
            elif item.type == "file" and item.path.endswith(".md"):
                doc = await self._fetch_document(repo, item)
                if doc is not None:
                    documents.append(doc)

        return documents

    async def _fetch_document(
        self, repo: Repository, item: ContentFile
    ) -> Document | None:
        """Fetch and decode a single Markdown file from the repository.

        Args:
            repo: The PyGithub ``Repository`` object.
            item: A ``ContentFile`` representing the file to fetch.

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
            "repo": self._repo_slug,
            "path": item.path,
            "sha": item.sha,
            "url": item.html_url,
            "content_type": self._content_type,
        }

        return Document(
            content=text,
            metadata=metadata,
            source=item.html_url
            or f"https://github.wdf.sap.corp/{self._repo_slug}/blob/HEAD/{item.path}",
        )
