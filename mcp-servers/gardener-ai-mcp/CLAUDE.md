# Gardener AI MCP

## Mission

Build a production-grade MCP server for Gardener.

The MCP server must allow AI agents to:

- Search Gardener Documentation
- Search GitHub Issues
- Search GitHub Pull Requests
- Search Proposals
- Search Source Code
- Perform RAG retrieval
- Perform Root Cause Analysis

---

## Data Sources

Primary:

https://github.com/gardener/documentation

Secondary:

https://github.com/gardener/gardener

Future:

- gardener-extension-provider-openstack
- gardener-extension-provider-aws
- gardener-extension-provider-gcp

---

## Architecture

Documentation
→ Ingestion
→ Chunking
→ Embedding
→ Qdrant
→ Retrieval
→ MCP Server

---

## Required Project Structure

gardener-ai-mcp/

├── ingestion/
│   ├── github_docs.py
│   ├── github_issues.py
│   ├── github_prs.py
│   └── code_indexer.py
│
├── vectorstore/
│   └── qdrant.py
│
├── retrieval/
│   ├── semantic.py
│   └── hybrid.py
│
├── mcp/
│   ├── tools.py
│   └── server.py
│
├── docker/
├── helm/
├── tests/
└── docs/

---

## Technology

Python 3.12

FastMCP
Qdrant
OpenAI Embeddings
LangChain

---

## Coding Rules

Always:

- create tests
- create type hints
- create docstrings
- use dependency injection
- use async where possible

Never:

- hardcode secrets
- create monolithic files
- skip tests

---

## Development Process

Phase 1:
Architecture

Phase 2:
Ingestion

Phase 3:
Vector Store

Phase 4:
Retrieval

Phase 5:
MCP Tools

Phase 6:
Docker

Phase 7:
Helm

Phase 8:
CI/CD

Always work phase-by-phase.
Never generate the entire project in a single response.
