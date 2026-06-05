# Gardener AI MCP

A production-grade MCP (Model Context Protocol) server for the [Gardener](https://gardener.cloud) Kubernetes project.

Enables AI agents to search Gardener documentation, GitHub issues, pull requests, proposals, and source code — and perform root cause analysis via RAG retrieval backed by Qdrant and the SAP Hyperspace LLM Proxy.

## Features

- **`search_docs`** — semantic search over Gardener documentation
- **`search_issues`** — search GitHub issues from `gardener/gardener`
- **`search_prs`** — search GitHub pull requests
- **`search_proposals`** — search Gardener Enhancement Proposals (GEPs)
- **`search_code`** — search Go source code
- **`rag_retrieve`** — RAG retrieval across any collection
- **`root_cause_analysis`** — hybrid retrieval + LLM synthesis via Claude

## Quick Start

```bash
cp .env.example .env
# Fill in ANTHROPIC_AUTH_TOKEN and GITHUB_TOKEN
uv sync
uv run python -m gardener_mcp.server
```

## Local kind Cluster

```bash
bash scripts/setup_kind.sh
```

See [docs/deploy-local.md](docs/deploy-local.md) for the full guide.

## Architecture

See [docs/adr/001_phase1_architecture.md](docs/adr/001_phase1_architecture.md) for the full architecture decision record.
