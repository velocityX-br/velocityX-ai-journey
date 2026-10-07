# =============================================================================
# Agent Chat — Multi-stage Dockerfile (mirrors gardener-ai-mcp conventions)
#
# Stage 1 (builder): install uv, resolve deps into /app/.venv, add source.
# Stage 2 (runtime): copy only venv + source; run as non-root (uid 1001).
#
# Build from the repo root so the path dependency on packages/ai-observability
# is inside the build context:
#   docker build -f ai_agent/docker/Dockerfile -t agent-chat:dev .
#
# Run (generic demo, needs an LLM key in the environment):
#   docker run --rm -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
#     agent-chat:dev run --config generic_react --query "Say hi"
#
# NOTE: the sap_ops config launches the sibling gardener-ai-mcp server via
# `uv run` on the host filesystem; that path is NOT present inside this image.
# Containerize the sap_ops demo by mounting the repo or running gardener-ai-mcp
# as its own service over streamable_http and pointing a config at its URL.
# =============================================================================

# ── Stage 0: chat UI ─────────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS web

WORKDIR /web
COPY ai_agent/web/package.json ai_agent/web/package-lock.json ./
RUN npm ci
COPY ai_agent/web/ ./
ENV WEB_OUT_DIR=/web-dist
RUN npm run build

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# The project depends on ../packages/ai-observability (repo-root packages/).
COPY packages/ai-observability /packages/ai-observability
COPY ai_agent/pyproject.toml ai_agent/README.md /app/
COPY ai_agent/uv.lock* /app/
RUN uv sync --no-dev --no-install-project

COPY ai_agent/src/ /app/src/
COPY ai_agent/configs/ /app/configs/
RUN uv sync --no-dev

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /bin/false appuser

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src/ src/
COPY --from=web /web-dist /app/src/agent_chat/static
COPY --from=builder /app/configs/ configs/

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    AGENT_CONFIGS_DIR=/app/configs

USER appuser

# Web UI port (used by the `serve` command). CLI usage ignores it.
EXPOSE 8000

# Healthcheck hits the side-effect-free config listing endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/configs').status==200 else 1)"

ENTRYPOINT ["agent-chat"]
CMD ["--help"]
