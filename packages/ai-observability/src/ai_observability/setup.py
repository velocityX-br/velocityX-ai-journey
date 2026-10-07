"""TracerProvider setup. Off unless OTEL env vars ask for it."""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.trace import SpanContext

logger = logging.getLogger("ai_observability")

_CONFIGURED = False


def telemetry_enabled() -> bool:
    """True when traces should be exported (console or OTLP)."""
    return _exporter_mode() is not None


def _exporter_mode() -> str | None:
    mode = os.environ.get("OTEL_TRACES_EXPORTER", "").strip().lower()
    if mode in ("none", "false", "off"):
        return None
    if mode == "console":
        return "console"
    if mode == "otlp" or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return "otlp"
    return None


def configure(service_name: str) -> None:
    """Install a process-wide TracerProvider. Idempotent and a no-op when disabled.

    Export failures are logged by the SDK and do not fail the request that
    produced the span.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    mode = _exporter_mode()
    if mode is None:
        logger.debug("OpenTelemetry traces disabled")
        return

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    if mode == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _CONFIGURED = True
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    logger.info("OpenTelemetry traces enabled mode=%s service=%s endpoint=%s", mode, service_name, endpoint or "-")


def current_trace_id() -> str:
    """W3C trace id of the current span, or "" when there is no recording trace."""
    ctx: SpanContext = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return ""
    return format(ctx.trace_id, "032x")


def trace_url(trace_id: str) -> str:
    """Jaeger trace URL for this turn.

    ``OTEL_UI_BASE_URL`` overrides the host. When it is unset, the address is
    the local Jaeger UI. An explicit empty value turns the link off.
    """
    if not trace_id:
        return ""
    base = os.environ.get("OTEL_UI_BASE_URL", "http://127.0.0.1:16686").rstrip("/")
    if not base:
        return ""
    return f"{base}/trace/{trace_id}"
