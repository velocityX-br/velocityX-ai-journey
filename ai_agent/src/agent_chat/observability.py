"""Observability — enterprise governance hooks, off by default.

LangSmith tracing is toggled purely via environment variables so no code path
changes between dev and prod. Also configures a structured-ish log format.
"""

from __future__ import annotations

import logging
import os

from ai_observability import configure
from ai_observability.propagate import install_mcp_client_propagation

_CONFIGURED = False


def configure_observability(level: int = logging.INFO) -> None:
    """Set up logging and report whether LangSmith tracing is active.

    Idempotent — safe to call from CLI, tests, or a future API entrypoint.
    Tracing itself is driven by ``LANGCHAIN_TRACING_V2`` / ``LANGCHAIN_API_KEY``
    which the LangChain runtime reads directly; we only surface its state.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logger = logging.getLogger("agent_chat")

    if tracing_enabled():
        project = os.environ.get("LANGCHAIN_PROJECT", "default")
        logger.info("LangSmith tracing ENABLED (project=%s)", project)
    else:
        logger.debug("LangSmith tracing disabled")

    configure("agent-chat")
    install_mcp_client_propagation()

    _CONFIGURED = True


def tracing_enabled() -> bool:
    """True when LangSmith tracing env vars are set to on."""
    return os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in ("1", "true", "yes")
