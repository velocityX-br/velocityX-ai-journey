"""MCP client interceptor: tool span plus traceparent on HTTP headers."""

from __future__ import annotations

from typing import Any

from ai_observability import inject_meta, preview, span


async def otel_mcp_interceptor(request: Any, handler: Any) -> Any:
    """Span one MCP tool call and attach W3C headers for HTTP transports.

    stdio propagation uses ``ClientSession.call_tool(meta=...)``, installed
    separately. Headers cover SSE and streamable HTTP.
    """
    headers = dict(request.headers or {})
    headers.update(inject_meta())
    request = request.override(headers=headers or None)
    async with span(
        f"execute_tool {request.name}",
        **{
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": request.name,
            "mcp.server.name": request.server_name,
            "ai.tool.args": preview(request.args),
        },
    ) as current:
        result = await handler(request)
        if current.is_recording():
            current.set_attribute("ai.tool.result_preview", preview(result))
        return result
