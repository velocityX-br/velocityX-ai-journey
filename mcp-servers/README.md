# MCP Servers

This directory contains a collection of [Model Context Protocol (MCP)](https://modelcontextprotocol.io) servers built to supercharge your Claude Code workflow at SAP. Each server gives Claude direct access to internal systems and knowledge bases — no copy-pasting, no tab-switching, no context loss.

> **New to MCP?** MCP servers are extensions that connect Claude Code to real tools, APIs, and data sources. Once registered, Claude can call them automatically as part of any conversation.

---

## Available Servers

### `gardener-ai-mcp` — Gardener Knowledge Assistant

A production-grade RAG (Retrieval-Augmented Generation) MCP server for the [Gardener](https://gardener.cloud) Kubernetes project. It indexes documentation, GitHub issues, pull requests, enhancement proposals (GEPs), and Go source code into a Qdrant vector database — and makes all of it searchable from Claude Code.

**Why you want this:** Instead of manually searching GitHub or reading through docs, ask Claude directly. It searches across all Gardener knowledge sources simultaneously and can synthesise a structured root cause analysis when your Shoot cluster misbehaves.

| Tool | What it does |
|---|---|
| `search_docs` | Semantic search across Gardener documentation |
| `search_issues` | Search GitHub issues (filterable by state/labels) |
| `search_prs` | Search pull requests (filterable by state) |
| `search_proposals` | Search Gardener Enhancement Proposals (GEPs) |
| `search_code` | Search Go source code (filterable by repo) |
| `rag_retrieve` | Low-level RAG retrieval against a named collection |
| `root_cause_analysis` | Hybrid search + LLM synthesis → root cause + evidence + remediation steps |

**Example prompts:**
- *"Why is my Shoot cluster stuck in Reconciling?"*
- *"Find GEPs related to worker pool autoscaling"*
- *"Search Gardener issues about DNS provider failures"*

---

### `sap-wiki-mcp` — SAP Confluence Wiki

A TypeScript MCP server that connects Claude Code to your SAP Confluence spaces. Read, search, create, and update wiki pages without ever leaving your terminal.

**Why you want this:** Your team's documentation lives in Confluence. Now Claude can read it, reference it in answers, and even draft new pages for you — all inline in your coding session.

| Tool | What it does |
|---|---|
| `list_pages` | List pages across all configured spaces |
| `list_spaces` | List configured Confluence spaces |
| `get_page` | Fetch full content of a page by ID |
| `search_pages` | Search by title across one or all spaces |
| `get_child_pages` | Navigate parent-child page hierarchy |
| `create_page` | Create a new wiki page |
| `update_page` | Update an existing page |
| `clear_cache` / `cache_stats` / `refresh_page_cache` | Cache management |

**Example prompts:**
- *"Find the on-boarding guide in the DEV space"*
- *"What does the architecture page say about the payment service?"*
- *"Create a new page summarising today's design decisions"*

---

### `plato-mcp` — CIA Plato AI Agent

A Python MCP server that pipes Claude Code directly into the [CIA Plato AI Agent](https://portal.cia.net.sap) — SAP's internal AI assistant for systems investigation, architecture questions, and incident analysis. Authentication via OAuth 2.0 / SAP IAS SSO is handled automatically.

**Why you want this:** Plato has deep knowledge of SAP internal systems that Claude alone doesn't have. With this server, you can combine Claude's coding capabilities with Plato's SAP-specific expertise in a single conversation.

| Tool | What it does |
|---|---|
| `plato_query` | Send a question to the Plato backend; choose model and agent adapter |
| `refresh_cia_token` | Force a browser-based SSO token refresh |

**Example prompts:**
- *"Ask Plato to investigate the failure in UUID 27E7E8420BEF11F1BE56A38E4828B749"*
- *"Use Plato to explain the architecture of the inventory system"*
- *"Query Plato: what services are running on host xyz?"*

---

## Quick Registration

Each server has its own installation guide. The general pattern for adding a server to Claude Code is:

```bash
# Node.js server (sap-wiki-mcp)
claude mcp add --scope user sap-wiki -- node /path/to/sap-wiki-mcp/dist/server.js

# Python server (plato-mcp, gardener-ai-mcp)
claude mcp add --scope user plato -- python3 /path/to/plato-mcp/server.py

# Verify all servers are connected
claude mcp list
```

See each server's individual `README.md` for full setup instructions, environment variables, and troubleshooting.

---

## Server Overview

| Server | Language | Primary Use | Auth |
|---|---|---|---|
| `gardener-ai-mcp` | Python 3.12 | Gardener docs / issues / code search + RCA | GitHub token + SAP Hyperspace |
| `sap-wiki-mcp` | TypeScript / Node.js | SAP Confluence read/write | Confluence API token |
| `plato-mcp` | Python 3.10+ | SAP internal system investigation via Plato | SAP IAS OAuth SSO (auto) |
