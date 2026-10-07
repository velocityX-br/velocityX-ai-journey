# Architecture

Agent Chat is a thin, **config-driven** wrapper around LangChain's standard
ReAct agent (`langchain.agents.create_agent`, the LangGraph 1.x entrypoint that
supersedes the deprecated `langgraph.prebuilt.create_react_agent`), with tools
supplied by any Model Context Protocol (MCP) server. A YAML file changes the
prompt, model, and tools. Every config shares this same loop.

## Layers

```
              ┌──────────────────────────────────────────────┐
   configs/   │  YAML use case  (name, prompt, llm, mcp, mem) │  ← prompt, model,
   *.yaml     └──────────────────────────────────────────────┘    and tools
                              │ load_config()
                              ▼
              ┌──────────────────────────────────────────────┐
   config.py  │  AgentConfig (pydantic)  — validated, merged  │
              └──────────────────────────────────────────────┘
                 │              │                │
        build_llm│   get_tools  │  build_checkpointer
                 ▼              ▼                ▼
          ┌───────────┐  ┌────────────┐  ┌───────────────┐
   llm.py │ ChatModel │  │ MCP tools  │  │ Checkpointer  │ memory.py
          │ anthropic │  │ (Multi-    │  │ InMemory /    │
          │ openai    │  │  Server    │  │ Sqlite        │
          │ ollama    │  │  MCPClient)│  └───────────────┘
          └───────────┘  └────────────┘         │
                 │              │                │
                 └──────────────┴────────────────┘
                              │ build_graph()
                              ▼
              ┌──────────────────────────────────────────────┐
   graph.py   │  create_agent(model, tools, system_prompt,    │
              │                checkpointer)                  │  ← LangChain 1.x std
              └──────────────────────────────────────────────┘
                              │ ainvoke() / astream_events()
                 ┌────────────┴───────────┐
              cli.py                     api.py
        (run/show/serve)          (FastAPI + SSE Web UI)
```

## Two entrypoints, one core

Both surfaces build on the identical `load_config → build_graph` seam — no graph,
config, memory, or tool logic is duplicated.

| Surface | Command | How it drives the graph |
|---|---|---|
| **CLI** | `agent run` / `agent show` | `ainvoke()` for one answer. |
| **Web UI** | `agent serve` → `http://127.0.0.1:8000` | `astream_events(v2)` mapped to SSE frames. |

### Web UI (`api.py` + `static/index.html`)

```
  browser  ──POST /api/chat {config,query,thread_id}──▶  FastAPI
     ▲                                                     │
     │        SSE frames                                   │  AgentRegistry
     │  token / tool_start / tool_end / done / error       │  (lazy per-config
     └─────────────────────────────────────────────────── ▼   compiled-graph cache)
                                                    graph.astream_events(v2)
```

- **`AgentRegistry`** on `app.state` builds each config's graph **once, lazily**,
  and caches it for the server's lifetime. A per-config `asyncio.Lock` prevents
  duplicate MCP-subprocess spawns on concurrent first-hits.
- The checkpointer (`build_checkpointer`, a sync `@contextmanager`) and MCP
  subprocesses must outlive per-request calls, so each is entered into a single
  `AsyncExitStack` opened in the FastAPI **lifespan** and released on shutdown.
- **POST + `fetch` streaming**, not `EventSource` — EventSource is GET-only and
  can't carry a JSON body. The frontend reads `res.body.getReader()` and splits
  on `\n\n`.
- **Same-origin, no CORS**: the HTML is served by the same app as `/api/*`.
- `_chunk_text` flattens Anthropic's list-of-content-blocks streaming into plain
  text so the UI never renders raw block dicts.

## The ReAct loop (what `create_agent` runs)

```
   user query
       │
       ▼
   ┌────────┐   tool_calls?   ┌────────┐
   │  LLM   │ ───────────────▶│  tools │
   │ (agent)│◀─────────────── │ (MCP)  │
   └────────┘   tool results  └────────┘
       │  no tool_calls
       ▼
   final answer  (+ checkpoint written for this thread_id)
```

## Extension points

| I want to… | Change | Code edit? |
|---|---|---|
| Add a chat config | new `configs/<case>.yaml` (prompt, model, tools) | **No** |
| Attach tools | add entries under `mcp_servers:` | **No** |
| Switch LLM vendor | `llm.provider` (or `AGENT_LLM__PROVIDER`) | **No** |
| Persist conversations | `memory.backend: sqlite` | **No** |
| Turn on tracing | set `LANGCHAIN_TRACING_V2=true` and/or `OTEL_TRACES_EXPORTER` | **No** |
| Serve a browser UI | `agent-chat serve` | **No** |
| Add a new provider (e.g. bedrock) | `llm.py` factory branch | Yes |
| Change graph topology | `graph.py` | Yes |

OpenTelemetry spans (LLM, MCP tools, RAG, external calls) are off until
`OTEL_TRACES_EXPORTER` or `OTEL_EXPORTER_OTLP_ENDPOINT` is set. How to read a
trace in the kind cluster's Jaeger UI: [OBSERVABILITY.md](OBSERVABILITY.md).

## Why these choices

- **LangGraph** — durable, production-grade agent runtime (checkpointing,
  human-in-the-loop, streaming). Used at ServiceNow, LinkedIn, Uber, Klarna.
- **MCP via `langchain-mcp-adapters`** — open standard; lets the agent reuse the
  MCP servers already in this repo (`gardener-ai-mcp`, `sci-ai-mcp`, ...).
- **Pydantic config** — one validated seam so a use case is data, not code.

## Sources
- LangGraph: https://www.langchain.com/langgraph
- MCP: https://modelcontextprotocol.io/introduction
- langchain-mcp-adapters: https://github.com/langchain-ai/langchain-mcp-adapters
