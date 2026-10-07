"""Config loading, merging, env overlay, and MCP connection rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_chat.config import AgentConfig, MCPServerConfig, load_config


def test_loads_and_merges_base(configs_dir: Path) -> None:
    cfg = load_config("generic", configs_dir=configs_dir)
    assert isinstance(cfg, AgentConfig)
    assert cfg.name == "generic"
    assert cfg.system_prompt == "you are generic"
    # inherited from base.yaml
    assert cfg.llm.provider == "anthropic"
    assert cfg.recursion_limit == 25
    assert cfg.mcp_servers == {}


def test_env_override_wins(configs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LLM__PROVIDER", "openai")
    monkeypatch.setenv("AGENT_RECURSION_LIMIT", "7")
    cfg = load_config("generic", configs_dir=configs_dir)
    assert cfg.llm.provider == "openai"
    assert cfg.recursion_limit == 7


def test_env_placeholder_expansion(configs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "abc123")
    (configs_dir / "http.yaml").write_text(
        "name: http\n"
        "mcp_servers:\n"
        "  remote:\n"
        "    transport: streamable_http\n"
        "    url: https://x/mcp\n"
        "    headers:\n"
        "      Authorization: Bearer ${SECRET_TOKEN}\n"
    )
    cfg = load_config("http", configs_dir=configs_dir)
    assert cfg.mcp_servers["remote"].headers["Authorization"] == "Bearer abc123"


def test_missing_config_raises(configs_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config("does_not_exist", configs_dir=configs_dir)


def test_suffix_is_stripped(configs_dir: Path) -> None:
    assert load_config("generic.yaml", configs_dir=configs_dir).name == "generic"


def test_sqlite_backend_gets_default_path(configs_dir: Path) -> None:
    cfg = load_config("sqlite", configs_dir=configs_dir)
    assert cfg.memory.backend == "sqlite"
    assert cfg.memory.path == "./agent_state.sqlite"


def test_stdio_connection_resolves_relative_cwd(configs_dir: Path) -> None:
    cfg = load_config("with_stdio", configs_dir=configs_dir)
    conn = cfg.mcp_servers["demo"].to_connection(configs_dir)
    assert conn["transport"] == "stdio"
    assert conn["command"] == "python"
    assert Path(conn["cwd"]).is_absolute()
    assert conn["cwd"].endswith("some/server")


def test_stdio_env_forwards_otel_but_not_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "agent-chat")
    monkeypatch.setenv("PATH", "/usr/bin")
    server = MCPServerConfig(
        transport="stdio",
        command="uv",
        args=["run", "python"],
        env={"CUSTOM": "from-yaml"},
    )
    conn = server.to_connection(Path("."))
    env = conn["env"]
    assert env["PATH"] == "/usr/bin"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"
    assert "OTEL_SERVICE_NAME" not in env
    assert env["CUSTOM"] == "from-yaml"


def test_stdio_requires_command() -> None:
    with pytest.raises(ValueError, match="requires a 'command'"):
        MCPServerConfig(transport="stdio")


def test_http_requires_url() -> None:
    with pytest.raises(ValueError, match="requires a 'url'"):
        MCPServerConfig(transport="streamable_http")
