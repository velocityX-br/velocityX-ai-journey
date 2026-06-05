# Phase 1 Architecture: Gardener AI MCP

**Date:** 2026-06-02
**Status:** Accepted

---

## 1. High-Level Architecture Diagram

```
+====================================================================+
|                        EXTERNAL DATA SOURCES                       |
+====================================================================+
|  github.com/gardener/documentation  (primary)                      |
|  github.com/gardener/gardener       (secondary)                    |
|  github.com/gardener/gardener-extension-provider-*  (future)       |
+============================+=======================================+
                             | GitHub REST API / git clone
                             v
+====================================================================+
|                         INGESTION LAYER                            |
|  github_docs.py  |  github_issues.py  |  github_prs.py            |
|                    code_indexer.py                                 |
+============================+=======================================+
                             | Documents + Metadata
                             v
+====================================================================+
|                        CHUNKING LAYER                              |
|  MarkdownTextSplitter (docs/proposals)                             |
|  RecursiveCharacterTextSplitter (issues, PRs, code)                |
+============================+=======================================+
                             | Chunks + Metadata
                             v
+====================================================================+
|                       EMBEDDING LAYER                              |
|  Hyperspace OpenAI-compatible endpoint                             |
|  http://localhost:6655/openai/v1/embeddings                        |
|  Models: text-embedding-3-small (default) / text-embedding-3-large |
+============================+=======================================+
                             | Vectors + Payloads
                             v
+====================================================================+
|                       VECTOR STORE (Qdrant)                        |
|  gardener_docs  |  gardener_issues  |  gardener_prs  |  gardener_code |
+============================+=======================================+
                             | Query + Filters
                             v
+====================================================================+
|                       RETRIEVAL LAYER                              |
|  SemanticRetriever (dense)  |  HybridRetriever (RRF fusion)        |
+============================+=======================================+
                             | Ranked Documents
                             v
+====================================================================+
|                          MCP LAYER (FastMCP)                       |
|  server.py (lifespan, transport)  |  tools.py (7 tools)            |
+====================================================================+
                             | MCP Protocol (stdio / HTTP+SSE)
                             v
+====================================================================+
|               AI AGENT (Claude / GPT-4 / any MCP client)          |
+====================================================================+
```

---

## 2. Repository Structure

> **Note:** This is a reference layout, not a rigid contract. Module boundaries and file names may evolve as implementation progresses. The key invariants are: separation of concerns between ingestion / embedding / retrieval / MCP layers, abstract base classes for all injectable components, and test coverage alongside each module. Directory names and file organisation can be adjusted freely.

```
gardener-ai-mcp/
├── CLAUDE.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
│
├── ingestion/
│   ├── __init__.py
│   ├── base.py                  # Abstract BaseIngester
│   ├── github_docs.py
│   ├── github_issues.py
│   ├── github_prs.py
│   ├── code_indexer.py
│   └── chunking.py              # MarkdownChunker + CodeChunker
│
├── vectorstore/
│   ├── __init__.py
│   ├── base.py                  # Abstract BaseVectorStore
│   └── qdrant.py
│
├── embeddings/
│   ├── __init__.py
│   ├── base.py                  # Abstract BaseEmbedder
│   └── openai_embedder.py
│
├── retrieval/
│   ├── __init__.py
│   ├── base.py                  # Abstract BaseRetriever
│   ├── semantic.py
│   └── hybrid.py
│
├── mcp/
│   ├── __init__.py
│   ├── server.py                # FastMCP app + lifespan
│   ├── tools.py                 # 7 MCP tool definitions
│   ├── models.py                # Pydantic request/response models
│   └── context.py               # AppContext (DI container)
│
├── config/
│   ├── __init__.py
│   └── settings.py              # Pydantic BaseSettings
│
├── tests/
│   ├── conftest.py
│   ├── ingestion/
│   ├── vectorstore/
│   ├── embeddings/
│   ├── retrieval/
│   └── mcp/
│
├── docker/
│   ├── Dockerfile
│   └── .dockerignore
│
├── helm/
│   ├── Chart.yaml
│   ├── values.yaml
│   ├── values.schema.json
│   └── templates/
│
├── docs/
│   ├── architecture.md
│   ├── deploy-local.md          # kind cluster deployment guide
│   ├── deploy-cloud.md          # generic Kubernetes cloud deployment guide
│   ├── adr/
│   └── runbooks/
│
└── scripts/
    ├── ingest_docs.py
    ├── ingest_issues.py
    ├── healthcheck.py
    └── setup_kind.sh             # bootstraps local kind cluster
```

