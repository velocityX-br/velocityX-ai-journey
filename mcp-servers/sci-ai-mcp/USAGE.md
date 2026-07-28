# sci-ai-mcp — Usage Process & Guidelines

This document defines the **standard process** for running, feeding, and consuming
`sci-ai-mcp`. It is the operational companion to [`README.md`](./README.md) (which
covers the "what") — this file covers the "how" and "when".

`sci-ai-mcp` is a **documentation-RAG** MCP server: it answers questions from the
SCI (SAP Converged Infrastructure) operation and customer documentation. It does
**not** ingest issues, PRs, or source code.

---

## 1. Prerequisites

Before doing anything, confirm the three external dependencies are reachable:

| Dependency            | Purpose                          | Default endpoint                    | Verify |
| --------------------- | -------------------------------- | ----------------------------------- | ------ |
| **Qdrant**            | Vector store (the RAG corpus)    | `http://localhost:6333`             | `curl -s localhost:6333/healthz` → `healthz check passed` |
| **Hyperspace (OpenAI)** | Embeddings for query + ingestion | `http://localhost:6655/openai/v1`   | needed for **every** query — server is useless without it |
| **Hyperspace (Anthropic)** | LLM for `root_cause_analysis`  | `http://localhost:6655/anthropic/`  | only needed by the one synthesis tool |
| **SAP GHE PAT**       | Fetching docs during ingestion   | `github.wdf.sap.corp`               | only needed for `ingest-docs`, **not** for serving |

Rule of thumb:
- **Serving queries** needs: Qdrant (with data) + Hyperspace embeddings.
- **Ingesting docs** additionally needs: a valid SAP GitHub Enterprise PAT + the SAP CA bundle.

---

## 2. One-time setup

```bash
cd mcp-servers/sci-ai-mcp

# 1. Config
cp .env.example .env
#    Fill in at minimum:
#      ANTHROPIC_AUTH_TOKEN   (Hyperspace bearer token)
#      GITHUB_TOKEN           (real SAP GHE PAT — only if you will ingest)
#      GITHUB_CA_BUNDLE       (SAP CA chain — see .env.example for the build recipe)

# 2. Dependencies
uv sync

# 3. Sanity check dependencies are up
uv run python scripts/healthcheck.py    # probes Qdrant /healthz; exit 0 = ok
```

> **Never commit `.env`.** It is git-ignored. Secrets go in `.env` only.
> `SCI_MCP_*`-prefixed vars always win over ambient `ANTHROPIC_*` vars, so this
> server can be configured independently of the Claude Code CLI's own env.

---

## 3. Ingestion workflow (feeding the corpus)

Ingestion is a **deliberate, occasional** operation — run it to bootstrap a fresh
Qdrant or to refresh docs. It is **not** part of normal serving.

```bash
# Ingest both collections from github.wdf.sap.corp
uv run ingest-docs --collections operation customer

# Or a single collection
uv run ingest-docs --collections operation

# Always verify counts afterward
uv run ingest-docs --check
```

### Guidelines

- **Re-ingesting is not idempotent by default.** A previous *interrupted* run can
  leave WAL-persisted vectors behind; re-running then **adds a second pass** and
  you end up with ~2× the expected point count. If `--check` shows roughly double
  the chunk count, the collection is dirty.
- **To get a clean single pass**, delete the collection first, then re-ingest:
  ```bash
  curl -X DELETE localhost:6333/collections/sci_docs_customer
  uv run ingest-docs --collections customer
  uv run ingest-docs --check     # confirm count == chunk count of this run
  ```
- **Expected collections & rough sizes** (dims=1536, Cosine, float32):
  | Collection            | Repo                          | ~Points |
  | --------------------- | ----------------------------- | ------- |
  | `sci_docs_operation`  | `cc/documentation-operation`  | ~16k    |
  | `sci_docs_customer`   | `cc/documentation-customer`   | ~5.3k   |
- **TLS gotcha:** `github.wdf.sap.corp` is signed by the internal SAP Global Root
  CA, not in certifi. Without `GITHUB_CA_BUNDLE` you get
  `SSLCertVerificationError: self-signed certificate in certificate chain`.

---

## 4. Running the server

```bash
# stdio transport (default) — for MCP clients that spawn the process
uv run python -m sci_mcp.server

# SSE / network transport — expose as an HTTP service
SCI_MCP_TRANSPORT=sse SCI_MCP_HOST=0.0.0.0 SCI_MCP_PORT=8080 \
  uv run python -m sci_mcp.server
```

