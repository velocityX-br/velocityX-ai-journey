"""Agent turn spans: invoke_agent parents chat and execute_tool, and SSE carries the trace id."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("langgraph")

from uuid import uuid4  # noqa: E402

from langchain_core.messages import AIMessage  # noqa: E402
from opentelemetry import context as otel_context  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from agent_chat.api import create_app  # noqa: E402
from agent_chat.graph import build_graph  # noqa: E402
from agent_chat.otel_callback import OtelCallbackHandler  # noqa: E402

from .test_api import _delegate_graph_factory  # noqa: E402
from .test_graph_smoke import ScriptedChatModel, add  # noqa: E402

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)


def _factory():
    async def factory(cfg: Any, checkpointer: Any):
        llm = ScriptedChatModel(
            [
                AIMessage(content="", tool_calls=[{"name": "add", "args": {"a": 3, "b": 5}, "id": "call_1"}]),
                AIMessage(content="The sum is 8."),
            ]
        )
        return await build_graph(cfg, checkpointer=checkpointer, llm=llm, tools=[add])

    return factory


def _frames(raw: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                frames.append(json.loads(line[5:].strip()))
    return frames


def test_chat_trace_parents_chat_and_tool(configs_dir) -> None:
    _EXPORTER.clear()
    app = create_app(configs_dir=configs_dir, graph_factory=_factory())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"config": "generic", "query": "add 3 and 5", "thread_id": "t-otel"},
        ) as res:
            body = "".join(res.iter_text())

    frames = _frames(body)
    done = frames[-1]
    assert done["type"] == "done"
    assert done["trace_id"]
    assert len(done["trace_id"]) == 32
    assert done["trace_url"].endswith("/trace/" + done["trace_id"])

    spans = _EXPORTER.get_finished_spans()
    agent = next(item for item in spans if item.name == "invoke_agent")
    chat = next(item for item in spans if item.name.startswith("chat "))
    tool = next(item for item in spans if item.name == "execute_tool add")
    assert chat.parent is not None and chat.parent.span_id == agent.context.span_id
    assert tool.parent is not None and tool.parent.span_id == agent.context.span_id
    assert chat.context.trace_id == agent.context.trace_id
    assert done["trace_id"] == format(agent.context.trace_id, "032x")
    assert agent.attributes["gen_ai.conversation.id"] == "t-otel"
    assert chat.attributes["ai.agent.role"] == "main"
    assert tool.attributes["ai.agent.role"] == "main"


def _ancestor_names(span: Any, by_id: dict[int, Any]) -> list[str]:
    names: list[str] = []
    parent = span.parent
    while parent is not None and parent.span_id in by_id:
        names.append(by_id[parent.span_id].name)
        parent = by_id[parent.span_id].parent
    return names


def test_subagent_spans_stay_on_the_request_trace(configs_dir) -> None:
    """delegate_source and the child agent's chat and tools share invoke_agent's trace."""
    _EXPORTER.clear()
    app = create_app(configs_dir=configs_dir, graph_factory=_delegate_graph_factory())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat",
            json={"config": "generic", "query": "compare DNS across sources", "thread_id": "t-sub"},
        ) as res:
            body = "".join(res.iter_text())

    done = _frames(body)[-1]
    assert done["type"] == "done"

    spans = _EXPORTER.get_finished_spans()
    agent = next(item for item in spans if item.name == "invoke_agent")
    delegate = next(item for item in spans if item.name == "execute_tool delegate_source")
    child_tool = next(item for item in spans if item.name == "execute_tool mcp__sci-ai-mcp__search_docs")
    chats = [item for item in spans if item.name.startswith("chat ")]
    assert chats, "sub-agent and main chat spans should be recorded"

    trace_id = agent.context.trace_id
    assert done["trace_id"] == format(trace_id, "032x")
    assert {item.context.trace_id for item in spans} == {trace_id}
    assert delegate.parent is not None and delegate.parent.span_id == agent.context.span_id
    assert delegate.attributes["ai.agent.role"] == "subagent"
    assert delegate.attributes["ai.delegate.source"] == "sci-ai-mcp"

    by_id = {item.context.span_id: item for item in spans}
    assert "execute_tool delegate_source" in _ancestor_names(child_tool, by_id)
    assert "invoke_agent" in _ancestor_names(child_tool, by_id)
    assert child_tool.attributes["ai.agent.role"] == "subagent"
    subagent_chats = [item for item in chats if item.attributes.get("ai.agent.role") == "subagent"]
    assert subagent_chats
    assert all("invoke_agent" in _ancestor_names(item, by_id) for item in subagent_chats)


def test_callback_span_rejoins_request_trace_when_context_is_lost() -> None:
    """A callback that runs with a blank context still hangs off invoke_agent."""
    _EXPORTER.clear()
    tracer = trace.get_tracer("agent_chat")
    run_id = uuid4()
    with tracer.start_as_current_span("invoke_agent"):
        handler = OtelCallbackHandler()
        blank = otel_context.attach(otel_context.Context())
        try:
            handler.on_tool_start(
                {"name": "delegate_source"},
                "",
                run_id=run_id,
                name="delegate_source",
                inputs={"source": "sci-ai-mcp", "task": "Research DNS"},
            )
            handler.on_chat_model_start(
                {"id": ["scripted"]},
                [[]],
                run_id=uuid4(),
                invocation_params={"model": "scripted"},
            )
            handler.on_tool_end("summary", run_id=run_id)
        finally:
            otel_context.detach(blank)

    spans = _EXPORTER.get_finished_spans()
    agent = next(item for item in spans if item.name == "invoke_agent")
    delegate = next(item for item in spans if item.name == "execute_tool delegate_source")
    assert delegate.context.trace_id == agent.context.trace_id
    assert delegate.parent is not None and delegate.parent.span_id == agent.context.span_id
    assert delegate.attributes["ai.delegate.source"] == "sci-ai-mcp"
