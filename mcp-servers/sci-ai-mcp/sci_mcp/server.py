"""FastMCP server entry point for the SCI AI MCP.

Responsibilities:
- Create the ``FastMCP`` application instance.
- Define the lifespan context manager that initialises all singletons
  exactly once and stores them in lifespan state.
- Register all 5 MCP tools via ``sci_mcp.tools.register_tools``.
- Select the transport (stdio or SSE) from settings.
- Expose a ``main()`` entrypoint (also runnable via ``python -m sci_mcp.server``).

Design notes:
- The ``FastMCP`` instance is created at module level so it can be
  discovered by the runner without executing any business logic.
- ``build_app_context`` is only called inside the lifespan — never at
  import time and never inside a tool handler.
- Transport selection is runtime-configurable via the
  ``MCP_TRANSPORT`` / ``SCI_MCP_MCP_TRANSPORT`` environment variable.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from config.settings import Settings, get_settings
from sci_mcp.context import build_app_context
from sci_mcp.tools import configure_tool_cache, register_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[dict]:  # type: ignore[type-arg]
    """FastMCP lifespan context manager.

    Builds the ``AppContext`` once at server startup and stores it in
    the lifespan state dict under the ``"app_context"`` key.  All tool
    handlers retrieve it via ``ctx.lifespan_context["app_context"]``.

    Args:
        app: The ``FastMCP`` application instance (passed by the framework).

    Yields:
        A dict containing ``{"app_context": AppContext}`` which FastMCP
        makes available via ``Context.lifespan_context`` in every tool call.
    """
    settings: Settings = get_settings()
    logger.info(
        "Starting SCI AI MCP server — qdrant_url=%s anthropic_model=%s",
        settings.qdrant_url,
        settings.anthropic_model,
    )
    configure_tool_cache(
        ttl_seconds=settings.tool_cache_ttl_seconds,
        max_size=settings.tool_cache_max_size,
    )
    app_context = await build_app_context(settings)
    logger.info("AppContext built successfully.")
    yield {"app_context": app_context}
    logger.info("SCI AI MCP server shutting down.")


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="sci-ai-mcp",
    instructions=(
        "SCI AI MCP: search SAP Converged Infrastructure operation and customer "
        "documentation. Perform semantic and hybrid RAG retrieval and "
        "LLM-assisted root cause analysis over the SCI documentation corpus."
    ),
    lifespan=lifespan,
)

# Register all 5 tools against this FastMCP instance.
register_tools(mcp)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the SCI AI MCP server with the configured transport.

    Reads ``mcp_transport`` from settings.  When ``sse`` is selected the
    server binds on ``mcp_host:mcp_port``; otherwise it runs over stdio.
    """
    logging.basicConfig(level=logging.INFO)

    settings = get_settings()
    transport = getattr(settings, "mcp_transport", "stdio")

    logger.info("Running with transport=%s", transport)
    if transport == "sse":
        host = getattr(settings, "mcp_host", "0.0.0.0")
        port = getattr(settings, "mcp_port", 8080)
        logger.info("SSE server binding on %s:%s", host, port)
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
