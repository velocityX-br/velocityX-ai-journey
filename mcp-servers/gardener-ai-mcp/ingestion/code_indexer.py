"""Ingester for Go source code from the ``gardener/gardener`` repository.

Traverses ``.go`` source files via the GitHub Contents API and extracts
structural information using regex (not a full AST parser).  For each
file the following are extracted:

- Package declaration (first ``package <name>`` line)
- Function signatures (lines matching ``func ``)
- Type declarations (lines matching ``type ``)

Each file is returned as a single ``Document`` whose content is the
concatenation of extracted signatures — not the full file source.  This
keeps chunk sizes small and focuses retrieval on structural elements.

All synchronous PyGithub calls are wrapped with ``asyncio.to_thread``.

Metadata schema per Document:
    repo     (str) — repository slug
    path     (str) — file path within the repository
    language (str) — always ``"go"``
    package  (str) — Go package name extracted from the source
    sha      (str) — Git blob SHA of the file
    url      (str) — GitHub HTML URL for the file
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any

import github
from github.ContentFile import ContentFile
from github.Repository import Repository

from config.settings import Settings
from ingestion.base import BaseIngester, Document, IngestionError

logger = logging.getLogger(__name__)

# Regex patterns for Go structural extraction.
_PACKAGE_RE = re.compile(r"^package\s+(\w+)", re.MULTILINE)
_FUNC_RE = re.compile(r"^func\s+.+", re.MULTILINE)
_TYPE_RE = re.compile(r"^type\s+\w+.+", re.MULTILINE)

# Directories to skip — generated code, vendor, and test fixtures produce
# noise without adding retrieval value.
_SKIP_DIRS = frozenset(
    {
        "vendor",
        "third_party",
        "hack",
        ".git",
        "node_modules",
    }
)

# Maximum number of files to ingest per run.  The gardener/gardener repo
# contains thousands of Go files; a ceiling prevents runaway API usage
# during development.  Set to 0 to disable.
_MAX_FILES: int = 2000


def _decode_go_content(content_file: ContentFile) -> str:
    """Decode a base64-encoded Go source file returned by the Contents API.

    Args:
        content_file: PyGithub ``ContentFile`` with ``encoding`` and
            ``content`` attributes populated.

    Returns:
        Decoded UTF-8 source text.
    """
    if content_file.encoding == "base64" and content_file.content:
        raw: bytes = base64.b64decode(content_file.content)
        return raw.decode("utf-8", errors="replace")
    decoded = content_file.decoded_content
    if isinstance(decoded, bytes):
        return decoded.decode("utf-8", errors="replace")
    return str(decoded)


def _extract_package(source: str) -> str:
    """Extract the Go package name from source text.

    Args:
        source: Full Go source file content.

    Returns:
        The package name, or ``"unknown"`` if not found.
    """
    match = _PACKAGE_RE.search(source)
    return match.group(1) if match else "unknown"


def _extract_signatures(source: str) -> str:
    """Extract function signatures and type declarations from Go source.

    Captures only the declaration lines — not function bodies.  The
    extracted lines are joined with newlines to form the document content.

    Args:
        source: Full Go source file content.

    Returns:
        Newline-separated string of matched signature lines.  Returns the
        first 500 characters of the original source if nothing matches
        (e.g. a file containing only comments or constants).
    """
    funcs = _FUNC_RE.findall(source)
    types = _TYPE_RE.findall(source)
    combined = funcs + types

    if combined:
        return "\n".join(combined)

    # Fallback: return a truncated header so we still have something to embed.
    return source[:500]


class CodeIngester(BaseIngester):
    """Ingest Go source files from ``gardener/gardener``.

    Traverses the repository tree via the GitHub Contents API, collecting
    ``.go`` files.  For each file, regex-based structural extraction
    produces a compact document containing function signatures and type
    declarations rather than full source.

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
                attribute determines which repository is traversed.
        """
        self._github = github_client
        self._settings = settings
        self._file_count: int = 0

    async def ingest(self) -> list[Document]:
        """Traverse the repository and return structural documents for Go files.

        Returns:
            List of ``Document`` objects, one per ``.go`` file processed.

        Raises:
            IngestionError: If the repository cannot be accessed.
        """
        repo_slug = self._settings.github_gardener_repo
        logger.info("Starting code ingestion from %s", repo_slug)

        self._file_count = 0

        try:
            repo: Repository = await asyncio.to_thread(
                self._github.get_repo, repo_slug
            )
        except Exception as exc:
            raise IngestionError(
                f"Failed to access repository {repo_slug!r}: {exc}", source=repo_slug
            ) from exc

        documents: list[Document] = await self._walk_directory(repo, "")

        logger.info(
            "Code ingestion complete: %d documents from %s", len(documents), repo_slug
        )
        return documents

    async def _walk_directory(
        self, repo: Repository, path: str
    ) -> list[Document]:
        """Recursively walk a directory collecting ``.go`` source files.

        Args:
            repo: The PyGithub ``Repository`` object.
            path: Repository-relative path to start from.  Empty string
                means the repository root.

        Returns:
            List of ``Document`` objects for each ``.go`` file found.
        """
        if _MAX_FILES > 0 and self._file_count >= _MAX_FILES:
            return []

        documents: list[Document] = []

        try:
            contents: list[ContentFile] | ContentFile = await asyncio.to_thread(
                repo.get_contents, path
            )
        except Exception as exc:
            logger.debug("Cannot access path %r: %s", path, exc)
            return documents

        if not isinstance(contents, list):
            contents = [contents]

        for item in contents:
            if _MAX_FILES > 0 and self._file_count >= _MAX_FILES:
                break

            if item.type == "dir":
                dir_name = item.path.split("/")[-1]
                if dir_name in _SKIP_DIRS:
                    logger.debug("Skipping directory %r", item.path)
                    continue
                sub_docs = await self._walk_directory(repo, item.path)
                documents.extend(sub_docs)

            elif item.type == "file" and item.path.endswith(".go"):
                doc = await self._fetch_go_document(repo, item)
                if doc is not None:
                    documents.append(doc)
                    self._file_count += 1

        return documents

    async def _fetch_go_document(
        self, repo: Repository, item: ContentFile
    ) -> Document | None:
        """Fetch and structurally parse a single Go source file.

        Args:
            repo: The PyGithub ``Repository`` object.
            item: A ``ContentFile`` representing the ``.go`` file.

        Returns:
            A ``Document`` with extracted signatures, or ``None`` on error.
        """
        try:
            file_content: ContentFile = await asyncio.to_thread(
                repo.get_contents, item.path
            )
            if isinstance(file_content, list):
                file_content = file_content[0]

            source = _decode_go_content(file_content)
        except Exception as exc:
            logger.warning("Failed to fetch Go file %r: %s", item.path, exc)
            return None

        package_name = _extract_package(source)
        signatures = _extract_signatures(source)

        repo_slug = self._settings.github_gardener_repo

        metadata: dict[str, Any] = {
            "repo": repo_slug,
            "path": item.path,
            "language": "go",
            "package": package_name,
            "sha": item.sha,
            "url": item.html_url,
        }

        return Document(
            content=signatures,
            metadata=metadata,
            source=item.html_url or f"https://github.com/{repo_slug}/blob/HEAD/{item.path}",
        )
