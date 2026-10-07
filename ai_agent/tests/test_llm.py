"""LLM factory tests — focused on the ``litellm`` provider (SAP Hyperspace).

Network-free: ``build_llm`` only *constructs* a LangChain chat model; it never
makes a request. We assert the litellm branch builds a ``ChatOpenAI`` pointed at
the resolved LiteLLM-proxy endpoint (so gpt-5.6-sol / gemini-3.5-flash are
selectable through one OpenAI-compatible gateway), resolves the base URL/key
from env, and fails clearly when no endpoint is configured.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_openai")

from agent_chat.config import LLMConfig  # noqa: E402
from agent_chat.llm import build_llm  # noqa: E402


def test_litellm_uses_config_base_url_and_key() -> None:
    cfg = LLMConfig(
        provider="litellm",
        model="gpt-5.6-sol",
        base_url="http://localhost:4000",
        api_key="sk-test",
        max_tokens=32,
    )
    model = build_llm(cfg)
    assert model.model_name == "gpt-5.6-sol"
    assert str(model.openai_api_base) == "http://localhost:4000"


def test_litellm_resolves_base_url_and_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://proxy:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-env")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    cfg = LLMConfig(provider="litellm", model="gemini-3.5-flash", max_tokens=32)
    model = build_llm(cfg)
    assert model.model_name == "gemini-3.5-flash"
    assert str(model.openai_api_base) == "http://proxy:4000"


def test_litellm_auth_token_is_accepted_as_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A bearer/auth token in the config should be usable as the proxy key.
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = LLMConfig(
        provider="litellm",
        model="gpt-5.6-sol",
        base_url="http://localhost:4000",
        auth_token="bearer-xyz",
        max_tokens=32,
    )
    model = build_llm(cfg)  # should not raise
    assert model.model_name == "gpt-5.6-sol"


def test_litellm_falls_back_to_anthropic_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # The SAP Hyperspace LiteLLM proxy reuses the Anthropic bearer token, so when
    # no litellm/openai key is set the ANTHROPIC_AUTH_TOKEN should be used.
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "hyperspace-token-123")

    cfg = LLMConfig(provider="litellm", model="gpt-5.6-sol", base_url="http://localhost:6655/litellm/v1", max_tokens=32)
    model = build_llm(cfg)
    key = model.openai_api_key
    key = key.get_secret_value() if hasattr(key, "get_secret_value") else str(key)
    assert key == "hyperspace-token-123"


def test_litellm_requires_a_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    cfg = LLMConfig(provider="litellm", model="gpt-5.6-sol")
    with pytest.raises(ValueError, match="LITELLM_BASE_URL"):
        build_llm(cfg)
