"""MCP tool loader — reuses existing MCP servers as the agent's tool layer.

Builds a ``MultiServerMCPClient`` from ``config.mcp_servers`` and returns the
discovered tools as LangChain tools ready to hand to the graph. This is what
makes the scaffold reuse the repo's existing MCP servers (gardener-ai-mcp,
sci-ai-mcp, sap-wiki-mcp, ...) with no bespoke integration code.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_chat.config import CONFIGS_DIR, MCPServerConfig

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


def claude_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Claude Code tool id: ``mcp__<server>__<tool>``.

    The name sent to the MCP server stays the short ``tool_name``. Only the
    LangChain tool the model sees uses this form, so two servers can both
    expose ``search_docs``.
    """
    return f"mcp__{server_name}__{tool_name}"


def mcp_catalog(tools: list[BaseTool]) -> str:
    """List each server's ``mcp__<server>__<tool>`` names for the system prompt.

    Returns ``""`` when none of the tools use that naming.
    """
    grouped: dict[str, list[str]] = {}
    for tool in tools:
        parts = tool.name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp" or not parts[1] or not parts[2]:
            continue
        grouped.setdefault(parts[1], []).append(tool.name)
    if not grouped:
        return ""
    lines = [
        "# Available MCP servers",
        "Tool names are mcp__<server>__<tool>, the same form Claude Code uses.",
        "用户点了 MCP 服务名时，只调用该服务的 mcp__服务__*，不用另一个服务的同名工具代替。",
        "用户点了完整工具名时，调用该工具。",
        "点名后必须调用、不得声称未发生的调用。",
        "工具列表里没有的服务要说明未接入，不能写成已经查过。",
        "",
    ]
    for server, names in grouped.items():
        lines.append(f"- {server}: {', '.join(names)}")
    return "\n".join(lines)


async def get_tools(
    mcp_servers: dict[str, MCPServerConfig],
    base_dir: Path | None = None,
) -> list[BaseTool]:
    """Connect to every configured MCP server and return their tools.

    Each tool is renamed to ``mcp__<server>__<tool>``. The MCP call still uses
    the server's short name, which the adapter closes over before the rename.

    Returns an empty list when no servers are configured (a plain ReAct agent),
    so callers never need to special-case the tool-less path.
    """
    if not mcp_servers:
        return []

    from ai_observability.propagate import install_mcp_client_propagation
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from agent_chat.otel_mcp import otel_mcp_interceptor

    install_mcp_client_propagation()
    resolved = base_dir or CONFIGS_DIR
    connections = {name: cfg.to_connection(resolved) for name, cfg in mcp_servers.items()}

    client = MultiServerMCPClient(connections, tool_interceptors=[otel_mcp_interceptor])
    tools: list[BaseTool] = []
    for name in connections:
        for tool in await client.get_tools(server_name=name):
            tool.name = claude_mcp_tool_name(name, tool.name)
            tools.append(tool)
    return tools
