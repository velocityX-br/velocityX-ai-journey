"""A minimal stdio MCP server used only by the test-suite.

Exposes a single ``echo`` tool. Uses FastMCP (bundled with the ``mcp`` SDK that
langchain-mcp-adapters depends on) to keep this tiny.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stub")


@mcp.tool()
def echo(text: str) -> str:
    """Return the text prefixed with 'echo:'."""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
