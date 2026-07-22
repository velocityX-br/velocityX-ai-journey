# SCI AI MCP

An MCP (Model Context Protocol) server for **SCI** (SAP Converged Infrastructure) documentation.

Enables AI agents to perform semantic search and root cause analysis over two SAP GitHub Enterprise documentation repositories — backed by RAG retrieval over Qdrant and the SAP Hyperspace LLM Proxy.

## Data Sources

- **Operation docs** — `https://github.wdf.sap.corp/cc/documentation-operation`
- **Customer docs** — `https://github.wdf.sap.corp/cc/documentation-customer`

Both repositories are ingested from SAP GitHub Enterprise (`github.wdf.sap.corp`) into two separate Qdrant collections:

| Repository                  | Collection            |
| --------------------------- | --------------------- |
| `cc/documentation-operation`| `sci_docs_operation`  |
| `cc/documentation-customer` | `sci_docs_customer`   |

## Features

- **`search_operation_docs`** — semantic search over the operation documentation
- **`search_customer_docs`** — semantic search over the customer documentation
- **`search_docs`** — semantic search across both documentation sets (hybrid RRF fusion)
- **`rag_retrieve`** — RAG retrieval against a named collection
- **`root_cause_analysis`** — hybrid retrieval + LLM synthesis via Claude

## Quick Start

```bash
cp .env.example .env
# Fill in ANTHROPIC_AUTH_TOKEN and a real SAP GitHub Enterprise PAT (GITHUB_TOKEN)
uv sync
uv run python -m sci_mcp.server
```

## Ingestion

Requires a running Qdrant instance and a valid SAP GitHub Enterprise PAT.

```bash
# Ingest both collections
uv run ingest-docs --collections operation customer

# Verify collection counts
uv run ingest-docs --check
```

## Architecture

```
Documentation (github.wdf.sap.corp)
  → Ingestion  (GitHub Enterprise Contents API)
  → Chunking   (LangChain MarkdownTextSplitter)
  → Embedding  (Hyperspace OpenAI-compatible, text-embedding-3-small)
  → Qdrant     (sci_docs_operation / sci_docs_customer)
  → Retrieval  (SemanticRetriever / HybridRetriever + RRF)
  → MCP Server (FastMCP, stdio / sse transport)
```

Modelled on the reference `gardener-ai-mcp` project. Scoped to **pure documentation RAG** — no issues, PRs, or source-code ingestion.
