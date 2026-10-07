"""API tests — network-free, provider-free, MCP-free.

Uses Starlette's bundled TestClient (runs the app lifespan) and injects a fake
``graph_factory`` so ``build_graph`` — and thus any real LLM provider or MCP
subprocess — is never touched. The fake reuses the scripted-tool-call pattern
from ``tests/test_graph_smoke.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402
from langchain_core.outputs import ChatGenerationChunk  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from agent_chat import web_search as web_search_mod  # noqa: E402
from agent_chat.api import _chunk_reasoning, _chunk_text, create_app  # noqa: E402
from agent_chat.graph import build_graph  # noqa: E402

from .test_graph_smoke import ScriptedChatModel, add  # noqa: E402


def _tool_calling_graph_factory():
    """Return a graph_factory that builds a real graph with a scripted, tool-calling fake LLM."""

    async def factory(cfg: Any, checkpointer: Any):
        llm = ScriptedChatModel(
            [
                AIMessage(content="", tool_calls=[{"name": "add", "args": {"a": 3, "b": 5}, "id": "call_1"}]),
                AIMessage(content="The sum is 8."),
            ]
        )
        return await build_graph(cfg, checkpointer=checkpointer, llm=llm, tools=[add])

    return factory


def _parse_frames(raw: str) -> list[dict[str, Any]]:
    """Split an SSE body into decoded frame payloads."""
    frames: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                frames.append(json.loads(line[5:].strip()))
    return frames


def test_list_configs(configs_dir: Path) -> None:
    app = create_app(configs_dir=configs_dir, graph_factory=_tool_calling_graph_factory())
    with TestClient(app) as client:
        res = client.get("/api/configs")
        assert res.status_code == 200
        items = res.json()

    names = {c["name"] for c in items}
    assert "base" not in names  # base.yaml is excluded
    assert "generic" in names
    generic = next(c for c in items if c["name"] == "generic")
    assert generic["provider"] == "anthropic"
    assert generic["model"] == "claude-sonnet-4-6"
    assert generic["tools"] == []  # generic has no mcp_servers


def test_chat_streams_tool_and_done(configs_dir: Path) -> None:
    app = create_app(configs_dir=configs_dir, graph_factory=_tool_calling_graph_factory())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"config": "generic", "query": "add 3 and 5", "thread_id": "t-api"},
        ) as res:
            assert res.status_code == 200
            body = "".join(res.iter_text())

    frames = _parse_frames(body)
    types = [f["type"] for f in frames]

    # Ordered: a tool_start, then its matching tool_end, then terminal done.
    assert "tool_start" in types and "tool_end" in types and types[-1] == "done"

    start = next(f for f in frames if f["type"] == "tool_start")
    end = next(f for f in frames if f["type"] == "tool_end")
    assert start["name"] == "add"
    assert start["input"] == {"a": 3, "b": 5}
    assert end["id"] == start["id"]  # same run id links the pair
    assert "8" in end["output"]


class _StreamingScriptedChatModel(ScriptedChatModel):
    """Same script, but yields one chunk so ``astream_events`` emits tokens."""

    def _stream(self, messages, stop=None, run_manager=None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        msg = self.responses[self._idx]
        object.__setattr__(self, "_idx", self._idx + 1)
        chunk = AIMessageChunk(content=msg.content, tool_calls=msg.tool_calls)
        if run_manager and msg.content:
            run_manager.on_llm_new_token(msg.content)
        yield ChatGenerationChunk(message=chunk)


def _delegate_graph_factory():
    """Main agent delegates once; the child calls one MCP tool then summarizes.

    One scripted model serves both agents. Call order is main tool-call, child
    tool-call, child summary, main answer.
    """

    def _search(query: str) -> str:
        return f"raw hit for {query}"

    sci = StructuredTool.from_function(
        func=_search,
        name="mcp__sci-ai-mcp__search_docs",
        description="Search SCI docs",
    )

    async def factory(cfg: Any, checkpointer: Any):
        llm = _StreamingScriptedChatModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_source",
                            "args": {"source": "sci-ai-mcp", "task": "Research DNS"},
                            "id": "call_del",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "mcp__sci-ai-mcp__search_docs",
                            "args": {"query": "dns"},
                            "id": "call_sci",
                        }
                    ],
                ),
                AIMessage(content="CHILD_ONLY_SUMMARY"),
                AIMessage(content="MAIN_ANSWER_ONLY"),
            ]
        )
        return await build_graph(cfg, checkpointer=checkpointer, llm=llm, tools=[sci])

    return factory


def test_chat_subagent_tools_stay_off_the_main_answer(configs_dir: Path) -> None:
    """Child model text is not a bare token, and inner tools point at the delegate run."""
    app = create_app(configs_dir=configs_dir, graph_factory=_delegate_graph_factory())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"config": "generic", "query": "compare dns sources", "thread_id": "t-sub"},
        ) as res:
            assert res.status_code == 200
            body = "".join(res.iter_text())

    frames = _parse_frames(body)
    assert frames[-1]["type"] == "done"

    bare_tokens = [f for f in frames if f["type"] == "token" and "parent_id" not in f]
    assert any("MAIN_ANSWER_ONLY" in f["text"] for f in bare_tokens)
    assert all("CHILD_ONLY_SUMMARY" not in f["text"] for f in bare_tokens)

    delegate = next(f for f in frames if f["type"] == "tool_start" and f["name"] == "delegate_source")
    assert "parent_id" not in delegate
    inner = next(f for f in frames if f["type"] == "tool_start" and f["name"] == "mcp__sci-ai-mcp__search_docs")
    inner_end = next(f for f in frames if f["type"] == "tool_end" and f["id"] == inner["id"])
    assert inner["parent_id"] == delegate["id"]
    assert inner_end["parent_id"] == delegate["id"]


def test_chat_error_frame(configs_dir: Path) -> None:
    async def boom(cfg: Any, checkpointer: Any):
        raise RuntimeError("factory blew up")

    app = create_app(configs_dir=configs_dir, graph_factory=boom)
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"config": "generic", "query": "hi", "thread_id": "t-err"},
        ) as res:
            body = "".join(res.iter_text())

    frames = _parse_frames(body)
    assert frames and frames[-1]["type"] == "error"
    assert "factory blew up" in frames[-1]["message"]


def _web_search_graph_factory():
    """A graph_factory wiring the REAL web_search tool into a scripted LLM.

    The LLM's first turn calls ``web_search``; the second turn answers with the
    result. The tool itself is real (``build_web_search_tool``) so the full graph
    → tool-node → run_web_search → SSE path is exercised. Only the network is
    stubbed (see the monkeypatch in the test), keeping this CI-safe.
    """

    async def factory(cfg: Any, checkpointer: Any):
        tool = web_search_mod.build_web_search_tool()
        llm = ScriptedChatModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "web_search", "args": {"query": "latest python release"}, "id": "call_ws"}],
                ),
                AIMessage(content="The latest release is documented at the cited link."),
            ]
        )
        return await build_graph(cfg, checkpointer=checkpointer, llm=llm, tools=[tool])

    return factory


def test_chat_web_search_streams_tool_frames(configs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end SSE test: the agent calls web_search and the UI gets its frames.

    Network-free: TAVILY is unset and the DuckDuckGo backend is stubbed with a
    deterministic result, so no real HTTP request is made.
    """
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    def fake_ddg(query: str, max_results: int):
        assert query == "latest python release"
        return [{"title": "Python 3.13", "url": "https://python.org/downloads", "snippet": "newest release"}]

    monkeypatch.setattr(web_search_mod, "_search_duckduckgo", fake_ddg)

    app = create_app(configs_dir=configs_dir, graph_factory=_web_search_graph_factory())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"config": "generic", "query": "what is the latest python?", "thread_id": "t-ws"},
        ) as res:
            assert res.status_code == 200
            body = "".join(res.iter_text())

    frames = _parse_frames(body)
    types = [f["type"] for f in frames]
    assert "tool_start" in types and "tool_end" in types and types[-1] == "done"

    start = next(f for f in frames if f["type"] == "tool_start")
    end = next(f for f in frames if f["type"] == "tool_end")
    assert start["name"] == "web_search"
    assert start["input"] == {"query": "latest python release"}
    assert end["id"] == start["id"]  # same run id links the pair
    # The formatted, stubbed result flows through to the tool_end output frame.
    assert "Python 3.13" in end["output"]
    assert "https://python.org/downloads" in end["output"]


