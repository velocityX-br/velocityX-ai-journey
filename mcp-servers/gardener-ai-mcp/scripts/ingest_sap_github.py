"""CLI entry point: ingest issues from github.tools.sap repositories into Qdrant.

Fetches issues from one or more SAP GitHub Enterprise repositories page by
page, chunking, embedding, and upserting each page immediately so that:

- Memory usage stays bounded (one page in flight at a time).
- GitHub API rate limits are respected (optional ``--page-delay`` between pages).
- Progress is visible after every page, not only at the end.

All upserted documents carry ``source_origin="sap_github"`` metadata so that
search results can be distinguished from public github.com/gardener/* content.

The script uses the same embedding and vector store infrastructure as
``scripts/ingest_docs.py`` but targets github.tools.sap via a dedicated
PyGithub client configured with ``GITHUB_SAP_BASE_URL`` and
``GITHUB_SAP_TOKEN``.

Usage::

    # Ingest both canary and live issue repos (default repos):
    uv run python scripts/ingest_sap_github.py

    # Ingest specific repos:
    uv run python scripts/ingest_sap_github.py \\
        --repos kubernetes-canary/issues-canary kubernetes-live/issues-live

    # Limit to 200 issues per repo, pause 2 s between pages:
    uv run python scripts/ingest_sap_github.py --max 200 --page-delay 2.0

    # Check Qdrant collection status without ingesting:
    uv run python scripts/ingest_sap_github.py --check

Required environment variables::

    GITHUB_SAP_TOKEN    — PAT with read:repo scope on github.tools.sap
    GITHUB_SAP_BASE_URL — GitHub Enterprise API root (default: https://github.tools.sap/api/v3)
    HYPERSPACE_OPENAI_BASE_URL, ANTHROPIC_AUTH_TOKEN, QDRANT_URL
    (same as ingest_docs.py — see .env.example)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import github
from dotenv import load_dotenv

# Load .env before anything reads os.environ (e.g. GITHUB_SAP_TOKEN check).
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

from config.settings import get_settings
from embeddings.openai_embedder import HyperspaceEmbedder
from ingestion.chunking import MarkdownChunker
from ingestion.sap_github_issues import SapGithubIssuesIngester
from vectorstore.qdrant import QdrantVectorStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Target Qdrant collection — SAP GitHub issues share the same collection as
# github.com/gardener issues.  They are distinguished by the
# source_origin="sap_github" metadata field.
_SAP_GITHUB_ISSUES_COLLECTION = "gardener_issues"

# Default SAP GitHub Enterprise API URL.
_DEFAULT_SAP_GITHUB_BASE_URL = "https://github.tools.sap/api/v3"

# Default repositories to ingest when --repos is not specified.
_DEFAULT_SAP_GITHUB_REPOS = [
    "kubernetes-canary/issues-canary",
    "kubernetes-live/issues-live",
]

# Embedding batch size — how many chunks are sent to the embedding API at once.
_EMBED_BATCH_SIZE = 256


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


def _print_step(step: int, total: int, label: str) -> None:
    print(f"\n  [{step}/{total}] {label}", flush=True)


def _print_progress(current: int, total: int | None, label: str, width: int = 30) -> None:
    if total:
        pct = current / total
        filled = int(width * pct)
        bar = "█" * filled + "░" * (width - filled)
        print(f"\r         [{bar}] {current:>6}/{total}  {label}", end="", flush=True)
    else:
        # total unknown — show spinner-style counter
        print(f"\r         {current:>6} docs   {label}", end="", flush=True)


def _elapsed(start: float) -> str:
    secs = time.monotonic() - start
    if secs < 60:
        return f"{secs:.1f}s"
    return f"{int(secs // 60)}m {int(secs % 60)}s"


def _page_summary_line(
    page_index: int,
    issues_on_page: int,
    prs_skipped: int,
    chunks: int,
    vectors: int,
    cumulative_docs: int,
    cumulative_vectors: int,
    page_elapsed: str,
) -> str:
    return (
        f"  page {page_index:>4} │ "
        f"issues {issues_on_page:>4}  prs_skip {prs_skipped:>3} │ "
        f"chunks {chunks:>5}  vectors {vectors:>6} │ "
        f"total docs {cumulative_docs:>6}  total vec {cumulative_vectors:>7} │ "
        f"{page_elapsed:>7}"
    )


# ---------------------------------------------------------------------------
# SAP GitHub client factory
# ---------------------------------------------------------------------------


def _build_sap_github_client() -> github.Github:
    """Construct a PyGithub client targeting github.tools.sap.

    Reads credentials from environment variables:
    - ``GITHUB_SAP_TOKEN``    — required; PAT with read:repo scope
    - ``GITHUB_SAP_BASE_URL`` — optional; defaults to
      ``https://github.tools.sap/api/v3``

    Returns:
        An authenticated ``github.Github`` instance pointing at
        github.tools.sap.

    Raises:
        SystemExit: If ``GITHUB_SAP_TOKEN`` is not set.
    """
    token = os.environ.get("GITHUB_SAP_TOKEN") or os.environ.get("GARDENER_MCP_GITHUB_SAP_TOKEN")
    if not token:
        print(
            "ERROR: GITHUB_SAP_TOKEN is not set.\n"
            "       Export a PAT with read:repo scope on github.tools.sap:\n"
            "         export GITHUB_SAP_TOKEN=your-pat-here\n"
            "       Or add it to your .env file.",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = (
        os.environ.get("GITHUB_SAP_BASE_URL")
        or os.environ.get("GARDENER_MCP_GITHUB_SAP_BASE_URL")
        or _DEFAULT_SAP_GITHUB_BASE_URL
    )

    logger.info("Connecting to SAP GitHub at %s", base_url)
    return github.Github(login_or_token=token, base_url=base_url)


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


async def _check() -> None:
    """Print gardener_issues collection point count and exit."""
    qdrant_url = (
        os.environ.get("GARDENER_MCP_QDRANT_URL")
        or os.environ.get("QDRANT_URL")
        or "http://localhost:6333"
    )

    from qdrant_client import AsyncQdrantClient

    client = AsyncQdrantClient(url=qdrant_url)
    try:
        await client.get_collections()
    except Exception as exc:
        print(f"ERROR: Cannot reach Qdrant at {qdrant_url}: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        result = await client.count(collection_name=_SAP_GITHUB_ISSUES_COLLECTION)
        count = result.count
    except Exception:
        count = 0

    sap_count = 0
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        filtered = await client.count(
            collection_name=_SAP_GITHUB_ISSUES_COLLECTION,
            count_filter=Filter(
                must=[
                    FieldCondition(
                        key="source_origin",
                        match=MatchValue(value="sap_github"),
                    )
                ]
            ),
        )
        sap_count = filtered.count
    except Exception:
        sap_count = -1  # filter not supported or collection empty

    print()
    print("=" * 58)
    print(f"  Qdrant  {qdrant_url}")
    print("=" * 58)
    print(f"  {_SAP_GITHUB_ISSUES_COLLECTION:<24} {count:>8,} points total")
    if sap_count >= 0:
        print(f"  {'  └─ sap_github origin':<24} {sap_count:>8,} points")
    print("=" * 58)


# ---------------------------------------------------------------------------
# Ingestion result
# ---------------------------------------------------------------------------


@dataclass
class _RepoIngestionResult:
    repo_slug: str
    documents_fetched: int
    chunks_produced: int
    vectors_upserted: int
    pages_processed: int = field(default=0)
    elapsed_seconds: float = field(default=0.0)
    error: str = field(default="")


# ---------------------------------------------------------------------------
# Per-repo page-by-page ingestion
# ---------------------------------------------------------------------------


async def _ingest_repo(
    repo_slug: str,
    gh: github.Github,
    embedder: HyperspaceEmbedder,
    vector_store: QdrantVectorStore,
    chunker: MarkdownChunker,
    settings,
    max_issues: int,
    batch_size: int,
    page_delay: float,
) -> _RepoIngestionResult:
    """Run the full page-by-page ingest pipeline for a single SAP GitHub repo.

    Each page is fetched from GitHub, immediately chunked, embedded, and
    upserted into Qdrant before the next page is requested.  An optional
    ``page_delay`` sleep between pages gives the GitHub API time to breathe.

    Args:
        repo_slug: Repository slug on github.tools.sap.
        gh: Authenticated PyGithub client.
        embedder: HyperspaceEmbedder instance.
        vector_store: QdrantVectorStore instance.
        chunker: MarkdownChunker instance.
        settings: Application settings.
        max_issues: Maximum issues to ingest (0 = unlimited).
        batch_size: GitHub API page size.
        page_delay: Seconds to sleep after each page (0 = no delay).

    Returns:
        Populated :class:`_RepoIngestionResult`.
    """
    t_repo_start = time.monotonic()
    result = _RepoIngestionResult(
        repo_slug=repo_slug,
        documents_fetched=0,
        chunks_produced=0,
        vectors_upserted=0,
    )

    print(f"\n{'─' * 78}", flush=True)
    print(f"  SAP GitHub repo: {repo_slug}", flush=True)
    print(f"  Collection:      {_SAP_GITHUB_ISSUES_COLLECTION}", flush=True)
    print(
        f"  Max issues: {max_issues if max_issues > 0 else 'unlimited':<12}  "
        f"Batch size: {batch_size}  "
        f"Page delay: {page_delay:.1f}s",
        flush=True,
    )
    print(f"{'─' * 78}", flush=True)

    # ------------------------------------------------------------------
    # Ensure Qdrant collection exists once per repo
    # ------------------------------------------------------------------
    _print_step(1, 2, f"Ensuring Qdrant collection '{_SAP_GITHUB_ISSUES_COLLECTION}' …")
    t_step = time.monotonic()
    await vector_store.ensure_collection(
        _SAP_GITHUB_ISSUES_COLLECTION, settings.embedding_dimensions
    )
    print(f"\n         Ready  ({_elapsed(t_step)})", flush=True)

    # ------------------------------------------------------------------
    # Page-by-page fetch → chunk → embed → upsert
    # ------------------------------------------------------------------
    _print_step(2, 2, "Fetching, chunking, embedding, and upserting page by page …")
    print(flush=True)
    print(
        f"  {'Page':>5} │ {'Issues':>6}  {'Skip':>4} │ "
        f"{'Chunks':>6}  {'Vecs':>7} │ "
        f"{'CumDocs':>8}  {'CumVecs':>9} │ {'Time':>7}",
        flush=True,
    )
    print(f"  {'─' * 74}", flush=True)

    ingester = SapGithubIssuesIngester(
        github_client=gh,
        repo_slug=repo_slug,
        max_issues=max_issues,
        batch_size=batch_size,
    )

    try:
        async for page_result in ingester.ingest_page_by_page():
            t_page_start = time.monotonic()

            # Chunk
            page_chunks = chunker.chunk_many(page_result.documents)

            # Embed in sub-batches
            page_vectors: list[list[float]] = []
            for batch_start in range(0, len(page_chunks), _EMBED_BATCH_SIZE):
                batch_texts = [
                    c.content
                    for c in page_chunks[batch_start : batch_start + _EMBED_BATCH_SIZE]
                ]
                batch_vecs = await embedder.embed_documents(batch_texts)
                page_vectors.extend(batch_vecs)

            # Upsert
            upserted = 0
            if page_vectors:
                upserted = await vector_store.upsert(
                    collection=_SAP_GITHUB_ISSUES_COLLECTION,
                    documents=page_chunks,
                    vectors=page_vectors,
                )

            # Accumulate totals
            result.documents_fetched += len(page_result.documents)
            result.chunks_produced += len(page_chunks)
            result.vectors_upserted += upserted
            result.pages_processed += 1

            print(
                _page_summary_line(
                    page_index=page_result.page_index,
                    issues_on_page=len(page_result.documents),
                    prs_skipped=page_result.prs_skipped,
                    chunks=len(page_chunks),
                    vectors=upserted,
                    cumulative_docs=result.documents_fetched,
                    cumulative_vectors=result.vectors_upserted,
                    page_elapsed=_elapsed(t_page_start),
                ),
                flush=True,
            )

            if page_result.capped:
                print(
                    f"  (cap reached: max_issues={max_issues})",
                    flush=True,
                )
                break

            # Throttle to avoid hammering the GitHub API.
            if page_delay > 0:
                await asyncio.sleep(page_delay)

    except Exception as exc:
        result.error = str(exc)
        result.elapsed_seconds = time.monotonic() - t_repo_start
        print(f"\n  ERROR: {exc}", file=sys.stderr, flush=True)
        return result

    result.elapsed_seconds = time.monotonic() - t_repo_start
    print(f"  {'─' * 74}", flush=True)
    print(
        f"\n  Done in {_elapsed(t_repo_start)}.  "
        f"{result.documents_fetched:,} issues → "
        f"{result.chunks_produced:,} chunks → "
        f"{result.vectors_upserted:,} vectors in '{_SAP_GITHUB_ISSUES_COLLECTION}'.",
        flush=True,
    )
    return result


# ---------------------------------------------------------------------------
# Multi-repo orchestration
# ---------------------------------------------------------------------------


async def _ingest_repos(
    repos: list[str],
    max_issues_per_repo: int,
    batch_size: int,
    page_delay: float,
) -> None:
    """Run the page-by-page pipeline for each SAP GitHub repository in sequence.

    Args:
        repos: Repository slugs on github.tools.sap.
        max_issues_per_repo: Maximum issues per repository (0 = unlimited).
        batch_size: GitHub API page size.
        page_delay: Seconds to sleep between pages (0 = no delay).
    """
    t_total_start = time.monotonic()

    settings = get_settings()
    gh = _build_sap_github_client()
    embedder = HyperspaceEmbedder(settings=settings)
    vector_store = QdrantVectorStore(settings=settings)
    chunker = MarkdownChunker(chunk_size=1000, chunk_overlap=200)

    results: list[_RepoIngestionResult] = []

    for repo_slug in repos:
        result = await _ingest_repo(
            repo_slug=repo_slug,
            gh=gh,
            embedder=embedder,
            vector_store=vector_store,
            chunker=chunker,
            settings=settings,
            max_issues=max_issues_per_repo,
            batch_size=batch_size,
            page_delay=page_delay,
        )
        results.append(result)

    # ------------------------------------------------------------------
    # Final summary table
    # ------------------------------------------------------------------
    total_docs = sum(r.documents_fetched for r in results)
    total_chunks = sum(r.chunks_produced for r in results)
    total_vectors = sum(r.vectors_upserted for r in results)
    total_pages = sum(r.pages_processed for r in results)
    n_errors = sum(1 for r in results if r.error)

    print()
    print("═" * 82)
    print("  SAP GitHub ingestion complete")
    print(f"  Collection: {_SAP_GITHUB_ISSUES_COLLECTION}")
    print("═" * 82)
    print(
        f"  {'Repository':<38}  {'Issues':>6}  {'Pages':>5}  "
        f"{'Chunks':>7}  {'Vectors':>7}  {'Time':>7}"
    )
    print("  " + "─" * 78)
    for r in results:
        mins, secs = divmod(int(r.elapsed_seconds), 60)
        t_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
        status = f"  ERROR: {r.error[:30]}" if r.error else ""
        print(
            f"  {r.repo_slug:<38}  {r.documents_fetched:>6,}  {r.pages_processed:>5}  "
            f"{r.chunks_produced:>7,}  {r.vectors_upserted:>7,}  {t_str:>7}"
            f"{status}"
        )
    print("  " + "─" * 78)
    mins, secs = divmod(int(time.monotonic() - t_total_start), 60)
    t_total = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
    print(
        f"  {'TOTAL':<38}  {total_docs:>6,}  {total_pages:>5}  "
        f"{total_chunks:>7,}  {total_vectors:>7,}  {t_total:>7}"
    )
    print("═" * 82)
    print(f"  Model: {settings.embedding_model}  ({settings.embedding_dimensions}d)")
    if n_errors:
        print(f"  WARNING: {n_errors} repo(s) encountered errors — check logs above.")
    print("═" * 82)

    try:
        gh.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Synchronous CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Ingest issues from github.tools.sap repositories into Qdrant "
            f"({_SAP_GITHUB_ISSUES_COLLECTION} collection) page by page."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  GITHUB_SAP_TOKEN       — PAT with read:repo scope on github.tools.sap (required)\n"
            "  GITHUB_SAP_BASE_URL    — API root (default: https://github.tools.sap/api/v3)\n"
            "  QDRANT_URL             — Qdrant URL (default: http://localhost:6333)\n"
            "  HYPERSPACE_OPENAI_BASE_URL — embedding endpoint\n"
            "  ANTHROPIC_AUTH_TOKEN   — Hyperspace bearer token\n"
            "\nExamples:\n"
            "  # Ingest default repos (canary + live):\n"
            "  uv run python scripts/ingest_sap_github.py\n\n"
            "  # Ingest specific repos:\n"
            "  uv run python scripts/ingest_sap_github.py \\\n"
            "      --repos kubernetes-canary/issues-canary kubernetes-live/issues-live\n\n"
            "  # Limit to 200 issues per repo, 2 s delay between pages:\n"
            "  uv run python scripts/ingest_sap_github.py --max 200 --page-delay 2.0\n\n"
            "  # Check collection status:\n"
            "  uv run python scripts/ingest_sap_github.py --check\n"
        ),
    )
    parser.add_argument(
        "--repos",
        nargs="+",
        metavar="ORG/REPO",
        default=None,
        help=(
            "Repository slugs on github.tools.sap to ingest "
            f"(default: {', '.join(_DEFAULT_SAP_GITHUB_REPOS)})"
        ),
    )
    parser.add_argument(
        "--max",
        type=int,
        default=500,
        metavar="N",
        help=(
            "Maximum issues to ingest per repository, most-recently updated first "
            "(default: 500; use 0 for no limit — not recommended for large repos)"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        metavar="N",
        dest="batch_size",
        help="GitHub API page size (default: 100)",
    )
    parser.add_argument(
        "--page-delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        dest="page_delay",
        help=(
            "Seconds to sleep between GitHub API pages to avoid rate limiting "
            "(default: 1.0; use 0 to disable)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Show gardener_issues collection point counts without ingesting.",
    )

    args = parser.parse_args()

    if args.check:
        asyncio.run(_check())
        return

    repos = args.repos if args.repos else list(_DEFAULT_SAP_GITHUB_REPOS)

    print()
    print("═" * 70)
    print("  SAP GitHub Issues Ingester  (page-by-page)")
    print(f"  Repos:      {', '.join(repos)}")
    print(f"  Max/repo:   {args.max if args.max > 0 else 'unlimited'}")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Page delay: {args.page_delay:.1f}s")
    print(f"  Target:     {_SAP_GITHUB_ISSUES_COLLECTION} (Qdrant)")
    print("═" * 70)

    asyncio.run(_ingest_repos(repos, args.max, args.batch_size, args.page_delay))


if __name__ == "__main__":
    main()
