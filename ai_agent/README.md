# Agent Chat — Config-driven ReAct chat runtime (LangGraph + MCP)

A chat runtime on **[LangGraph](https://www.langchain.com/langgraph)** and
**[MCP](https://modelcontextprotocol.io/introduction)** (via `langchain-mcp-adapters`).
One runnable demo is the **SAP Ops assistant**.

**A YAML file changes the prompt, model, and tools.** Every config still runs the
same `create_agent` loop. Changing the control flow means editing `graph.py`.

## Why this stack

| Concern | Choice | Reason |
|---|---|---|
| Orchestration | **LangGraph** | Production-grade durable runtime (checkpointing, HITL, streaming). Used at ServiceNow, LinkedIn, Uber, Klarna. |
| Tools/data | **MCP** | Open standard; reuses the MCP servers already in this repo (`gardener-ai-mcp`, `sci-ai-mcp`, ...). |
| Providers | anthropic / openai / ollama / litellm | Vendor-neutral via a factory. Hyperspace models go through LiteLLM. |
| Skills | Claude Code `SKILL.md` | Each file becomes a `skill_<name>` tool. The composer can call one with `/<name>`. |
| Language | Python | Richest LangGraph + MCP adapter ecosystem. |

## Layout

```
ai_agent/
├── configs/            base.yaml + sap_ops.yaml + generic_react.yaml
│                       + hyperspace_gpt5_6_sol.yaml + hyperspace_gemini_3_5_flash.yaml
├── src/agent_chat/ config · llm · mcp_tools · skills · subagents · web_search
│                       memory · graph · otel_callback · observability · cli · api
│                       └── static/          built chat UI (do not edit by hand)
├── web/                Vite + assistant-ui source. `npm run build` writes static/
├── tests/              config · skills · sub-agents · OTEL · graph smoke · API
├── docker/             multi-stage Dockerfile (Node build + Python) + compose
└── docs/               ARCHITECTURE.md · ADD_A_USE_CASE.md · WEB_UI.md · OBSERVABILITY.md · OTLP-AI.md
```

## Quick start

```bash
cd ai_agent
uv sync                      # install
uv run pytest                # CI-safe: no API key / no network needed

cp .env.example .env         # then set credentials (see "Authentication" below)

# Plain ReAct agent, no SAP coupling:
uv run agent-chat run --config generic_react --query "What is 2+2?"

# DEMO — reuses the in-repo gardener-ai-mcp server as tools:
uv run agent-chat run --config sap_ops \
  --query "How do I debug a shoot stuck in Reconciling?"

# Inspect a fully-resolved config:
uv run agent-chat show sap_ops
```

## Authentication (LLM gateway or direct API)

The Anthropic provider supports two auth modes, resolved from the environment
(no secrets in YAML). This mirrors how Claude Code targets an internal gateway.

**Mode A — gateway / proxy (Bearer token).** Set `ANTHROPIC_AUTH_TOKEN` and
`ANTHROPIC_BASE_URL`; the agent sends `Authorization: Bearer <token>` to the
gateway. Use gateway model names in the config (the default is already
`anthropic--claude-sonnet-latest`):

```bash
export ANTHROPIC_AUTH_TOKEN="cef..."               # your gateway token
export ANTHROPIC_BASE_URL="http://localhost:6655/anthropic/"
uv run agent-chat run -c generic_react -q "What is 2+2?"
```

These are the same variables `~/.claude/settings.json` sets under `"env"`, so if
your shell already has them exported, **it just works** — no `.env` needed.

**Mode B — direct Anthropic API (x-api-key).** Set `ANTHROPIC_API_KEY` and use a
standard model name (e.g. `claude-sonnet-4-6`) in the config.

Precedence: explicit YAML (`llm.auth_token` / `llm.base_url` / `llm.api_key`,
which accept `${ENV}` refs) > environment variables. If `ANTHROPIC_AUTH_TOKEN`
is present it takes the gateway path; otherwise `ANTHROPIC_API_KEY` is used.

**LiteLLM / Hyperspace.** `hyperspace_gpt5_6_sol` and `hyperspace_gemini_3_5_flash`
talk to a LiteLLM proxy. Set `LITELLM_BASE_URL` and `LITELLM_API_KEY`. No
secrets go in those YAML files.

## Web UI

The same core (`load_config → build_graph`) is a browser UI. FastAPI streams
SSE; the page is a Vite + [assistant-ui](https://www.assistant-ui.com/) app in
`web/`. Build it once, then serve. The CLI is unchanged.

```bash
cd web && npm install && npm run build   # writes src/agent_chat/static
cd .. && uv run agent-chat serve              # http://127.0.0.1:8000
```

`npm run dev` in `web/` proxies `/api` to port 8000. Docker builds the page in
its Node stage. Full design and security notes: [docs/WEB_UI.md](docs/WEB_UI.md).

What you get:

- Config dropdown. The choice is stored per session. Selectable configs are
  `generic_react`, `sap_ops`, `hyperspace_gpt5_6_sol`, and
  `hyperspace_gemini_3_5_flash` (`base.yaml` is not listed).
- A sidebar of chats in `localStorage` (`agent-chat:sessions:v1`). Each
  chat's id is the backend `thread_id`, so memory stays isolated. New, rename,
  and delete are in the sidebar.
- A collapsible Thinking timeline: model reasoning, skills, MCP calls, web
  search, and sub-agents, with elapsed time. A sub-agent's tools nest under
  its `delegate_source` card.
- Markdown answers, including GFM tables.
- After each turn, a Jaeger link when a trace exists.

API surface (same-origin, no CORS):

| Endpoint | Purpose |
|---|---|
| `GET /` | Serves the built page. `503` if `web/` has not been built. |
| `GET /assets/*` | Built JS and CSS. |
| `GET /api/configs` | Lists configs and the skills each one loaded. No MCP spawn. |
| `POST /api/chat` | `{config, query, thread_id}` → SSE: `token`, `reasoning`, `tool_start`, `tool_end`, `done` (`trace_id`, `trace_url`), `error`. Sub-agent frames also carry `parent_id`. |

### Skills in the composer

Type `/` to open the menu for the **selected config**. `/skills` lists what that
config actually loaded. `/<name>` loads that skill's instructions and then
answers; the timeline shows a `skill_*` chip. An unknown name returns the list
and does not call the model.

Which directory a config scans is in its YAML. `sap_ops` scans `../../skills`
(repo skills, including `kb-capture`). `hyperspace_gpt5_6_sol` also scans
`~/.claude/skills`, which is where `sap-jira` and `sap-authentication` live.
A skill that is not on that config's list cannot be invoked.

### Thinking, web search, sub-agents

- **Thinking** — collapsed by default. Reasoning appears for any provider that
  emits it. Anthropic extended thinking needs `llm.thinking_budget_tokens: <N>`
  (default `0` = off).
- **Web search** — `web_search: true` adds a `web_search` tool. Tavily when
  `TAVILY_API_KEY` is set, otherwise keyless DuckDuckGo.
- **Sub-agents** — a question that needs more than one source makes the main
  agent call `delegate_source` once per source. Each call is its own ReAct
  agent and returns only a short summary. The main agent writes the answer.

## 可观测性（OTEL）

一次提问对应一条 trace。主 agent 的 `chat` / `execute_tool`，以及 `delegate_source` 拉起的子 agent，都在同一个 `invoke_agent` 下面。子 agent 的模型和工具是 `execute_tool delegate_source` 的子 span（`ai.agent.role=subagent`，`ai.delegate.source` 是那一路数据源）。本机默认关闭。实践说明：[docs/OTLP-AI.md](docs/OTLP-AI.md)。打开 Jaeger：[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)。

查看界面是 kind 集群 `kind-gardener-ai-mcp`、命名空间 `observability` 里已有的 Jaeger，不在本仓库另起 Collector。两个 Service 都是 ClusterIP，先把端口转到本机并保持不退出：

```bash
kubectl --context kind-gardener-ai-mcp -n observability port-forward svc/otel-collector 4318:4318
kubectl --context kind-gardener-ai-mcp -n observability port-forward svc/jaeger 16686:16686
```

`curl http://127.0.0.1:16686/` 应返回 `200`。`curl http://127.0.0.1:4318/v1/traces` 返回 `400` 或 `405` 即表示 Collector 在听。

本机打开导出后再启动。页面要先在 `web/` 里构建过。`8010` 是本机常用端口，默认仍是 `8000`：

```bash
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_UI_BASE_URL=http://127.0.0.1:16686
uv run agent-chat serve --host 127.0.0.1 --port 8010
```

agent 会把这些 `OTEL_*` 变量传给 stdio 启动的 gardener / sci，它们的 span 和这次回答在同一条 trace 里。每一轮结束后，回答下方和 CLI 都会给出 Jaeger 地址，默认是 `http://127.0.0.1:16686/trace/<trace_id>`。没设 `OTEL_UI_BASE_URL` 时也用这个地址；设成空字符串则不给链接。

瀑布图从上到下是 `invoke_agent` → `chat` → `execute_tool`（含 `delegate_source` 及其子 agent）→ `retrieve` → `embeddings` / `qdrant.search`。回答变差时，用两次的地址对照，或在 Jaeger 里搜 tag `gen_ai.conversation.id=<thread_id>`。

`docker compose` 已打开 OTEL，容器里的 endpoint 是 `http://host.docker.internal:4318`，同样依赖上面的 port-forward。Collector 大约 5 秒才把 span 交给 Jaeger。Jaeger 是内存存储，Pod 重启后 trace 消失。

## Container

```bash
docker compose -f docker/docker-compose.yaml up --build
```

Brings up two services on the same image: `agent` (one-shot CLI demo) and
`agent-web` (the Web UI on `http://localhost:8000`, healthchecked on
`/api/configs`). Both export traces to `http://host.docker.internal:4318`.
Port-forward the kind Collector and Jaeger first; see
[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

## Add your own agent

See [docs/ADD_A_USE_CASE.md](docs/ADD_A_USE_CASE.md). In short:
`cp configs/generic_react.yaml configs/my_case.yaml`, edit the prompt, point
`mcp_servers` at any MCP server, and run. That is another chat config on the
same `create_agent` loop.

## How it fits together

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the graph diagram, layers,
and extension points.

## Sources
- [LangGraph](https://www.langchain.com/langgraph)
- [Model Context Protocol](https://modelcontextprotocol.io/introduction)
- [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)
