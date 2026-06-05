# Gardener AI MCP — Phase-by-Phase Execution Plan

**Date:** 2026-06-03
**Status:** Active
**Owner:** gardener-architect

---

## Overview

This document defines the ordered execution plan for building the Gardener AI MCP server. Each phase is self-contained, has a defined scope, and exits with verifiable criteria before the next phase begins.

The plan covers 8 phases as defined in the development roadmap in `CLAUDE.md` and elaborated in `docs/adr/001_phase1_architecture.md`.

---

## Phase Status Summary

| Phase | Name | Status |
|---|---|---|
| 1 | Architecture | COMPLETED |
| 2 | Ingestion | Pending |
| 3 | Vector Store | Pending |
| 4 | Retrieval | Pending |
| 5 | MCP Tools | Pending |
| 6 | Docker | Pending |
| 7 | Helm | Pending |
| 8 | CI/CD | Pending |

---

## Phase 1 — Architecture (COMPLETED)

**Completed:** 2026-06-02

### Summary of Decisions

Phase 1 produced the full architectural blueprint for the project. The following was delivered and accepted:

- `docs/adr/001_phase1_architecture.md` — the primary architecture document containing all ADRs, the module responsibility table, the dependency list, and the development roadmap
- High-level architecture diagram covering all layers: ingestion, chunking, embedding, vector store, retrieval, and MCP
- Full repository structure with abstract base classes for all injectable components
- `pyproject.toml` with all runtime and development dependencies pinned

### ADRs Accepted in Phase 1

| ADR | Decision |
|---|---|
| ADR-001 | Four separate Qdrant collections: `gardener_docs`, `gardener_issues`, `gardener_prs`, `gardener_code` |
| ADR-002 | Embeddings via Hyperspace OpenAI-compatible endpoint at `http://localhost:6655/openai/v1/embeddings` using `text-embedding-3-small` (1536 dims). Two separate base URL env vars: `HYPERSPACE_OPENAI_BASE_URL` for embeddings, `ANTHROPIC_BASE_URL` for LLM calls |
| ADR-003 | FastMCP as the MCP server framework (decorator-based, automatic Pydantic schema generation, stdio/SSE transport) |
| ADR-004 | Dependency injection via frozen `AppContext` Pydantic model created once in FastMCP lifespan |
| ADR-005 | `HybridRetriever` with RRF fusion as the default for `root_cause_analysis`, crossing all four collections |
| ADR-006 | All LLM calls routed through SAP Hyperspace LLM Proxy. `GARDENER_MCP_*` prefixed env vars take priority over ambient `ANTHROPIC_*` vars. No direct calls to `api.anthropic.com` |

### Exit Criteria Met

- All abstract interfaces defined and documented
- All data contracts specified (Pydantic models for documents, chunks, search results)
- All environment variables catalogued in `.env.example`
- `mypy` passes on the skeleton
- ADR document written and accepted

---

## Phase 2 — Ingestion

---
@gardener-architect

Phase 1 is complete. The architecture document at docs/adr/001_phase1_architecture.md defines all module boundaries, abstract base classes, data contracts, and ADRs. The pyproject.toml dependency list is finalised.

Create Phase 2: Ingestion.

Deliver:
1. `ingestion/base.py` — `BaseIngester` abstract class with `async def ingest() -> list[Document]` signature and a `Document` dataclass or Pydantic model carrying `content`, `metadata`, and `source` fields
2. `ingestion/chunking.py` — `MarkdownChunker` using `MarkdownTextSplitter` from LangChain and `CodeChunker` using `RecursiveCharacterTextSplitter`; each chunk metadata envelope must carry `chunk_index`, `total_chunks`, and `parent_id`
3. `ingestion/github_docs.py` — `GitHubDocsIngester(BaseIngester)` that walks `gardener/documentation` via the GitHub Contents API using `PyGithub`; fetches Markdown files and proposal documents; attaches `repo`, `path`, `sha`, `url`, and `content_type` metadata
4. `ingestion/github_issues.py` — `GitHubIssuesIngester(BaseIngester)` that fetches issues from `gardener/gardener` via `PyGithub` including title, body, comments, labels, and state; paginates through all open and closed issues
5. `ingestion/github_prs.py` — `GitHubPRsIngester(BaseIngester)` that fetches PRs with review comments, linked issues, and diff summary (not raw diff); attaches `pr_number`, `state`, `merged`, and `linked_issues` metadata
6. `ingestion/code_indexer.py` — `CodeIngester(BaseIngester)` that traverses Go source files in `gardener/gardener` using the GitHub Contents API; extracts function signatures and type declarations via regex; does not parse full ASTs
7. `scripts/ingest_docs.py` — CLI entry point that runs `GitHubDocsIngester` against the real GitHub API and prints a summary; reads `GITHUB_TOKEN` via `config/settings.py`
8. `tests/ingestion/` — one test module per ingester; mock the GitHub API using `pytest-mock`; assert chunk metadata envelope fields are populated; assert `BaseIngester` contract is enforced

