"""Configuration layer — the reusability engine of the scaffold.

A use case is defined entirely by a YAML file under ``configs/``. This module
loads that YAML, merges it onto ``base.yaml`` defaults, expands ``${ENV}``
placeholders, overlays ``AGENT_*`` environment variables, and validates the
result into typed Pydantic models. No code changes are needed to add a new
agent — only a new config file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Directory holding the YAML configs. Overridable for tests / packaging.
CONFIGS_DIR = Path(os.environ.get("AGENT_CONFIGS_DIR", Path(__file__).resolve().parents[2] / "configs"))

# Project root (…/ai_agent) — where a local .env is expected to live.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_dotenv_loaded = False


def _load_dotenv_once() -> None:
    """Load ``.env`` from the project root into ``os.environ`` (idempotent).

    Both entrypoints (CLI and API) resolve config through ``load_config``, so
    loading here ensures ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / AGENT_* set
    in ``ai_agent/.env`` are picked up without the user exporting them manually.
    Existing environment variables win over ``.env`` (``override=False``).
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is a transitive dep; degrade gracefully
        return
    load_dotenv(_PROJECT_ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# Typed config models
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    """Which model provider to talk to and how."""

    provider: Literal["anthropic", "openai", "ollama", "litellm"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.0
    max_tokens: int = 4096
    base_url: str | None = None
    # Optional explicit credentials. When omitted, ``build_llm`` falls back to
    # the standard provider env vars (e.g. ANTHROPIC_API_KEY) and, for Anthropic,
    # to the gateway/proxy env vars ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL
    # (the same scheme Claude Code uses for an internal LLM gateway).
    api_key: str | None = None
    auth_token: str | None = None
    # Optional extended-thinking budget (tokens). When > 0 and supported by the
    # provider/model (Anthropic), the model streams its reasoning, which the Web
    # UI shows in a live "Thinking" card. Left at 0 (off) by default so configs
    # targeting models/gateways without thinking support are unaffected.
    thinking_budget_tokens: int = 0


class MemoryConfig(BaseModel):
    """Checkpointer / persistence backend for the graph."""

    backend: Literal["memory", "sqlite"] = "memory"
    path: str | None = None

    @model_validator(mode="after")
    def _sqlite_needs_path(self) -> MemoryConfig:
        if self.backend == "sqlite" and not self.path:
            self.path = "./agent_state.sqlite"
        return self


def _stdio_child_env(extra: dict[str, str]) -> dict[str, str]:
    """Environment for a stdio MCP child.

    The MCP SDK otherwise keeps only a small safe subset and drops ``OTEL_*``,
    so the child never exports. ``OTEL_SERVICE_NAME`` is omitted because each
    server sets ``service.name`` in ``configure()``. ``extra`` (YAML ``env``)
    overrides both.
    """
    from mcp.client.stdio import get_default_environment

    env = get_default_environment()
    for key, value in os.environ.items():
        if key.startswith("OTEL_") and key != "OTEL_SERVICE_NAME":
            env[key] = value
    env.update(extra)
    return env


class MCPServerConfig(BaseModel):
    """One MCP server the agent may use as a tool source.

    Mirrors the shape expected by ``langchain_mcp_adapters.MultiServerMCPClient``.
    """

    transport: Literal["stdio", "streamable_http", "sse"] = "stdio"
    # stdio
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    # http / sse
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_transport(self) -> MCPServerConfig:
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server requires a 'command'")
        if self.transport in ("streamable_http", "sse") and not self.url:
            raise ValueError(f"{self.transport} MCP server requires a 'url'")
        return self

    def to_connection(self, base_dir: Path) -> dict[str, Any]:
        """Render the dict shape MultiServerMCPClient expects.

        Relative ``cwd`` values are resolved against ``base_dir`` (the configs
        directory) so a config is portable regardless of the launch directory.
        """
        if self.transport == "stdio":
            conn: dict[str, Any] = {
                "transport": "stdio",
                "command": self.command,
                "args": list(self.args),
            }
            if self.cwd:
                cwd = Path(self.cwd)
                conn["cwd"] = str(cwd if cwd.is_absolute() else (base_dir / cwd).resolve())
            # Passing env replaces the SDK default. Start from that safe subset
            # (PATH, HOME, …), then add OTEL_* so the child exports traces.
            # Skip OTEL_SERVICE_NAME; each server sets service.name in configure().
            # YAML env wins over both.
            conn["env"] = _stdio_child_env(self.env)
            return conn
        conn = {"transport": self.transport, "url": self.url}
        if self.headers:
            conn["headers"] = dict(self.headers)
        return conn


class AgentConfig(BaseModel):
    """Fully-resolved configuration for one agent use case."""

    name: str = "agent"
    system_prompt: str = "You are a helpful assistant."
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    # Directories to scan for Claude Code `SKILL.md` files. Each entry is a
    # skills root (containing `*/SKILL.md`) or a single skill dir. Relative
    # paths are resolved against CONFIGS_DIR (same convention as MCP `cwd`).
    skills: list[str] = Field(default_factory=list)
    # Enable the built-in web-search tool (see web_search.py). Opt-in so the
    # agent only reaches the internet when a use case wants it. Provider of the
    # search itself is env-driven (TAVILY_API_KEY → Tavily, else DuckDuckGo).
    web_search: bool = False
    recursion_limit: int = 25


# ---------------------------------------------------------------------------
# Env overlay (AGENT_ prefix, nested via "__")
# ---------------------------------------------------------------------------


class _EnvOverrides(BaseSettings):
    """Env vars that override YAML, e.g. AGENT_LLM__PROVIDER=openai."""

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_nested_delimiter="__", extra="ignore")

    name: str | None = None
    system_prompt: str | None = None
    recursion_limit: int | None = None
    llm: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Loading + merging
# ---------------------------------------------------------------------------


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` placeholders in strings using os.environ."""
    if isinstance(value, str):
        return _ENV_PLACEHOLDER.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``override`` onto ``base`` recursively (override wins)."""
    out = dict(base)
    for key, val in override.items():
        if val is None:
            continue
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config {path} must be a YAML mapping, got {type(data).__name__}")
    return data


def load_config(name: str, configs_dir: Path | None = None) -> AgentConfig:
    """Load ``configs/{name}.yaml`` merged onto ``base.yaml`` + env overrides.

    Precedence (lowest to highest): base.yaml < {name}.yaml < ${ENV} expansion
    < AGENT_* environment variables.
    """
    _load_dotenv_once()
    directory = configs_dir or CONFIGS_DIR
    name = name.removesuffix(".yaml").removesuffix(".yml")

    merged: dict[str, Any] = {}
    base_path = directory / "base.yaml"
    if base_path.exists():
        merged = _read_yaml(base_path)

    if name != "base":
        merged = _deep_merge(merged, _read_yaml(directory / f"{name}.yaml"))

    merged = _expand_env(merged)

    env_overrides = _EnvOverrides().model_dump(exclude_none=True)
    if env_overrides:
        merged = _deep_merge(merged, env_overrides)

    return AgentConfig.model_validate(merged)
