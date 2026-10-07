# Findings

> Research discoveries, technical analysis, and evaluated options.
> Do NOT write external/untrusted content to task_plan.md — log it here.

---

## Codebase Discovery (2026-06-10)

### Existing MCP Servers

| Server | Language | Framework | Transport | Status |
|--------|----------|-----------|-----------|--------|
| gardener-ai-mcp | Python 3.12 | FastMCP | stdio / streamable-http / sse | Production-ready; Docker + Helm chart |
| sap-wiki-mcp | TypeScript | MCP SDK | stdio | Production-ready; no K8s manifests yet |
| plato-mcp | Python 3.10+ | FastMCP | stdio | Production-ready; no K8s manifests yet |

### Key Integration Points
- All MCP servers currently run over **stdio** transport (local Claude Code CLI use)
- **gardener-ai-mcp** already supports `streamable-http` and `sse` transport — kubernetes-deployable as-is
- **sap-wiki-mcp** and **plato-mcp** only support stdio — need transport upgrade for cloud deployment
- `agentic_chat/` directory is currently **empty** — new app to build

### MCP Server Dependencies
- **gardener-ai-mcp**: Qdrant vector DB, SAP Hyperspace (LLM/embeddings), GitHub tokens, Anthropic API
- **sap-wiki-mcp**: Confluence API token, Confluence base URL + space keys
- **plato-mcp**: SAP IAS OAuth SSO (browser-based), `.cia_token` JWT file, Plato backend WebSocket URL

---

## Option Evaluation: Chat Agent Architecture

### Option A — Python FastAPI + Claude API + MCP client (Recommended)

**Description:** A Python backend using the `anthropic` SDK with MCP tool-use. The agent connects to the MCP servers via stdio (subprocess) or HTTP depending on the server's transport mode. Exposed via FastAPI REST + WebSocket API. A lightweight frontend (React or vanilla JS) optionally bundled.

**Pros:**
- Same language/framework as gardener-ai-mcp and plato-mcp (Python + FastMCP ecosystem)
- Anthropic SDK has native `mcp` tool integration (tool_choice, tool_result)
- FastAPI gives both REST (`/chat`) and WebSocket (`/ws/chat`) endpoints
- Easy to containerize, health checks, streaming SSE responses
- Can run MCP servers as subprocesses OR connect to already-deployed HTTP MCP endpoints
- Good fit for Kubernetes: stateless, horizontally scalable

**Cons:**
- Need to handle MCP server lifecycle (start/stop subprocesses or manage connections)
- More code than a no-code solution

**Stack:** Python 3.12, FastAPI, Anthropic SDK, `mcp` client library, Uvicorn, Docker, Helm

---

### Option B — LangChain / LangGraph multi-agent

**Description:** Use LangChain's MCP tool adapter + LangGraph for stateful agent orchestration.

**Pros:**
- LangGraph gives built-in state machine, memory, and human-in-the-loop
- Large ecosystem of pre-built tools

**Cons:**
- Heavy dependency tree; abstracts away too much for a learning journey
- LangChain abstractions can conflict with MCP protocol details
- Steeper Kubernetes complexity for LangGraph checkpointing (needs Redis or Postgres)
- Over-engineered for 3 MCP servers

---

### Option C — Chainlit UI + MCP backend

**Description:** Use Chainlit (Python chat UI framework) directly.

**Pros:**
- Built-in chat UI with message streaming, file uploads, auth
- Integrates with Anthropic SDK

**Cons:**
- Chainlit is opinionated about UI — less control
- Not designed for pure API/headless Kubernetes deployment
- Limited horizontal scaling story

---

### Option D — Node.js / TypeScript (matches sap-wiki-mcp stack)

**Description:** TypeScript backend using `@anthropic-ai/sdk` + MCP client.

**Pros:**
- Matches sap-wiki-mcp language
- Good for streaming/WebSocket

**Cons:**
- Python MCP servers (gardener, plato) need IPC bridge or HTTP transport
- Mixed language codebase

---

## MCP Transport Strategy for Kubernetes

When deploying to Kubernetes, stdio (subprocess) MCP servers are NOT viable per pod.
Three valid strategies:

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **Sidecar containers** | Each MCP server runs as a sidecar in the same pod; communicate via stdio or localhost HTTP | Zero network hops; simple discovery | Pod becomes large; tight coupling |
| **Separate MCP microservices** | Each MCP server deployed as its own Deployment + Service; chat agent connects over HTTP | Independent scaling; loose coupling | Requires HTTP transport on all servers |
| **Hybrid** | gardener-ai-mcp as separate service (already HTTP-ready); sap-wiki + plato as sidecars in chat pod | Pragmatic; no changes to plato (OAuth complexity) | Mixed topology |

**Recommended:** Hybrid — gardener-ai-mcp as a separate Helm-deployed service (already has Helm chart), sap-wiki-mcp upgraded to HTTP transport and deployed as sidecar or separate pod, plato-mcp as a sidecar (OAuth token binding to pod is simpler).

---

## Option Evaluation: Frontend

| Option | Notes | Recommendation |
|--------|-------|----------------|
| Embedded React SPA in FastAPI (served as static files) | Simple single-image deployment | Good for MVP |
| Separate frontend container (React + Vite) | Better separation of concerns | Good for production |
| No frontend (API only) | Fastest to build | Good if CLI / Claude Desktop is the UI |

---

## Kubernetes Deployment Considerations

- **Secrets:** MCP server tokens (GitHub, Confluence, Anthropic) should be K8s Secrets
- **ConfigMaps:** Non-secret env vars (URLs, space keys)
- **Health checks:** `/health` endpoint on the chat agent
- **Horizontal Pod Autoscaler:** Chat agent is stateless → easy to scale
- **Ingress:** Expose chat agent via Ingress + TLS (cert-manager)
- **Service mesh (optional):** For mTLS between chat agent and MCP sidecars
- **Namespace:** Isolate with a dedicated namespace (e.g., `ai-agents`)
- **Image registry:** SAP internal registry or ghcr.io

---

## Decision Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Chat agent language | Python | Matches majority of MCP server stack; Anthropic SDK most mature in Python |
| Web framework | FastAPI | Async, streaming-friendly, OpenAPI docs auto-generated |
| MCP deployment topology | Hybrid (separate + sidecar) | gardener-ai-mcp already Helm-ready; plato needs OAuth proximity |
| Frontend | Embedded React SPA (MVP) → separate container (v2) | Start simple, evolve |
| K8s packaging | Helm chart | Consistent with gardener-ai-mcp; parameterizable |