Rules:
- Read `GITHUB_TOKEN` exclusively through `config/settings.py` using `pydantic-settings`; never read `os.environ` directly in any ingestion module
- Every ingester class must accept its `PyGithub` client and `Settings` object via constructor injection; no module-level singletons
- All ingestion methods must be `async`; use `asyncio.to_thread` to wrap synchronous PyGithub calls
- Chunk metadata envelopes are mandatory: every chunk must carry `chunk_index`, `total_chunks`, and `parent_id` before leaving `chunking.py`
- Do not write any vectors to Qdrant in this phase; ingestion produces `list[Document]` only
- Do not implement any part of the embedding, vector store, retrieval, or MCP layers
- All new files must have full type hints and docstrings
- `ruff` and `mypy` must pass on all new files before phase is considered complete

Do not generate code outside Phase 2 scope.

---

### Exit Criteria

- Each ingester tested with mocked GitHub API; all tests pass
- Chunk metadata envelope fields (`chunk_index`, `total_chunks`, `parent_id`) verified in tests
- `scripts/ingest_docs.py` runs successfully against the real `gardener/documentation` repository
- `ruff check .` and `mypy .` pass with zero errors

---

## Phase 3 — Vector Store

---
@gardener-architect

Phase 2 is complete. All four ingesters and the chunking layer are implemented and tested. Each ingester produces a typed list of Documents with populated chunk metadata envelopes. No vectors have been written yet.

Create Phase 3: Vector Store.

Deliver:
1. `embeddings/base.py` — `BaseEmbedder` abstract class with `async def embed_documents(texts: list[str]) -> list[list[float]]` and `async def embed_query(text: str) -> list[float]`
2. `embeddings/openai_embedder.py` — `HyperspaceEmbedder(BaseEmbedder)` that calls `POST /openai/v1/embeddings` on the Hyperspace proxy using the `openai` SDK with `base_url` set to `HYPERSPACE_OPENAI_BASE_URL`; supports `text-embedding-3-small` (1536 dims, default) and `text-embedding-3-large` (3072 dims); batch size capped at 2048 texts per call; retry logic via `tenacity` with exponential backoff; token counting via `tiktoken`
3. `vectorstore/base.py` — `BaseVectorStore` abstract class with `async def upsert`, `async def search`, `async def delete`, and `async def ensure_collection` abstract methods
4. `vectorstore/qdrant.py` — `QdrantVectorStore(BaseVectorStore)` using `qdrant-client` async client; creates and manages four collections (`gardener_docs`, `gardener_issues`, `gardener_prs`, `gardener_code`) with HNSW indexes and payload indexes; batch upsert with configurable batch size; cosine distance metric; health check method
5. `config/settings.py` — add the embedding-related settings fields: `hyperspace_openai_base_url` resolving from `GARDENER_MCP_HYPERSPACE_OPENAI_BASE_URL` with fallback to `HYPERSPACE_OPENAI_BASE_URL`; `embedding_model` defaulting to `text-embedding-3-small`; `embedding_dimensions` defaulting to `1536`; `qdrant_url` and `qdrant_api_key`
6. `scripts/ingest_docs.py` — extend the existing CLI to wire ingestion through embedding and upsert into the `gardener_docs` collection; this proves the end-to-end path: fetch → chunk → embed → upsert
7. `tests/embeddings/` — unit tests for `HyperspaceEmbedder`; mock the HTTP call using `respx`; assert batching behaviour when input exceeds 2048 texts; assert retry fires on a 429 response
8. `tests/vectorstore/` — unit tests for `QdrantVectorStore`; mock the Qdrant client; assert `ensure_collection` creates collections with correct vector size and distance metric; assert batch upsert calls are issued correctly

