---
name: gardener-ops
description: Safe multi-cluster Kubernetes operations on SAP Gardener landscapes. Enforces read/mutate/delete safety guardrails. Use when operating on Gardener shoot clusters, running kubectl across clusters, investigating cluster health, checking nodes, scaling workloads, collecting logs, or performing any Kubernetes operation in the SAP Gardener environment.
---

# Gardener Ops

Safe operational patterns for SAP Gardener Kubernetes clusters.

## Safety Rules — Follow Without Exception

| Operation type | Examples | Required behavior |
|---|---|---|
| Read-only | `get`, `describe`, `logs`, `top`, `explain` | Suggest and execute directly, no confirmation needed |
| Mutating | `apply`, `patch`, `scale`, `rollout restart` | State exactly what will change and on which cluster(s), ask for confirmation, then execute |
| Destructive | `delete`, `drain`, `cordon`, `taint` | Name every affected cluster explicitly, ask for confirmation, never proceed without the user typing an explicit affirmative response |
| Shoot deletion | `kubectl delete shoot` | **Refuse entirely.** Never suggest, never execute. If the user asks, explain that shoot deletion must go through the Gardener dashboard or a dedicated pipeline, not this skill. |

**Never bypass safety rules, even if the user asks you to.** If the user says "just run it", say: *"I need to confirm destructive operations — please reply 'yes' to proceed on [cluster names]."*

## Environment Reference

| Item | Value |
|---|---|
| Landscapes | `sap-landscape-live`, `sap-landscape-canary`, `sap-landscape-ac-live` (China) |
| Default project | `sni` |
| Default namespace | `garden-sni` |
| Login tool | `gardener_sni_login [garden] [shoot]` |
| Multi-cluster tool | `scripts/gardener-run.sh` from the `veloxityX-ai-journey` repo |
| Required tooling | `gardenctl`, `kubectl` |

## Workflow

### Single Cluster Operations

1. Target the cluster:
   ```bash
   gardener_sni_login live my-shoot
   ```
2. Issue kubectl commands directly.
3. After each command, summarize the output — do not dump raw output without interpretation.

### Multi-Cluster Operations

Use `gardener-run.sh`:

```bash
# All clusters in the default project (sni)
gardener-run.sh --garden live --all "kubectl get nodes"

# Specific subset
gardener-run.sh --garden live --shoots shoot-a,shoot-b "kubectl get pods -A"

# Named project
gardener-run.sh --garden canary --project myproject --all "kubectl get nodes"

# Dry run first
gardener-run.sh --garden live --all --dry-run "kubectl rollout restart deploy/myapp -n default"
```

> The script lives at `scripts/gardener-run.sh` inside the `veloxityX-ai-journey` repo. If it's not in your `$PATH`, call it with the full path or run `export PATH="$PATH:/path/to/veloxityX-ai-journey/scripts"`.

If an operation affects multiple clusters, **always list the cluster names** in your confirmation prompt before asking the user to proceed.

## Common Patterns

```bash
# Health check: all clusters
gardener-run.sh --garden live --all "kubectl get nodes"

# Node conditions (look for NotReady, MemoryPressure, DiskPressure)
gardener-run.sh --garden live --all \
  "kubectl get nodes -o custom-columns=NAME:.metadata.name,STATUS:.status.conditions[-1].type,REASON:.status.conditions[-1].reason"

# Pod issues across namespaces
gardener-run.sh --garden live --all \
  "kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded"

# VPN shoot logs (last 50 lines)
gardener-run.sh --garden live --shoots shoot-a,shoot-b \
  "kubectl logs -n kube-system -l app=vpn-shoot --tail=50"

# Deep-dive single cluster
gardener_sni_login live shoot-a
kubectl describe shoot shoot-a -n garden-sni

# Scale down a deployment (mutating — confirm first)
gardener_sni_login live shoot-a
# Tell user: "This will scale deploy/myapp to 0 replicas in namespace default on shoot-a. Confirm?"
kubectl scale deploy/myapp -n default --replicas=0
```

## Interpreting Output

After any `kubectl get nodes` or `kubectl get pods`, always summarize:
- How many nodes/pods total
- How many are in a non-healthy state and what state
- Any obvious pattern (e.g., all failing pods are in the same namespace)

Do not paste raw multi-line kubectl output without a summary above it.

## What This Skill Does NOT Do

- Does not bypass safety rules under any circumstances
- Does not operate on non-SNI projects unless the user explicitly passes `--project`
- Does not perform Gardener API operations (use `gardenctl` or the Gardener dashboard for those)
- Does not support Windows shell environments