---

## 3. Module Responsibilities

| Module | Responsibility |
|---|---|
| `config/settings.py` | All runtime config via `pydantic-settings`. Single source of truth for env vars. No module reads `os.environ` directly. Key vars: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, `GITHUB_TOKEN`, `QDRANT_URL`, `QDRANT_API_KEY`. No `OPENAI_API_KEY`. |
| `ingestion/base.py` | `BaseIngester` abstract class: `async def ingest() -> list[Document]` |
| `ingestion/github_docs.py` | Walks `gardener/documentation` via GitHub Contents API. Fetches Markdown, proposals. Returns `Document` with full metadata. |
| `ingestion/github_issues.py` | Fetches issues from `gardener/gardener` via PyGithub. Title, body, comments, labels, state. |
| `ingestion/github_prs.py` | Fetches PRs with review comments, linked issues, diff summary (not raw diff). |
| `ingestion/code_indexer.py` | Traverses Go source in `gardener/gardener`. Regex-based structural extraction (functions, types). |
| `ingestion/chunking.py` | `MarkdownChunker` and `CodeChunker`. Attaches `chunk_index`, `total_chunks`, `parent_id` to each chunk. |
| `embeddings/base.py` | `BaseEmbedder` abstract class: `async def embed_documents()` and `async def embed_query()` |
| `embeddings/openai_embedder.py` | Calls `POST /openai/v1/embeddings` on Hyperspace proxy using `openai` SDK with `base_url=HYPERSPACE_OPENAI_BASE_URL`. Batching (max 2048/call), retry via `tenacity`, configurable model and dimensions. |
| `vectorstore/base.py` | `BaseVectorStore` abstract class: upsert, search, delete, collection management. |
| `vectorstore/qdrant.py` | 4 collections with HNSW + payload indexes. Batch upsert, search, delete, health. |
| `retrieval/base.py` | `BaseRetriever` abstract class: `async def retrieve(query, filters, limit)` |
| `retrieval/semantic.py` | Embeds query → cosine similarity search → metadata filters → ranked results. |
| `retrieval/hybrid.py` | Parallel dense + sparse search → RRF score fusion → optional re-ranking. |
| `mcp/context.py` | `AppContext` (frozen Pydantic model): holds all initialized singletons. Created once in lifespan. |
| `mcp/models.py` | All MCP tool input/output Pydantic models. Field descriptions become agent-visible schema. |
| `mcp/tools.py` | 7 `@mcp.tool` async handlers: `search_docs`, `search_issues`, `search_prs`, `search_proposals`, `search_code`, `rag_retrieve`, `root_cause_analysis`. The `root_cause_analysis` tool calls the LLM via SAP Hyperspace LLM Proxy (Anthropic-compatible). |
| `mcp/server.py` | FastMCP app, lifespan, stdio/SSE transport config. |

---

## 4. Dependency List

### Runtime

| Package | Version | Purpose |
|---|---|---|
| `python` | `^3.12` | Language runtime |
| `fastmcp` | `^3.3.1` | MCP server framework |
| `qdrant-client` | `^1.18.0` | Qdrant async client |
| `openai` | `^2.40.0` | Embeddings via `POST /openai/v1/embeddings` — `base_url` set to `HYPERSPACE_OPENAI_BASE_URL` |
| `anthropic` | `^0.40.0` | LLM calls (RCA, synthesis) — pointed at SAP Hyperspace LLM Proxy via `base_url` config |
| `langchain` | `^1.3.2` | Text splitters, document models |
| `langchain-openai` | `^1.2.2` | LangChain OpenAI integration — `base_url` overridden to SAP AI Proxy |
| `langchain-qdrant` | `^1.1.0` | LangChain Qdrant integration |
| `pydantic` | `^2.13.4` | Data models, validation |
| `pydantic-settings` | `^2.7.0` | Settings from env vars |
| `PyGithub` | `^2.9.1` | GitHub REST API client |
| `httpx` | `^0.28.1` | Async HTTP |
| `tenacity` | `^9.1.4` | Retry logic |
| `tiktoken` | `^0.7.0` | Token counting for chunk sizing |

### Development

