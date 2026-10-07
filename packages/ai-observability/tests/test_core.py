"""Redaction and W3C propagation. No collector required."""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ai_observability.propagate import extract_meta, inject_meta
from ai_observability.redact import preview, redact
from ai_observability.setup import trace_url


def test_redact_strips_token_but_keeps_usage_counts() -> None:
    cleaned = redact({"token": "sekret", "input_tokens": 12, "nested": {"api_key": "k"}})
    assert cleaned["token"] == "[REDACTED]"
    assert cleaned["nested"]["api_key"] == "[REDACTED]"
    assert cleaned["input_tokens"] == 12


def test_trace_url_defaults_to_local_jaeger(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_UI_BASE_URL", raising=False)
    assert trace_url("abc") == "http://127.0.0.1:16686/trace/abc"
    assert trace_url("") == ""


def test_trace_url_honors_override(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_UI_BASE_URL", "http://jaeger.example:16686/")
    assert trace_url("abc") == "http://jaeger.example:16686/trace/abc"
    monkeypatch.setenv("OTEL_UI_BASE_URL", "")
    assert trace_url("abc") == ""


def test_preview_truncates() -> None:
    text = preview("x" * 50, limit=10)
    assert text == "x" * 10 + "…"


def test_inject_extract_share_trace_id() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # This test builds spans from an explicit provider so it does not depend
    # on the process-global provider (other tests may have set it already).
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("parent"):
        carrier = inject_meta()
        assert "traceparent" in carrier
        parent_ctx = extract_meta(carrier)
        with tracer.start_as_current_span("child", context=parent_ctx):
            pass
    finished = exporter.get_finished_spans()
    child = next(item for item in finished if item.name == "child")
    parent = next(item for item in finished if item.name == "parent")
    assert child.context.trace_id == parent.context.trace_id
    assert child.parent is not None
    assert child.parent.span_id == parent.context.span_id
