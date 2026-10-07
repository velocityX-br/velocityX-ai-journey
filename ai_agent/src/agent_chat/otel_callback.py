"""LangChain callback that records chat and tool spans on the current trace.

One user turn is one trace. The handler is constructed inside the
``invoke_agent`` span and keeps that context. Later callbacks — including a
sub-agent's model and tools, which may run after the ambient context was
detached — parent to an open ancestor on that same trace, never a new root.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ai_observability.redact import preview
from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


def _model_name(serialized: dict[str, Any], kwargs: dict[str, Any]) -> str:
    params = kwargs.get("invocation_params") or {}
    for key in ("model", "model_name"):
        if params.get(key):
            return str(params[key])
    nested = (serialized.get("kwargs") or {}) if isinstance(serialized, dict) else {}
    for key in ("model", "model_name"):
        if nested.get(key):
            return str(nested[key])
    return "unknown"


def _provider_name(serialized: dict[str, Any]) -> str:
    ident = serialized.get("id") if isinstance(serialized, dict) else None
    if isinstance(ident, list) and ident:
        return str(ident[-2] if len(ident) >= 2 else ident[-1])
    return "unknown"


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    return content if isinstance(content, str) else preview(content)


class OtelCallbackHandler(BaseCallbackHandler):
    """Chat and tool spans. The span is current for the duration of the call.

    Construct this inside the ``invoke_agent`` span. ``_root`` is that context.
    """

    def __init__(self) -> None:
        super().__init__()
        self._open: dict[str, tuple[Any, Any]] = {}
        # LangChain run id -> parent run id, including chain runs that have no span.
        # A sub-agent's chat parent is a chain, whose parent is ``delegate_source``.
        self._parents: dict[str, str | None] = {}
        self._root = otel_context.get_current()
        self._subagent_spans: set[int] = set()

    def _remember(self, run_id: UUID, parent_run_id: UUID | None) -> None:
        self._parents[str(run_id)] = str(parent_run_id) if parent_run_id else None

    def _parent_context(self, parent_run_id: UUID | None) -> otel_context.Context:
        """Context on the request trace.

        Walks LangChain's parent run ids to the nearest open span, so a sub-agent
        stays under ``delegate_source`` even when its callback runs in a context
        that no longer has that span attached. A lost ambient context falls back
        to the ``invoke_agent`` root captured at construction.
        """
        current_id = str(parent_run_id) if parent_run_id else None
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            item = self._open.get(current_id)
            if item is not None:
                return trace.set_span_in_context(item[0])
            if current_id not in self._parents:
                break
            current_id = self._parents[current_id]
        current = trace.get_current_span().get_span_context()
        root = trace.get_current_span(self._root).get_span_context()
        if current.is_valid and (not root.is_valid or current.trace_id == root.trace_id):
            return otel_context.get_current()
        return self._root

    def _begin(
        self,
        run_id: UUID,
        name: str,
        attributes: dict[str, Any],
        *,
        parent_run_id: UUID | None = None,
    ) -> None:
        self._remember(run_id, parent_run_id)
        parent_ctx = self._parent_context(parent_run_id)
        parent_sid = trace.get_current_span(parent_ctx).get_span_context().span_id
        tool_name = attributes.get("gen_ai.tool.name")
        role = "subagent" if tool_name == "delegate_source" or parent_sid in self._subagent_spans else "main"
        attributes = {**attributes, "ai.agent.role": role}
        tracer = trace.get_tracer("agent_chat")
        span = tracer.start_span(name, context=parent_ctx)
        if span.is_recording():
            for key, value in attributes.items():
                if value is None:
                    continue
                if isinstance(value, bool | int | float | str):
                    span.set_attribute(key, value)
                else:
                    span.set_attribute(key, preview(value))
        if role == "subagent":
            sid = span.get_span_context().span_id
            if sid:
                self._subagent_spans.add(sid)
        token = otel_context.attach(trace.set_span_in_context(span))
        self._open[str(run_id)] = (span, token)

    def _finish(self, run_id: UUID, *, error: BaseException | None = None, **attributes: Any) -> None:
        item = self._open.pop(str(run_id), None)
        if item is None:
            return
        span, token = item
        try:
            if span.is_recording():
                for key, value in attributes.items():
                    if value is None:
                        continue
                    if isinstance(value, bool | int | float | str):
                        span.set_attribute(key, value)
                    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                        span.set_attribute(key, value)
                    else:
                        span.set_attribute(key, preview(value))
                if error is not None:
                    span.record_exception(error)
                    span.set_status(Status(StatusCode.ERROR, f"{type(error).__name__}: {error}"))
            span.end()
        finally:
            # Detach on the contextvar directly. ``otel_context.detach`` logs an
            # error when the callback ends in another context — sub-agent model
            # calls do that — even though the span parent was already chosen.
            try:
                token.var.reset(token)
            except ValueError:
                pass

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        # Chain runs are not spans. They only keep the parent link so a nested
        # sub-agent chat or tool can find the open delegate_source span.
        del serialized, inputs, kwargs
        self._remember(run_id, parent_run_id)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        serialized = serialized or {}
        model = _model_name(serialized, kwargs)
        flat = [msg for batch in messages for msg in batch]
        self._begin(
            run_id,
            f"chat {model}",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model,
                "gen_ai.provider.name": _provider_name(serialized),
                "ai.prompt": preview("\n".join(_message_text(msg) for msg in flat)),
            },
            parent_run_id=kwargs.get("parent_run_id"),
        )

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        message = None
        generations = getattr(response, "generations", None) or []
        if generations and generations[0]:
            message = getattr(generations[0][0], "message", None)
        usage = getattr(message, "usage_metadata", None) or {}
        meta = getattr(message, "response_metadata", None) or {}
        finish = meta.get("finish_reason") or meta.get("stop_reason")
        self._finish(
            run_id,
            **{
                "gen_ai.usage.input_tokens": usage.get("input_tokens"),
                "gen_ai.usage.output_tokens": usage.get("output_tokens"),
                "gen_ai.response.finish_reasons": [str(finish)] if finish else None,
                "ai.completion": preview(_message_text(message)) if message is not None else None,
            },
        )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, error=error)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, *, run_id: UUID, **kwargs: Any) -> None:
        serialized = serialized or {}
        name = kwargs.get("name") or serialized.get("name") or "tool"
        inputs = kwargs.get("inputs", input_str)
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "ai.tool.args": preview(inputs),
        }
        if name == "delegate_source" and isinstance(inputs, dict) and inputs.get("source"):
            attributes["ai.delegate.source"] = str(inputs["source"])
        self._begin(
            run_id,
            f"execute_tool {name}",
            attributes,
            parent_run_id=kwargs.get("parent_run_id"),
        )

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, **{"ai.tool.result_preview": preview(output)})

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, error=error)
