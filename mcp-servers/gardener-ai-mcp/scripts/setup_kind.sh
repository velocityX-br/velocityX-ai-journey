#!/usr/bin/env bash
# =============================================================================
# setup_kind.sh — Bootstrap the Gardener AI MCP stack on a local kind cluster.
#
# Usage:
#   bash scripts/setup_kind.sh
#
# Prerequisites (must be installed and available on PATH):
#   - kind    (https://kind.sigs.k8s.io/docs/user/quick-start/#installation)
#   - kubectl (https://kubernetes.io/docs/tasks/tools/)
#   - helm    v3.17+ (https://helm.sh/docs/intro/install/)
#   - docker  (https://docs.docker.com/get-docker/)
#
# Required environment variables (or defined in a .env file):
#   ANTHROPIC_AUTH_TOKEN — Hyperspace bearer token for LLM calls
#   GITHUB_TOKEN         — GitHub Personal Access Token (read:repo scope)
#
# Optional:
#   QDRANT_API_KEY — leave empty for unauthenticated local Qdrant
#
# Make this script executable:
#   chmod +x scripts/setup_kind.sh
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
CLUSTER_NAME="gardener-ai-mcp"
IMAGE_NAME="gardener-ai-mcp:dev"
CHART_DIR="./helm"
NAMESPACE="gardener-mcp"

# ── Helpers ───────────────────────────────────────────────────────────────────
log_step()  { echo "→ $*"; }
log_ok()    { echo "  ✓ $*"; }
log_warn()  { echo "  ⚠ $*"; }

# ── Prerequisite checks ───────────────────────────────────────────────────────
for cmd in kind kubectl helm docker; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH. Install it before running this script."
    exit 1
  fi
done

# ── 1. Create kind cluster ────────────────────────────────────────────────────
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
  log_ok "kind cluster '${CLUSTER_NAME}' already exists, skipping creation"
else
  log_step "Creating kind cluster '${CLUSTER_NAME}'..."
  kind create cluster --name "${CLUSTER_NAME}"
  log_ok "Cluster created"
fi

# ── 2. Build Docker image ─────────────────────────────────────────────────────
log_step "Building Docker image ${IMAGE_NAME}..."
docker build -f docker/Dockerfile -t "${IMAGE_NAME}" .
log_ok "Image built: ${IMAGE_NAME}"

# ── 3. Load image into kind ───────────────────────────────────────────────────
# kind clusters are isolated from the host Docker daemon registry.
# 'kind load docker-image' copies the image directly into the kind node so
# that Kubernetes can pull it with imagePullPolicy=Never (no registry required).
log_step "Loading image into kind cluster '${CLUSTER_NAME}'..."
kind load docker-image "${IMAGE_NAME}" --name "${CLUSTER_NAME}"
log_ok "Image loaded"

# ── 4. Create namespace ───────────────────────────────────────────────────────
# --dry-run=client -o yaml | kubectl apply -f - is idempotent:
# it creates the namespace on first run and is a no-op on subsequent runs.
log_step "Ensuring namespace '${NAMESPACE}'..."
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
log_ok "Namespace '${NAMESPACE}' ready"

# ── 5. Create secrets from environment ───────────────────────────────────────
# Load from .env file if it exists in the project root.
# set -a exports all variables so they become available to subsequent commands.
if [[ -f .env ]]; then
  log_step "Loading environment from .env file..."
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  log_ok ".env loaded"
fi

# Validate required variables — the script will abort here with a clear message
# if either variable is missing from the environment and the .env file.
: "${ANTHROPIC_AUTH_TOKEN:?ANTHROPIC_AUTH_TOKEN must be set (or defined in .env)}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN must be set (or defined in .env)}"

# Create or update the Kubernetes Secret using dry-run + apply — idempotent.
# Secrets are passed as individual --from-literal flags rather than --from-env-file
# to ensure only the required keys are included (not the entire .env file).
log_step "Applying Kubernetes secret 'gardener-mcp-secrets'..."
kubectl create secret generic gardener-mcp-secrets \
  --namespace="${NAMESPACE}" \
  --from-literal=ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN}" \
  --from-literal=GITHUB_TOKEN="${GITHUB_TOKEN}" \
  --from-literal=QDRANT_API_KEY="${QDRANT_API_KEY:-}" \
  --dry-run=client -o yaml | kubectl apply -f -
log_ok "Secret applied"

# ── 6. Install Helm chart ─────────────────────────────────────────────────────
if [[ ! -d "${CHART_DIR}" ]]; then
  log_warn "Helm chart not found at ${CHART_DIR} — skipping Helm install."
  log_warn "Run this script again after Phase 7 (Helm) is complete."
  echo ""
  echo "Manual next steps:"
  echo "  1. Implement the Helm chart in ${CHART_DIR}/"
  echo "  2. Re-run:  bash scripts/setup_kind.sh"
  exit 0
fi

log_step "Updating Helm chart dependencies (Qdrant subchart)..."
helm dependency update "${CHART_DIR}"
log_ok "Dependencies updated"

log_step "Installing/upgrading Helm release 'gardener-ai-mcp'..."
helm upgrade --install gardener-ai-mcp "${CHART_DIR}" \
  --namespace="${NAMESPACE}" \
  --values "${CHART_DIR}/values.yaml" \
  --set image.repository=gardener-ai-mcp \
  --set image.tag=dev \
  --set image.pullPolicy=Never \
  --wait --timeout=120s
log_ok "Helm chart installed"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Stack is running.  Verify with:"
echo "  kubectl get pods -n ${NAMESPACE}"
echo ""
echo "Tail MCP server logs:"
echo "  kubectl logs -f deployment/gardener-ai-mcp -n ${NAMESPACE}"
echo ""
echo "Connect via port-forward (MCP SSE endpoint):"
echo "  kubectl port-forward -n ${NAMESPACE} svc/gardener-ai-mcp 8080:8080"
echo "  # then point your MCP client at http://localhost:8080/sse"
echo ""
echo "Connect via port-forward (Qdrant dashboard / ingestion):"
echo "  kubectl port-forward -n ${NAMESPACE} svc/gardener-ai-mcp-qdrant 6333:6333"
echo ""
echo "Tear down:"
echo "  kind delete cluster --name ${CLUSTER_NAME}"
