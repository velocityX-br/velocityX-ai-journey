"""Application settings loaded from environment variables.

All runtime configuration is centralised here.  No other module reads
``os.environ`` directly.  Settings are loaded via ``pydantic-settings``
which merges values from a ``.env`` file and the process environment.

The ``GARDENER_MCP_*`` prefixed variables take priority over the
unprefixed ambient variables, enabling independent configuration of
this MCP server even when running alongside Claude Code in the same
shell session (see ADR-006).

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
    1. Process environment variables (``GARDENER_MCP_*`` prefix)
    2. Process environment variables (unprefixed ambient vars)
    3. Values from ``.env`` file
    4. Field defaults

    Attributes:
        github_token: GitHub personal access token.  Required — startup
            fails without it.
        github_docs_repo: Repository slug for the documentation source.
            Defaults to ``"gardener/documentation"``.
        github_gardener_repo: Repository slug for the main Gardener source.
            Defaults to ``"gardener/gardener"``.
        github_base_url: GitHub API base URL.  ``None`` (default) targets
            ``github.com``.  Set to ``"https://github.tools.sap/api/v3"``
            for SAP GitHub Enterprise.
        anthropic_base_url: Base URL for the Anthropic-compatible LLM proxy
            (SAP Hyperspace).
        anthropic_auth_token: Bearer token for the Anthropic proxy.
        anthropic_model: Model identifier for LLM calls.
        api_timeout_ms: HTTP timeout in milliseconds for LLM calls.
        hyperspace_openai_base_url: Base URL for the OpenAI-compatible
            embeddings endpoint on Hyperspace.
        embedding_model: Embedding model identifier.
        embedding_dimensions: Number of dimensions for embedding vectors.
        qdrant_url: URL of the Qdrant vector database instance.
        qdrant_api_key: Optional API key for Qdrant.
        qdrant_batch_size: Number of points to upsert per Qdrant request.
        mcp_transport: MCP transport mechanism — ``"stdio"`` for CLI/local use,
            ``"sse"`` for HTTP+SSE server mode.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # GitHub
    # ------------------------------------------------------------------

    github_token: str = Field(
        validation_alias=AliasChoices(
            "GARDENER_MCP_GITHUB_TOKEN",
            "GITHUB_TOKEN",
        ),
        description="GitHub personal access token.  Required.",
    )

    github_docs_repo: str = Field(
        default="gardener/documentation",
        validation_alias=AliasChoices(
            "GARDENER_MCP_GITHUB_DOCS_REPO",
            "GITHUB_DOCS_REPO",
        ),
        description="Repository slug for the Gardener documentation source.",
    )

    github_gardener_repo: str = Field(
        default="gardener/gardener",
        validation_alias=AliasChoices(
            "GARDENER_MCP_GITHUB_GARDENER_REPO",
            "GITHUB_GARDENER_REPO",
        ),
        description="Repository slug for the main Gardener source code.",
    )

    github_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GARDENER_MCP_GITHUB_BASE_URL",
            "GITHUB_BASE_URL",
        ),
        description=(
            "GitHub API base URL.  Leave unset (None) to target github.com. "
            "Set to e.g. 'https://github.tools.sap/api/v3' for GitHub Enterprise."
        ),
    )

    # ------------------------------------------------------------------
    # Anthropic LLM proxy (SAP Hyperspace)
    # ------------------------------------------------------------------

    anthropic_base_url: str = Field(
        default="http://localhost:6655/anthropic/",
        validation_alias=AliasChoices(
            "GARDENER_MCP_ANTHROPIC_BASE_URL",
            "ANTHROPIC_BASE_URL",
        ),
        description="Base URL for the Anthropic-compatible LLM proxy.",
    )

    anthropic_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GARDENER_MCP_ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_AUTH_TOKEN",
        ),
        description="Bearer token for the Anthropic-compatible LLM proxy.",
    )

    anthropic_model: str = Field(
        default="anthropic--claude-sonnet-latest",
        validation_alias=AliasChoices(
            "GARDENER_MCP_ANTHROPIC_MODEL",
            "ANTHROPIC_MODEL",
        ),
        description="Model identifier for LLM calls via the Hyperspace proxy.",
    )

    api_timeout_ms: int = Field(
        default=3_000_000,
        validation_alias=AliasChoices(
            "GARDENER_MCP_API_TIMEOUT_MS",
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
            "GARDENER_MCP_HYPERSPACE_OPENAI_BASE_URL",
            "HYPERSPACE_OPENAI_BASE_URL",
        ),
        description="Base URL for the Hyperspace OpenAI-compatible embeddings endpoint.",
    )

    embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices(
            "GARDENER_MCP_EMBEDDING_MODEL",
            "EMBEDDING_MODEL",
        ),
        description="Embedding model identifier.",
    )

    embedding_dimensions: int = Field(
        default=1536,
        validation_alias=AliasChoices(
            "GARDENER_MCP_EMBEDDING_DIMENSIONS",
            "EMBEDDING_DIMENSIONS",
        ),
        description="Number of dimensions for embedding vectors.",
    )

    embedding_cache_size: int = Field(
        default=256,
        validation_alias=AliasChoices(
            "GARDENER_MCP_EMBEDDING_CACHE_SIZE",
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
            "GARDENER_MCP_QDRANT_URL",
            "QDRANT_URL",
        ),
        description="URL of the Qdrant vector database instance.",
    )

    qdrant_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GARDENER_MCP_QDRANT_API_KEY",
            "QDRANT_API_KEY",
        ),
        description="Optional API key for Qdrant.  Leave empty for unauthenticated access.",
    )

    qdrant_batch_size: int = Field(
        default=100,
        validation_alias=AliasChoices(
            "GARDENER_MCP_QDRANT_BATCH_SIZE",
            "QDRANT_BATCH_SIZE",
        ),
        description="Number of points to upsert per Qdrant request.  Larger values "
        "increase throughput but consume more memory per request.",
    )

    # ------------------------------------------------------------------
    # Ingestion limits
    # ------------------------------------------------------------------

    ingestion_max_prs: int = Field(
        default=500,
        validation_alias=AliasChoices(
            "GARDENER_MCP_INGESTION_MAX_PRS",
            "INGESTION_MAX_PRS",
        ),
        description=(
            "Maximum number of PRs to ingest.  Fetches the most-recently updated "
            "PRs first (state='all', sorted by updated).  Set to 0 for no limit "
            "(not recommended for large repos — will exhaust GitHub API rate limits)."
        ),
    )

    ingestion_max_issues: int = Field(
        default=1000,
        validation_alias=AliasChoices(
            "GARDENER_MCP_INGESTION_MAX_ISSUES",
            "INGESTION_MAX_ISSUES",
        ),
        description=(
            "Maximum number of issues to ingest.  Fetches the most-recently updated "
            "issues first (state='all', sorted by updated).  Set to 0 for no limit "
            "(not recommended for large repos — will exhaust GitHub API rate limits)."
        ),
    )

    ingestion_issues_batch_size: int = Field(
        default=100,
        validation_alias=AliasChoices(
            "GARDENER_MCP_INGESTION_ISSUES_BATCH_SIZE",
            "INGESTION_ISSUES_BATCH_SIZE",
        ),
        description=(
            "Number of issues to process per batch during ingestion.  "
            "Each batch fetches issue comments sequentially before moving on.  "
            "Smaller values reduce peak memory usage."
        ),
    )

    # ------------------------------------------------------------------
    # MCP tool result cache
    # ------------------------------------------------------------------

    tool_cache_ttl_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices(
            "GARDENER_MCP_TOOL_CACHE_TTL_SECONDS",
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
            "GARDENER_MCP_TOOL_CACHE_MAX_SIZE",
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
            "GARDENER_MCP_TRANSPORT",
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
            "GARDENER_MCP_HOST",
            "MCP_HOST",
        ),
        description="Host address to bind when transport='sse'.",
    )

    mcp_port: int = Field(
        default=8080,
        validation_alias=AliasChoices(
            "GARDENER_MCP_PORT",
            "MCP_PORT",
        ),
        description="TCP port to bind when transport='sse'.",
    )


def build_github_client(settings: Settings) -> "github.Github":
    """Construct a PyGithub client from settings.

    Uses ``settings.github_base_url`` when set so that the client targets
    the correct GitHub instance (``github.com`` vs GitHub Enterprise).
    When ``github_base_url`` is ``None`` the PyGithub default is used,
    which resolves to ``https://api.github.com``.

    Args:
        settings: Validated application settings.

    Returns:
        An authenticated ``github.Github`` instance.
    """
    import github  # local import to keep module-level deps minimal

    if settings.github_base_url:
        return github.Github(
            login_or_token=settings.github_token,
            base_url=settings.github_base_url,
        )
    return github.Github(settings.github_token)


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
