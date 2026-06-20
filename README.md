# velocityX-ai-journey

---

## Skills & Scripts

### Claude Code Skills

Skills are loaded automatically when you open this repo in Claude Code.

| Skill | Trigger | Description |
|-------|---------|-------------|
| `sap-slides` | "Create a SAP presentation / internal deck / BTP demo" | Generates SAP-branded HTML presentations by wrapping `frontend-slides` with SAP Horizon palette and 72 typeface |
| `gardener-ops` | "Run kubectl on Gardener / check cluster health / operate on shoots" | Safe multi-cluster Kubernetes operations on SAP Gardener landscapes with read/mutate/delete guardrails |

### Scripts

#### `scripts/gardener-run.sh`

Fan out a `kubectl` command to multiple Gardener shoot clusters.

```bash
# Add to PATH (add this to your ~/.zshrc or ~/.bashrc)
export PATH="$PATH:/Users/I577081/Workdir/Github/veloxityX-ai-journey/scripts"

# Run on all shoots
gardener-run.sh --garden live --all "kubectl get nodes"

# Run on specific shoots
gardener-run.sh --garden live --shoots shoot-a,shoot-b "kubectl get pods -A"

# Dry run
gardener-run.sh --garden live --all --dry-run "kubectl rollout restart deploy/myapp -n default"
```

**Prerequisites:** `gardenctl`, `kubectl`, `KUBECONFIG` set via `eval "$(gardenctl kubectl-env bash)"`.
