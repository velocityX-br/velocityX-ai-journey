# Web UI Feature — FastAPI + SSE

> A browser interface for agent-chat, built on the same core as the CLI.
> See also: [ARCHITECTURE.md](ARCHITECTURE.md) · [README.md](../README.md)

## Background & Motivation

Agent Chat is a **config-driven ReAct chat runtime** built on
the current industry-standard open-source stack:

- **LangGraph** (LangChain 1.x) — durable agent runtime (checkpointing, streaming,
  human-in-the-loop).
- **MCP (Model Context Protocol)** — vendor-neutral tool/data integration via
  `langchain-mcp-adapters`.

Its shape: **a YAML file sets the prompt, model, and tools.** Dropping a new
file under `configs/` adds another chat config on the same `create_agent` loop.
The **CLI** (`agent-chat run` / `agent-chat show`) is proven end-to-end against
a real in-repo MCP server (`gardener-ai-mcp`).

**The gap:** a CLI alone is not how enterprises consume an agent. Business users,
ops teams, and demos need a **browser interface**. The motivation was to add that
interface *following industry norms* while keeping the CLI untouched and —
critically — **not duplicating any graph / config / memory / tool logic**.

## What Was Developed

A **FastAPI + Server-Sent Events (SSE) Web UI** sitting on the *exact same*
`load_config → build_graph` seam as the CLI. Two entrypoints, one core.

| Component | What it does |
|---|---|
| `src/agent_chat/api.py` | FastAPI app factory (`create_app`), `AgentRegistry`, SSE streaming, config listing, HTML serving |
| `web/` | Vite + assistant-ui chat shell. `npm run build` writes into `static/` |
| `agent serve` (in `cli.py`) | Boots uvicorn on the same graph; `run` / `show` untouched |
| `tests/test_api.py` | 3 network-free tests via Starlette's `TestClient` |
| `docker/` + docs | `agent-web` compose service, healthcheck, README / ARCHITECTURE updates |

### Delivered capabilities (all verified live)

1. **Token streaming over SSE** — `graph.astream_events(v2)` mapped to typed
   frames: `token`, `tool_start`, `tool_end`, `done` (`trace_id`, `trace_url`), `error`.
2. **Config selector** — dropdown to switch use cases at runtime
   (`generic_react`, `sap_ops`, …); the choice is remembered **per session**.
3. **Sessions sidebar (localStorage)** — a ChatGPT-style left sidebar lists saved
   sessions. Each session owns a unique `id` that doubles as its backend
   `thread_id`, giving every conversation an **isolated agent memory context**.
   Sessions **persist across refresh / browser restart** in `localStorage`
   (key `agent-chat:sessions:v1`); selecting one restores its full
   transcript. "+ New chat" starts a fresh, isolated session; each row supports
   inline rename (✎) and delete (🗑). Titles derive from the first user message.
4. **Unified "Thinking" session** — a single **default-collapsed** 💭 card
   above the answer that holds the *whole* thought process for the turn: the
   model's reasoning **and** every tool-call step (name · input · output), in
   the order they happened. The user clicks to expand and study it; the summary
   shows a live indicator and a step count while collapsed. This replaces the
   earlier separate per-tool cards, gathering reasoning + tool steps in one
   place so the process is easy to follow and learn from.
5. **Web search tool** — an opt-in built-in `web_search` tool lets the agent
   retrieve current information from the internet (and cite it); its call shows
   up as a step inside the Thinking session.

## New feature: Thinking process (reasoning + tool steps) in the UI

The model's reasoning is streamed to the browser as a separate SSE frame type,
`reasoning`, and — together with each tool call — is shown in one **unified,
default-collapsed** 💭 **Thinking** session:

- **Backend** — `_event_stream` inspects each `on_chat_model_stream` chunk with
  `_chunk_reasoning`, which extracts Anthropic extended-thinking blocks
  (`{"type": "thinking", "thinking": "..."}`) and gateway/OpenAI-style
  `additional_kwargs.reasoning_content`. Reasoning is emitted as
  `{"type": "reasoning", "text": "..."}` — distinct from `token`, so the answer
  and the thinking never intermix. Tool calls keep their existing
  `tool_start` / `tool_end` frames.
