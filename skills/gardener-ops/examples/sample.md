# Example: Node Health Check Across All `live` Shoots

This example shows how a Claude session should structure a multi-cluster read-only
operation using the `gardener-ops` skill. The user's request was:

> "Can you check node health across all live shoots?"

---

## 1. Intent

- Goal: Confirm every worker node across the `live` landscape is in `Ready` state.
- Requested by: on-call engineer, ad-hoc check before weekend.

## 2. Scope

| Field | Value |
|---|---|
| Landscape | `sap-landscape-live` |
| Project | `sni` |
| Shoots | `--all` (resolved to 4 shoots: `shoot-app-a`, `shoot-app-b`, `shoot-data-a`, `shoot-vpn-a`) |

## 3. Operation Classification

- [x] **Read-only** — `kubectl get nodes`. Safe to proceed directly.

## 4. Command

```bash
scripts/gardener-run.sh --garden live --all "kubectl get nodes"
```

## 5. Dry Run

_Not required for read-only operations._

## 6. Confirmation Prompt

_Not required for read-only operations._

## 7. Execution

**Summary:**
- Total nodes inspected: 23
- Non-healthy count: 1 (`shoot-data-a`, node `worker-data-a-z1-abc12`, `NotReady`)
- Pattern: single node in a single shoot; the other three shoots are fully healthy.

**Per-cluster status:**

| Shoot | Status | Notes |
|---|---|---|
| shoot-app-a  | ✓ pass | 6/6 nodes `Ready` |
| shoot-app-b  | ✓ pass | 6/6 nodes `Ready` |
| shoot-data-a | ✗ attention | 5/6 nodes `Ready`, 1 `NotReady` for 14m |
| shoot-vpn-a  | ✓ pass | 5/5 nodes `Ready` |

## 8. Follow-up

- [x] Deep-dive `shoot-data-a`:
  ```bash
  gardener_sni_login live shoot-data-a
  kubectl describe node worker-data-a-z1-abc12
  ```
  → `KubeletNotReady`, `PLEG is not healthy`. Recommended: raise ticket for cluster
  owner and, if pressure builds, `cordon` + `drain` (destructive — requires explicit
  confirmation per the safety rules).
- [ ] No canary/live drift to reconcile.
- [x] Ticket handed to `#team-data-platform`.
