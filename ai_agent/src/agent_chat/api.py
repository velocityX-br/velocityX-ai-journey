"""FastAPI Web UI — a browser front-end over the same build seam as the CLI.

The CLI and this API are two entrypoints onto one core: ``load_config`` →
``build_graph``. Nothing about the graph, config, memory, or tools is duplicated
here. This module adds an HTTP surface that streams the agent's output token by
token over Server-Sent Events (SSE) and visualizes tool calls.

Lifetime model (why this is a bit more than a thin wrapper):

  * A compiled graph holds long-lived resources — a checkpointer (a SQLite
    connection or in-memory saver) and the MCP stdio subprocesses spawned by
    ``build_graph``. Those must **outlive** any single request, so we build each
    config's graph once, lazily, and keep it in an ``AgentRegistry`` cached on
    ``app.state`` for the server's lifetime.
  * ``build_checkpointer`` is a sync ``@contextmanager``; we enter each one into
    a single ``AsyncExitStack`` opened in the lifespan and closed on shutdown,
    which releases SQLite connections and MCP subprocesses cleanly.
  * A per-config ``asyncio.Lock`` (guarded by a global lock) prevents two
    concurrent first-hits from spawning duplicate MCP subprocesses.

Tests inject a ``graph_factory`` so ``build_graph`` (and thus a real provider /
MCP connection) is never touched — see ``tests/test_api.py``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_observability import current_trace_id, preview, span, trace_url
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent_chat.config import CONFIGS_DIR, AgentConfig, load_config
from agent_chat.graph import build_graph
from agent_chat.memory import build_checkpointer
from agent_chat.observability import configure_observability
from agent_chat.otel_callback import OtelCallbackHandler
from agent_chat.skills import (
    Skill,
    discover_skills,
    find_skill,
    format_skills_message,
    missing_skill_message,
    parse_slash,
    resolve_skill_dirs,
    skill_tool_name,
    skill_turn_query,
)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

# Truncate very large tool outputs so a single frame can't balloon the stream.
_MAX_TOOL_OUTPUT = 8192

# A factory that turns a resolved config into a compiled graph. The default
# builds the real graph inside the given checkpointer's lifetime; tests swap in
# a fake so no provider/MCP is contacted.
GraphFactory = Callable[[AgentConfig, Any], Awaitable["CompiledStateGraph"]]


async def _default_graph_factory(cfg: AgentConfig, checkpointer: Any) -> CompiledStateGraph:
    return await build_graph(cfg, checkpointer=checkpointer)


class AgentRegistry:
    """Lazily builds and caches one compiled graph per config name.

    Each graph's checkpointer context is entered into the shared ``AsyncExitStack``
    so it stays open for the server's lifetime and is torn down on shutdown.
    """

    def __init__(self, stack: AsyncExitStack, graph_factory: GraphFactory) -> None:
        self._stack = stack
        self._graph_factory = graph_factory
        self._graphs: dict[str, CompiledStateGraph] = {}
        self._configs: dict[str, AgentConfig] = {}
        self._global_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, name: str, configs_dir: Path | None) -> tuple[AgentConfig, CompiledStateGraph]:
        """Return (config, compiled graph) for ``name``, building once on first hit."""
        if name in self._graphs:
            return self._configs[name], self._graphs[name]

        # Serialize first-build per config so concurrent requests don't spawn
        # duplicate MCP subprocesses.
        async with self._global_lock:
            lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if name in self._graphs:  # another waiter built it
                return self._configs[name], self._graphs[name]

            cfg = load_config(name, configs_dir=configs_dir)
            # build_checkpointer is a sync contextmanager; enter it into the
            # async stack so the SQLite conn / InMemorySaver outlives requests.
            saver = self._stack.enter_context(build_checkpointer(cfg.memory))
            graph = await self._graph_factory(cfg, saver)
            self._configs[name] = cfg
            self._graphs[name] = graph
            return cfg, graph


def _chunk_text(content: Any) -> str:
    """Flatten an ``AIMessageChunk.content`` into plain text.

    Anthropic streams content as a list of blocks (``{"type": "text", ...}``),
    while other providers stream a plain string. Return only the text so the UI
    never shows raw block dicts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _tool_output_text(output: Any) -> str:
    """Render a tool's output as text, preferring a ToolMessage's ``.content``."""
    content = getattr(output, "content", output)
    text = content if isinstance(content, str) else str(content)
    if len(text) > _MAX_TOOL_OUTPUT:
        text = text[:_MAX_TOOL_OUTPUT] + "…[truncated]"
    return text