- **Enabling it** — reasoning frames appear automatically for any provider that
  emits reasoning. For Anthropic extended thinking, set
  `llm.thinking_budget_tokens` in a config (e.g. `2048`); `build_llm` then passes
  `thinking={"type": "enabled", "budget_tokens": N}` (and sets `temperature: 1`,
  as the API requires). Left at `0` (off) by default, so configs targeting a
  model/gateway without thinking support are unaffected.
- **Frontend** — the Thinking session is created lazily on the first `reasoning`
  **or** `tool_start` frame (so even a tools-only turn with no reasoning still
  records its steps). It starts **collapsed and stays collapsed** — the user
  opens it on demand. `reasoning` frames append text blocks; `tool_start` /
  `tool_end` frames add and complete tool steps inside the same timeline; only
  the final answer `token`s go to the assistant bubble. Like before, the
  Thinking session is **not persisted** (only user/assistant text is
  saved to `localStorage`).

## New feature: Web search

An opt-in built-in tool (`src/agent_chat/web_search.py`) that lets the agent
search the public web for up-to-date information:

- **Opt-in per config** — set `web_search: true` (see `generic_react.yaml`). The
  tool is appended in `build_graph` alongside MCP and skill tools, so it appears
  as a normal live tool card and is advertised in `/api/configs`.
- **Provider selection (env-driven, graceful)** — uses **Tavily** when
  `TAVILY_API_KEY` is set (LLM-optimized search), otherwise falls back to a
  **keyless DuckDuckGo** query so it works out of the box with no signup. All
  network access is lazy and wrapped: any failure returns a short diagnostic
  string instead of crashing the agent turn.
- **Output** — a numbered list of `title · URL · snippet` results the model reads
  and cites.

## Use Cases

- **Interactive ops assistant** — e.g. the `sap_ops` config exposes the
  `gardener` MCP server; an operator asks "how do I debug a shoot stuck in
  Reconciling?" and watches the reasoning + tool calls stream in real time.
- **Demoing a new agent** — drop a YAML, run `agent serve`, share a URL. No
  frontend work.
- **Multi-use-case console** — one server, many configs; users pick the agent
  they need from the dropdown.
- **Session-scoped conversations** — support / triage flows that need memory
  across turns, keyed by `thread_id`.
- **Parallel topics, side by side** — the sidebar lets a user keep several
  independent conversations open (e.g. one debugging a shoot, another drafting a
  runbook), each with its own clean context, and return to any of them later.

## Why It Works as an Enterprise Scaffold

- **One core, many faces** — CLI *and* Web UI both build on
  `load_config → build_graph`. No divergence, no drift. A future gRPC / Slack
  surface would plug into the same seam.
- **Config = product** — a new enterprise use case (SAP Ops, HR bot, finance
  assistant) is a new YAML pointing at whatever MCP servers it needs. No
  engineering cycle.
- **Vendor-neutral** — LLM provider (anthropic / openai / ollama) is
  config-driven; MCP is an open standard, so existing enterprise MCP servers are
  reused as tools.
- **Governance built in** — LangSmith tracing toggled purely by env vars;
  OpenTelemetry spans for the LLM, MCP tools, RAG, and external calls (see
  [OBSERVABILITY.md](OBSERVABILITY.md)); structured logging via idempotent
  `configure_observability()`; durable SQLite checkpointing for auditable,
  resumable conversations.
- **Trivially containerizable** — multi-stage Dockerfile, compose service,
  healthcheck — ships like any modern cloud-native service.
- **Correct resource lifecycle** — the `AgentRegistry` builds each graph once and
  holds its checkpointer + MCP subprocesses open in a lifespan-scoped
  `AsyncExitStack`, released cleanly on shutdown. This is the hard part done
  right for real concurrent traffic.

## Architecture (request flow)