| Package | Version | Purpose |
|---|---|---|
| `pytest` | `^8.4` | Test runner |
| `pytest-asyncio` | `^1.4.0` | Async test support |
| `pytest-cov` | `^6.0` | Coverage reporting |
| `ruff` | `^0.9.0` | Linter + formatter |
| `mypy` | `^1.15.0` | Static type checker |
| `pre-commit` | `^4.2.0` | Git hooks |
| `respx` | `^0.22.0` | httpx mock |
| `pytest-mock` | `^3.14.0` | Mock fixtures |

### Infrastructure

| Component | Version | Purpose |
|---|---|---|
| Qdrant | `^1.13` | Self-hosted vector database |
| Docker | `^27` | Container runtime |
| Helm | `^3.17` | Kubernetes packaging |

---

## 5. Development Roadmap

| Phase | Goal | Exit Criteria |
|---|---|---|
| **1 - Architecture** | Abstract interfaces, data contracts, `pyproject.toml`, `models.py`, `settings.py`, empty `__init__.py` files | All abstractions compile, mypy passes on skeleton |
| **2 - Ingestion** | Implement all 4 ingesters + chunking layer. No vector writes yet. | Each ingester tested with mocked GitHub API. CLI runs against real GitHub. |
| **3 - Vector Store** | Qdrant + Hyperspace OpenAI-compatible embedder (`/openai/v1/embeddings`). End-to-end: fetch → chunk → embed → upsert. | Collections populated, queryable via dashboard. Health check passes. |
| **4 - Retrieval** | SemanticRetriever + HybridRetriever with RRF. | Relevant results for Gardener test queries. All tests pass. |
| **5 - MCP Tools** | 7 FastMCP tools, AppContext DI, stdio + SSE transport. | All tools callable via MCP. `root_cause_analysis` returns structured output. |
| **6 - Docker** | Multi-stage Dockerfile + kind cluster setup script. Helm chart used for local stack. | `setup_kind.sh` brings up full stack on kind. Image under 400MB. `trivy` scan: no critical CVEs. |
| **7 - Helm** | Production-grade Kubernetes Helm chart. | `helm lint` passes. Installs on `kind`. HPA works. |
| **8 - CI/CD** | GitHub Actions: lint, test, build, release, scheduled ingestion. | PR pipeline green. Release publishes image + chart. Ingestion runs on schedule. |

---

## 6. Architectural Decision Records

### ADR-001: Separate Qdrant Collections Per Source Type

**Decision:** Use four separate Qdrant collections: `gardener_docs`, `gardener_issues`, `gardener_prs`, `gardener_code`.

**Rationale:** Each source type has a fundamentally different metadata schema. Separate collections allow per-collection HNSW tuning and clean schema evolution without wide sparse payloads.

**Trade-off:** Cross-collection queries (used in RCA) require parallel searches and result merging, handled in `HybridRetriever`.

---

### ADR-002: Embeddings via Hyperspace OpenAI-Compatible Endpoint — RESOLVED ✅

**Status:** Decided. Hyperspace LLM Proxy exposes an OpenAI-compatible endpoint that serves `text-embedding-3-small` and `text-embedding-3-large`. No external OpenAI account or separate embedding service required.

**Reference:** https://ai-docs.portal.hyperspace.tools.sap/llm-proxy/configuration/api-endpoints/

---

**Why Claude models are NOT used for embeddings:**

Claude (`anthropic--claude-*`) models are generative LLMs. They produce text, not vectors. There is no Anthropic embedding API. Embedding models and generative models are fundamentally different tools:

| Concern | Model type | Endpoint | Used in |
|---|---|---|---|
| Vector embeddings | `text-embedding-3-small` / `text-embedding-3-large` | `POST /openai/v1/embeddings` | Ingestion, retrieval |
| Text generation / RCA | `anthropic--claude-sonnet-latest` etc. | `POST /anthropic/v1/messages` | `root_cause_analysis` tool |

---

**Why not call Claude Code directly for embeddings:**

Claude Code is a developer CLI tool, not an embedding service. Routing embedding calls through it would introduce a runtime dependency on a developer laptop tool, add latency, and provide no semantic advantage — Claude models do not produce float vectors regardless of how they are called.

---

**Confirmed API — Hyperspace OpenAI-compatible embedding endpoint:**

```
POST http://localhost:6655/openai/v1/embeddings
Authorization: Bearer <HYPERSPACE_OPENAI_API_KEY>
Content-Type: application/json
```

Example request:
```bash
curl -X POST http://localhost:6655/openai/v1/embeddings \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "text-embedding-3-small",
    "input": "The food was delicious and the waiter was very friendly."
  }'
```

