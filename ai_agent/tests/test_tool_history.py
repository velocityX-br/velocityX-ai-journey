"""Dangling tool_use blocks are paired with a tool_result before the next model call."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent_chat.tool_history import ToolResultRepairMiddleware, repair_dangling_tool_calls


def test_history_with_results_is_left_alone() -> None:
    messages = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"id": "toolu_a", "name": "search", "args": {}}]),
        ToolMessage(content="ok", tool_call_id="toolu_a"),
    ]
    assert repair_dangling_tool_calls(messages) is None


def test_inserts_missing_results_before_the_next_user_message() -> None:
    messages = [
        HumanMessage(content="q", id="h1"),
        AIMessage(
            content=[{"type": "tool_use", "id": "toolu_bdrk_one", "name": "search", "input": {}}],
            tool_calls=[
                {"id": "toolu_bdrk_one", "name": "search", "args": {}},
                {"id": "toolu_bdrk_two", "name": "search_operation_docs", "args": {}},
            ],
            id="a1",
        ),
        HumanMessage(content="again", id="h2"),
    ]
    repaired = repair_dangling_tool_calls(messages)
    assert repaired is not None
    assert [type(message) for message in repaired] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        ToolMessage,
        HumanMessage,
    ]
    assert [message.tool_call_id for message in repaired if isinstance(message, ToolMessage)] == [
        "toolu_bdrk_one",
        "toolu_bdrk_two",
    ]
    assert repaired[-1].content == "again"


def test_keeps_an_existing_result_and_fills_only_the_gap() -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"id": "a", "name": "t", "args": {}}, {"id": "b", "name": "t", "args": {}}]),
        ToolMessage(content="done", tool_call_id="a"),
        HumanMessage(content="next"),
    ]
    repaired = repair_dangling_tool_calls(messages)
    assert repaired is not None
    tool_ids = [message.tool_call_id for message in repaired if isinstance(message, ToolMessage)]
    assert tool_ids == ["a", "b"]
    assert repaired[-1].content == "next"


def test_middleware_replaces_the_message_list() -> None:
    messages = [
        AIMessage(
            content=[{"type": "tool_use", "id": "toolu_only_in_content", "name": "search", "input": {}}],
            id="a1",
        ),
        HumanMessage(content="next", id="h1"),
    ]
    update = ToolResultRepairMiddleware().before_model({"messages": messages}, None)
    assert update is not None
    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    assert any(isinstance(message, ToolMessage) for message in update["messages"])
