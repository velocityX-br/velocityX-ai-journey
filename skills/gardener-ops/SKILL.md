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
| Multi-cluster tool | `scripts/gardener-run.sh` (bundled with this skill) |
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

> The script is bundled with this skill at `skills/gardener-ops/scripts/gardener-run.sh`. Invoke it with the full path, or add it to `$PATH`:
> `export PATH="$PATH:/path/to/veloxityX-ai-journey/skills/gardener-ops/scripts"`.
>
> Run `gardener-run.sh --help` to see all options, flags, and examples.

If an operation affects multiple clusters, **always list the cluster names** in your confirmation prompt before asking the user to proceed.

### Multi-Cluster Deep Dives (and when subagents help)

**`gardener-run.sh` is the primary tool for multi-cluster work.** It runs in *this*
session, which already holds valid gardenctl/kubectl credentials, so it reliably
reaches every shoot. For a deep investigation you can pack multiple read-only
commands into a single fan-out call and let the script iterate the fleet:

```bash
# Deep-dive one command chain across all shoots (read-only).
gardener-run.sh --garden canary --project sni --all \
  "kubectl get nodes -o wide; echo '--- non-running pods ---'; \
   kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded; \
   echo '--- recent events ---'; kubectl get events -A --sort-by=.lastTimestamp | tail -30"
```

Then summarize the fan-out output per cluster in the main thread. This is the
**default, reliable path** — prefer it for almost all fleet investigations.

**⚠️ Subagents usually CANNOT run kubectl.** The `Explore` subagent type is a
file/search specialist — it has no shell access to a live cluster and will refuse
`gardenctl`/`kubectl` commands. Even `general-purpose` subagents run in a sandbox
that may **not inherit this session's `KUBECONFIG`/gardenctl target**, so they can
fail with DNS/credential errors. Do **not** assume a subagent can reach a cluster.

**When a subagent *is* worth it:** only when the work is large-context *reasoning*
rather than live cluster access — e.g. you have already collected raw logs/events
(via `gardener-run.sh` or `kubectl ... > file`) and want a subagent to analyze the
saved output, or to search the repo/docs for a known error signature. In that case:

- Use `subagent_type: "general-purpose"`, hand it the **already-collected data**
  (file paths or pasted output), and ask for a **structured summary, not raw dumps**.
- Never rely on the subagent to target or query the cluster itself.
- Before delegating live commands, verify access in the main session first
  (`kubectl get ns` on one shoot); if that only works here, keep the kubectl in the
  main thread and use subagents purely for analysis.

**Safety rules (non-negotiable):**

- All Safety Rules above still apply. **Mutating and destructive operations stay in
  the main thread** with explicit per-cluster confirmation — never inside a subagent
  and never hidden inside a `gardener-run.sh` fan-out (the script's delete-guard
  prompts, but you must still confirm with the user first).
- Any delegated analysis is **read-only** — subagents summarize collected data, they
  do not act on clusters.

**Rule of thumb:** multi-cluster data collection → `gardener-run.sh` (reliable,
credentialed). Heavy *analysis* of already-collected output → optional
`general-purpose` subagent. Do not use subagents to reach clusters directly.

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
- Does not delegate mutating or destructive operations to subagents — those stay in the main thread with user confirmation
- Does not operate on non-SNI projects unless the user explicitly passes `--project`
- Does not perform Gardener API operations (use `gardenctl` or the Gardener dashboard for those)
- Does not support Windows shell environments

## Bundled Files

| File | Purpose |
|---|---|
| `scripts/gardener-run.sh` | Multi-cluster kubectl fan-out (execute; do not modify) |
| `template.md` | Fill-in template for planning and confirming a Gardener operation before running it |
| `examples/sample.md` | Example of a completed operation write-up (health check across all live shoots) |

Use `template.md` at the start of any multi-cluster operation to structure the plan and safety confirmation. Refer to `examples/sample.md` to see the expected shape of the final report.
