"""CLI entry point: fetch, chunk, embed, and upsert Gardener content into Qdrant.

Supports four collections:

- ``gardener_docs``   — Markdown documentation from ``gardener/documentation``
- ``gardener_issues`` — GitHub issues from ``gardener/gardener``
- ``gardener_prs``    — GitHub pull requests from ``gardener/gardener``
- ``gardener_code``   — Go source code from ``gardener/gardener``

Usage::

    # Check Qdrant collection status (no ingestion):
    uv run python scripts/ingest_docs.py --check

    # Ingest all four collections:
    uv run python scripts/ingest_docs.py

    # Ingest specific collections only:
    uv run python scripts/ingest_docs.py --collections docs issues
    uv run python scripts/ingest_docs.py --collections code
    uv run python scripts/ingest_docs.py --collections docs issues prs code

Environment variables (see .env.example)::

    GITHUB_TOKEN=<your-pat>                           # required
    GITHUB_DOCS_REPO=gardener/documentation           # optional override
    GITHUB_GARDENER_REPO=gardener/gardener            # optional override
    HYPERSPACE_OPENAI_BASE_URL=http://localhost:6655/openai/v1
    ANTHROPIC_AUTH_TOKEN=<hyperspace-bearer-token>
    QDRANT_URL=http://localhost:6333
    QDRANT_BATCH_SIZE=100                             # optional override
    EMBEDDING_MODEL=text-embedding-3-small            # optional override
    EMBEDDING_DIMENSIONS=1536                         # optional override
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import github

from config.settings import Settings, build_github_client, get_settings
from embeddings.openai_embedder import HyperspaceEmbedder
from ingestion.chunking import MarkdownChunker
from ingestion.code_indexer import CodeIngester
from ingestion.github_docs import GitHubDocsIngester
from ingestion.github_issues import GitHubIssuesIngester
from ingestion.github_prs import GitHubPRsIngester
from vectorstore.qdrant import QdrantVectorStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
# Suppress noisy httpx request logs — they clutter ingestion progress output.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


def _print_step(step: int, total: int, label: str) -> None:
    """Print a numbered step header to stdout."""
    print(f"\n  [{step}/{total}] {label}", flush=True)


def _print_progress(current: int, total: int, label: str, width: int = 30) -> None:
    """Print an in-place progress bar to stdout."""
    pct = current / total if total else 1.0
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    print(f"\r         [{bar}] {current:>6}/{total}  {label}", end="", flush=True)


def _elapsed(start: float) -> str:
    """Return elapsed seconds since *start* as a human-readable string."""
    secs = time.monotonic() - start
    if secs < 60:
        return f"{secs:.1f}s"
    return f"{int(secs // 60)}m {int(secs % 60)}s"

# Embedding batch size for the CLI progress logging.
# The HyperspaceEmbedder already splits at 2048 internally.
_EMBED_BATCH_SIZE = 256

_ALL_COLLECTIONS = [
    "gardener_docs",
    "gardener_issues",
    "gardener_prs",
    "gardener_code",
]

# Short aliases accepted by --collections
_COLLECTION_ALIASES: dict[str, str] = {
    "docs": "gardener_docs",
    "issues": "gardener_issues",
    "prs": "gardener_prs",
    "code": "gardener_code",
    # full names also accepted
    "gardener_docs": "gardener_docs",
    "gardener_issues": "gardener_issues",
    "gardener_prs": "gardener_prs",
    "gardener_code": "gardener_code",
}


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


async def _check() -> None:
    """Check Qdrant collection point counts without running ingestion.

    Prints a table of collection names and their point counts to stdout.
    Exits with code 1 if any collection is empty, to make CI scripting easy.

    Does NOT require GITHUB_TOKEN — only QDRANT_URL is needed.
    """
    import os

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

    rows: list[tuple[str, int]] = []
    for collection in _ALL_COLLECTIONS:
        try:
            result = await client.count(collection_name=collection)
            count = result.count
        except Exception:
            count = 0
        rows.append((collection, count))

    any_empty = any(count == 0 for _, count in rows)

    print()
    print("=" * 58)
    print(f"  Qdrant  {qdrant_url}")
    print("=" * 58)
    for collection, count in rows:
        if count > 0:
            status = "✓ OK"
            indicator = f"{count:>8,} points"
        else:
            status = "✗ EMPTY"
            indicator = "       0 points"
        print(f"  {collection:<24} {indicator}   {status}")
    print("=" * 58)

    if any_empty:
        empty = [c for c, n in rows if n == 0]
        print(
            f"\n  {len(empty)} collection(s) are empty: {', '.join(empty)}\n"
            "  Run:  uv run python scripts/ingest_docs.py\n"
            "  Or:   uv run python scripts/ingest_docs.py --collections "
            + " ".join(c.replace("gardener_", "") for c in empty),
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Per-collection ingestion helpers
# ---------------------------------------------------------------------------


@dataclass
class _IngestionResult:
    collection: str
    documents_fetched: int
    chunks_produced: int
    vectors_upserted: int
    elapsed_seconds: float = field(default=0.0)


async def _ingest_collection(
    collection: str,
    gh: github.Github,
    settings: Settings,
    embedder: HyperspaceEmbedder,
    vector_store: QdrantVectorStore,
) -> _IngestionResult:
    """Ingest a single collection end-to-end and return a result summary.

    Args:
        collection: One of the four ``_ALL_COLLECTIONS`` names.
        gh: Authenticated PyGithub client.
        settings: Application settings.
        embedder: Shared embedder instance.
        vector_store: Shared vector store instance.

    Returns:
        An ``_IngestionResult`` with counts for logging and the final summary.
    """
    t_start = time.monotonic()

    print(f"\n{'─' * 60}", flush=True)
    print(f"  Collection: {collection}", flush=True)
    print(f"{'─' * 60}", flush=True)

    # ------------------------------------------------------------------
    # Step 1: Fetch documents
    # ------------------------------------------------------------------
    _print_step(1, 5, "Fetching documents from GitHub...")
    t_step = time.monotonic()

    if collection == "gardener_docs":
        ingester: Any = GitHubDocsIngester(github_client=gh, settings=settings)
        chunker: Any = MarkdownChunker(chunk_size=1000, chunk_overlap=200)
    elif collection == "gardener_issues":
        ingester = GitHubIssuesIngester(github_client=gh, settings=settings)
        chunker = MarkdownChunker(chunk_size=1000, chunk_overlap=200)
    elif collection == "gardener_prs":
        ingester = GitHubPRsIngester(github_client=gh, settings=settings)
        chunker = MarkdownChunker(chunk_size=1000, chunk_overlap=200)
    elif collection == "gardener_code":
        ingester = CodeIngester(github_client=gh, settings=settings)
        chunker = MarkdownChunker(chunk_size=800, chunk_overlap=100)
    else:
        raise ValueError(f"Unknown collection: {collection!r}")

    documents = await ingester.ingest()
    print(f"\n         Fetched {len(documents):,} documents  ({_elapsed(t_step)})", flush=True)

    # ------------------------------------------------------------------
    # Step 2: Chunk
    # ------------------------------------------------------------------
    _print_step(2, 5, "Chunking documents...")
    t_step = time.monotonic()
    chunks = chunker.chunk_many(documents)
    print(f"\n         Produced {len(chunks):,} chunks  ({_elapsed(t_step)})", flush=True)

    if not chunks:
        logger.warning("[%s] No chunks produced — skipping upsert.", collection)
        return _IngestionResult(
            collection=collection,
            documents_fetched=len(documents),
            chunks_produced=0,
            vectors_upserted=0,
            elapsed_seconds=time.monotonic() - t_start,
        )

    # ------------------------------------------------------------------
    # Step 3: Ensure Qdrant collection exists
    # ------------------------------------------------------------------
    _print_step(3, 5, f"Ensuring Qdrant collection (dims={settings.embedding_dimensions})...")
    t_step = time.monotonic()
    await vector_store.ensure_collection(collection, settings.embedding_dimensions)
    print(f"\n         Ready  ({_elapsed(t_step)})", flush=True)

    # ------------------------------------------------------------------
    # Step 4: Embed in batches
    # ------------------------------------------------------------------
    total_chunks = len(chunks)
    total_batches = (total_chunks + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE
    _print_step(4, 5, f"Embedding {total_chunks:,} chunks in {total_batches} batches...")

    t_step = time.monotonic()
    all_vectors: list[list[float]] = []

    for batch_idx, batch_start in enumerate(range(0, total_chunks, _EMBED_BATCH_SIZE), start=1):
        batch_chunks = chunks[batch_start : batch_start + _EMBED_BATCH_SIZE]
        batch_texts = [c.content for c in batch_chunks]
        batch_vectors = await embedder.embed_documents(batch_texts)
        all_vectors.extend(batch_vectors)
        done = min(batch_start + _EMBED_BATCH_SIZE, total_chunks)
        _print_progress(done, total_chunks, f"batch {batch_idx}/{total_batches}")

    print(f"\n         Embedded {len(all_vectors):,} vectors  ({_elapsed(t_step)})", flush=True)

    # ------------------------------------------------------------------
    # Step 5: Upsert to Qdrant
    # ------------------------------------------------------------------
    _print_step(5, 5, f"Upserting {len(all_vectors):,} vectors to Qdrant...")
    t_step = time.monotonic()
    upserted = await vector_store.upsert(
        collection=collection,
        documents=chunks,
        vectors=all_vectors,
    )
    print(f"\n         Upserted {upserted:,} points  ({_elapsed(t_step)})", flush=True)

    elapsed = time.monotonic() - t_start
    print(f"\n  Done in {_elapsed(t_start)}.  {upserted:,} vectors in '{collection}'.", flush=True)

    return _IngestionResult(
        collection=collection,
        documents_fetched=len(documents),
        chunks_produced=total_chunks,
        vectors_upserted=upserted,
        elapsed_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------


async def _run(collections: list[str]) -> None:
    """Main async entry point for the ingestion pipeline.

    Args:
        collections: List of collection names to ingest (resolved from aliases).
    """
    settings = get_settings()
    gh = build_github_client(settings)
    embedder = HyperspaceEmbedder(settings=settings)
    vector_store = QdrantVectorStore(settings=settings)

    results: list[_IngestionResult] = []
    for collection in collections:
        result = await _ingest_collection(
            collection=collection,
            gh=gh,
            settings=settings,
            embedder=embedder,
            vector_store=vector_store,
        )
        results.append(result)

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    total_docs = sum(r.documents_fetched for r in results)
    total_chunks = sum(r.chunks_produced for r in results)
    total_vectors = sum(r.vectors_upserted for r in results)
    total_elapsed = sum(r.elapsed_seconds for r in results)

    print()
    print("═" * 66)
    print("  Ingestion complete")
    print("═" * 66)
    print(f"  {'Collection':<24}  {'Docs':>6}  {'Chunks':>8}  {'Vectors':>8}  {'Time':>8}")
    print("  " + "─" * 62)
    for r in results:
        mins, secs = divmod(int(r.elapsed_seconds), 60)
        t_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
        print(
            f"  {r.collection:<24}  {r.documents_fetched:>6,}  "
            f"{r.chunks_produced:>8,}  {r.vectors_upserted:>8,}  {t_str:>8}"
        )
    print("  " + "─" * 62)
    mins, secs = divmod(int(total_elapsed), 60)
    t_total = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
    print(
        f"  {'TOTAL':<24}  {total_docs:>6,}  {total_chunks:>8,}  {total_vectors:>8,}  {t_total:>8}"
    )
    print("═" * 66)
    print(f"  Model: {settings.embedding_model}  ({settings.embedding_dimensions}d)")
    print("═" * 66)

    try:
        gh.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Synchronous wrapper — called when the script is run directly."""
    parser = argparse.ArgumentParser(
        description="Ingest Gardener content into Qdrant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Collection aliases:\n"
            "  docs    → gardener_docs\n"
            "  issues  → gardener_issues\n"
            "  prs     → gardener_prs\n"
            "  code    → gardener_code\n"
            "\nExamples:\n"
            "  uv run python scripts/ingest_docs.py\n"
            "  uv run python scripts/ingest_docs.py --collections docs issues\n"
            "  uv run python scripts/ingest_docs.py --collections code\n"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check Qdrant collection point counts without running ingestion.",
    )
    parser.add_argument(
        "--collections",
        nargs="+",
        metavar="COLLECTION",
        choices=list(_COLLECTION_ALIASES.keys()),
        default=None,
        help=(
            "Collections to ingest. Accepts short aliases (docs, issues, prs, code) "
            "or full names. Defaults to all four collections."
        ),
    )
    args = parser.parse_args()

    if args.check:
        asyncio.run(_check())
        return

    # Resolve aliases and deduplicate while preserving order
    if args.collections:
        seen: set[str] = set()
        resolved: list[str] = []
        for alias in args.collections:
            name = _COLLECTION_ALIASES[alias]
            if name not in seen:
                seen.add(name)
                resolved.append(name)
        collections = resolved
    else:
        collections = list(_ALL_COLLECTIONS)

    asyncio.run(_run(collections))


if __name__ == "__main__":
    main()
