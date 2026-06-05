# Cloud Deployment — Kubernetes

## Overview

This guide deploys the Gardener AI MCP server to any production Kubernetes
cluster using the Helm chart located in `helm/`.  The same chart is used for
local kind development (see `docs/deploy-local.md`) — only the image source,
secret values, and resource settings differ between environments.

The deployed stack comprises:

- **MCP server** — the FastMCP application (`python -m gardener_mcp.server`)
  running behind a Kubernetes Service.
- **Qdrant** — deployed as a Helm subchart providing the vector store.
  Alternatively Qdrant can be replaced by an external managed vector database
  by overriding `qdrant.enabled=false` and setting `QDRANT_URL` to the
  external endpoint.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Kubernetes cluster | 1.29+ — any conformant cluster (GKE, EKS, AKS, Gardener shoot) |
| `kubectl` | Configured with a valid kubeconfig pointing at the target cluster |
| `helm` | v3.17+ |
| Container registry | Any OCI-compatible registry (ghcr.io, GAR, ECR, Docker Hub) |
| Docker | For local image builds |

Verify cluster access before proceeding:

```bash
kubectl cluster-info
kubectl get nodes
```

---

## Build and Push the Image

Build the multi-stage image and push it to your registry:

```bash
# Set your registry coordinates
REGISTRY=ghcr.io/your-org/gardener-ai-mcp
TAG=$(git describe --tags --always --dirty)

docker build -f docker/Dockerfile -t "${REGISTRY}:${TAG}" -t "${REGISTRY}:latest" .
docker push "${REGISTRY}:${TAG}"
docker push "${REGISTRY}:latest"
```

For GitHub Container Registry, authenticate first:

```bash
echo "${GITHUB_TOKEN}" | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
```

---

## Create Namespace and Secrets

Create the namespace:

```bash
kubectl create namespace gardener-mcp
```

Create the Kubernetes Secret holding all runtime credentials.  Pass values
from environment variables or a secrets manager — never hardcode them:

```bash
kubectl create secret generic gardener-mcp-secrets \
  --namespace gardener-mcp \
  --from-literal=ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN}" \
  --from-literal=GITHUB_TOKEN="${GITHUB_TOKEN}" \
  --from-literal=QDRANT_API_KEY="${QDRANT_API_KEY:-}"
```

Verify the Secret was created (key names only — values are not printed):

```bash
kubectl describe secret gardener-mcp-secrets -n gardener-mcp
```

---

## Helm Install

Update subchart dependencies (pulls the Qdrant chart):

```bash
helm dependency update ./helm
```

Install the release:

```bash
helm upgrade --install gardener-ai-mcp ./helm \
  --namespace gardener-mcp \
  --values helm/values.yaml \
  --set image.repository="${REGISTRY}" \
  --set image.tag="${TAG}" \
  --set image.pullPolicy=Always
```

To supply environment-specific overrides without modifying `values.yaml`,
create a `values.override.yaml` and pass it with an additional `--values`
flag:

```bash
helm upgrade --install gardener-ai-mcp ./helm \
  --namespace gardener-mcp \
  --values helm/values.yaml \
  --values helm/values.override.yaml \
  --set image.repository="${REGISTRY}" \
  --set image.tag="${TAG}"
```

---

## Rollout Verification

Watch the rollout until completion:

```bash
kubectl rollout status deployment/gardener-ai-mcp -n gardener-mcp
```

Check pod status:

```bash
kubectl get pods -n gardener-mcp
```

Expected output:

```
NAME                               READY   STATUS    RESTARTS   AGE
gardener-ai-mcp-7d9f8b6c4-xk2pj   1/1     Running   0          2m
gardener-ai-mcp-qdrant-0           1/1     Running   0          2m
```

Confirm Qdrant is reachable from inside the MCP server pod:

```bash
kubectl exec deployment/gardener-ai-mcp -n gardener-mcp -- python scripts/healthcheck.py
```

Tail the MCP server logs:

```bash
kubectl logs -f deployment/gardener-ai-mcp -n gardener-mcp
```

---

## Upgrade Workflow

Build and push a new image, then upgrade the Helm release:

```bash
TAG=$(git describe --tags --always)

docker build -f docker/Dockerfile -t "${REGISTRY}:${TAG}" .
docker push "${REGISTRY}:${TAG}"

helm upgrade gardener-ai-mcp ./helm \
  --namespace gardener-mcp \
  --values helm/values.yaml \
  --set image.repository="${REGISTRY}" \
  --set image.tag="${TAG}"
```

Monitor the rolling update:

```bash
kubectl rollout status deployment/gardener-ai-mcp -n gardener-mcp
```

Roll back to the previous release if needed:

```bash
helm rollback gardener-ai-mcp -n gardener-mcp
```

---

## Secret Rotation

Rotate credentials without rebuilding or redeploying the image.  The
`--dry-run=client -o yaml | kubectl apply -f -` pattern is idempotent — it
updates the Secret in place rather than deleting and recreating it:

```bash
kubectl create secret generic gardener-mcp-secrets \
  --namespace gardener-mcp \
  --from-literal=ANTHROPIC_AUTH_TOKEN="${NEW_ANTHROPIC_AUTH_TOKEN}" \
  --from-literal=GITHUB_TOKEN="${GITHUB_TOKEN}" \
  --from-literal=QDRANT_API_KEY="${QDRANT_API_KEY:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Trigger a rolling restart to mount the updated Secret
kubectl rollout restart deployment/gardener-ai-mcp -n gardener-mcp
```

For automated rotation in production, use the
[External Secrets Operator](https://external-secrets.io/) to sync secrets from
HashiCorp Vault, AWS Secrets Manager, or Azure Key Vault into the Kubernetes
Secret resource.  The Helm chart deployment reads secrets via `envFrom` — the
operator updates the `Secret` object and a rolling restart picks up the new
values automatically.

Note: the SAP Hyperspace bearer token (`ANTHROPIC_AUTH_TOKEN`) has a short TTL
(see ADR-006).  In long-running Kubernetes deployments this token must be
rotated via a CronJob, a sidecar, or an External Secrets Operator sync — not
manually.

---

## Gardener Shoot Cluster Note

Deploying to a Gardener-managed shoot cluster follows the same steps as any
other Kubernetes cluster.  The only difference is how you obtain the
kubeconfig.

Obtain the shoot kubeconfig via `gardenctl`:

```bash
# Target the garden project and shoot
gardenctl target --garden <garden-name> --project <project-name> --shoot <shoot-name>

# Export the kubeconfig
export KUBECONFIG="$(gardenctl kubectl-env bash | grep KUBECONFIG | cut -d= -f2)"

# Verify
kubectl cluster-info
```

Then proceed from [Create Namespace and Secrets](#create-namespace-and-secrets)
above.  All `kubectl` and `helm` commands work identically against a shoot
cluster.

Shoot-specific notes:

- Shoot clusters are fully managed — no node-level access is needed or
  available.
- Persistent volumes for the Qdrant subchart are provisioned automatically via
  the shoot's cloud-provider CSI driver (configured by Gardener).
- For SAP-internal deployments the Hyperspace LLM proxy base URL
  (`ANTHROPIC_BASE_URL`) must point to the in-cluster or VPN-accessible
  Hyperspace endpoint — not `localhost`.
- Qdrant can run as the Helm subchart (default) or be replaced by an external
  managed vector database by setting `qdrant.enabled=false` in
  `values.override.yaml` and providing `QDRANT_URL` and `QDRANT_API_KEY` in
  the Secret.
- Refer to the [Gardener documentation repository](https://github.com/gardener/documentation)
  for shoot cluster management procedures, including kubeconfig rotation and
  worker pool configuration.

---

## Resource Recommendations

The following resource requests and limits are recommended starting points.
Adjust based on observed usage in your specific cluster.

| Workload | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---|---|---|---|---|
| MCP server (`gardener-ai-mcp`) | 250m | 1000m | 256Mi | 1Gi |
| Qdrant | 500m | 2000m | 1Gi | 4Gi |

Notes:

- The MCP server is IO-bound (GitHub API calls, Qdrant queries, LLM calls).
  CPU limits are generous relative to requests because burst is brief.
- Qdrant memory usage scales with the number of indexed vectors and the HNSW
  graph size.  1Gi covers the initial four collections
  (`gardener_docs`, `gardener_issues`, `gardener_prs`, `gardener_code`) with
  moderate data volumes.  Increase the limit if Qdrant OOMKills are observed.
- For production, configure a `PodDisruptionBudget` for Qdrant (StatefulSet)
  to ensure at least one replica remains available during node drain operations.

---

## Uninstall

Remove the Helm release and namespace:

```bash
helm uninstall gardener-ai-mcp -n gardener-mcp
kubectl delete namespace gardener-mcp
```

Qdrant's `PersistentVolumeClaim` may not be deleted automatically depending on
the cluster's volume reclaim policy.  Check and delete manually if needed:

```bash
kubectl get pvc -n gardener-mcp
kubectl delete pvc <qdrant-pvc-name> -n gardener-mcp
```