```
  browser  ──POST /api/chat {config,query,thread_id}──▶  FastAPI
     ▲                                                     │
     │        SSE frames                                   │  AgentRegistry
     │  token / tool_start / tool_end / done / error       │  (lazy per-config
     └─────────────────────────────────────────────────── ▼   compiled-graph cache)
                                                    graph.astream_events(v2)
```

- **`AgentRegistry`** on `app.state` builds each config's graph **once, lazily**,
  and caches it for the server's lifetime. A per-config `asyncio.Lock` (guarded
  by a global lock) prevents duplicate MCP-subprocess spawns on concurrent
  first-hits.
- The checkpointer (`build_checkpointer`, a sync `@contextmanager`) and MCP
  subprocesses must outlive per-request calls, so each is entered into a single
  `AsyncExitStack` opened in the FastAPI **lifespan** and released on shutdown.
- **POST + `fetch` streaming**, not `EventSource` — EventSource is GET-only and
  cannot carry a JSON body. The frontend reads `res.body.getReader()` and splits
  on `\n\n`.
- `_chunk_text` flattens Anthropic's list-of-content-blocks streaming into plain
  text so the UI never renders raw block dicts.

## API Surface

Same-origin (no CORS). The HTML is served by the same app as `/api/*`.

| Endpoint | Purpose |
|---|---|
| `GET /` | Serves the single-file frontend. |
| `GET /api/configs` | Lists selectable configs (`base.yaml` excluded); **no MCP spawn**. |
| `POST /api/chat` | `{config, query, thread_id}` → SSE frames: `token`, `reasoning`, `tool_start`, `tool_end`, `done` (`trace_id`, `trace_url`), `error`. |

### SSE frame shapes

```jsonc
{ "type": "token",      "text": "partial text" }
{ "type": "reasoning",  "text": "partial thinking" }
{ "type": "tool_start", "id": "<run_id>", "name": "search_docs", "input": {...} }
{ "type": "tool_end",   "id": "<run_id>", "name": "search_docs", "output": "..." }
{ "type": "done",       "trace_id": "<32 hex>", "trace_url": "http://127.0.0.1:16686/trace/<trace_id>" }
{ "type": "error",      "message": "..." }
```

Frames that belong to a `delegate_source` sub-agent also carry `parent_id`, set to that
delegate tool's `run_id`. The main answer is only a `token` frame **without**
`parent_id`. A sub-agent's draft text is a `token` (or `reasoning`) frame **with**
`parent_id`, so it stays inside that sub-agent card. Nested MCP / `web_search`
tool frames use the same `parent_id`. The delegate call itself has no `parent_id`.

The UI classifies tool names itself: `skill_*` is a skill, `mcp__<server>__<tool>`
is an MCP call, `web_search` is web, and `delegate_source` is a sub-agent (titled
from `input.source`). Durations are measured in the browser from frame arrival.

## Running It

