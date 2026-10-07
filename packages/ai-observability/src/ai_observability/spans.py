"""Span context manager. No-op when the global provider is the default proxy."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from ai_observability.redact import preview


class span:
    """Start a current span for the duration of a sync or async block.

    Exceptions are recorded and marked ERROR, then re-raised.
    """

    def __init__(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Any = None,
        **attributes: Any,
    ) -> None:
        self._name = name
        self._kind = kind
        self._parent = parent
        self._attributes = attributes
        self._cm: Any = None
        self._span: Any = None

    def __enter__(self) -> Any:
        tracer = trace.get_tracer("ai_observability")
        kwargs: dict[str, Any] = {"kind": self._kind}
        if self._parent is not None:
            kwargs["context"] = self._parent
        self._cm = tracer.start_as_current_span(self._name, **kwargs)
        self._span = self._cm.__enter__()
        if self._span.is_recording():
            for key, value in self._attributes.items():
                if value is None:
                    continue
                if isinstance(value, bool | int | float | str):
                    self._span.set_attribute(key, value)
                else:
                    self._span.set_attribute(key, preview(value))
        return self._span

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc is not None and self._span is not None and self._span.is_recording():
            self._span.record_exception(exc)
            self._span.set_status(Status(StatusCode.ERROR, f"{exc_type.__name__}: {exc}"))
        if self._cm is not None:
            return bool(self._cm.__exit__(exc_type, exc, tb))
        return False

    async def __aenter__(self) -> Any:
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return self.__exit__(exc_type, exc, tb)


def note_external(peer: str, *, model: str | None = None) -> None:
    """Annotate the current span as a call to an external system. Never records vectors."""
    current = trace.get_current_span()
    if not current.is_recording():
        return
    current.set_attribute("peer.service", peer)
    if model:
        current.set_attribute("gen_ai.request.model", model)