Rules:
- `HYPERSPACE_OPENAI_BASE_URL` and `QDRANT_URL` must be read exclusively through `config/settings.py`; follow the `GARDENER_MCP_*` prefix pattern with unprefixed fallback as defined in ADR-006
- `HyperspaceEmbedder` must accept `Settings` and an optional pre-constructed `openai.AsyncOpenAI` client via constructor injection for testability
- `QdrantVectorStore` must accept `Settings` and an optional pre-constructed `qdrant_client.AsyncQdrantClient` via constructor injection for testability
- Qdrant collection vector dimensions must be driven by `settings.embedding_dimensions`; hardcoding 1536 in `qdrant.py` is not permitted
- Do not implement any part of the retrieval or MCP layers
- All new files must have full type hints and docstrings
- `ruff` and `mypy` must pass on all new files before phase is considered complete

Do not generate code outside Phase 3 scope.

---

### Exit Criteria

- All four Qdrant collections created and visible in the Qdrant dashboard
- `scripts/ingest_docs.py` populates the `gardener_docs` collection end-to-end
- Health check against `QDRANT_URL` passes
- `ruff check .` and `mypy .` pass with zero errors
- All tests pass

---

## Phase 4 — Retrieval

---
@gardener-architect

Phase 3 is complete. The Qdrant vector store and the Hyperspace embedder are implemented. All four collections are populated and queryable. End-to-end ingestion is verified.

Create Phase 4: Retrieval.

Deliver:
1. `retrieval/base.py` — `BaseRetriever` abstract class with `async def retrieve(query: str, filters: dict | None, limit: int) -> list[SearchResult]`; define the `SearchResult` Pydantic model carrying `content`, `score`, `metadata`, and `collection` fields
2. `retrieval/semantic.py` — `SemanticRetriever(BaseRetriever)` that embeds the query via `BaseEmbedder`, performs cosine similarity search against a specified Qdrant collection, applies optional metadata filters, and returns ranked `SearchResult` objects
3. `retrieval/hybrid.py` — `HybridRetriever(BaseRetriever)` that runs dense semantic search and sparse BM25-style keyword search in parallel using `asyncio.gather`; merges results using Reciprocal Rank Fusion (RRF); supports querying across all four collections simultaneously; used as the default retriever for `root_cause_analysis`
4. `tests/retrieval/` — unit tests for both retrievers; mock `BaseEmbedder` and `QdrantVectorStore`; assert that `HybridRetriever` calls both dense and sparse search concurrently (verify with `asyncio.gather`); assert RRF score fusion produces correct rank ordering given a known input

Rules:
- `SemanticRetriever` and `HybridRetriever` must accept `BaseEmbedder` and `BaseVectorStore` exclusively via constructor injection; no module-level instantiation of clients
- All retrieval methods must be `async`; the parallel search in `HybridRetriever` must use `asyncio.gather` — sequential calls are not acceptable
- RRF fusion must be implemented in a standalone function in `retrieval/hybrid.py` that is independently unit-testable without a live Qdrant instance
- `SearchResult` must include the `collection` field so callers can distinguish which of the four collections a result came from
- Do not implement any part of the MCP, Docker, or Helm layers
- All new files must have full type hints and docstrings
- `ruff` and `mypy` must pass on all new files before phase is considered complete

Do not generate code outside Phase 4 scope.

---

### Exit Criteria

- `SemanticRetriever` returns relevant results for known Gardener test queries
- `HybridRetriever` returns fused results from multiple collections
- All unit tests pass with mocked dependencies
- `ruff check .` and `mypy .` pass with zero errors

---

## Phase 5 — MCP Tools

---
@gardener-architect

