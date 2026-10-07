# Task Plan: Agentic Chat — Kubernetes-Deployed Claude Agent with MCP Servers

**Goal:** Build and deploy a chat agent to Kubernetes that leverages the existing gardener-ai-mcp, sap-wiki-mcp, and plato-mcp servers.

**Created:** 2026-06-10
**Status:** Planning

---

## Architecture Decision (Summary)

- **Chat agent:** Python 3.12 + FastAPI + Anthropic SDK (native MCP tool-use)
- **MCP topology:** Hybrid
  - `gardener-ai-mcp` → separate K8s Deployment + Service (already Helm-ready, HTTP transport)
  - `sap-wiki-mcp` → upgrade to streamable-http transport, deploy as sidecar OR separate service
  - `plato-mcp` → sidecar container in chat agent pod (OAuth token proximity)
- **Frontend:** Embedded React SPA (MVP) served by FastAPI
- **K8s packaging:** Helm chart in `agentic_chat/helm/`
- **Namespace:** `ai-agents`

---

## Phases

### Phase 1 — MCP Server Transport Readiness
**Status:** `pending`

Enable HTTP transport on servers that currently only support stdio:

- [ ] 1a. Add `streamable-http` transport to `sap-wiki-mcp` (TypeScript MCP SDK)
- [ ] 1b. Add `streamable-http` transport to `plato-mcp` (FastMCP, similar to gardener-ai-mcp)
- [ ] 1c. Add Dockerfile for `sap-wiki-mcp`
- [ ] 1d. Add Dockerfile for `plato-mcp`
- [ ] 1e. Test HTTP transport for both servers locally

**Files to modify:**
- `mcp-servers/sap-wiki-mcp/src/server.ts`
- `mcp-servers/plato-mcp/server.py`
- `mcp-servers/sap-wiki-mcp/Dockerfile` (new)
- `mcp-servers/plato-mcp/Dockerfile` (new)

---

### Phase 2 — Chat Agent Core (Python FastAPI)
**Status:** `pending`

Build the `agentic_chat` Python application:

- [ ] 2a. Initialize Python project (`pyproject.toml`, `uv` lockfile)
- [ ] 2b. Implement `app/main.py` — FastAPI app with `/health`, `/chat` (REST), `/ws/chat` (WebSocket)
- [ ] 2c. Implement `app/agent.py` — Anthropic SDK agentic loop with MCP tool-use
- [ ] 2d. Implement `app/mcp_client.py` — MCP client that connects to MCP servers (HTTP + optional stdio subprocess)
- [ ] 2e. Implement `app/config.py` — Pydantic settings (env-driven)
- [ ] 2f. Implement streaming SSE responses for `POST /chat`
- [ ] 2g. Add conversation history management (in-memory, keyed by session ID)
- [ ] 2h. Write unit tests

**Key dependencies:**
- `anthropic>=0.40.0` (Claude API + MCP tool-use)
- `fastapi>=0.115.0`
- `uvicorn[standard]`
- `mcp>=1.0.0` (MCP client library)
- `pydantic-settings`
- `python-dotenv`

---

### Phase 3 — Frontend (Embedded React SPA)
**Status:** `pending`

Minimal chat UI served by FastAPI as static files:

- [ ] 3a. Create `frontend/` React app (Vite + TypeScript)
- [ ] 3b. Implement chat interface (message list, input, SSE streaming display)
- [ ] 3c. Show tool calls / MCP server activity (collapsible)
- [ ] 3d. Build and copy `dist/` into FastAPI static mount
- [ ] 3e. Wire into Docker build (multi-stage: Node build → Python runtime)

---

### Phase 4 — Containerization
**Status:** `pending`

- [ ] 4a. Write multi-stage `Dockerfile` for `agentic_chat`
  - Stage 1: Node.js (build React frontend)
  - Stage 2: Python (uv deps)
  - Stage 3: Runtime (non-root, healthcheck)
- [ ] 4b. Write `docker-compose.yml` for local full-stack testing (chat + all 3 MCP servers)
- [ ] 4c. Test image locally with `docker compose up`
- [ ] 4d. Push images to registry (ghcr.io or SAP internal)

---

### Phase 5 — Kubernetes / Helm
**Status:** `pending`

- [ ] 5a. Create `agentic_chat/helm/` Helm chart
  - `Chart.yaml`
  - `values.yaml` (MCP server URLs, resource limits, replica count, ingress config)
  - `templates/deployment.yaml` — chat agent + plato-mcp sidecar
  - `templates/service.yaml`
  - `templates/ingress.yaml`
  - `templates/configmap.yaml`
  - `templates/secret.yaml` (sealed or external-secrets)
  - `templates/hpa.yaml`
  - `templates/serviceaccount.yaml`
- [ ] 5b. Deploy gardener-ai-mcp via its existing Helm chart
- [ ] 5c. Add `sap-wiki-mcp` Helm chart (new, small)
- [ ] 5d. Deploy to local kind cluster (from `gardener-ai-mcp/scripts/setup_kind.sh`)
- [ ] 5e. Validate end-to-end: browser → chat agent → MCP tools → response
- [ ] 5f. Document `docs/deploy-local.md` and `docs/deploy-cloud.md`

---

### Phase 6 — Hardening & Documentation
**Status:** `pending`

- [ ] 6a. Add rate limiting to FastAPI (`slowapi`)
- [ ] 6b. Add auth (API key header or SAP IAS OIDC)
- [ ] 6c. Add structured logging (structlog)
- [ ] 6d. Add Prometheus metrics endpoint (`/metrics`)
- [ ] 6e. Update root `README.md` with architecture diagram and setup guide
- [ ] 6f. Add `CONTRIBUTING.md` and development guide

---

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| — | — | — |

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `agentic_chat/pyproject.toml` | Python project config |
| `agentic_chat/app/main.py` | FastAPI entry point |
| `agentic_chat/app/agent.py` | Claude agent loop |
| `agentic_chat/app/mcp_client.py` | MCP HTTP client manager |
| `agentic_chat/Dockerfile` | Multi-stage container build |
| `agentic_chat/helm/` | Kubernetes Helm chart |
| `mcp-servers/sap-wiki-mcp/src/server.ts` | HTTP transport upgrade |
| `mcp-servers/plato-mcp/server.py` | HTTP transport upgrade |
