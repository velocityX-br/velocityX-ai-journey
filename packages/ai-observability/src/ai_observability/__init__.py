"""OpenTelemetry helpers shared by the agent and the RAG MCP servers."""

from ai_observability.propagate import extract_meta, inject_meta, install_mcp_client_propagation
from ai_observability.rag import embed_query, search_collection, top_sources
from ai_observability.redact import preview, redact
from ai_observability.setup import configure, current_trace_id, telemetry_enabled, trace_url
from ai_observability.spans import note_external, span

__all__ = [
    "configure",
    "current_trace_id",
    "embed_query",
    "extract_meta",
    "inject_meta",
    "install_mcp_client_propagation",
    "note_external",
    "preview",
    "redact",
    "search_collection",
    "span",
    "telemetry_enabled",
    "top_sources",
    "trace_url",
]
