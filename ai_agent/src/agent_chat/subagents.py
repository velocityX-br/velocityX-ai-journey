"""Per-source sub-agents for multi-source research.

The main agent keeps its leaf tools for a single-source question. When a prompt
needs a comprehensive answer across several data sources, it calls
``delegate_source`` once per source in the same turn. Each call runs a fresh
ReAct agent that sees only that source's tools and returns its final message.

Tool ownership stays in each MCP package. This module never names a tool inside
a server. It groups whatever ``get_tools`` discovered by the ``mcp__<server>__``
prefix, plus the built-in ``web_search`` tool as its own source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool

WEB_SOURCE = "web_search"


def source_of_tool(name: str) -> str | None:
    """Return the source id for a tool, or None if it is not delegatable.

    MCP tools use ``mcp__<server>__<tool>``. ``web_search`` is its own source.
    Skill tools and ``delegate_source`` itself are not sources.
    """
    if name == WEB_SOURCE:
        return WEB_SOURCE
    parts = name.split("__", 2)
    if len(parts) == 3 and parts[0] == "mcp" and parts[1] and parts[2]:
        return parts[1]
    return None


def tools_for_source(tools: list[BaseTool], source: str) -> list[BaseTool]:
    """Tools that belong to ``source``. Matching is by name prefix, not a fixed catalog."""
    return [tool for tool in tools if source_of_tool(tool.name) == source]


def connected_sources(tools: list[BaseTool]) -> list[str]:
    """Source ids present on ``tools``, in first-seen order."""
    sources: list[str] = []
    seen: set[str] = set()
    for tool in tools:
        source = source_of_tool(tool.name)
        if source is None or source in seen:
            continue
        seen.add(source)
        sources.append(source)
    return sources


def delegate_catalog(sources: list[str]) -> str:
    """Tell the main agent when to fan out and which sources are actually connected.

    Returns ``""`` when there is nothing to delegate to.
    """
    if not sources:
        return ""
    lines = [
        "# Source delegation",
        "When a prompt or skill asks for a comprehensive answer across more than one",
        "data source, call delegate_source once per source in the same turn so the",
        "sub-agents run in parallel. Pass the shared topic and search angles in task.",
        "Each call returns a short cited summary (findings, contradictions, gaps).",
        "After they return, synthesize one answer: where sources agree, where they",
        "conflict, facts only one source has, and gaps nobody covered. Do not invent",
        "content for a source, and do not paste raw search dumps into the answer.",
        "A source named by the prompt that is not in the list below is not connected.",
        "Say so. Do not treat it as a search that already happened.",
        "A narrow question about one source calls that source's tools directly.",
        "Do not delegate it.",
        "The single-source defaults elsewhere in this prompt do not apply when the",
        "request needs more than one source.",
        "",
        "Connected sources for delegate_source:",
    ]
    lines.extend(f"- {source}" for source in sources)
    return "\n".join(lines)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
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
    return "" if content is None else str(content)


def _child_prompt(source: str) -> str:
    return (
        f"You are a research sub-agent for exactly one source: {source}.\n"
        "Use only the tools you were given. Search the angles in the task.\n"
        "Return a short structured summary, not raw dumps:\n"
        "- Key findings, each with a citation (title and URL, or the doc title the tool returned)\n"
        "- Contradictions or outdated items\n"
        "- Gaps this source does not cover\n"
        "Every fact must come from a tool result. If the tools return nothing, say so.\n"
        "Do not invent content and do not answer from prior knowledge."
    )


def _gap(source: str, reason: str) -> str:
    return (
        f"Source {source!r} {reason}. Report this as a gap. "
        "Do not treat it as a completed search and do not invent findings for it."
    )


def build_delegate_tool(
    model: BaseChatModel,
    tools: list[BaseTool],
    *,
    recursion_limit: int,
) -> BaseTool | None:
    """A ``delegate_source(source, task)`` tool, or None when no source is connected.

    The closure keeps the leaf tools (MCP tools and ``web_search``). It does not
    receive skills or itself, so a sub-agent cannot fan out again.
    """
    sources = connected_sources(tools)
    if not sources:
        return None

    from langchain_core.tools import StructuredTool

    listed = ", ".join(sources)

    async def _run(source: str, task: str, config: RunnableConfig) -> str:
        """Research one connected source and return only its short summary."""
        subset = tools_for_source(tools, source)
        if not subset:
            return _gap(source, "is not connected")

        from langchain.agents import create_agent

        child = create_agent(model, tools=subset, system_prompt=_child_prompt(source))
        # Keep the parent callbacks so the child's model and tool events show up
        # on the caller's astream_events. The tool still returns only the summary.
        child_config = dict(config)
        child_config["recursion_limit"] = recursion_limit
        try:
            result = await child.ainvoke(
                {"messages": [{"role": "user", "content": task}]},
                config=child_config,
            )
        except Exception as exc:  # noqa: BLE001 — one source must not fail the others
            return _gap(source, f"failed before producing a summary ({exc})")

        messages = result.get("messages", [])
        text = ""
        for message in reversed(messages):
            if getattr(message, "type", "") == "ai":
                text = _message_text(message).strip()
                if text:
                    break
        if not text:
            return _gap(source, "returned no summary")
        return text

    return StructuredTool.from_function(
        coroutine=_run,
        name="delegate_source",
        description=(
            "Run one source in its own sub-agent and return a short cited summary. "
            f"source is one of: {listed}. "
            "task is the topic plus the shared search angles for that source only. "
            "Call this once per source in the same turn when the answer must combine "
            "multiple MCP servers and/or web_search. A source that is not connected "
            "returns a gap instead of search results."
        ),
    )