Available models:
- `text-embedding-3-small` — 1536 dims, default (cost-efficient)
- `text-embedding-3-large` — 3072 dims, optional (higher precision)

---

**Env var design — two separate Hyperspace base URLs:**

The Hyperspace proxy exposes distinct paths per provider. The project uses two separate base URL env vars to keep embedding and LLM concerns independently configurable:

| Var | Path | Used for |
|---|---|---|
| `HYPERSPACE_OPENAI_BASE_URL` | `http://localhost:6655/openai/v1` | Embeddings (`openai` SDK) |
| `ANTHROPIC_BASE_URL` | `http://localhost:6655/anthropic/` | LLM calls (`anthropic` SDK) |

Both share the same bearer token (`ANTHROPIC_AUTH_TOKEN` / `GARDENER_MCP_ANTHROPIC_AUTH_TOKEN`).

`settings.py` resolution (embedding vars follow the same `GARDENER_MCP_*` prefix pattern as ADR-006):

```python
hyperspace_openai_base_url: str = Field(
    default="http://localhost:6655/openai/v1",
    validation_alias=AliasChoices(
        "GARDENER_MCP_HYPERSPACE_OPENAI_BASE_URL",
        "HYPERSPACE_OPENAI_BASE_URL",
    )
)
embedding_model: str = Field(
    default="text-embedding-3-small",
    validation_alias=AliasChoices(
        "GARDENER_MCP_EMBEDDING_MODEL",
        "EMBEDDING_MODEL",
    )
)
embedding_dimensions: int = Field(
    default=1536,
    validation_alias=AliasChoices(
        "GARDENER_MCP_EMBEDDING_DIMENSIONS",
        "EMBEDDING_DIMENSIONS",
    )
)
```

**Complete `.env.example` (embedding section):**
```dotenv
# Embeddings — Hyperspace OpenAI-compatible endpoint
GARDENER_MCP_HYPERSPACE_OPENAI_BASE_URL=http://localhost:6655/openai/v1
GARDENER_MCP_EMBEDDING_MODEL=text-embedding-3-small
GARDENER_MCP_EMBEDDING_DIMENSIONS=1536
```

**Rationale:**
- Hyperspace confirmed to serve both `text-embedding-3-small` and `text-embedding-3-large` via its OpenAI-compatible endpoint.
- The `openai` SDK can target any OpenAI-compatible endpoint via `base_url` — zero code change from standard OpenAI usage.
- Keeping embedding and LLM base URLs as separate env vars allows independent routing (e.g. different Hyperspace deployments per environment) without coupling.
- `text-embedding-3-small` chosen as default: lower cost, 1536 dims sufficient for documentation search recall.

---

### ADR-003: FastMCP Over Raw MCP SDK

**Decision:** Use FastMCP as the MCP server framework.

**Rationale:** Provides decorator-based tool registration, automatic Pydantic schema generation, and stdio/SSE transport abstraction. Eliminates significant boilerplate.

---

### ADR-004: Dependency Injection via AppContext

**Decision:** All singletons (Qdrant client, Anthropic client, retrievers) are created once in the FastMCP lifespan function and held in a frozen `AppContext` Pydantic model.

**Rationale:** No module-level globals. Every component is testable in isolation by substituting test doubles without patching internals.

---

### ADR-005: Hybrid Retrieval as Default for Root Cause Analysis

**Decision:** `root_cause_analysis` tool uses `HybridRetriever` (RRF fusion of dense + sparse) across all four collections.

**Rationale:** RCA queries combine semantic concepts ("shoot cluster crash") with exact technical terms ("DNSRecord controller", "error code 503"). RRF fusion ensures both dimensions are captured without requiring query reformulation by the agent.

---

### ADR-006: All LLM Calls Routed via SAP Hyperspace LLM Proxy

**Decision:** All LLM calls (RCA synthesis, future agent steps) are routed through the **SAP Hyperspace LLM Proxy**. The `anthropic` Python SDK is configured with `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`. No direct calls are ever made to `api.anthropic.com`.

**Env var resolution — prefixed vars take priority:**

