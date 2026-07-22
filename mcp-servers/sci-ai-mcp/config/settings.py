"""Application settings loaded from environment variables.

All runtime configuration is centralised here.  No other module reads
``os.environ`` directly.  Settings are loaded via ``pydantic-settings``
which merges values from a ``.env`` file and the process environment.

The ``SCI_MCP_*`` prefixed variables take priority over the unprefixed
ambient variables, enabling independent configuration of this MCP server
even when running alongside Claude Code in the same shell session.

Usage::

    from config.settings import get_settings

    settings = get_settings()
    token = settings.github_token
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings resolved from environment variables and .env files.

    Variable resolution order (highest to lowest priority):
    1. Process environment variables (``SCI_MCP_*`` prefix)
    2. Process environment variables (unprefixed ambient vars)
    3. Values from ``.env`` file
    4. Field defaults

    Attributes:
        github_token: SAP GitHub Enterprise personal access token.  Required —
            startup fails without it.
        github_docs_operation_repo: Repository slug for the operation
            documentation source.  Defaults to ``"cc/documentation-operation"``.
        github_docs_customer_repo: Repository slug for the customer
            documentation source.  Defaults to ``"cc/documentation-customer"``.
        github_base_url: GitHub API base URL.  Defaults to the SAP GitHub
            Enterprise API v3 endpoint.
        github_ca_bundle: Optional path to a PEM CA bundle used to verify
            TLS for the GitHub client.  Required for SAP GitHub Enterprise,
            whose chain is signed by the internal SAP Global Root CA.
        anthropic_base_url: Base URL for the Anthropic-compatible LLM proxy
            (SAP Hyperspace).
        anthropic_auth_token: Bearer token for the Anthropic proxy.
        anthropic_model: Model identifier for LLM calls.
        api_timeout_ms: HTTP timeout in milliseconds for LLM calls.
        hyperspace_openai_base_url: Base URL for the OpenAI-compatible
            embeddings endpoint on Hyperspace.
        embedding_model: Embedding model identifier.
        embedding_dimensions: Number of dimensions for embedding vectors.
        embedding_cache_size: In-memory query-embedding cache size (FIFO).
        qdrant_url: URL of the Qdrant vector database instance.
        qdrant_api_key: Optional API key for Qdrant.
        qdrant_batch_size: Number of points to upsert per Qdrant request.
        tool_cache_ttl_seconds: TTL for the MCP tool result cache.
        tool_cache_max_size: Maximum entries in the MCP tool result cache.
        mcp_transport: MCP transport mechanism — ``"stdio"`` for CLI/local use,
            ``"sse"`` for HTTP+SSE server mode.
        mcp_host: Host address to bind when transport is ``"sse"``.
        mcp_port: TCP port to bind when transport is ``"sse"``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # GitHub Enterprise (github.wdf.sap.corp)
    # ------------------------------------------------------------------

    github_token: str = Field(
        validation_alias=AliasChoices(
            "SCI_MCP_GITHUB_TOKEN",
            "GITHUB_TOKEN",
        ),
        description="SAP GitHub Enterprise personal access token.  Required.",
    )

    github_docs_operation_repo: str = Field(
        default="cc/documentation-operation",
        validation_alias=AliasChoices(
            "SCI_MCP_GITHUB_DOCS_OPERATION_REPO",
            "GITHUB_DOCS_OPERATION_REPO",
        ),
        description="Repository slug for the SCI operation documentation source.",
    )

    github_docs_customer_repo: str = Field(
        default="cc/documentation-customer",
        validation_alias=AliasChoices(
            "SCI_MCP_GITHUB_DOCS_CUSTOMER_REPO",
            "GITHUB_DOCS_CUSTOMER_REPO",
        ),
        description="Repository slug for the SCI customer documentation source.",
    )

    github_base_url: str | None = Field(
        default="https://github.wdf.sap.corp/api/v3",
        validation_alias=AliasChoices(
            "SCI_MCP_GITHUB_BASE_URL",
            "GITHUB_BASE_URL",
        ),
        description=(
            "GitHub API base URL.  Defaults to the SAP GitHub Enterprise API v3 "
            "endpoint.  Set to None (or empty) to target github.com."
        ),
    )

    github_ca_bundle: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SCI_MCP_GITHUB_CA_BUNDLE",
            "GITHUB_CA_BUNDLE",
            "REQUESTS_CA_BUNDLE",
        ),
        description=(
            "Path to a PEM CA bundle used to verify TLS for the GitHub client. "
            "SAP GitHub Enterprise (github.wdf.sap.corp) presents a certificate "
            "chain signed by the internal SAP Global Root CA, which is absent "
            "from the default certifi trust store.  Point this at a PEM that "
            "contains the SAP CA chain (plus the public CAs).  When unset, "
            "PyGithub uses its default trust store."
        ),
    )

    # ------------------------------------------------------------------
    # Anthropic LLM proxy (SAP Hyperspace)
    # ------------------------------------------------------------------

    anthropic_base_url: str = Field(
        default="http://localhost:6655/anthropic/",
        validation_alias=AliasChoices(
            "SCI_MCP_ANTHROPIC_BASE_URL",
            "ANTHROPIC_BASE_URL",
        ),
        description="Base URL for the Anthropic-compatible LLM proxy.",
    )

    anthropic_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "SCI_MCP_ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_AUTH_TOKEN",
        ),
        description="Bearer token for the Anthropic-compatible LLM proxy.",
    )

    anthropic_model: str = Field(
        default="anthropic--claude-sonnet-latest",
        validation_alias=AliasChoices(
            "SCI_MCP_ANTHROPIC_MODEL",
            "ANTHROPIC_MODEL",
        ),
        description="Model identifier for LLM calls via the Hyperspace proxy.",
    )

    api_timeout_ms: int = Field(
        default=3_000_000,
        validation_alias=AliasChoices(
            "SCI_MCP_API_TIMEOUT_MS",
            "API_TIMEOUT_MS",
        ),
        description="HTTP timeout in milliseconds for LLM API calls.",
    )

    # ------------------------------------------------------------------
    # Embeddings (Hyperspace OpenAI-compatible endpoint)
    # ------------------------------------------------------------------

    hyperspace_openai_base_url: str = Field(
        default="http://localhost:6655/openai/v1",
        validation_alias=AliasChoices(
            "SCI_MCP_HYPERSPACE_OPENAI_BASE_URL",
            "HYPERSPACE_OPENAI_BASE_URL",
        ),
        description="Base URL for the Hyperspace OpenAI-compatible embeddings endpoint.",
    )

    embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices(
            "SCI_MCP_EMBEDDING_MODEL",
            "EMBEDDING_MODEL",
        ),
        description="Embedding model identifier.",
    )

    embedding_dimensions: int = Field(
        default=1536,
        validation_alias=AliasChoices(
            "SCI_MCP_EMBEDDING_DIMENSIONS",
            "EMBEDDING_DIMENSIONS",
        ),
        description="Number of dimensions for embedding vectors.",
    )

    embedding_cache_size: int = Field(
        default=256,
        validation_alias=AliasChoices(
            "SCI_MCP_EMBEDDING_CACHE_SIZE",
            "EMBEDDING_CACHE_SIZE",
        ),
        description=(
            "Number of query embeddings to cache in memory (FIFO eviction). "
            "Set to 0 to disable the cache."
        ),
    )

    # ------------------------------------------------------------------
    # Qdrant vector store
    # ------------------------------------------------------------------

    qdrant_url: str = Field(
        default="http://localhost:6333",
        validation_alias=AliasChoices(
            "SCI_MCP_QDRANT_URL",
            "QDRANT_URL",
        ),
        description="URL of the Qdrant vector database instance.",
    )

    qdrant_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "SCI_MCP_QDRANT_API_KEY",
            "QDRANT_API_KEY",
        ),
        description="Optional API key for Qdrant.  Leave empty for unauthenticated access.",
    )

    qdrant_batch_size: int = Field(
        default=100,
        validation_alias=AliasChoices(
            "SCI_MCP_QDRANT_BATCH_SIZE",
            "QDRANT_BATCH_SIZE",
        ),
        description="Number of points to upsert per Qdrant request.  Larger values "
        "increase throughput but consume more memory per request.",
    )

    # ------------------------------------------------------------------
    # MCP tool result cache
    # ------------------------------------------------------------------

    tool_cache_ttl_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices(
            "SCI_MCP_TOOL_CACHE_TTL_SECONDS",
            "TOOL_CACHE_TTL_SECONDS",
        ),
        description=(
            "TTL in seconds for MCP tool result cache. "
            "Set to 0 to disable tool-level caching."
        ),
    )

    tool_cache_max_size: int = Field(
        default=128,
        validation_alias=AliasChoices(
            "SCI_MCP_TOOL_CACHE_MAX_SIZE",
            "TOOL_CACHE_MAX_SIZE",
        ),
        description="Maximum number of cached MCP tool results (LRU eviction).",
    )

    # ------------------------------------------------------------------
    # MCP server transport
    # ------------------------------------------------------------------

    mcp_transport: str = Field(
        default="stdio",
        validation_alias=AliasChoices(
            "SCI_MCP_TRANSPORT",
            "MCP_TRANSPORT",
        ),
        description=(
            "MCP transport mechanism.  'stdio' for local/CLI use;"
            " 'sse' for HTTP+SSE server mode."
        ),
    )

    mcp_host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices(
            "SCI_MCP_HOST",
            "MCP_HOST",
        ),
        description="Host address to bind when transport='sse'.",
    )

    mcp_port: int = Field(
        default=8080,
        validation_alias=AliasChoices(
            "SCI_MCP_PORT",
            "MCP_PORT",
        ),
        description="TCP port to bind when transport='sse'.",
    )


def build_github_client(settings: Settings) -> "github.Github":
    """Construct a PyGithub client from settings.

    Uses ``settings.github_base_url`` when set so that the client targets
    the correct GitHub instance (SAP GitHub Enterprise vs github.com).
    When ``github_base_url`` is falsy the PyGithub default is used, which
    resolves to ``https://api.github.com``.

    TLS verification: when ``settings.github_ca_bundle`` is set it is passed
    through to PyGithub's ``verify`` argument (forwarded to the underlying
    ``requests`` session).  This is required for SAP GitHub Enterprise,
    whose chain is signed by the internal SAP Global Root CA that is not
    present in the default certifi trust store.  When unset, PyGithub uses
    its default trust store.

    Args:
        settings: Validated application settings.

    Returns:
        An authenticated ``github.Github`` instance.
    """
    import github  # local import to keep module-level deps minimal

    # PyGithub forwards ``verify`` to the requests session:
    #   True  -> use default trust store (certifi)
    #   <path> -> use the given PEM CA bundle
    verify: bool | str = True
    if settings.github_ca_bundle:
        # Expand ~ and environment variables so paths like
        # "~/.config/sap-ca/sap-ca-bundle.pem" resolve correctly.
        import os

        verify = os.path.expanduser(os.path.expandvars(settings.github_ca_bundle))

    if settings.github_base_url:
        return github.Github(
            login_or_token=settings.github_token,
            base_url=settings.github_base_url,
            verify=verify,
        )
    return github.Github(settings.github_token, verify=verify)


def get_settings() -> Settings:
    """Create and return a fresh ``Settings`` instance.

    This is a factory function, not a singleton.  Call it at the
    composition root (lifespan, CLI entry point, test setup) and inject
    the returned object via constructor arguments.  Never call it inside
    library code — doing so would bypass dependency injection.

    Returns:
        A fully validated ``Settings`` instance loaded from the current
        environment and any ``.env`` file present in the working directory.

    Raises:
        pydantic_core.ValidationError: If required fields (e.g.
            ``github_token``) are absent from both the environment and
            the ``.env`` file.
    """
    return Settings()
