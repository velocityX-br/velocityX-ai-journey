"""Per-source sub-agents. No network and no real MCP server.

A scripted chat model stands in for the child LLM. The test checks that a
delegate call sees only one source's tools and that the raw tool payload stays
inside the child.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402

from agent_chat.config import AgentConfig  # noqa: E402
from agent_chat.graph import build_graph  # noqa: E402
from agent_chat.subagents import (  # noqa: E402
    build_delegate_tool,
    connected_sources,
    delegate_catalog,
    source_of_tool,
    tools_for_source,
)


def _tool(name: str, payload: str) -> StructuredTool:
    def _run(query: str) -> str:
        return payload

    return StructuredTool.from_function(func=_run, name=name, description=f"Tool {name}")


class ScriptedChatModel(BaseChatModel):
    """Returns AIMessages in order. bind_tools records the names it was given."""

    responses: list[AIMessage]
    bound: list[list[str]] = []
    _idx: int = 0

    def __init__(self, responses: list[AIMessage], **kwargs: Any) -> None:
        super().__init__(responses=responses, bound=[], **kwargs)

    @property
    def _llm_type(self) -> str:
        return "scripted-subagent"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:  # noqa: ARG002
        self.bound.append([tool.name for tool in tools])
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        msg = self.responses[self._idx]
        object.__setattr__(self, "_idx", self._idx + 1)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_source_of_tool_uses_prefix_not_a_fixed_catalog() -> None:
    assert source_of_tool("mcp__gardener-ai-mcp__search_docs") == "gardener-ai-mcp"
    assert source_of_tool("mcp__sci-ai-mcp__search_operation_docs") == "sci-ai-mcp"
    assert source_of_tool("web_search") == "web_search"
    assert source_of_tool("skill_sci_multi_source_research") is None
    assert source_of_tool("delegate_source") is None


def test_tools_for_source_keeps_one_server() -> None:
    gardener = _tool("mcp__gardener-ai-mcp__search_docs", "g")
    sci = _tool("mcp__sci-ai-mcp__search_docs", "s")
    web = _tool("web_search", "w")
    tools = [gardener, sci, web]
    assert [t.name for t in tools_for_source(tools, "sci-ai-mcp")] == ["mcp__sci-ai-mcp__search_docs"]
    assert connected_sources(tools) == ["gardener-ai-mcp", "sci-ai-mcp", "web_search"]


def test_catalog_lists_only_connected_sources() -> None:
    catalog = delegate_catalog(["sci-ai-mcp", "web_search"])
    assert "delegate_source" in catalog
    assert "sci-ai-mcp" in catalog
    assert "web_search" in catalog
    assert "not connected" in catalog
    assert delegate_catalog([]) == ""


def test_no_sources_means_no_delegate_tool() -> None:
    model = ScriptedChatModel([AIMessage(content="unused")])
    assert build_delegate_tool(model, [_tool("add", "1")], recursion_limit=5) is None


async def test_unknown_source_is_a_gap() -> None:
    model = ScriptedChatModel([AIMessage(content="should not run")])
    tool = build_delegate_tool(
        model,
        [_tool("mcp__sci-ai-mcp__search_docs", "hit")],
        recursion_limit=5,
    )
    assert tool is not None
    result = await tool.ainvoke({"source": "sap-wiki", "task": "look up dns"})
    assert "sap-wiki" in result
    assert "not connected" in result
    assert model.bound == []


async def test_delegate_hides_raw_hits_and_limits_tools() -> None:
    raw = "RAW_DUMP_SHOULD_NOT_LEAK title=SCI DNS url=https://example.test/sci"
    sci = _tool("mcp__sci-ai-mcp__search_docs", raw)
    gardener = _tool("mcp__gardener-ai-mcp__search_code", "other-server")
    web = _tool("web_search", "web-dump")
    model = ScriptedChatModel(
        [
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
            AIMessage(content="Finding: DNS handover. Citation: SCI DNS https://example.test/sci"),
        ]
    )
    tool = build_delegate_tool(model, [sci, gardener, web], recursion_limit=8)
    assert tool is not None
    result = await tool.ainvoke({"source": "sci-ai-mcp", "task": "Research DNS. Angles: handover, zone."})

    assert "Finding: DNS handover" in result
    assert "RAW_DUMP_SHOULD_NOT_LEAK" not in result
    assert model.bound
    assert model.bound[-1] == ["mcp__sci-ai-mcp__search_docs"]


async def test_graph_exposes_delegate_tool_beside_leaf_tools() -> None:
    model = ScriptedChatModel([AIMessage(content="done")])
    cfg = AgentConfig(name="smoke", system_prompt="test")
    graph = await build_graph(
        cfg,
        llm=model,
        tools=[_tool("mcp__sci-ai-mcp__search_docs", "hit"), _tool("web_search", "web")],
    )
    await graph.ainvoke({"messages": [{"role": "user", "content": "hi"}]})
    assert model.bound
    names = model.bound[-1]
    assert "delegate_source" in names
    assert "mcp__sci-ai-mcp__search_docs" in names
    assert "web_search" in names
