# Findings: Local Qdrant Gardener Documentation Check

## Runtime configuration
- Project `.env` sets `QDRANT_URL=http://localhost:6333`.
- `GET http://localhost:6333/` succeeds.
- Server identity: Qdrant 1.18.1, commit `e01c207f40a2fe01ed23a191957a76e224fe5726`.

## Collection listing
`GET http://localhost:6333/collections` reports:
- `gardener_code`
- `gardener_issues`
- `gardener_prs`
- `sci_docs_customer`
- `sci_docs_operation`
- `terraforming`

The expected `gardener_docs` collection is absent from this local Qdrant endpoint.

## Direct target query
- `GET /collections/gardener_docs` returned HTTP 404 with: `Collection gardener_docs doesn't exist!`
- `POST /collections/gardener_docs/points/count` with `{"exact": true}` returned the same HTTP 404.
- Therefore there are no `gardener_docs` points or payloads to sample on this server.

## Runtime identity
- Port 6333 is being served through a `kubectl` listener/port-forward.
- Kubernetes context contains a running `gardener-ai-mcp-qdrant-0` pod and Qdrant service in namespace `gardener-mcp`.
- Thus the checked endpoint is the Qdrant associated with the local gardener-ai-mcp Kubernetes runtime, not an unrelated Docker Qdrant container.

## Expected provenance metadata
The project ingester `ingestion/github_docs.py` is designed for the `gardener/documentation` repository. Its payload metadata includes `repo`, `path`, `url`, and `content_type`; a valid ingested point should normally have `repo: gardener/documentation` and/or a GitHub URL under `https://github.com/gardener/documentation`.

## Initial verdict
Before this ingestion run, the local gardener-ai-mcp Qdrant did **not** contain an ingested `gardener_docs` dataset from `https://github.com/gardener/documentation`. This was stronger than an empty-collection result: the collection did not exist at all.

## Ingestion prerequisites validated
- Authenticated access to `gardener/documentation` succeeded; default branch is `master`.
- A live embedding request succeeded using `text-embedding-3-small`, returning the configured 1536 dimensions.
- Qdrant 1.18.1 is reachable through localhost:6333.
- The Kubernetes Qdrant pod is running with a bound 10 Gi persistent volume claim.
- Documentation-only ingestion was started with `.venv/bin/python scripts/ingest_docs.py --collections docs` after exporting `.env`.

## Repository layout correction
- The first ingestion run fetched 0 documents because the code only traversed `website/`.
- The repository currently has 1,079 Markdown files, with 1,073 under `hugo/content/`.
- The ingester now tries `hugo/content/` first and retains `website/` as a compatibility fallback.
- Proposal detection now applies to nested files as well as directories.
- Unit test result after the change: 15 passed; related ingestion regression suite: 37 passed.

## Completed ingestion
- A shallow sparse clone fetched the current `hugo/content/` tree efficiently.
- Markdown files loaded: 1,073.
- Chunks produced and vectors upserted: 14,811.
- Embedding model/dimensions: `text-embedding-3-small`, 1536.

## Final Qdrant verification
- `gardener_docs` status: green; optimizer status: ok.
- Exact point count: 14,811.
- Exact count filtered by `repo=gardener/documentation`: 14,811.
- Exact proposal chunk count: 3,304.
- Payload samples contain `hugo/content/...` paths and `https://github.com/gardener/documentation/blob/master/...` URLs.
- Semantic smoke query (`How does Gardener manage shoot clusters?`) returned 3 results from the expected repository, including architecture and gardenadm documentation.