Phase 4 is complete. SemanticRetriever and HybridRetriever are implemented and tested. RRF fusion is verified. Both retrievers are injectable and fully decoupled from concrete dependencies.

Create Phase 5: MCP Tools.

Deliver:
1. `mcp/context.py` — `AppContext` frozen Pydantic model holding all initialised singletons: `settings: Settings`, `embedder: BaseEmbedder`, `vector_store: BaseVectorStore`, `semantic_retriever: SemanticRetriever`, `hybrid_retriever: HybridRetriever`, `anthropic_client: anthropic.AsyncAnthropic`; the `anthropic.AsyncAnthropic` client is constructed with `base_url=settings.anthropic_base_url` and `api_key=settings.anthropic_auth_token` as specified in ADR-006
2. `mcp/models.py` — Pydantic input and output models for all 7 tools; field descriptions must be written as agent-visible documentation (agents read the JSON schema); models include `SearchDocsInput`, `SearchIssuesInput`, `SearchPRsInput`, `SearchProposalsInput`, `SearchCodeInput`, `RAGRetrieveInput`, `RootCauseAnalysisInput`, and a shared `SearchResult` output model
3. `mcp/tools.py` — 7 `@mcp.tool` async handlers: `search_docs` (searches `gardener_docs` collection), `search_issues` (searches `gardener_issues`), `search_prs` (searches `gardener_prs`), `search_proposals` (filters `gardener_docs` by `content_type=proposal`), `search_code` (searches `gardener_code`), `rag_retrieve` (semantic retrieval across a specified collection with configurable limit), `root_cause_analysis` (uses `HybridRetriever` across all four collections then calls the LLM via `anthropic.AsyncAnthropic` to synthesise a structured root cause analysis); each tool receives the `AppContext` via FastMCP context injection
4. `mcp/server.py` — FastMCP app with a lifespan function that constructs `AppContext` once at startup, registers all tools from `tools.py`, and configures stdio and SSE transports; reads all configuration via `config/settings.py`
5. `config/settings.py` — verify all `GARDENER_MCP_*` env vars are present: `GARDENER_MCP_ANTHROPIC_BASE_URL`, `GARDENER_MCP_ANTHROPIC_AUTH_TOKEN`, `GARDENER_MCP_ANTHROPIC_MODEL`, `GARDENER_MCP_API_TIMEOUT_MS`, `GARDENER_MCP_HYPERSPACE_OPENAI_BASE_URL`, `GARDENER_MCP_EMBEDDING_MODEL`, `GARDENER_MCP_EMBEDDING_DIMENSIONS`; each must fall back to the unprefixed var if the prefixed form is absent
6. `.env.example` — complete example file covering all `GARDENER_MCP_*` vars, fallback `ANTHROPIC_*` vars, `GITHUB_TOKEN`, `QDRANT_URL`, and `QDRANT_API_KEY`
7. `tests/mcp/` — unit tests for all 7 tools using a mock `AppContext`; assert each tool calls the correct retriever method; assert `root_cause_analysis` calls `hybrid_retriever.retrieve` then `anthropic_client.messages.create`; assert model input validation rejects bad input

Rules:
- `AppContext` is constructed once inside the FastMCP lifespan function; it must never be created inside a tool handler or at module level
- All tool handlers must be `async` and receive dependencies exclusively through `AppContext` context injection — no direct imports of concrete classes inside `tools.py`
- The `anthropic.AsyncAnthropic` client in `AppContext` must be configured with `base_url` and `api_key` sourced from `settings` as specified in ADR-006; never hardcode the base URL or token
- `root_cause_analysis` must use `HybridRetriever` and not `SemanticRetriever`; this is a hard constraint from ADR-005
- The `ANTHROPIC_MODEL` used in `root_cause_analysis` must be read from `settings.anthropic_model`; the default value must be `anthropic--claude-sonnet-latest`
- Do not implement Docker, Helm, or CI/CD in this phase
- All new files must have full type hints and docstrings
- `ruff` and `mypy` must pass on all new files before phase is considered complete

Do not generate code outside Phase 5 scope.

---

### Exit Criteria