class _Chunk:
    """Minimal stand-in for an AIMessageChunk (content + additional_kwargs)."""

    def __init__(self, content: Any, additional_kwargs: dict[str, Any] | None = None) -> None:
        self.content = content
        self.additional_kwargs = additional_kwargs or {}


def test_chunk_reasoning_extracts_anthropic_thinking_block() -> None:
    chunk = _Chunk([{"type": "thinking", "thinking": "step 1... "}, {"type": "text", "text": "answer"}])
    assert _chunk_reasoning(chunk) == "step 1... "
    # The plain-text extractor ignores the thinking block and returns only text.
    assert _chunk_text(chunk.content) == "answer"


def test_chunk_reasoning_extracts_gateway_reasoning_content() -> None:
    chunk = _Chunk("visible text", additional_kwargs={"reasoning_content": "because..."})
    assert _chunk_reasoning(chunk) == "because..."


def test_chunk_reasoning_empty_when_no_reasoning() -> None:
    chunk = _Chunk("just text")
    assert _chunk_reasoning(chunk) == ""


def _attach_skills(configs_dir: Path) -> None:
    from .test_skills import _write_skill

    root = configs_dir / "skills"
    _write_skill(root, "sap-jira", name="sap-jira", description="Manage Jira", body="DO JIRA")
    _write_skill(root, "kb-capture", name="kb-capture", description="Write docs", body="DO KB")
    _write_skill(
        root,
        "sap-authentication",
        name="sap-authentication",
        description="SAP login",
        body="DO AUTH",
    )
    path = configs_dir / "generic.yaml"
    path.write_text(path.read_text() + "skills:\n  - ./skills\n")


