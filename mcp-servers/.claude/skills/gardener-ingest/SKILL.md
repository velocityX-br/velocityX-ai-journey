---
name: gardener-ingest
description: Ad-hoc incremental data ingestion for the locally-running gardener-ai-mcp Qdrant vector store. Refreshes the latest Gardener documentation, GitHub issues, PRs, and code into the local Qdrant instance. Use when the user says "ingest gardener", "refresh gardener docs/PRs/issues", "update gardener vector store", "run gardener ingestion", "pull latest gardener data", or wants the gardener-ai-mcp RAG index brought up to date.
---

# Gardener Incremental Ingestion

Run an ad-hoc incremental ingestion into the **locally-running** `gardener-ai-mcp` Qdrant vector store to bring the RAG index up to date with the latest upstream Gardener documentation, GitHub PRs, issues, and code.

**Server directory (fixed):**
`/Users/I577081/Workdir/Github/veloxityX-ai-journey/mcp-servers/gardener-ai-mcp`

All commands run from that directory. The vector store is a **local Qdrant** at `QDRANT_URL` (default `http://localhost:6333`) with four collections:

| Alias    | Collection         | Source                             |
| -------- | ------------------ | ---------------------------------- |
| `docs`   | `gardener_docs`    | `gardener/documentation` (.md)     |
| `issues` | `gardener_issues`  | `gardener/gardener` issues         |
| `prs`    | `gardener_prs`     | `gardener/gardener` pull requests  |
| `code`   | `gardener_code`    | `gardener/gardener` (.go)          |

The CLI entry point is `scripts/ingest_docs.py`, run via `uv`.

---

## Ingestion semantics (READ FIRST — important)

Ingestion is **append-mostly, NOT idempotent**. Point IDs are random UUIDs per run (`ingestion/base.py`), so re-running does NOT overwrite unchanged content:

- Upstream **adds** a doc → ✅ new content indexed.
- Upstream **updates** a doc → ⚠️ old + new chunks BOTH exist (duplicates).
- Upstream **deletes** a doc → ❌ stale vectors remain.
- Re-running the same ingest → ⚠️ collection point count keeps growing.

Two workflows follow from this. **Always ask the user which they want** if it's not obvious:

- **Mode A — Append (fast):** just run the ingest. Tolerates duplicates. Good for a quick top-up of newly-added content.
- **Mode B — Clean rebuild (consistent):** delete the target collection(s) first, then ingest. Guarantees the collection matches upstream. Recommended for `docs`, `prs`, and `issues` since they are relatively small. **Deleting a collection is destructive — always confirm the exact collection names with the user before deleting.**

---

## Workflow

### 1. Confirm scope with the user

Determine, and if unclear ask:
- **Which collections?** Default target for this skill is `docs prs` (docs + pull requests), since PRs are actively used for this repo. The user may also request `issues` and/or `code`, or "everything".
- **Which mode?** Append (Mode A) or clean rebuild (Mode B). If they want the index to exactly match upstream, use Mode B.

### 2. Preflight checks (no side effects)

```bash
cd /Users/I577081/Workdir/Github/veloxityX-ai-journey/mcp-servers/gardener-ai-mcp

# Local Qdrant reachable?
curl -sf http://localhost:6333/collections >/dev/null && echo "Qdrant OK" || echo "Qdrant UNREACHABLE"

# Record current point counts (baseline for before/after comparison)
uv run python scripts/ingest_docs.py --check
```

- If Qdrant is unreachable, stop and tell the user — the local vector store must be running first. Do NOT attempt to start containers unless the user asks.
- If `uv sync` has never been run, run it once: `uv sync`.
- Ingestion needs `GITHUB_TOKEN` (or `GARDENER_MCP_GITHUB_TOKEN`) and the embedding endpoint (`HYPERSPACE_OPENAI_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`) configured in `.env`. If a run fails on auth/rate-limit, surface the error rather than retrying blindly.

### 3a. Mode A — Append (default, fast)

```bash
uv run python scripts/ingest_docs.py --collections docs prs
```

Adjust the alias list to whatever the user asked for, e.g.:

```bash
uv run python scripts/ingest_docs.py --collections docs          # docs only
uv run python scripts/ingest_docs.py --collections docs prs issues
uv run python scripts/ingest_docs.py                             # all four collections
```

### 3b. Mode B — Clean rebuild (consistent) — CONFIRM BEFORE DELETING

```bash
# 1. Delete ONLY the collections the user confirmed (destructive):
curl -X DELETE http://localhost:6333/collections/gardener_docs
curl -X DELETE http://localhost:6333/collections/gardener_prs

# 2. Reingest (collections are auto-recreated with correct HNSW/cosine config):
uv run python scripts/ingest_docs.py --collections docs prs
```

Map aliases → collection names for deletion:
`docs → gardener_docs`, `issues → gardener_issues`, `prs → gardener_prs`, `code → gardener_code`.

### 4. Verify

```bash
uv run python scripts/ingest_docs.py --check
```

Compare against the baseline from step 2 and report to the user:
- Which collections were ingested and in which mode.
- Point-count before → after per collection.
- Documents fetched / chunks produced / vectors upserted (from the run summary).

---

## Rules

- Operate **only** within `mcp-servers/gardener-ai-mcp`. Never run `ingest_docs.py` from any other directory.
- The target vector store is the **local** Qdrant; never point at a remote/prod instance unless the user explicitly changes `QDRANT_URL`.
- **Always confirm collection names before any `DELETE`** — deletion is irreversible and stale/wrong deletion loses indexed data.
- Prefer **Mode A (append)** for quick top-ups; use **Mode B (clean rebuild)** when the user needs exact consistency with upstream.
- Always run `--check` before and after so the user sees the delta.
- Do not hardcode or echo secrets from `.env`. If auth/token errors occur, report them and stop.
- Do not modify ingestion source code as part of this skill — this skill only runs the existing pipeline.

## What this skill does NOT do

- Does not stand up / start Qdrant or the embedding endpoint (assumes they are already running locally).
- Does not implement true idempotent/delta ingestion (not supported by the current pipeline).
- Does not delete any collection without explicit user confirmation.
- Does not ingest SAP GitHub Enterprise issues — that is a separate script (`scripts/ingest_sap_github.py`) outside this skill's scope.
