"""Graph smoke test — compiles and runs with a fake LLM + a local tool.

No API key and no network needed (CI-safe). A tiny scripted chat model plays
the LLM's turns: first a tool call to ``add``, then a final answer that
includes the tool result. Proves build_graph wires tools + checkpointer + prompt.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from agent_chat.config import AgentConfig  # noqa: E402
from agent_chat.graph import build_graph  # noqa: E402
from agent_chat.memory import build_checkpointer  # noqa: E402


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


class ScriptedChatModel(BaseChatModel):
    """A fake chat model that returns a fixed list of AIMessages in order.

    Unlike GenericFakeChatModel it implements ``bind_tools`` (a no-op that
    returns self), which ``create_agent`` requires when tools are attached.
    """

    responses: list[AIMessage]
    _idx: int = 0

    def __init__(self, responses: list[AIMessage], **kwargs: Any) -> None:
        super().__init__(responses=responses, **kwargs)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:  # noqa: ARG002
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        msg = self.responses[self._idx]
        object.__setattr__(self, "_idx", self._idx + 1)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _tool_calling_llm() -> ScriptedChatModel:
    return ScriptedChatModel(
        [
            AIMessage(content="", tool_calls=[{"name": "add", "args": {"a": 3, "b": 5}, "id": "call_1"}]),
            AIMessage(content="The sum is 8."),
        ]
    )


async def test_graph_compiles_and_runs_with_tool() -> None:
    cfg = AgentConfig(name="smoke", system_prompt="test")
    with build_checkpointer(cfg.memory) as saver:
        graph = await build_graph(cfg, checkpointer=saver, llm=_tool_calling_llm(), tools=[add])
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "add 3 and 5"}]},
            config={"configurable": {"thread_id": "t1"}},
        )

    messages = result["messages"]
    # user -> ai(toolcall) -> tool -> ai(final)
    assert messages[-1].content == "The sum is 8."
    tool_msgs = [m for m in messages if m.type == "tool"]
    assert tool_msgs and "8" in str(tool_msgs[0].content)


async def test_graph_compiles_with_no_tools() -> None:
    cfg = AgentConfig(name="smoke-notools", system_prompt="test")
    llm = ScriptedChatModel([AIMessage(content="hello")])
    graph = await build_graph(cfg, llm=llm, tools=[])
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "hi"}]})
    assert result["messages"][-1].content == "hello"
