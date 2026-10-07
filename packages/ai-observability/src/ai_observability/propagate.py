"""W3C trace context carried in MCP ``params._meta``."""

from __future__ import annotations

from typing import Any

from opentelemetry import context as otel_context
from opentelemetry.propagate import extract, inject

_INSTALLED = False


def inject_meta() -> dict[str, str]:
    """Carrier for the current span. Empty when there is no valid trace."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return {k: v for k, v in carrier.items() if k in ("traceparent", "tracestate") and v}


def extract_meta(meta: dict[str, Any] | None) -> otel_context.Context:
    """Context whose parent is the traceparent inside an MCP meta dict."""
    carrier = {}
    if meta:
        for key in ("traceparent", "tracestate"):
            value = meta.get(key)
            if isinstance(value, str) and value:
                carrier[key] = value
    return extract(carrier)


def install_mcp_client_propagation() -> None:
    """Make ``ClientSession.call_tool`` forward the current traceparent.

    ``langchain-mcp-adapters`` does not pass ``meta`` through. The MCP SDK
    accepts it, so this wrapper merges ``inject_meta()`` into that argument.
    No-op unless telemetry is enabled. Idempotent.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    from ai_observability.setup import telemetry_enabled

    if not telemetry_enabled():
        return

    from mcp.client.session import ClientSession

    original = ClientSession.call_tool

    async def call_tool_with_trace(self: Any, *args: Any, **kwargs: Any) -> Any:
        extra = inject_meta()
        if extra:
            merged = dict(kwargs.get("meta") or {})
            merged.update(extra)
            kwargs["meta"] = merged
        return await original(self, *args, **kwargs)

    ClientSession.call_tool = call_tool_with_trace  # type: ignore[method-assign]
    _INSTALLED = True
