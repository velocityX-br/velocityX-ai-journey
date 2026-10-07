"""Repair a checkpoint that has ``tool_use`` blocks without ``tool_result``s.

Anthropic rejects the next request when an assistant message contains
``tool_use`` ids and the following message does not carry a ``tool_result``
for each of them. That happens when a turn is cancelled after the model asked
for tools and before those results were checkpointed. The next user message on
the same thread then fails with HTTP 400.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

logger = logging.getLogger(__name__)

_INTERRUPTED = "Tool call was interrupted before its result was saved."


def _tool_use_ids(message: AIMessage) -> list[str]:
    """Ids Anthropic will require a ``tool_result`` for, in first-seen order."""
    ids: list[str] = []
    seen: set[str] = set()
    for call in message.tool_calls or []:
        tool_id = call.get("id")
        if isinstance(tool_id, str) and tool_id and tool_id not in seen:
            seen.add(tool_id)
            ids.append(tool_id)
    content = message.content
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_id = block.get("id")
            if isinstance(tool_id, str) and tool_id and tool_id not in seen:
                seen.add(tool_id)
                ids.append(tool_id)
    return ids


def repair_dangling_tool_calls(messages: list[BaseMessage]) -> list[BaseMessage] | None:
    """Insert an error ``ToolMessage`` for every tool id that has no result.

    Placeholders sit with the other tool results for that assistant message,
    before the next user message, which is where Anthropic requires them.
    Returns ``None`` when the history is already valid.
    """
    repaired: list[BaseMessage] = []
    changed = False
    index = 0
    while index < len(messages):
        message = messages[index]
        repaired.append(message)
        index += 1
        if not isinstance(message, AIMessage):
            continue
        needed = _tool_use_ids(message)
        if not needed:
            continue
        satisfied: set[str] = set()
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            repaired.append(messages[index])
            tool_id = messages[index].tool_call_id
            if isinstance(tool_id, str) and tool_id:
                satisfied.add(tool_id)
            index += 1
        missing = [tool_id for tool_id in needed if tool_id not in satisfied]
        if not missing:
            continue
        changed = True
        repaired.extend(ToolMessage(content=_INTERRUPTED, tool_call_id=tool_id, status="error") for tool_id in missing)
    if not changed:
        return None
    logger.info("Repaired %s dangling tool call(s) before the next model request", _count_inserted(repaired, messages))
    return repaired


def _count_inserted(repaired: list[BaseMessage], original: list[BaseMessage]) -> int:
    return len(repaired) - len(original)


class ToolResultRepairMiddleware(AgentMiddleware):
    """Fill in missing tool results before each model call."""

    def before_model(self, state: dict[str, Any], runtime: object) -> dict[str, Any] | None:
        repaired = repair_dangling_tool_calls(list(state["messages"]))
        if repaired is None:
            return None
        # ``add_messages`` appends. Replacing the list is what puts the new
        # tool results between the assistant message and the following user turn.
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]}

    async def abefore_model(self, state: dict[str, Any], runtime: object) -> dict[str, Any] | None:
        return self.before_model(state, runtime)
