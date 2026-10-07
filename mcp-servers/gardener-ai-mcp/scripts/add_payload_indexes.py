"""CLI entry point: add missing payload indexes to an existing Qdrant collection.

Payload indexes are normally created at collection-creation time in
``QdrantVectorStore.ensure_collection`` (from ``_INDEXED_PAYLOAD_FIELDS``).
When new indexed fields are added to that tuple *after* a collection already
exists, the running collection does not automatically gain the new indexes.

This one-off script backfills those indexes on an existing collection so that
filtered searches on the new fields (e.g. ``source_origin`` and
``sap_github_repo`` on ``gardener_issues``) are index-backed rather than
falling back to a slower full scan.

The operation is idempotent: creating an index that already exists is treated
as a success and reported as "already present".

Usage::

    # Backfill every field in _INDEXED_PAYLOAD_FIELDS on gardener_issues:
    uv run python scripts/add_payload_indexes.py

    # Target a specific collection:
    uv run python scripts/add_payload_indexes.py --collection gardener_issues

    # Only create indexes for specific fields:
    uv run python scripts/add_payload_indexes.py \\
        --fields source_origin sap_github_repo

Required environment variables::

    QDRANT_URL      — Qdrant instance URL (default: http://localhost:6333)
    QDRANT_API_KEY  — optional API key (default: unauthenticated)
    (same as ingest_docs.py — see .env.example)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env before anything reads os.environ.
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import PayloadSchemaType

from config.settings import get_settings
from vectorstore.qdrant import _INDEXED_PAYLOAD_FIELDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("add_payload_indexes")


def _build_client() -> AsyncQdrantClient:
    """Construct an async Qdrant client from settings.

    Returns:
        An ``AsyncQdrantClient`` targeting ``settings.qdrant_url``, using
        the configured API key when present.
    """
    settings = get_settings()
    api_key = settings.qdrant_api_key or None
    return AsyncQdrantClient(url=settings.qdrant_url, api_key=api_key)


async def add_indexes(collection: str, fields: tuple[str, ...]) -> int:
    """Create keyword payload indexes for ``fields`` on ``collection``.

    Each index is created independently.  If Qdrant reports that an index
    already exists, that field is counted as "already present" rather than
    an error, keeping the operation idempotent.

    Args:
        collection: Name of the existing Qdrant collection to index.
        fields: Payload field names to create keyword indexes for.

    Returns:
        Process exit code: 0 on success, 1 if the collection is missing or
        any index creation fails for a reason other than "already exists".
    """
    client = _build_client()
    settings = get_settings()

    print("=" * 58)
    print(f"  Qdrant  {settings.qdrant_url}")
    print(f"  Collection  {collection}")
    print("=" * 58)

    try:
        exists = await client.collection_exists(collection_name=collection)
    except Exception as exc:  # noqa: BLE001 — surface connection errors clearly
        logger.error("Failed to reach Qdrant at %s: %s", settings.qdrant_url, exc)
        await client.close()
        return 1

    if not exists:
        logger.error("Collection %r does not exist — nothing to index.", collection)
        await client.close()
        return 1

    created = 0
    already = 0
    failed = 0

    for field_name in fields:
        try:
            await client.create_payload_index(
                collection_name=collection,
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
            )
            created += 1
            print(f"  [created]  {field_name}")
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "already exists" in message or "already" in message:
                already += 1
                print(f"  [present]  {field_name}")
            else:
                failed += 1
                print(f"  [FAILED ]  {field_name}  ({exc})")

    print("=" * 58)
    print(f"  created={created}  already_present={already}  failed={failed}")
    print("=" * 58)

    await client.close()
    return 0 if failed == 0 else 1


def main() -> None:
    """Parse CLI arguments and run the index-creation coroutine."""
    parser = argparse.ArgumentParser(
        description="Backfill keyword payload indexes on an existing Qdrant collection.",
    )
    parser.add_argument(
        "--collection",
        default="gardener_issues",
        help="Target collection name (default: gardener_issues).",
    )
    parser.add_argument(
        "--fields",
        nargs="+",
        default=list(_INDEXED_PAYLOAD_FIELDS),
        help=(
            "Payload field names to index. "
            f"Defaults to all _INDEXED_PAYLOAD_FIELDS: {', '.join(_INDEXED_PAYLOAD_FIELDS)}."
        ),
    )
    args = parser.parse_args()

    exit_code = asyncio.run(add_indexes(args.collection, tuple(args.fields)))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
