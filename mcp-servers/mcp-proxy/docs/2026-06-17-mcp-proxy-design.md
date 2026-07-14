# MCP Proxy — Design Spec

**Date:** 2026-06-17
**Status:** Approved
**Tech stack:** TypeScript / Node.js

---

## Overview

A standalone MCP proxy that sits between AI clients (e.g., Claude Code) and multiple backend MCP servers. It dynamically routes requests to the appropriate backend(s) based on tool name matching, configuration rules, and optionally LLM-based semantic pruning for large tool sets.

---

## Goals

- Expose a single MCP endpoint (stdio + SSE) to AI clients
- Expose an HTTP REST API for direct tool invocation and tool discovery
- Dynamically connect to backend MCP servers on demand (lazy loading)
- Route requests via: exact tool name match → config rules → optional LLM pruning
- Support both stdio and SSE/HTTP backend transports
- Deploy locally (personal use) or on a server (multi-user, with auth)

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   MCP Proxy                         │
│                                                     │
│  ┌─────────────┐     ┌─────────────────────────┐   │
│  │  MCP Server │     │      HTTP API Server    │   │
│  │  (stdio/SSE)│     │   (REST + SSE endpoint) │   │
│  └──────┬──────┘     └────────────┬────────────┘   │
│         │                         │                 │
│         └──────────┬──────────────┘                 │
│                    ▼                                 │
│           ┌────────────────┐                        │
│           │  Router        │                        │
│           │  Layer A: tool → server (exact match)   │
│           │  Layer C: config rule-based             │
│           │  Layer B: LLM prune (optional)          │
│           └───────┬────────┘                        │
│                   ▼                                 │
│        ┌──────────────────────┐                     │
│        │  Connection Pool     │                     │
│        │  (lazy, per-server)  │                     │
│        └──────────┬───────────┘                     │
│                   ▼                                 │
│        ┌──────────────────────┐                     │
│        │  Backend Registry    │                     │
│        │  (config-driven)     │                     │
│        └──────────────────────┘                     │
└─────────────────────────────────────────────────────┘
          │               │
    [stdio backends]  [SSE/HTTP backends]
```

---

## Components

### 1. MCP Server Interface

- Exposes standard MCP protocol to clients (Claude Code, etc.)
- Supports both stdio transport (local use) and SSE transport (server deployment)
- Implements: `tools/list`, `tools/call`, `resources/list`, `resources/read` (forwarded)

### 2. HTTP API Interface

- REST endpoints:
  - `GET /tools` — list all available tools (with optional context for LLM pruning)
  - `POST /tools/:name/call` — call a specific tool directly
  - `GET /servers` — list registered backend servers and their health status
  - `GET /health` — proxy health check
- Supports API key auth (optional, for server deployment)

### 3. Router

Three-layer routing executed in order:

1. **Layer A — Exact tool name match**: tool name → server name mapping, read from config `tools` arrays
2. **Layer C — Config rule-based**: tag matching, regex patterns, server affinity rules
3. **Layer B — LLM semantic pruning** (optional): activated when total tool count exceeds `router.llm_prune.threshold`. Sends tool descriptions + request context to an LLM to select the relevant subset. Degrades gracefully: if LLM call fails, returns full tool list.

### 4. Connection Pool

- Lazy initialization: connects to a backend only when first needed
- Caches live connections per server
- Supports two transport types:
  - **stdio**: spawns child process, manages stdin/stdout pipes, auto-restarts on crash (max 3 retries before marking unhealthy)
  - **SSE/HTTP**: maintains persistent SSE connection or uses stateless HTTP calls
- Periodic health checks for active connections

### 5. Backend Registry

- Loads server definitions from `config.yaml` at startup
- Stores per-server metadata: name, transport, connection params, tool list, tags, health status
- Tool list in config is the source of truth for routing (no need to connect to discover tools)
- Supports runtime reload via `POST /admin/reload` (optional)

---

## Configuration

```yaml
servers:
  - name: filesystem
    transport: stdio
    command: ["npx", "@modelcontextprotocol/server-filesystem", "/tmp"]
    tools: ["read_file", "write_file", "list_directory"]
    tags: ["file", "storage"]

  - name: github
    transport: sse
    url: "https://mcp.github.com/sse"
    auth:
      type: bearer
      token_env: GITHUB_TOKEN
    tools: ["search_repos", "create_issue", "get_pr"]
    tags: ["code", "vcs"]

router:
  llm_prune:
    enabled: true
    threshold: 20          # activate LLM pruning when tool count exceeds this
    model: "claude-haiku-4-5-20251001"
    api_key_env: ANTHROPIC_API_KEY

proxy:
  mcp_port: 3000           # MCP SSE interface
  http_port: 3001          # HTTP REST API
  auth:                    # optional, for server deployment
    type: api_key
    keys_env: PROXY_API_KEYS   # comma-separated list of valid keys
```

---

## Request Data Flow

### `tools/call`

```
Client: tools/call { name: "read_file", arguments: {...} }
  → Router Layer A: "read_file" matches server "filesystem"
  → Connection Pool: connection exists? reuse : lazy init stdio process
  → Forward request to filesystem backend
  → Return result to client
```

### `tools/list`

```
Client: tools/list
  → Registry: collect tool metadata from config (no connections needed)
  → tool count > threshold (20)?
      No  → return full list
      Yes → call LLM with tool descriptions + request context
           → LLM returns pruned relevant subset
           → return pruned list (fallback: full list if LLM fails)
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Backend connection failure | Mark server unhealthy; continue serving other backends; tools from failed server excluded from `tools/list` |
| Tool not found in any backend | Return MCP `ToolNotFound` error with hint of available tools |
| LLM pruning failure | Degrade gracefully: return full tool list, log warning |
| stdio process crash | Auto-restart up to 3 times; mark unhealthy after 3 failures |
| Auth failure (HTTP API) | Return HTTP 401 |
| Config parse error at startup | Exit with clear error message |

---

## Testing Strategy

- **Unit tests**: Router matching logic (all three layers), Connection Pool lazy init + reuse, config parsing, LLM pruning fallback behavior
- **Integration tests**: Full request chain using mock MCP servers for both stdio and SSE transports; `tools/list` with and without LLM pruning
- **End-to-end test**: Connect real Claude Code client to proxy, verify `tools/list` and `tools/call` work correctly against real or mock backends

---

## Project Structure

```
mcp-proxy/
├── src/
│   ├── index.ts              # entry point
│   ├── config/
│   │   └── loader.ts         # config.yaml parsing + validation
│   ├── registry/
│   │   └── backend-registry.ts
│   ├── router/
│   │   ├── router.ts         # orchestrates all three layers
│   │   ├── exact-match.ts    # Layer A
│   │   ├── rule-match.ts     # Layer C
│   │   └── llm-prune.ts      # Layer B
│   ├── pool/
│   │   ├── connection-pool.ts
│   │   ├── stdio-connection.ts
│   │   └── sse-connection.ts
│   ├── server/
│   │   ├── mcp-server.ts     # MCP protocol interface
│   │   └── http-server.ts    # HTTP REST API
│   └── types.ts
├── tests/
│   ├── unit/
│   └── integration/
├── config.yaml.example
├── package.json
└── tsconfig.json
```

---

## Out of Scope (v1)

- Dynamic server registration at runtime (beyond config reload)
- Tool result caching
- Request logging / observability dashboard
- Multi-tenant isolation (beyond API key auth)
