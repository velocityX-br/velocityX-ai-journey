# Progress Log

## Session — 2026-08-24

### Task
Verify whether the local Qdrant used by gardener-ai-mcp contains Gardener documentation in `gardener_docs`.

### Actions
- [x] Confirmed working directory.
- [x] Read project Qdrant configuration (`http://localhost:6333`).
- [x] Confirmed Qdrant is reachable and running version 1.18.1.
- [x] Listed all collections; `gardener_docs` is absent.
- [x] Queried target collection metadata and exact-count endpoints; both returned HTTP 404 because `gardener_docs` does not exist.
- [x] Confirmed localhost port 6333 maps through `kubectl` to the running gardener-ai-mcp Qdrant runtime.
- [x] Verified expected ingester provenance fields; initially documentation had not been ingested into `gardener_docs`.

## Ingestion run — 2026-08-25
- [x] Validated GitHub access, embedding endpoint (1536 dimensions), Qdrant, and persistent storage.
- [x] Initial ingestion exposed a stale hard-coded `website/` source root and fetched 0 documents.
- [x] Updated the ingester to support the current `hugo/content/` layout with fallback to legacy `website/`.
- [x] Corrected nested proposal classification and added tests for the current layout.
- [x] Documentation ingester tests pass: 15 passed.
- [x] Replaced the slow recursive API run with a shallow sparse clone ingestion.
- [x] Loaded 1,073 Markdown files and upserted 14,811 chunks.
- [x] Verified all 14,811 points carry `repo=gardener/documentation`.
- [x] Verified 3,304 proposal chunks and valid GitHub source URLs.
- [x] Semantic retrieval smoke test returned relevant Gardener documentation.
- [x] Removed the temporary clone and one-off ingestion helper.
