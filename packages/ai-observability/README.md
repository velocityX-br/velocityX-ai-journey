# ai-observability

Small OpenTelemetry helpers shared by `ai_agent` and the RAG MCP servers.

`configure(service_name)` is a no-op unless `OTEL_TRACES_EXPORTER` is `console` or `otlp`, or `OTEL_EXPORTER_OTLP_ENDPOINT` is set. See `ai_agent/docs/OBSERVABILITY.md` for the kind-cluster viewer.
