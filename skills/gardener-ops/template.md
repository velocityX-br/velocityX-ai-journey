# Gardener Operation Plan

Fill in every section BEFORE running any command. Never skip the confirmation section
for mutating or destructive operations.

## 1. Intent

_One sentence: what are we trying to accomplish?_

- Goal:
- Requested by:
- Ticket / thread link (optional):

## 2. Scope

| Field | Value |
|---|---|
| Landscape | `sap-landscape-live` / `sap-landscape-canary` / `sap-landscape-ac-live` |
| Project | `sni` (default) or other |
| Shoots | (list explicitly, or `--all` with the count from `kubectl get shoots -n garden-<project>`) |

## 3. Operation Classification

Tick exactly one:

- [ ] **Read-only** (`get`, `describe`, `logs`, `top`, `explain`) — proceed directly
- [ ] **Mutating** (`apply`, `patch`, `scale`, `rollout restart`) — require confirmation
- [ ] **Destructive** (`delete`, `drain`, `cordon`, `taint`) — require explicit `yes` per cluster list
- [ ] `kubectl delete shoot` — **STOP.** Refuse. Direct the user to the Gardener dashboard.

## 4. Command

```bash
# Single cluster
gardener_sni_login <landscape> <shoot>
kubectl <verb> <resource> ...

# OR multi-cluster
scripts/gardener-run.sh --garden <landscape> [--project <name>] \
  {--all | --shoots s1,s2} \
  "kubectl <verb> <resource> ..."
```

## 5. Dry Run (mutating & destructive only)

```bash
scripts/gardener-run.sh --garden <landscape> --shoots ... --dry-run "kubectl ..."
```

Paste dry-run output here:

```
<dry-run output>
```

## 6. Confirmation Prompt (mutating & destructive only)

Show the user this exact prompt and wait for an affirmative reply before executing:

> This will `<verb>` `<resource>` on the following cluster(s):
> - <shoot-a>
> - <shoot-b>
>
> Reply `yes` to proceed.

User response: _________________

## 7. Execution

Paste the summarized output. **Do not paste raw multi-line kubectl output without a
summary above it.**

- Total resources inspected:
- Non-healthy count:
- Observed patterns:

Per-cluster status:

| Shoot | Status | Notes |
|---|---|---|
| shoot-a | ✓ pass | |
| shoot-b | ✗ fail (exit 1) | see logs |

## 8. Follow-up

- [ ] Any cluster in a non-healthy state that needs a ticket?
- [ ] Any destructive operation that should be repeated on other landscapes (canary → live)?
- [ ] Anything to escalate to the cluster owner?
