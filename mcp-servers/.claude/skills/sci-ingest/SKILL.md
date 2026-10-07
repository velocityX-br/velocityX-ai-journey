---
name: sci-ingest
description: Ad-hoc incremental documentation ingestion for the locally-running sci-ai-mcp Qdrant vector store. Refreshes the latest SCI operation and customer documentation into the local Qdrant instance (docs only — PRs/issues are not used for this repo). Use when the user says "ingest sci", "refresh sci docs", "update sci vector store", "run sci ingestion", "pull latest sci docs", or wants the sci-ai-mcp RAG index brought up to date.
---

# SCI Incremental Documentation Ingestion

Run an ad-hoc incremental ingestion into the **locally-running** `sci-ai-mcp` Qdrant vector store to bring the RAG index up to date with the latest SCI (SAP Converged Infrastructure) documentation. This repo ingests **documentation only** — there are no PR/issue/code collections, because PRs are not actively used for the SCI documentation repositories.

**Server directory (fixed):**
`/Users/I577081/Workdir/Github/veloxityX-ai-journey/mcp-servers/sci-ai-mcp`

All commands run from that directory. The vector store is a **local Qdrant** at `QDRANT_URL` (default `http://localhost:6333`) with two documentation collections:

| Alias       | Collection            | Source (SAP GHE)                 |
| ----------- | --------------------- | -------------------------------- |
| `operation` | `sci_docs_operation`  | `cc/documentation-operation` (.md) |
| `customer`  | `sci_docs_customer`   | `cc/documentation-customer` (.md)  |

The CLI entry point is `scripts/ingest_docs.py`, run via `uv`.

---

## Ingestion semantics (READ FIRST — important)

sci-ai-mcp ingestion is **full re-fetch, NOT delta-aware**. Each run re-fetches ALL markdown from GitHub, re-chunks, re-embeds, and upserts. Point IDs are UUIDs, so re-running can leave duplicate/stale vectors (especially if a prior run was interrupted mid-way and left partial WAL-persisted points).

Two workflows follow from this. **Ask the user which they want** if it's not obvious:

- **Mode A — Re-ingest in place (fast to start):** just run the ingest. Simplest, but if a previous run was interrupted the collection count may end up inflated (~2× on the affected pass).
- **Mode B — Clean rebuild (recommended for consistency):** delete the target collection(s) first, then ingest. Guarantees the collection matches upstream and avoids duplicate accumulation. **Deleting a collection is destructive — always confirm the exact collection names with the user before deleting.**

Because the doc collections are small (`operation` ~16k points, `customer` ~5.3k points), **Mode B clean rebuild is usually the right choice**.

---

## Workflow

### 1. Confirm scope with the user

Determine, and if unclear ask:
- **Which collections?** Default is `operation customer` (both). The user may want just one.
- **Which mode?** Re-ingest in place (Mode A) or clean rebuild (Mode B). If they want the index to exactly match upstream, use Mode B.

### 2. Preflight checks (no side effects)

```bash
cd /Users/I577081/Workdir/Github/veloxityX-ai-journey/mcp-servers/sci-ai-mcp

# Local Qdrant reachable?
curl -sf http://localhost:6333/collections >/dev/null && echo "Qdrant OK" || echo "Qdrant UNREACHABLE"

# Record current point counts (baseline for before/after comparison)
uv run python scripts/ingest_docs.py --check
```

- If Qdrant is unreachable, stop and tell the user — the local vector store must be running first. Do NOT attempt to start containers unless the user asks.
- If `uv sync` has never been run, run it once: `uv sync`.
- Ingestion needs `GITHUB_TOKEN` with access to the SAP GHE repos, `GITHUB_BASE_URL` (default `https://github.wdf.sap.corp/api/v3`), the SAP CA bundle (`GITHUB_CA_BUNDLE`), and the embedding endpoint (`HYPERSPACE_OPENAI_BASE_URL` + token) — all in `.env`. If a run fails on auth/TLS/rate-limit, surface the error rather than retrying blindly.

### 3a. Mode A — Re-ingest in place (fast to start)

```bash
uv run python scripts/ingest_docs.py --collections operation customer
```

Adjust the alias list to whatever the user asked for, e.g.:

```bash
uv run python scripts/ingest_docs.py --collections operation   # operation docs only
uv run python scripts/ingest_docs.py --collections customer    # customer docs only
```

### 3b. Mode B — Clean rebuild (recommended) — CONFIRM BEFORE DELETING

```bash
# 1. Delete ONLY the collections the user confirmed (destructive):
curl -X DELETE http://localhost:6333/collections/sci_docs_operation
curl -X DELETE http://localhost:6333/collections/sci_docs_customer

# 2. Reingest (collections are auto-recreated with correct HNSW/cosine config):
uv run python scripts/ingest_docs.py --collections operation customer
```

Map aliases → collection names for deletion:
`operation → sci_docs_operation`, `customer → sci_docs_customer`.

### 4. Verify

```bash
uv run python scripts/ingest_docs.py --check
```

Compare against the baseline from step 2 and report to the user:
- Which collections were ingested and in which mode.
- Point-count before → after per collection (rough expected sizes: operation ~16k, customer ~5.3k).
- Documents fetched / chunks produced / vectors upserted (from the run summary).

---

## Rules

- Operate **only** within `mcp-servers/sci-ai-mcp`. Never run `ingest_docs.py` from any other directory.
- **Docs only.** This repo has no PR/issue/code collections; do not attempt to ingest anything other than `operation` / `customer`.
- The target vector store is the **local** Qdrant; never point at a remote/prod instance unless the user explicitly changes `QDRANT_URL`.
- **Always confirm collection names before any `DELETE`** — deletion is irreversible.
- Prefer **Mode B (clean rebuild)** here since the collections are small and full re-fetch is the only supported mode; use **Mode A** only for a quick unverified top-up.
- Always run `--check` before and after so the user sees the delta; a big unexpected jump can indicate duplicate accumulation from an interrupted prior run (fix via clean rebuild).
- Do not hardcode or echo secrets from `.env`. If auth/TLS/token errors occur, report them and stop.
- Do not modify ingestion source code as part of this skill — this skill only runs the existing pipeline.

## What this skill does NOT do

- Does not stand up / start Qdrant or the embedding endpoint (assumes they are already running locally).
- Does not ingest PRs, issues, or source code (not applicable to the SCI documentation repos).
- Does not implement true idempotent/delta ingestion (not supported by the current pipeline).
- Does not delete any collection without explicit user confirmation.
