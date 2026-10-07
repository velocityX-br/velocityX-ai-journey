"""FastMCP middleware that continues the agent trace inside an MCP server.

Imported only by the MCP servers (they depend on FastMCP). The agent process
does not import this module.
"""

from __future__ import annotations

from typing import Any

from opentelemetry.trace import SpanKind

from ai_observability.propagate import extract_meta
from ai_observability.redact import preview
from ai_observability.spans import span

try:
    from fastmcp.server.middleware import Middleware
except ImportError:  # pragma: no cover - agent env has no fastmcp
    Middleware = object  # type: ignore[misc, assignment]


def _carrier(meta: Any) -> dict[str, str]:
    if meta is None:
        return {}
    if hasattr(meta, "model_dump"):
        dumped = meta.model_dump(exclude_none=True)
    elif isinstance(meta, dict):
        dumped = meta
    else:
        return {}
    return {k: v for k, v in dumped.items() if isinstance(v, str)}


class OtelToolMiddleware(Middleware):
    """SERVER span for each tools/call, parented by the incoming traceparent."""

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        message = context.message
        name = getattr(message, "name", "tool")
        args = getattr(message, "arguments", None) or {}
        carrier = _carrier(getattr(message, "meta", None))
        parent = extract_meta(carrier) if "traceparent" in carrier else None
        async with span(
            f"execute_tool {name}",
            kind=SpanKind.SERVER,
            parent=parent,
            **{
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "ai.tool.args": preview(args),
            },
        ) as current:
            result = await call_next(context)
            if current.is_recording():
                current.set_attribute("ai.tool.result_preview", preview(result))
            return result
