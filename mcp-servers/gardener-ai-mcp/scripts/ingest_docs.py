"""CLI entry point: fetch, chunk, embed, and upsert Gardener documentation.

This script is the Phase 3 end-to-end smoke-test.  It:

1. Loads settings from the environment / ``.env`` file.
2. Creates an authenticated PyGithub client.
3. Runs ``GitHubDocsIngester`` to fetch all Markdown files.
4. Chunks the fetched documents with ``MarkdownChunker``.
5. Initialises ``HyperspaceEmbedder`` and ``QdrantVectorStore``.
6. Ensures the ``gardener_docs`` collection exists in Qdrant.
7. Embeds all chunks in configurable batches.
8. Upserts the resulting vectors into Qdrant.
9. Prints a human-readable summary to stdout.

Usage::

    # With uv (recommended):
    uv run python -m scripts.ingest_docs

    # Or directly:
    python scripts/ingest_docs.py

Environment variables (see .env.example)::

    GITHUB_TOKEN=<your-pat>                           # required
    GITHUB_DOCS_REPO=gardener/documentation           # optional override
    HYPERSPACE_OPENAI_BASE_URL=http://localhost:6655/openai/v1
    ANTHROPIC_AUTH_TOKEN=<hyperspace-bearer-token>
    QDRANT_URL=http://localhost:6333
    QDRANT_BATCH_SIZE=100                             # optional override
    EMBEDDING_MODEL=text-embedding-3-small            # optional override
    EMBEDDING_DIMENSIONS=1536                         # optional override
"""

from __future__ import annotations

import asyncio
import logging
import sys

import github

from config.settings import get_settings
from embeddings.openai_embedder import HyperspaceEmbedder
from ingestion.chunking import MarkdownChunker
from ingestion.github_docs import GitHubDocsIngester
from vectorstore.qdrant import QdrantVectorStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)

# Collection name for Gardener documentation.
_COLLECTION = "gardener_docs"

# Embedding batch size for the CLI (not the Qdrant upsert batch size).
# The Hyperspace embedder already splits at 2048 internally; this controls
# how many chunks we hand to ``embed_documents`` in a single call so that
# progress logging is granular.
_EMBED_BATCH_SIZE = 256


async def _run() -> None:
    """Main async entry point for the full ingestion pipeline."""
    settings = get_settings()

    logger.info("Initialising GitHub client for repo: %s", settings.github_docs_repo)
    gh = github.Github(settings.github_token)

    ingester = GitHubDocsIngester(github_client=gh, settings=settings)
    chunker = MarkdownChunker(chunk_size=1000, chunk_overlap=200)
    embedder = HyperspaceEmbedder(settings=settings)
    vector_store = QdrantVectorStore(settings=settings)

    # ------------------------------------------------------------------
    # Step 1: Ingest
    # ------------------------------------------------------------------
    print("Starting documentation ingestion...", flush=True)
    documents = await ingester.ingest()
    logger.info("Fetched %d raw documents.", len(documents))

    # ------------------------------------------------------------------
    # Step 2: Chunk
    # ------------------------------------------------------------------
    chunks = chunker.chunk_many(documents)
    logger.info("Produced %d chunks.", len(chunks))

    # ------------------------------------------------------------------
    # Step 3: Ensure Qdrant collection exists
    # ------------------------------------------------------------------
    logger.info(
        "Ensuring collection %r exists (vector_size=%d).",
        _COLLECTION,
        settings.embedding_dimensions,
    )
    await vector_store.ensure_collection(_COLLECTION, settings.embedding_dimensions)

    # ------------------------------------------------------------------
    # Step 4: Embed in batches
    # ------------------------------------------------------------------
    all_vectors: list[list[float]] = []
    total_chunks = len(chunks)

    logger.info(
        "Embedding %d chunks in batches of %d...",
        total_chunks,
        _EMBED_BATCH_SIZE,
    )

    for batch_start in range(0, total_chunks, _EMBED_BATCH_SIZE):
        batch_chunks = chunks[batch_start : batch_start + _EMBED_BATCH_SIZE]
        batch_texts = [c.content for c in batch_chunks]

        batch_vectors = await embedder.embed_documents(batch_texts)
        all_vectors.extend(batch_vectors)

        logger.info(
            "Embedded batch %d/%d (%d/%d chunks).",
            batch_start // _EMBED_BATCH_SIZE + 1,
            (total_chunks + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE,
            min(batch_start + _EMBED_BATCH_SIZE, total_chunks),
            total_chunks,
        )

    # ------------------------------------------------------------------
    # Step 5: Upsert to Qdrant
    # ------------------------------------------------------------------
    logger.info(
        "Upserting %d vectors to collection %r (batch_size=%d)...",
        len(all_vectors),
        _COLLECTION,
        settings.qdrant_batch_size,
    )
    upserted = await vector_store.upsert(
        collection=_COLLECTION,
        documents=chunks,
        vectors=all_vectors,
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("Ingestion summary")
    print("=" * 60)
    print(f"Repository       : {settings.github_docs_repo}")
    print(f"Documents fetched: {len(documents)}")
    print(f"Chunks produced  : {len(chunks)}")
    print(f"Vectors upserted : {upserted}")
    print(f"Collection       : {_COLLECTION}")
    print(f"Embedding model  : {settings.embedding_model}")
    print(f"Dimensions       : {settings.embedding_dimensions}")

    if documents:
        doc_types: dict[str, int] = {}
        for doc in documents:
            ct: str = doc.metadata.get("content_type", "unknown")
            doc_types[ct] = doc_types.get(ct, 0) + 1
        print("By type:")
        for content_type, count in sorted(doc_types.items()):
            print(f"  {content_type:<14}: {count}")

    if chunks:
        avg_len = sum(len(c.content) for c in chunks) / len(chunks)
        print(f"Avg chunk length : {avg_len:.0f} chars")

    print("=" * 60)

    # Close the GitHub client's underlying connection pool.
    try:
        gh.close()
    except Exception:
        pass


def main() -> None:
    """Synchronous wrapper — called when the script is run directly."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
