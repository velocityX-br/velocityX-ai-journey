# =============================================================================
# Gardener AI MCP — Multi-stage Dockerfile
#
# Stage 1 (builder): installs uv, resolves and installs all runtime
#   dependencies into /app/.venv, then copies the application source.
#
# Stage 2 (runtime): copies only the venv and source from the builder.
#   Runs as a non-root user (uid 1001).  No build tooling is present in
#   the final image.
#
# CMD: python -m gardener_mcp.server
#   The module path is gardener_mcp.server — NOT mcp.server.
#   The mcp/ directory was renamed to gardener_mcp/ in Phase 5 to avoid
#   shadowing the mcp PyPI package.
#
# Build:
#   docker build -f docker/Dockerfile -t gardener-ai-mcp:dev .
#
# Run (SSE transport, secrets from environment):
#   docker run --rm \
#     -e GITHUB_TOKEN=$GITHUB_TOKEN \
#     -e ANTHROPIC_AUTH_TOKEN=$ANTHROPIC_AUTH_TOKEN \
#     -e QDRANT_URL=http://host.docker.internal:6333 \
#     -e GARDENER_MCP_TRANSPORT=sse \
#     -p 8080:8080 \
#     gardener-ai-mcp:dev
# =============================================================================

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv from the official image — avoids pip bootstrap and is faster
# than curl-based installs in restricted build environments.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency manifest first — layer is cached as long as pyproject.toml
# and uv.lock are unchanged, even when source files change.
COPY pyproject.toml .
COPY README.md .
COPY uv.lock* .

# Install runtime dependencies (no dev extras) into the project venv.
# --frozen: honour exact lock-file versions.
# --no-dev: exclude dev extras (pytest, ruff, mypy, etc.).
# --no-install-project: skip installing the project package itself for now
#   so that the dependency layer can be cached independently.
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source after the dependency layer is cached.
COPY config/ config/
COPY embeddings/ embeddings/
COPY vectorstore/ vectorstore/
COPY ingestion/ ingestion/
COPY retrieval/ retrieval/
COPY gardener_mcp/ gardener_mcp/
COPY scripts/ scripts/

# Install the project package itself into the venv.
RUN uv sync --frozen --no-dev

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Create a dedicated non-root user and group.
# uid/gid 1001 avoids collision with the default 'nobody' (65534) and any
# system accounts present in the base image.
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /bin/false appuser

# Copy the pre-built virtualenv and application source from the builder.
# Only the artefacts needed at runtime are copied — no uv binary, no build
# cache, no pip metadata.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/config/ config/
COPY --from=builder /app/embeddings/ embeddings/
COPY --from=builder /app/vectorstore/ vectorstore/
COPY --from=builder /app/ingestion/ ingestion/
COPY --from=builder /app/retrieval/ retrieval/
COPY --from=builder /app/gardener_mcp/ gardener_mcp/
COPY --from=builder /app/scripts/ scripts/

# ── Environment ───────────────────────────────────────────────────────────────
# PATH: use the venv Python and scripts without requiring 'source activate'.
# PYTHONPATH: make the repo root importable so 'from config.settings import ...'
#   resolves correctly without installing the project as a system package.
# PYTHONDONTWRITEBYTECODE: suppress .pyc creation (saves a few MB at runtime).
# PYTHONUNBUFFERED: ensure stdout/stderr are flushed immediately — important for
#   log aggregation in Kubernetes.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── Health check ──────────────────────────────────────────────────────────────
# The healthcheck script uses only stdlib (urllib) — no extra dependencies.
# It queries the Qdrant /healthz endpoint whose URL is read from $QDRANT_URL.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python scripts/healthcheck.py

# ── Security ──────────────────────────────────────────────────────────────────
# Drop to non-root.  All secrets are injected at runtime via environment
# variables — never baked into this image.
USER appuser

EXPOSE 8080

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Transport is controlled at runtime via GARDENER_MCP_TRANSPORT or MCP_TRANSPORT.
# Default is 'stdio' (for local/CLI MCP clients).
# Set MCP_TRANSPORT=sse for HTTP+SSE server mode inside Kubernetes.
CMD ["python", "-m", "gardener_mcp.server"]