- All 7 tools are callable via the MCP protocol
- `root_cause_analysis` returns structured output with cited sources
- `mcp/server.py` starts cleanly and accepts stdio connections
- All unit tests pass with mocked `AppContext`
- `ruff check .` and `mypy .` pass with zero errors

---

## Phase 6 — Docker

---
@gardener-architect

Phase 5 is complete. All 7 MCP tools are implemented, FastMCP server starts cleanly, AppContext dependency injection is wired, and all tools are callable via the MCP protocol.

Create Phase 6: Docker + kind local stack.

Deliver:
1. `docker/Dockerfile` — multi-stage build: stage 1 uses `python:3.12-slim` as builder, installs dependencies via `uv` into a virtual environment at `/app/.venv`; stage 2 uses `python:3.12-slim` as the final image, copies only the venv and application source, runs as a non-root user (`appuser`, UID 1001), exposes port 8080, and sets `CMD ["python", "-m", "mcp.server"]`; final image must be under 400 MB
2. `scripts/setup_kind.sh` — bootstraps the full local stack on a kind cluster: creates the cluster with `kind create cluster --name gardener-ai-mcp`, builds the Docker image (`docker build -f docker/Dockerfile -t gardener-ai-mcp:dev .`), loads it into the cluster (`kind load docker-image gardener-ai-mcp:dev --name gardener-ai-mcp`), and installs the Helm chart (`helm install gardener-ai-mcp ./helm --values helm/values.yaml`); Qdrant is deployed via the Helm subchart (defined in Phase 7) — no separate service required
3. `docker/.dockerignore` — excludes `.git`, `__pycache__`, `*.pyc`, `.env`, `.env.*`, `tests/`, `docs/`, `helm/`, `.github/`, and `uv.lock` from the build context
4. `scripts/healthcheck.py` — standalone script that checks Qdrant availability at `QDRANT_URL` and returns exit code 0 on success, 1 on failure; used as the `HEALTHCHECK` instruction in the Dockerfile
5. `docs/deploy-local.md` — kind cluster deployment guide covering prerequisites, quick start, script walkthrough, verification, MCP client connection (port-forward + Claude Desktop config), teardown, configuration, and troubleshooting
6. `docs/deploy-cloud.md` — generic Kubernetes cloud deployment guide covering image push to registry, namespace and secret creation, Helm install with overrides, rollout verification, upgrade workflow, secret rotation, and a Gardener shoot cluster note
7. A `trivy` scan instruction in the phase notes: after building the image, run `trivy image --exit-code 1 --severity CRITICAL gardener-ai-mcp:dev`; the phase is not complete until the scan reports zero critical CVEs

Rules:
- The final Docker image must run as a non-root user; `USER root` in the final stage is not permitted
- All secrets (`GARDENER_MCP_ANTHROPIC_AUTH_TOKEN`, `GITHUB_TOKEN`, `QDRANT_API_KEY`) must be injected at runtime via environment variables; they must not be baked into any image layer
- `uv` must be used for dependency installation inside the Dockerfile; `pip install` without `uv` is not permitted
- The kind cluster must be created with `kind create cluster --name gardener-ai-mcp`; Qdrant is deployed via the Helm subchart, not as a standalone service
- `docker-compose.yml` is not used in this project; do not create it
- Do not implement the Helm chart templates in this phase (those belong to Phase 7); `setup_kind.sh` may reference the chart but Phase 6 only delivers the script stub with a clear comment if the chart is not yet present
- Do not implement CI/CD in this phase

Do not generate code outside Phase 6 scope.

---

### Exit Criteria

- Multi-stage Docker image builds successfully and passes `trivy image --severity CRITICAL` with zero critical CVEs
- `scripts/setup_kind.sh` brings up the full local stack on a kind cluster
- MCP server pod reaches `Running` state in kind (verified with `kubectl get pods`)
- Final image size is under 400 MB
- Container runs as non-root user (verified with `kubectl exec -- id`)

---

## Phase 7 — Helm

---
@gardener-architect

Phase 6 is complete. A working multi-stage Docker image exists, `scripts/setup_kind.sh` brings up the full local stack on a kind cluster, the image is under 400 MB, and trivy reports zero critical CVEs.

Create Phase 7: Helm.

