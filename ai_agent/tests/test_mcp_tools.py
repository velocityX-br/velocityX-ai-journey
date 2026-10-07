"""MCP tool loader exercised against a real in-repo stub stdio server.

Spawns ``tests.stub_mcp_server`` over stdio via MultiServerMCPClient and asserts
the ``echo`` tool is discovered and callable. Skips cleanly if the ``mcp`` SDK
(pulled in by langchain-mcp-adapters) is unavailable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("langchain_mcp_adapters")
pytest.importorskip("mcp")

from agent_chat.config import MCPServerConfig  # noqa: E402
from agent_chat.mcp_tools import get_tools  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


async def test_no_servers_returns_empty() -> None:
    assert await get_tools({}) == []


async def test_loads_tools_from_stub_server() -> None:
    server = MCPServerConfig(
        transport="stdio",
        command=sys.executable,
        args=["-m", "tests.stub_mcp_server"],
        cwd=str(REPO),
    )
    tools = await get_tools({"stub": server}, base_dir=REPO)
    names = {t.name for t in tools}
    assert "mcp__stub__echo" in names

    echo = next(t for t in tools if t.name == "mcp__stub__echo")
    result = await echo.ainvoke({"text": "hi"})
    assert "hi" in str(result)