The MCP server supports its own `GARDENER_MCP_*` prefixed variables. This allows independent control even when the ambient Claude Code env vars (`ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, etc.) are already set in the same shell session. `pydantic-settings` resolves in this order:

```
GARDENER_MCP_ANTHROPIC_BASE_URL     →  fallback: ANTHROPIC_BASE_URL
GARDENER_MCP_ANTHROPIC_AUTH_TOKEN   →  fallback: ANTHROPIC_AUTH_TOKEN
GARDENER_MCP_ANTHROPIC_MODEL        →  fallback: ANTHROPIC_MODEL
GARDENER_MCP_API_TIMEOUT_MS         →  fallback: API_TIMEOUT_MS
```

If neither the prefixed nor the unprefixed var is set, startup fails with a clear validation error.

**Supported models (via Hyperspace):**

| Env var | Model ID | Use |
|---|---|---|
| `GARDENER_MCP_ANTHROPIC_MODEL` / `ANTHROPIC_MODEL` | `anthropic--claude-sonnet-latest` | Default for all LLM calls |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `anthropic--claude-sonnet-latest` | Balanced quality/cost |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `anthropic--claude-haiku-latest` | Fast / low-cost tasks |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | `anthropic--claude-opus-latest` | Highest quality (deep RCA) |

**Implementation:** The `anthropic.AsyncAnthropic` client is instantiated in `AppContext` with:
```python
client = anthropic.AsyncAnthropic(
    base_url=settings.anthropic_base_url,   # resolved from GARDENER_MCP_ prefix or fallback
    api_key=settings.anthropic_auth_token,  # resolved from GARDENER_MCP_ prefix or fallback
)
```

`settings.py` resolution pattern (pseudocode):
```python
class Settings(BaseSettings):
    anthropic_base_url: str = Field(
        validation_alias=AliasChoices(
            "GARDENER_MCP_ANTHROPIC_BASE_URL",
            "ANTHROPIC_BASE_URL",
        )
    )
    anthropic_auth_token: str = Field(
        validation_alias=AliasChoices(
            "GARDENER_MCP_ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_AUTH_TOKEN",
        )
    )
    anthropic_model: str = Field(
        default="anthropic--claude-sonnet-latest",
        validation_alias=AliasChoices(
            "GARDENER_MCP_ANTHROPIC_MODEL",
            "ANTHROPIC_MODEL",
        )
    )
    api_timeout_ms: int = Field(
        default=3000000,
        validation_alias=AliasChoices(
            "GARDENER_MCP_API_TIMEOUT_MS",
            "API_TIMEOUT_MS",
        )
    )
```

**Local development setup (`.env`):**
```dotenv
# Option A: reuse existing Claude Code Hyperspace vars (zero extra config)
# Leave these unset and the MCP server will pick up the ambient vars automatically.

# Option B: override independently for this MCP server only
GARDENER_MCP_ANTHROPIC_BASE_URL=http://localhost:6655/anthropic/
GARDENER_MCP_ANTHROPIC_AUTH_TOKEN=<your-hyperspace-bearer-token>
GARDENER_MCP_ANTHROPIC_MODEL=anthropic--claude-sonnet-latest
GARDENER_MCP_API_TIMEOUT_MS=3000000

# Fallback vars (used if GARDENER_MCP_* are not set — same as Claude Code setup)
# ANTHROPIC_BASE_URL=http://localhost:6655/anthropic/
# ANTHROPIC_AUTH_TOKEN=<your-hyperspace-bearer-token>
# ANTHROPIC_MODEL=anthropic--claude-sonnet-latest
# ANTHROPIC_DEFAULT_SONNET_MODEL=anthropic--claude-sonnet-latest
# ANTHROPIC_DEFAULT_HAIKU_MODEL=anthropic--claude-haiku-latest
# ANTHROPIC_DEFAULT_OPUS_MODEL=anthropic--claude-opus-latest
# API_TIMEOUT_MS=3000000

# GitHub
GITHUB_TOKEN=<your-github-pat>

# Qdrant (local via kind cluster or cloud K8s)
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=

# Embedding
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

**Rationale:**
- All LLM traffic remains within SAP-managed infrastructure via the Hyperspace proxy.
- No direct dependency on a public Anthropic API key — reduces secret sprawl and external exposure.
- `GARDENER_MCP_*` prefix gives independent control per deployment (e.g. point staging MCP server at a different Hyperspace deployment than the developer's Claude Code session).
- Fallback to unprefixed `ANTHROPIC_*` vars means a developer already configured for Claude Code + Hyperspace needs zero extra config to run locally.
- Model selection is runtime-configurable without code changes.
- `API_TIMEOUT_MS` is honoured for long-running RCA calls which may exceed default HTTP timeouts.

**Trade-off:** The Hyperspace token has a shorter TTL than a static API key. Token refresh for long-running Kubernetes deployments must be handled via a sidecar or mounted secret with rotation.
