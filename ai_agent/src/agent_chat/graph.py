"""Graph builder — the reusable agent core.

Wraps LangChain's battle-tested ``create_agent`` (the LangGraph 1.x standard
ReAct agent) so that the LLM provider, MCP tools, memory/checkpointing, and
system prompt are all injected from config. ``build_graph`` returns a compiled
graph callable from the CLI, tests, or a future API — the single construction
seam.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_chat.config import CONFIGS_DIR, AgentConfig
from agent_chat.llm import build_llm
from agent_chat.mcp_tools import get_tools, mcp_catalog
from agent_chat.skills import build_skill_tools, discover_skills, resolve_skill_dirs, skills_catalog
from agent_chat.subagents import build_delegate_tool, connected_sources, delegate_catalog
from agent_chat.tool_history import ToolResultRepairMiddleware
from agent_chat.web_search import build_web_search_tool

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph


async def build_graph(
    config: AgentConfig,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    llm: BaseChatModel | None = None,
    tools: list[BaseTool] | None = None,
    base_dir: Path | None = None,
) -> CompiledStateGraph:
    """Compile a ReAct agent from ``config``.

    Parameters ``llm`` and ``tools`` allow tests to inject fakes and skip both
    the provider SDK and any live MCP connection. When omitted they are built
    from the config (real provider, real MCP servers).
    """
    # LangChain 1.x standard entrypoint (supersedes langgraph.prebuilt.create_react_agent).
    from langchain.agents import create_agent

    model = llm if llm is not None else build_llm(config.llm)

    if tools is None:
        tools = await get_tools(config.mcp_servers, base_dir=base_dir or CONFIGS_DIR)
    mcp_tools = list(tools)

    # Optional built-in web-search tool (config.web_search). Additive: when off
    # or when tools are injected by tests this simply isn't appended.
    if config.web_search:
        tools = tools + [build_web_search_tool()]

    # Leaf tools only: MCP servers plus web_search. Skills and the delegate tool
    # stay on the main agent so a sub-agent cannot fan out or follow a skill.
    leaf_tools = list(tools)

    # Discover config-declared skills and expose each as a `skill_<name>` tool,
    # advertising the catalog in the system prompt (Claude Code behavior). When
    # `config.skills` is empty this is a no-op, so injected-tools tests are safe.
    resolved = base_dir or CONFIGS_DIR
    skills = discover_skills(resolve_skill_dirs(config.skills, resolved))
    tools = tools + build_skill_tools(skills)

    delegate = build_delegate_tool(model, leaf_tools, recursion_limit=config.recursion_limit)
    if delegate is not None:
        tools = tools + [delegate]

    prompt = config.system_prompt
    if catalog := mcp_catalog(mcp_tools):
        prompt = f"{prompt}\n\n{catalog}"
    if catalog := skills_catalog(skills):
        prompt = f"{prompt}\n\n{catalog}"
    if catalog := delegate_catalog(connected_sources(leaf_tools)):
        prompt = f"{prompt}\n\n{catalog}"

    return create_agent(
        model,
        tools=tools,
        system_prompt=prompt,
        checkpointer=checkpointer,
        middleware=[ToolResultRepairMiddleware()],
    )