Deliver:
1. `helm/Chart.yaml` — chart metadata with `name: gardener-ai-mcp`, `type: application`, `version: 0.1.0`, `appVersion` driven from a build argument; declare a subchart dependency on the official Qdrant Helm chart
2. `helm/values.yaml` — default values for all configurable parameters: replica count, image repository and tag, resource requests and limits, HPA min/max replicas and CPU target utilisation, ingress enabled flag, service type and port, Qdrant subchart values, and placeholder comments for all secret references
3. `helm/values.schema.json` — JSON Schema validating the shape of `values.yaml`; required fields must be enforced; prevents deployment with missing image repository or zero replica count
4. `helm/templates/deployment.yaml` — Kubernetes `Deployment` reading all secrets from a Kubernetes `Secret` resource via `envFrom`; never inlines secret values as plaintext in the deployment manifest
5. `helm/templates/secret.yaml` — Kubernetes `Secret` template with base64-encoded placeholders; the template must include a clear comment that values must be overridden via `--set` or an external secrets operator in production
6. `helm/templates/hpa.yaml` — `HorizontalPodAutoscaler` targeting the `Deployment`; reads `minReplicas`, `maxReplicas`, and `targetCPUUtilizationPercentage` from `values.yaml`
7. `helm/templates/service.yaml` and `helm/templates/ingress.yaml` — standard service and optional ingress templates controlled by `values.ingress.enabled`
8. `helm/templates/NOTES.txt` — post-install usage notes explaining how to connect an MCP client to the deployed server

Rules:
- All Kubernetes `Secret` values must be sourced from `values.yaml` overrides or an external secrets operator; plaintext secrets must never appear in any committed file under `helm/`
- The `HPA` template must be guarded by a `values.autoscaling.enabled` boolean so it can be disabled for single-replica development deployments
- `helm lint helm/` must pass with zero errors and zero warnings before the phase is considered complete
- The chart must install cleanly on a `kind` cluster using `helm install gardener-ai-mcp ./helm --values helm/values.yaml`
- Do not implement CI/CD in this phase

Do not generate code outside Phase 7 scope.

---

### Exit Criteria

- `helm lint helm/` passes with zero errors and zero warnings
- Chart installs on a `kind` cluster with `helm install`
- HPA is created and reports current/desired replicas
- Secrets are injected via Kubernetes `Secret`, not environment variable literals in the manifest

---

## Phase 8 — CI/CD

---
@gardener-architect

Phase 7 is complete. A production-grade Helm chart exists, passes helm lint, and installs cleanly on a kind cluster with HPA and Kubernetes secrets.

Create Phase 8: CI/CD.

Deliver:
1. `.github/workflows/ci.yml` — pull request pipeline with the following ordered jobs: (a) `lint` running `ruff check .` and `mypy .`; (b) `test` running `pytest --cov=. --cov-report=xml --cov-fail-under=80`; the pipeline must fail if coverage drops below 80%; (c) `build` running `docker build -f docker/Dockerfile .` and `trivy image --exit-code 1 --severity CRITICAL` against the built image; jobs (b) and (c) depend on job (a) passing
2. `.github/workflows/release.yml` — triggered on `push` to tags matching `v*.*.*`; builds and pushes the Docker image to `ghcr.io` tagged with the git tag and `latest`; packages and pushes the Helm chart to a GitHub Pages OCI registry; uses `GITHUB_TOKEN` from the GitHub Actions environment — no external secrets required for image push
3. `.github/workflows/ingest.yml` — scheduled ingestion pipeline running on cron `0 2 * * *` (02:00 UTC daily); runs `scripts/ingest_docs.py` and `scripts/ingest_issues.py`; authenticates to GitHub using the `GITHUB_TOKEN` secret; requires `QDRANT_URL` and the Hyperspace embedding secrets to be configured as GitHub Actions repository secrets; sends a Slack or webhook notification on failure (notification URL read from `GARDENER_MCP_ALERT_WEBHOOK_URL` secret)
4. `.pre-commit-config.yaml` — hooks for `ruff` (lint + format), `mypy`, and `trailing-whitespace`; ensures developer machines enforce the same rules as CI before every commit
5. `tests/` coverage audit — review all existing test modules and add missing tests to reach the 80% coverage gate; priority order: `mcp/tools.py`, `retrieval/hybrid.py`, `ingestion/chunking.py`

