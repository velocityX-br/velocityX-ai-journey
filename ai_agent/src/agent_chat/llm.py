"""LLM provider factory — keeps the scaffold vendor-neutral.

Switches on ``config.llm.provider`` so a use case can target Anthropic, OpenAI,
or a local Ollama model without touching graph code.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agent_chat.config import LLMConfig

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


def build_llm(config: LLMConfig) -> BaseChatModel:
    """Return a LangChain chat model for the configured provider.

    Imports are done lazily so tests that inject a fake model don't need every
    provider SDK installed.
    """
    provider = config.provider

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict = {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }

        # Resolve auth. Precedence: explicit config > env vars. Two modes:
        #   1. Gateway/proxy (Bearer token) — ANTHROPIC_AUTH_TOKEN, as used by
        #      Claude Code against an internal LLM gateway. The token is sent as
        #      `Authorization: Bearer <token>`; base_url points at the gateway.
        #   2. Direct Anthropic API key — ANTHROPIC_API_KEY (SDK default).
        base_url = config.base_url or os.environ.get("ANTHROPIC_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url

        auth_token = config.auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        api_key = config.api_key or os.environ.get("ANTHROPIC_API_KEY")

        if auth_token:
            # ChatAnthropic has no auth_token field; inject the Bearer header
            # directly. The SDK still requires a non-empty api_key, so pass a
            # placeholder — the gateway authenticates via the header, not x-api-key.
            kwargs["default_headers"] = {"Authorization": f"Bearer {auth_token}"}
            kwargs["api_key"] = api_key or "gateway-auth-token"
        elif api_key:
            kwargs["api_key"] = api_key

        # Extended thinking: when a budget is configured, ask Anthropic to stream
        # its reasoning. The Web UI renders these as a live "Thinking" card. The
        # API requires temperature=1 while thinking is enabled, and the budget
        # must leave room within max_tokens for the final answer.
        if config.thinking_budget_tokens and config.thinking_budget_tokens > 0:
            budget = config.thinking_budget_tokens
            if kwargs["max_tokens"] <= budget:
                kwargs["max_tokens"] = budget + 1024
            kwargs["temperature"] = 1.0
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}

        return ChatAnthropic(**kwargs)

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOpenAI(**kwargs)

    if provider == "litellm":
        # LiteLLM proxy — a single OpenAI-compatible endpoint that fronts many
        # backends (Azure OpenAI, Google Vertex, Bedrock, ...). This is how the
        # SAP "Hyperspace" gateway can expose e.g. gpt-5.6-sol and
        # gemini-3.5-flash behind one URL + one key. Because the proxy speaks the
        # OpenAI API, we reuse LangChain's ChatOpenAI (no extra dependency) and
        # simply point it at the proxy. The `model` is the LiteLLM *model name*
        # (its `model_list` alias), e.g. "gpt-5.6-sol" or "gemini-3.5-flash".
        from langchain_openai import ChatOpenAI

        # Resolve endpoint + key from config first, then LiteLLM-specific env,
        # then the generic OpenAI env vars (a LiteLLM proxy is OpenAI-shaped).
        base_url = config.base_url or os.environ.get("LITELLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        api_key = (
            config.api_key
            or config.auth_token
            or os.environ.get("LITELLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            # The SAP Hyperspace LiteLLM proxy accepts the same bearer token as
            # its Anthropic endpoint, so reuse it when no litellm-specific key
            # is provided — one token for the whole gateway.
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )
        if not base_url:
            raise ValueError(
                "provider 'litellm' requires a proxy URL: set llm.base_url in the "
                "config or the LITELLM_BASE_URL environment variable "
                "(e.g. http://localhost:4000)."
            )

        kwargs = {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "base_url": base_url,
            # ChatOpenAI requires a non-empty key; a LiteLLM proxy validates its
            # own virtual key. Fall back to a placeholder when the proxy is open.
            "api_key": api_key or "litellm-proxy",
        }
        return ChatOpenAI(**kwargs)

    if provider == "ollama":
        # langchain-ollama is optional; import only when selected.
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:  # pragma: no cover - optional dep
            raise ImportError(
                "provider 'ollama' requires the 'langchain-ollama' package: pip install langchain-ollama"
            ) from exc

        kwargs = {"model": config.model, "temperature": config.temperature}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOllama(**kwargs)

    raise ValueError(f"Unsupported LLM provider: {provider}")