The chat page is an [assistant-ui](https://www.assistant-ui.com/) app in `web/`.
It keeps using `POST /api/chat`. Build it before `agent serve` (the image builds
it in the Docker web stage):

```bash
cd web && npm install && npm run build   # writes src/agent_chat/static
cd .. && uv run agent-chat serve              # http://127.0.0.1:8000
```

`npm run dev` in `web/` proxies `/api` to port 8000.

```bash
uv run agent-chat serve                       # http://127.0.0.1:8000
# open the URL: pick a config, ask a question, watch tokens + tool cards stream.

# Container: brings up `agent` (CLI demo) + `agent-web` (UI on :8000).
# Traces go to the kind Collector. Port-forward it first — docs/OBSERVABILITY.md.
docker compose -f docker/docker-compose.yaml up --build
```

The composer accepts `/skills` to list the skills loaded by the selected config,
and `/<skill-name>` (for example `/sap-jira`, `/kb-capture`, `/sap-authentication`)
to run that skill. The skill body is placed on the turn before the model answers,
and the timeline shows a `skill_*` call. A name that is not loaded returns the
list instead of calling the model. Typing `/` opens a menu of the current config's
skills.

When OTEL is on, the answer ends with the Jaeger URL
(`http://127.0.0.1:16686/trace/<trace_id>` unless `OTEL_UI_BASE_URL` overrides it).
How to open that link in Jaeger and compare
two answers: [OBSERVABILITY.md](OBSERVABILITY.md).

## Security Precautions & Design Decisions

- **Same-origin, no CORS** — the HTML is served by the *same* app as `/api/*`, so
  no cross-origin surface is opened. Deliberate, documented decision.
- **Bind to `127.0.0.1` by default** — `agent serve` listens on localhost only;
  `0.0.0.0` is opt-in (and only used inside the container where the port is
  explicitly published).
- **Error containment** — the SSE loop is wrapped in try/except: any failure
  (missing API key, provider error, MCP crash) is caught and surfaced as a
  terminal `error` frame rather than leaking a stack trace or hanging the stream.
  Verified live: a missing `ANTHROPIC_API_KEY` produced a clean `error` frame and
  closed the connection.
- **Tool-output truncation** — outputs capped at ~8 KB per frame so a runaway
  tool cannot balloon the stream or the browser.
- **No side effects on discovery** — `GET /api/configs` only reads *configured*
  tool names; it does **not** spawn MCP subprocesses, keeping it fast and safe to
  poll (also used as the Docker healthcheck).
- **Non-root container** — the Dockerfile runs as uid 1001, unchanged.
- **Secrets stay in env** — API keys are read from the environment (never
  hardcoded, never sent to the browser).
- **Concurrency safety** — per-config `asyncio.Lock` prevents duplicate MCP
  subprocess spawns on concurrent first-hits.

## Known Limitations

The SQLite checkpointer is a **sync** connection reused across async requests —
fine for demo / single-user scale (matches the existing CLI behavior), but heavy
concurrent writes to the *same* `thread_id` could error (in-memory backend is
unaffected). Upgrade path: `AsyncSqliteSaver` (out of scope for this feature).

**Sessions sidebar tradeoffs** (frontend-only, by design):

- **Tool cards are not persisted.** Tool-call cards are transient run artifacts
  rendered live during a stream; only user + assistant *text* is saved to
  `localStorage`. Reloading or switching to a stored session replays the
  conversation without the tool cards. This keeps persistence simple, robust, and
  well within the `localStorage` quota.
- **Frontend transcript is the persistence source of truth — not backend
  memory.** `base.yaml` uses the in-memory `memory` backend, which is *not*
  durable across server restarts, and there is no history-list endpoint. So old
  sessions always render from `localStorage`; continuing one after a server
  restart simply starts a fresh backend context under the *same* `thread_id`
  (the UI history stays intact; the agent's server-side memory restarts
  silently). Per-session **isolation** is guaranteed purely by sending a distinct
  `thread_id` per session — no backend change was needed.
- **Multi-tab races.** Two browser tabs writing the shared `localStorage` key can
  clobber each other's last write; acceptable for a single-user demo. A `storage`
  event listener re-renders the sidebar when another tab mutates the store
  (best-effort).

## Verification

- `uv sync` — fastapi / uvicorn resolved cleanly, no conflicts.
- `uv run pytest` — **16 passed** (13 existing untouched + 3 new), fully
  network-free.
- `uv run ruff check src tests` — clean.
- `uv build --wheel` — wheel includes `agent_chat/static/index.html`.
- **Live server** (`agent serve`): clean startup → all 3 endpoints served 200 →
  SSE streamed a real frame → clean lifespan shutdown.

The CLI remains completely unchanged, so nothing regressed while gaining a
production-shaped browser interface.

## Sources

- [FastAPI — StreamingResponse / SSE](https://fastapi.tiangolo.com/advanced/custom-response/)
- [LangGraph streaming (astream_events v2)](https://langchain-ai.github.io/langgraph/how-tos/streaming/)
- [MDN — Using ReadableStream (fetch)](https://developer.mozilla.org/en-US/docs/Web/API/ReadableStream)
- [MDN — Window.localStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage)
- [MDN — Storage quotas and eviction criteria](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria)