def test_slash_skills_lists_loaded_skills(configs_dir: Path) -> None:
    _attach_skills(configs_dir)
    app = create_app(configs_dir=configs_dir, graph_factory=_tool_calling_graph_factory())
    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"config": "generic", "query": "/skills", "thread_id": "t-skills"}) as res:
            body = "".join(res.iter_text())

    frames = _parse_frames(body)
    text = "".join(frame.get("text", "") for frame in frames if frame["type"] == "token")
    assert "/sap-jira" in text
    assert "/kb-capture" in text
    assert "/sap-authentication" in text
    assert frames[-1]["type"] == "done"
    assert not any(frame["type"] == "tool_start" for frame in frames)


def test_slash_invokes_named_skill_before_the_model(configs_dir: Path) -> None:
    _attach_skills(configs_dir)
    app = create_app(configs_dir=configs_dir, graph_factory=_tool_calling_graph_factory())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"config": "generic", "query": "/sap-jira create a bug", "thread_id": "t-jira"},
        ) as res:
            body = "".join(res.iter_text())

    frames = _parse_frames(body)
    start = next(frame for frame in frames if frame["type"] == "tool_start")
    assert start["name"] == "skill_sap_jira"
    assert start["input"] == {"command": "/sap-jira"}
    assert frames[-1]["type"] == "done"


def test_slash_unknown_skill_does_not_call_the_model(configs_dir: Path) -> None:
    _attach_skills(configs_dir)
    app = create_app(configs_dir=configs_dir, graph_factory=_tool_calling_graph_factory())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"config": "generic", "query": "/not-a-skill", "thread_id": "t-missing"},
        ) as res:
            body = "".join(res.iter_text())

    frames = _parse_frames(body)
    text = "".join(frame.get("text", "") for frame in frames if frame["type"] == "token")
    assert "/not-a-skill" in text
    assert "没有加载" in text
    assert not any(frame["type"] == "tool_start" for frame in frames)
