"""FastMCP server entry point for the Gardener AI MCP.

Responsibilities:
- Create the ``FastMCP`` application instance.
- Define the lifespan context manager that initialises all singletons
  exactly once and stores them in lifespan state.
- Register all 7 MCP tools via ``gardener_mcp.tools.register_tools``.
- Select the transport (stdio or SSE) from settings.
- Expose a ``if __name__ == "__main__"`` entrypoint.

Design notes (ADR-003, ADR-004):
- The ``FastMCP`` instance is created at module level so it can be
  discovered by the ``uvicorn``/``stdio`` runner without executing any
  business logic.
- ``build_app_context`` is only called inside the lifespan — never at
  import time and never inside a tool handler.
- Transport selection is runtime-configurable via the
  ``MCP_TRANSPORT`` / ``GARDENER_MCP_TRANSPORT`` environment variable.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastmcp import FastMCP

from config.settings import Settings, get_settings
from gardener_mcp.context import build_app_context
from gardener_mcp.tools import register_tools

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
        "Starting Gardener AI MCP server — qdrant_url=%s anthropic_model=%s",
        settings.qdrant_url,
        settings.anthropic_model,
    )
    app_context = await build_app_context(settings)
    logger.info("AppContext built successfully.")
    yield {"app_context": app_context}
    logger.info("Gardener AI MCP server shutting down.")


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="gardener-ai-mcp",
    instructions=(
        "Gardener AI MCP: search Gardener documentation, GitHub issues, pull "
        "requests, enhancement proposals, and source code. Perform semantic RAG "
        "retrieval and LLM-assisted root cause analysis for Kubernetes Shoot "
        "cluster problems."
    ),
    lifespan=lifespan,
)

# Register all 7 tools against this FastMCP instance.
register_tools(mcp)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(level=logging.INFO)

    _settings = get_settings()
    transport = getattr(_settings, "mcp_transport", "stdio")

    logger.info("Running with transport=%s", transport)
    if transport == "sse":
        host = getattr(_settings, "mcp_host", "0.0.0.0")
        port = getattr(_settings, "mcp_port", 8080)
        logger.info("SSE server binding on %s:%s", host, port)
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport=transport)  # type: ignore[arg-type]
