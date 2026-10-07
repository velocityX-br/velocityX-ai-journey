"""Truncate span attributes and strip credential-shaped fields."""

from __future__ import annotations

import json
import os
import re
from typing import Any

_DEFAULT_LIMIT = 2048
_CAPTURE_LIMIT = 8192

# Whole key, or a key that clearly names a secret. "input_tokens" must survive.
_SECRET_KEY = re.compile(
    r"^(api[_-]?key|token|authorization|password|secret|auth_token|access_token)$"
    r"|_(api_key|token|password|secret)$",
    re.IGNORECASE,
)


def _capture_content() -> bool:
    return os.environ.get("OTEL_AI_CAPTURE_CONTENT", "").lower() in ("1", "true", "yes")


def _is_secret(key: str) -> bool:
    return bool(_SECRET_KEY.search(key.replace("-", "_")))


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with secret-shaped dict keys replaced."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _is_secret(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def preview(value: Any, limit: int | None = None) -> str:
    """Redact then truncate ``value`` for a span attribute."""
    cap = limit if limit is not None else (_CAPTURE_LIMIT if _capture_content() else _DEFAULT_LIMIT)
    redacted = redact(value)
    if isinstance(redacted, str):
        text = redacted
    else:
        text = json.dumps(redacted, default=str, ensure_ascii=False)
    if len(text) <= cap:
        return text
    return text[:cap] + "…"