Rules:
- The CI pipeline must use `ruff` for both linting and formatting checks; `flake8` and `black` are not permitted
- The 80% coverage gate is enforced by `pytest-cov` with `--cov-fail-under=80`; disabling or skipping this check is not permitted
- The release workflow must use `GITHUB_TOKEN` (built-in) for pushing to `ghcr.io`; no separate `CR_PAT` or personal access token should be required
- The scheduled ingestion job must read `GITHUB_TOKEN`, `QDRANT_URL`, and all `GARDENER_MCP_*` Hyperspace secrets from GitHub Actions repository secrets; no secrets may be hardcoded in any workflow YAML file
- `trivy` must run in the CI `build` job with `--exit-code 1 --severity CRITICAL`; the build job must fail if critical CVEs are detected
- Do not modify Helm chart or Dockerfile in this phase; scope is limited to GitHub Actions workflows, pre-commit config, and test coverage

Do not generate code outside Phase 8 scope.

---

### Exit Criteria

- PR pipeline (lint → test → build) passes on a clean branch
- Coverage report shows 80% or above across the full codebase
- `trivy` scan passes in CI with zero critical CVEs
- Release pipeline publishes Docker image and Helm chart on a `v*.*.*` tag push
- Scheduled ingestion job runs and completes without error in GitHub Actions
- `pre-commit run --all-files` passes on the full repository

---

## Dependency Graph

The phases have the following sequential dependency chain. No phase may begin before the preceding phase exit criteria are fully met.

```
Phase 1 (Architecture)
    └── Phase 2 (Ingestion)
            └── Phase 3 (Vector Store)
                    └── Phase 4 (Retrieval)
                            └── Phase 5 (MCP Tools)
                                    └── Phase 6 (Docker)
                                            └── Phase 7 (Helm)
                                                    └── Phase 8 (CI/CD)
```

---

## Key Environment Variables Reference

The following table summarises the environment variables that appear across multiple phases. All are read exclusively through `config/settings.py`.

| Variable | Fallback | Used In | Notes |
|---|---|---|---|
| `GARDENER_MCP_ANTHROPIC_BASE_URL` | `ANTHROPIC_BASE_URL` | Phase 5, 8 | Points to SAP Hyperspace LLM Proxy |
| `GARDENER_MCP_ANTHROPIC_AUTH_TOKEN` | `ANTHROPIC_AUTH_TOKEN` | Phase 5, 8 | Shared bearer token for Hyperspace |
| `GARDENER_MCP_ANTHROPIC_MODEL` | `ANTHROPIC_MODEL` | Phase 5 | Default: `anthropic--claude-sonnet-latest` |
| `GARDENER_MCP_API_TIMEOUT_MS` | `API_TIMEOUT_MS` | Phase 5 | Default: `3000000` |
| `GARDENER_MCP_HYPERSPACE_OPENAI_BASE_URL` | `HYPERSPACE_OPENAI_BASE_URL` | Phase 3, 8 | `http://localhost:6655/openai/v1` |
| `GARDENER_MCP_EMBEDDING_MODEL` | `EMBEDDING_MODEL` | Phase 3 | Default: `text-embedding-3-small` |
| `GARDENER_MCP_EMBEDDING_DIMENSIONS` | `EMBEDDING_DIMENSIONS` | Phase 3 | Default: `1536` |
| `GITHUB_TOKEN` | — | Phase 2, 8 | GitHub Personal Access Token |
| `QDRANT_URL` | — | Phase 3, 6, 7, 8 | Default: `http://localhost:6333` |
| `QDRANT_API_KEY` | — | Phase 3, 6, 7 | Empty string for local dev |
| `GARDENER_MCP_ALERT_WEBHOOK_URL` | — | Phase 8 | Slack/webhook for ingestion failure alerts |

---

*This execution plan is derived from `CLAUDE.md` and `docs/adr/001_phase1_architecture.md`. Update this document whenever an ADR is amended or a phase exit criterion changes.*