- **stdio** is the norm for Claude Desktop / Claude Code (client owns the lifecycle).
- **sse** is for shared/hosted deployments (multiple clients over the network).
- The server boots and serves **cached vectors even without a GitHub PAT** — the PAT
  is an ingestion-time concern only.

### Registering with an MCP client (stdio)

```json
{
  "mcpServers": {
    "sci-ai-mcp": {
      "command": "uv",
      "args": ["run", "python", "-m", "sci_mcp.server"],
      "cwd": "/absolute/path/to/mcp-servers/sci-ai-mcp"
    }
  }
}
```

---

## 5. Choosing the right tool

Five tools are exposed. Pick by intent:

| Tool                    | Use when…                                                        | Backing |
| ----------------------- | ---------------------------------------------------------------- | ------- |
| `search_operation_docs` | You know the answer is in **operation** docs                     | dense search on `sci_docs_operation` |
| `search_customer_docs`  | You know the answer is in **customer** docs                      | dense search on `sci_docs_customer` |
| `search_docs`           | **Default for search.** Unsure which set; want one merged ranking | dense-per-collection + **RRF fusion** across both |
| `rag_retrieve`          | You need a raw retrieval against **one named** collection        | low-level `SemanticRetriever` |
| `root_cause_analysis`   | You have a **symptom** and want a synthesized RCA, not raw chunks | hybrid retrieval → Claude synthesis |

Guidelines:
- **Prefer `search_docs`** as the general entry point — it spans both corpora and
  returns a single ranked list.
- Use the **targeted** `search_*_docs` tools only when you're confident which corpus
  applies; they're marginally cheaper and avoid cross-corpus noise.
- `root_cause_analysis` is the **only** tool that calls the LLM (and thus the only
  one requiring the Anthropic endpoint). It costs a model call — use it for
  investigation/summarization, not for plain lookups.
- `filters` on the search tools are **exact-match key/value** payload filters,
  AND-combined (e.g. `{"content_type": "operation"}`). Do **not** pass free-text or
  `$text`-style operators — Qdrant rejects unknown JSON paths.
- `limit` is 1–50 for search tools (default 10) and 1–20 for `root_cause_analysis`
  (default 5, per-collection).

---

## 6. Operational conventions

- **Caching:** tool results are cached in-process (TTL + LRU) when configured via
  `tool_cache_ttl_seconds` / `tool_cache_max_size`. Identical calls within the TTL
  return the cached result — expect stale-but-fast behavior after ingestion until
  the TTL elapses or the server restarts.
- **Health:** `scripts/healthcheck.py` is stdlib-only and probes Qdrant `/healthz`.
  Wire it into container `HEALTHCHECK`, a `brew services` readiness gate, or run it
  manually. Exit `0` = healthy.
- **Restart after ingestion** if you want caches cleared and fresh counts reflected
  immediately.

---

## 7. Development guidelines

Follow the repo's `CLAUDE.md` conventions:

- **Dependency injection:** retrievers receive a pre-built `BaseEmbedder` +
  `BaseVectorStore`; they take **no** `Settings` dependency. Keep it that way so
  they stay portable across test and prod.
- **Async everywhere** for I/O paths (embedding, vector search, LLM calls).
- **Type hints + docstrings** on public functions; keep modules focused (no
  monoliths).
- **Never hardcode secrets.** All config flows through `config/settings.py` and env.
- **Quality gates before commit:**
  ```bash
  uv run ruff check .
  uv run ruff format .
  uv run mypy .
  uv run pytest          # coverage gate: fail_under = 70
  ```
- **Clean up temp/smoke scripts** before committing (e.g. anything under
  `scripts/_smoke_*.py`). Verify with `git status` / a `git add --dry-run` before
  the commit.

---

## 8. Troubleshooting quick table

| Symptom                                              | Likely cause / fix |
| ---------------------------------------------------- | ------------------ |
| `SSLCertVerificationError` during ingestion          | Missing/incorrect `GITHUB_CA_BUNDLE` — see `.env.example` recipe |
| `400 Invalid json path: '$text'`                     | Passing a free-text/`$text` filter — filters are exact-match only |
| Collection count ~2× expected                        | Dirty double-ingest — DELETE the collection and re-ingest cleanly |
| Server boots but every search returns empty          | Collection empty or wrong Qdrant URL — run `ingest-docs --check` |
| `root_cause_analysis` errors, searches work          | Anthropic endpoint/token misconfigured (`ANTHROPIC_BASE_URL`/`_AUTH_TOKEN`) |
| Stale results right after ingestion                  | Tool cache TTL — wait it out or restart the server |
