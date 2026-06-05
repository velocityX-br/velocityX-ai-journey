# Local Deployment — kind Cluster

## Overview

This guide deploys the full Gardener AI MCP stack on a local
[kind](https://kind.sigs.k8s.io/) (Kubernetes-in-Docker) cluster.  The stack
comprises two workloads:

- **MCP server** (`gardener-ai-mcp`) — the FastMCP application running the 7
  Gardener tools.  Entry point: `python -m gardener_mcp.server`.
- **Qdrant** — the vector database deployed as a Helm subchart alongside the
  MCP server.

The local experience is intentionally identical to production: the same
multi-stage Docker image, the same Helm chart, and the same Kubernetes
primitives.  There is no docker-compose code path.

---

## Prerequisites

Install the following tools before proceeding:

| Tool | Minimum version | Install |
|---|---|---|
| Docker | 27+ | https://docs.docker.com/get-docker/ |
| kind | 0.22+ | `brew install kind` or https://kind.sigs.k8s.io/docs/user/quick-start/#installation |
| kubectl | 1.29+ | `brew install kubectl` or https://kubernetes.io/docs/tasks/tools/ |
| Helm | 3.17+ | `brew install helm` or https://helm.sh/docs/intro/install/ |
| uv | 0.5+ | `brew install uv` or https://docs.astral.sh/uv/getting-started/installation/ |

Verify all tools are on your PATH:

```bash
docker version
kind version
kubectl version --client
helm version
uv --version
```

---

## Quick Start

```bash
# 1. Copy the environment template and fill in your credentials
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_AUTH_TOKEN and GITHUB_TOKEN

# 2. Run the bootstrap script
bash scripts/setup_kind.sh
```

The script creates the kind cluster, builds the Docker image, loads it into
kind, creates the Kubernetes Secret from your `.env` values, and installs the
Helm chart.  The stack is ready when all pods reach `Running` state.

---

## Script Walkthrough

`scripts/setup_kind.sh` performs these steps in order:

### Step 1 — Create kind cluster

```bash
kind create cluster --name gardener-ai-mcp
```

Creates a single-node Kubernetes cluster inside Docker.  If a cluster with
that name already exists the step is skipped (idempotent).

### Step 2 — Build Docker image

```bash
docker build -f docker/Dockerfile -t gardener-ai-mcp:dev .
```

Runs the multi-stage build defined in `docker/Dockerfile`.  The builder stage
installs runtime dependencies via `uv sync --frozen --no-dev` into
`/app/.venv`.  The runtime stage copies only the venv and source — no build
tooling in the final layer.  Target image size is under 400 MB.

### Step 3 — Load image into kind

```bash
kind load docker-image gardener-ai-mcp:dev --name gardener-ai-mcp
```

kind clusters are isolated from the host Docker registry.  This command copies
the image directly into the kind node so that Kubernetes can resolve it with
`imagePullPolicy: Never` — no container registry push required.

### Step 4 — Create namespace

```bash
kubectl create namespace gardener-mcp --dry-run=client -o yaml | kubectl apply -f -
```

Idempotent: creates the namespace on first run, no-op on subsequent runs.

### Step 5 — Create Kubernetes Secret

```bash
kubectl create secret generic gardener-mcp-secrets \
  --namespace=gardener-mcp \
  --from-literal=ANTHROPIC_AUTH_TOKEN="..." \
  --from-literal=GITHUB_TOKEN="..." \
  --from-literal=QDRANT_API_KEY="" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Individual `--from-literal` flags (not `--from-env-file`) ensure only the
required keys enter the Secret — no accidental leakage of other `.env`
entries.  The `--dry-run=client -o yaml | kubectl apply -f -` pattern is
idempotent: it updates the Secret in place on re-runs.

### Step 6 — Install Helm chart

```bash
helm dependency update ./helm
helm upgrade --install gardener-ai-mcp ./helm \
  --namespace=gardener-mcp \
  --values helm/values.yaml \
  --set image.repository=gardener-ai-mcp \
  --set image.tag=dev \
  --set image.pullPolicy=Never \
  --wait --timeout=120s
```

`helm upgrade --install` is idempotent.  `--wait` blocks until all pods are
ready or the timeout expires.  If the `helm/` directory is absent (Phase 7
not yet complete) the script exits with a clear warning and skips this step.

---

## Verification

Check that all pods are running in the `gardener-mcp` namespace:

```bash
kubectl get pods -n gardener-mcp
```

Expected output (pod name suffixes will differ):

```
NAME                               READY   STATUS    RESTARTS   AGE
gardener-ai-mcp-7d9f8b6c4-xk2pj   1/1     Running   0          45s
gardener-ai-mcp-qdrant-0           1/1     Running   0          45s
```

Tail the MCP server logs:

```bash
kubectl logs -f deployment/gardener-ai-mcp -n gardener-mcp
```

Expected startup line:

```
INFO     Starting Gardener AI MCP server — qdrant_url=http://gardener-ai-mcp-qdrant:6333 ...
INFO     AppContext built successfully.
```

Run the built-in health check from inside the pod:

```bash
kubectl exec deployment/gardener-ai-mcp -n gardener-mcp -- python scripts/healthcheck.py
```

Exit code 0 with output `OK: Qdrant healthy at ...` confirms that Qdrant is
reachable from inside the MCP server container.

---

## Connecting an MCP Client

### Port-forward the MCP server

```bash
kubectl port-forward -n gardener-mcp svc/gardener-ai-mcp 8080:8080
```

The SSE endpoint is now reachable at `http://localhost:8080/sse`.

### Claude Desktop configuration

Add the following entry to your `claude_desktop_config.json` to connect via
HTTP+SSE transport:

```json
{
  "mcpServers": {
    "gardener-ai-mcp": {
      "transport": "sse",
      "url": "http://localhost:8080/sse"
    }
  }
}
```

> Note: the MCP server module is `gardener_mcp.server` — not `mcp.server`.
> The `mcp/` directory was renamed to `gardener_mcp/` in Phase 5 to avoid
> shadowing the `mcp` PyPI package.  The `CMD` in `docker/Dockerfile` and the
> Claude Desktop configuration above both reflect this correct path.

For stdio transport (Claude Desktop running the server as a subprocess rather
than connecting to a running service), use:

```json
{
  "mcpServers": {
    "gardener-ai-mcp": {
      "command": "kubectl",
      "args": [
        "exec", "-i", "-n", "gardener-mcp",
        "deployment/gardener-ai-mcp",
        "--", "python", "-m", "gardener_mcp.server"
      ]
    }
  }
}
```

---

## Running Ingestion

Before the MCP tools can return results, the Qdrant collections must be
populated.  Run ingestion against the local Qdrant instance via port-forward.

### 1. Forward the Qdrant port

In a separate terminal:

```bash
kubectl port-forward -n gardener-mcp svc/gardener-ai-mcp-qdrant 6333:6333
```

Leave this running while ingestion is in progress.

### 2. Run the ingestion script

From the project root (with your `.env` sourced or exported):

```bash
source .env  # or export variables individually

QDRANT_URL=http://localhost:6333 uv run python scripts/ingest_docs.py
```

The `QDRANT_URL=http://localhost:6333` override points the ingestion script at
the forwarded local Qdrant rather than whatever `QDRANT_URL` is set to in your
`.env`.  All other configuration (GitHub token, embedding endpoint) is read
from the environment as usual.

### 3. Verify collections

After ingestion completes, query the Qdrant REST API to confirm the collections
were created:

```bash
curl -s http://localhost:6333/collections | python3 -m json.tool
```

You should see entries for `gardener_docs`, `gardener_issues`, `gardener_prs`,
and `gardener_code`.

---

## Security Scanning

Run a Trivy image scan after building and before deploying to confirm there
are no critical CVEs in the final image.  Phase 6 is not considered complete
until this command reports zero critical findings:

```bash
# After building the image, run trivy before deploying:
trivy image --exit-code 1 --severity CRITICAL gardener-ai-mcp:dev
```

Install Trivy if needed:

```bash
brew install trivy        # macOS
# or
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
```

To scan for HIGH severity in addition to CRITICAL (recommended for production
sign-off):

```bash
trivy image --exit-code 1 --severity HIGH,CRITICAL gardener-ai-mcp:dev
```

---

## Teardown

Delete the entire kind cluster and all associated resources:

```bash
kind delete cluster --name gardener-ai-mcp
```

This removes the cluster, all pods, all persistent volumes, and the loaded
image.  No Docker volumes or networks are left behind.

---

## Configuration Reference

All runtime configuration is supplied via environment variables.  The
recommended local workflow is: copy `.env.example` to `.env`, fill in
values, then run `bash scripts/setup_kind.sh` — the script reads `.env`
and creates the Kubernetes Secret automatically.

| Variable | Default | Required | Description |
|---|---|---|---|
| `ANTHROPIC_AUTH_TOKEN` | — | Yes | Bearer token for the SAP Hyperspace LLM proxy. Used for `root_cause_analysis` tool calls. |
| `ANTHROPIC_BASE_URL` | `http://localhost:6655/anthropic/` | Yes | Base URL for the Anthropic-compatible endpoint on Hyperspace. |
| `ANTHROPIC_MODEL` | `anthropic--claude-sonnet-latest` | No | LLM model identifier for generative calls. |
| `API_TIMEOUT_MS` | `3000000` | No | HTTP timeout in milliseconds for LLM API calls. |
| `GITHUB_TOKEN` | — | Yes | GitHub Personal Access Token with `read:repo` scope. Required for all ingestion and search tools. |
| `GITHUB_DOCS_REPO` | `gardener/documentation` | No | Repository slug for the primary documentation source. |
| `GITHUB_GARDENER_REPO` | `gardener/gardener` | No | Repository slug for the main Gardener source code. |
| `HYPERSPACE_OPENAI_BASE_URL` | `http://localhost:6655/openai/v1` | Yes | Base URL for the Hyperspace OpenAI-compatible embeddings endpoint. |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | No | Embedding model identifier. |
| `EMBEDDING_DIMENSIONS` | `1536` | No | Vector dimensions.  Must match the collection schema in Qdrant. |
| `QDRANT_URL` | `http://localhost:6333` | No | URL of the Qdrant instance.  Inside the cluster this is set by the Helm chart. |
| `QDRANT_API_KEY` | `` (empty) | No | Qdrant API key.  Leave empty for unauthenticated local development. |
| `QDRANT_BATCH_SIZE` | `100` | No | Points per upsert request.  Increase for faster ingestion at the cost of higher memory per request. |
| `MCP_TRANSPORT` | `stdio` | No | MCP transport.  `stdio` for CLI/local use; `sse` for HTTP+SSE server mode inside Kubernetes. |

All variables above also accept the `GARDENER_MCP_` prefix, which takes
priority over the unprefixed form.  This allows the MCP server to be
configured independently from ambient `ANTHROPIC_*` variables set in the same
shell session (e.g. by Claude Code).  See `config/settings.py` and ADR-006
for the full resolution order.

---

## Troubleshooting

### `ErrImageNeverPull` or `ImagePullBackOff`

The image was not loaded into the kind node before the pod was scheduled.
Re-run the load step and restart the deployment:

```bash
kind load docker-image gardener-ai-mcp:dev --name gardener-ai-mcp
kubectl rollout restart deployment/gardener-ai-mcp -n gardener-mcp
```

### MCP server pod in `CrashLoopBackOff`

The most common cause is a missing or invalid `ANTHROPIC_AUTH_TOKEN`.
`config/settings.py` (pydantic-settings) raises a `ValidationError` on startup
if any required variable is absent — the error is visible in the pod logs:

```bash
kubectl logs deployment/gardener-ai-mcp -n gardener-mcp
```

Verify the Secret was created and contains the correct keys:

```bash
kubectl describe secret gardener-mcp-secrets -n gardener-mcp
```

If a key is missing, delete the Secret and recreate it:

```bash
kubectl delete secret gardener-mcp-secrets -n gardener-mcp
# Re-run the setup script or create the secret manually
bash scripts/setup_kind.sh
```

### Qdrant connection refused during ingestion or health check

The Qdrant pod may not be ready yet.  Wait for it to reach `Running`:

```bash
kubectl get pods -n gardener-mcp -w
```

If the Qdrant pod itself is in `CrashLoopBackOff`, check its logs:

```bash
kubectl logs statefulset/gardener-ai-mcp-qdrant -n gardener-mcp
```

Common cause: the PersistentVolumeClaim could not be provisioned.  Verify
that a default `StorageClass` exists:

```bash
kubectl get storageclass
```

kind ships with a `standard` StorageClass backed by `rancher.io/local-path`.
If it is absent, delete and recreate the cluster — it is installed automatically
on a fresh kind cluster.

### Port-forward drops connection

Long-lived `kubectl port-forward` sessions occasionally disconnect.  Re-run
the port-forward command.  For persistent connectivity during a long ingestion
run, consider using [telepresence](https://www.telepresence.io/) or a
Kubernetes `NodePort` service type instead.