def _chunk_reasoning(chunk: Any) -> str:
    """Extract streamed *reasoning* (extended-thinking) text from a chunk.

    Anthropic streams thinking as content blocks distinct from text ones:
    ``{"type": "thinking", "thinking": "..."}`` (and opaque
    ``{"type": "redacted_thinking"}`` blocks, which carry no readable text).
    Gateway/OpenAI-style streams instead surface reasoning under
    ``additional_kwargs.reasoning_content``. We return only the readable
    reasoning text so the UI can show it in a separate "Thinking" card.
    """
    parts: list[str] = []
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                parts.append(str(block.get("thinking", "")))
    extra = getattr(chunk, "additional_kwargs", None)
    if isinstance(extra, dict):
        rc = extra.get("reasoning_content")
        if isinstance(rc, str):
            parts.append(rc)
    return "".join(parts)


def _sse(payload: dict[str, Any]) -> str:
    """Encode one SSE frame: ``data: <json>\\n\\n``."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _delegate_parent(event: dict[str, Any], delegate_runs: set[str]) -> str | None:
    """Return the delegate_source run id that owns this nested event, if any."""
    for parent_id in event.get("parent_ids") or []:
        key = str(parent_id)
        if key in delegate_runs:
            return key
    return None


def _with_parent(payload: dict[str, Any], parent_id: str | None) -> dict[str, Any]:
    if parent_id:
        payload["parent_id"] = parent_id
    return payload


def _loaded_skills(cfg: AgentConfig, configs_dir: Path | None) -> list[Skill]:
    base = configs_dir or CONFIGS_DIR
    return discover_skills(resolve_skill_dirs(cfg.skills, base))


async def _local_reply(cfg: AgentConfig, thread_id: str, query: str, text: str) -> AsyncIterator[str]:
    """One answer with no model call. Still one trace, so the turn has a Jaeger URL."""
    async with span(
        "invoke_agent",
        **{
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.conversation.id": thread_id,
            "gen_ai.request.model": cfg.llm.model,
            "ai.agent.config": cfg.name,
            "ai.query": preview(query),
        },
    ) as current:
        yield _sse({"type": "token", "text": text})
        if current.is_recording():
            current.set_attribute("ai.answer", preview(text))
        trace_id = current_trace_id()
        yield _sse({"type": "done", "trace_id": trace_id, "trace_url": trace_url(trace_id)})


async def _event_stream(
    graph: CompiledStateGraph,
    cfg: AgentConfig,
    query: str,
    thread_id: str,
    *,
    invoked: Skill | None = None,
) -> AsyncIterator[str]:
    """Map ``graph.astream_events(v2)`` onto typed SSE frames."""
    inputs = {"messages": [{"role": "user", "content": query}]}
    run_config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": cfg.recursion_limit,
    }
    answer_parts: list[str] = []
    delegate_runs: set[str] = set()
    try:
        async with span(
            "invoke_agent",
            **{
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.conversation.id": thread_id,
                "gen_ai.request.model": cfg.llm.model,
                "ai.agent.config": cfg.name,
                "ai.query": preview(query),
            },
        ) as current:
            # Constructed here so every chat and tool span, including sub-agents,
            # parents onto this invoke_agent trace.
            run_config["callbacks"] = [OtelCallbackHandler()]
            if invoked is not None:
                # The skill body is already in the user message. Show the call
                # on the timeline without asking the model to load it again.
                run_id = uuid.uuid4().hex
                tool_name = skill_tool_name(invoked.name)
                yield _sse(
                    {
                        "type": "tool_start",
                        "id": run_id,
                        "name": tool_name,
                        "input": {"command": f"/{invoked.name}"},
                    }
                )
                yield _sse(
                    {
                        "type": "tool_end",
                        "id": run_id,
                        "name": tool_name,
                        "output": f"Loaded /{invoked.name}",
                    }
                )
            async for event in graph.astream_events(inputs, config=run_config, version="v2"):
                kind = event.get("event")
                parent_id = _delegate_parent(event, delegate_runs)
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    reasoning = _chunk_reasoning(chunk)
                    if reasoning:
                        yield _sse(_with_parent({"type": "reasoning", "text": reasoning}, parent_id))
                    text = _chunk_text(chunk.content)
                    if text:
                        # Sub-agent drafts stay on their delegate card. Only the
                        # main agent's text is the answer.
                        if parent_id is None:
                            answer_parts.append(text)
                        yield _sse(_with_parent({"type": "token", "text": text}, parent_id))
                elif kind == "on_tool_start":
                    run_id = str(event.get("run_id"))
                    name = event.get("name")
                    if name == "delegate_source" and parent_id is None:
                        delegate_runs.add(run_id)
                    yield _sse(
                        _with_parent(
                            {
                                "type": "tool_start",
                                "id": run_id,
                                "name": name,
                                "input": event.get("data", {}).get("input"),
                            },
                            parent_id,
                        )
                    )
                elif kind == "on_tool_end":
                    yield _sse(
                        _with_parent(
                            {
                                "type": "tool_end",
                                "id": str(event.get("run_id")),
                                "name": event.get("name"),
                                "output": _tool_output_text(event.get("data", {}).get("output")),
                            },
                            parent_id,
                        )
                    )
            if current.is_recording():
                current.set_attribute("ai.answer", preview("".join(answer_parts)))
            trace_id = current_trace_id()
            yield _sse({"type": "done", "trace_id": trace_id, "trace_url": trace_url(trace_id)})
    except Exception as exc:  # noqa: BLE001 — surface any failure to the client
        yield _sse({"type": "error", "message": str(exc)})


def create_app(configs_dir: Path | None = None, graph_factory: GraphFactory | None = None) -> FastAPI:
    """Build the FastAPI app.

    ``configs_dir`` overrides the config directory (tests); ``graph_factory``
    injects a fake graph builder so no real provider/MCP is contacted.
    """
    factory = graph_factory or _default_graph_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_observability()
        async with AsyncExitStack() as stack:
            app.state.registry = AgentRegistry(stack, factory)
            app.state.configs_dir = configs_dir
            yield
        # Stack exit here releases every checkpointer + MCP subprocess.

    app = FastAPI(title="agent-chat web UI", lifespan=lifespan)

    # IAS OIDC login (BFF). No-op unless IAS_* / SESSION_SECRET env vars are set,
    # so tests and unauthenticated local runs are unaffected. Load .env first so
    # those vars are present before install_auth reads them — create_app runs at
    # import time, before any load_config() would have triggered the dotenv load.
    from agent_chat.config import _load_dotenv_once

    _load_dotenv_once()

    from agent_chat.auth import install_auth

    auth_enabled = install_auth(app)

    @app.get("/", response_model=None)
    async def index() -> FileResponse | JSONResponse:
        # Built by web/ (Vite). FileResponse resolves from the source tree and the wheel.
        if not _INDEX_HTML.is_file():
            return JSONResponse(
                {"detail": "Web UI is not built. From ai_agent/web run: npm install && npm run build"},
                status_code=503,
            )
        return FileResponse(_INDEX_HTML)

    assets = _STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/api/configs")
    async def list_configs(request: Request) -> JSONResponse:
        """List selectable use-case configs (excludes base.yaml). No MCP spawn."""
        directory = request.app.state.configs_dir or CONFIGS_DIR
        items: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.yaml")):
            if path.stem == "base":
                continue
            cfg = load_config(path.stem, configs_dir=directory)
            # Resolve skill dirs (same idiom as graph.build_graph) and report the
            # discovered skill names, so the UI shows the real count — not the
            # number of declared directories.
            loaded = _loaded_skills(cfg, directory)
            # Surface the built-in web-search tool alongside MCP-provided tools so
            # the UI advertises the capability (it isn't an MCP server).
            tool_names = list(cfg.mcp_servers.keys())
            if cfg.web_search:
                tool_names.append("web_search")
            items.append(
                {
                    "name": path.stem,
                    "display_name": cfg.name,
                    "provider": cfg.llm.provider,
                    "model": cfg.llm.model,
                    "tools": tool_names,
                    "skills": [
                        {"name": skill.name, "description": " ".join(skill.description.split())}
                        for skill in loaded
                    ],
                }
            )
        return JSONResponse(items)

    @app.post("/api/chat", response_model=None)
    async def chat(request: Request) -> StreamingResponse | JSONResponse:
        """Stream one turn of the agent for the given config over SSE."""
        # When auth is enabled, require a logged-in session. When it's disabled
        # (no IAS_* env), SessionMiddleware isn't installed so skip the check.
        if auth_enabled and not request.session.get("user"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        body = await request.json()
        name = body.get("config") or "generic_react"
        query = body.get("query") or ""
        thread_id = body.get("thread_id") or "web-default"

        registry: AgentRegistry = request.app.state.registry
        configs_dir = request.app.state.configs_dir

        async def stream() -> AsyncIterator[str]:
            try:
                cfg, graph = await registry.get(name, configs_dir)
            except Exception as exc:  # noqa: BLE001 — build failure → error frame
                yield _sse({"type": "error", "message": str(exc)})
                return
            slash = parse_slash(query)
            skills = _loaded_skills(cfg, configs_dir)
            if slash and slash[0] == "skills":
                async for frame in _local_reply(cfg, thread_id, query, format_skills_message(skills)):
                    yield frame
                return
            invoked: Skill | None = None
            effective = query
            if slash:
                command, rest = slash
                skill = find_skill(skills, command)
                if skill is None:
                    async for frame in _local_reply(cfg, thread_id, query, missing_skill_message(command, skills)):
                        yield frame
                    return
                invoked = skill
                effective = skill_turn_query(skill, rest)
            async for frame in _event_stream(graph, cfg, effective, thread_id, invoked=invoked):
                yield frame

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


# Module-level app for `uvicorn agent_chat.api:app`.
app = create_app()
