# Corporate Skills Design
**Date:** 2026-06-17
**Status:** Approved

## Overview

Two skills and one bash wrapper script to codify recurring workflows in the SAP corporate environment:

1. **`sap-slides`** — generate SAP-branded presentations by wrapping the `frontend-slides` skill
2. **`gardener-ops`** — safe multi-cluster Kubernetes operations on SAP Gardener landscapes
3. **`gardener-run.sh`** — standalone bash script for fanning out `kubectl` commands across multiple shoot clusters

---

## Repository Structure

```
veloxityX-ai-journey/
├── skills/
│   ├── sap-slides/
│   │   └── skill.md
│   └── gardener-ops/
│       └── skill.md
└── scripts/
    └── gardener-run.sh
```

---

## Skill 1: `sap-slides`

**File:** `skills/sap-slides/skill.md`

### Trigger Conditions
Invoke when user asks to create a SAP presentation, internal deck, BTP demo, or any SAP-branded slides.

### Behavior

1. Invokes the `frontend-slides` skill from `/Users/I577081/Workdir/Github/frontend-slides`.
2. Before the `frontend-slides` Phase 2 style discovery, injects SAP brand context:

**SAP Brand Constraints:**
- **Primary palette:**
  - SAP Blue: `#0070F2`
  - SAP Dark Blue: `#003765`
  - White: `#FFFFFF`
  - Shell Gray: `#F5F6F7`
  - Accent/CTA: `#0064D9` (button blue)
- **Typography:** Prefer "72" font (SAP's typeface). CDN: `https://font.sap.com` — attempt to load; if unavailable (firewall), fall back to "Nunito" or "IBM Plex Sans" from Google Fonts (similar geometric sans-serif feel). Never use Arial, Roboto, Inter.
- **Design feel:** SAP-branded but layout-flexible. Professional corporate quality. NOT a Fiori UI component library clone.
- **Logo:** Ask the user if they have an SAP or team logo SVG/PNG to embed. If yes, request the file path. If no, skip — do not use a placeholder.

3. SAP brand context is a constraint on palette and type, NOT a straitjacket on layout. User's density/style choices still take priority.
4. After injecting brand context, hands off fully to `frontend-slides` Phase 2–6 (style previews, full deck generation, PDF export, deploy).

### What this skill does NOT do
- Does not re-implement any of `frontend-slides` logic.
- Does not hardcode a single SAP template — layout is flexible.
- Does not override the user's density or content choices.

---

## Script: `gardener-run.sh`

**File:** `scripts/gardener-run.sh`

### Purpose
Fan out a `kubectl` command to multiple Gardener shoot clusters with labelled output. Independently usable from the terminal (no Claude required).

### Interface

```bash
gardener-run.sh [OPTIONS] "kubectl command"

Options:
  --garden  live|canary|cn     Target landscape (required or prompted interactively)
  --all                         Run on all shoots in the project
  --shoots  s1,s2,s3           Run on named shoots (comma-separated)
  --project <name>              Garden project namespace (default: sni)
  --dry-run                     Print the commands without executing
```

### Behavior

1. **Pre-flight:** Validates `KUBECONFIG` is set. Exits with a clear error message if not.
2. **Garden selection:** If `--garden` not given, presents interactive menu (same landscape list as `gardener_sni_login`: live, canary, ac-live/china).
3. **Shoot resolution:**
   - `--all`: fetches all shoots from `kubectl get shoots -n garden-<project>`, targets all.
   - `--shoots s1,s2`: validates each name exists; exits with a clear error listing valid shoots if any are unknown.
   - Neither flag: falls to interactive multi-select (numbered list, `a` to select all).
4. **Delete guard:** If the command string contains any of: `delete`, `drain`, `cordon`, `taint` — the script prints a warning and prompts for explicit confirmation (`y/N`) before executing on each cluster. Default is `N` (abort).
5. **Execution (sequential):** For each target shoot:
   - `gardenctl target --garden $GARDEN --project $PROJECT --shoot $SHOOT`
   - `eval "$(gardenctl kubectl-env bash)"`
   - Executes the user command
   - Prefixes every output line with `[$SHOOT]`
6. **Summary:** After all clusters, prints a per-shoot pass/fail summary:
   ```
   === Results ===
   [shoot-a] ✓
   [shoot-b] ✗  (exit code 1)
   ```
7. **Exit code:** Non-zero if any cluster command failed.

### What this script does NOT do
- Does not delete shoot resources (`kubectl delete shoot` is blocked by the delete guard).
- Does not run in parallel (sequential only; parallel is a future enhancement).
- Does not store or cache credentials.

---

## Skill 2: `gardener-ops`

**File:** `skills/gardener-ops/skill.md`

### Trigger Conditions
Invoke when user asks to operate on Gardener shoot clusters, run kubectl across clusters, investigate cluster health, check nodes, scale workloads, collect logs, or perform any Kubernetes operation in the SAP Gardener environment.

### Safety Rules (Claude must follow these without exception)

| Operation type | Examples | Required behavior |
|---|---|---|
| Read-only | `get`, `describe`, `logs`, `top`, `explain` | Can suggest and execute directly |
| Mutating | `apply`, `patch`, `scale`, `rollout restart` | State what will change, ask for confirmation, then execute |
| Destructive | `delete`, `drain`, `cordon`, `taint` | Explicitly state cluster name(s) affected, ask for confirmation, never proceed without explicit `yes` |
| Shoot deletion | `kubectl delete shoot` | Never suggest or execute. Always refuse. |

### Workflow

1. **Single cluster:** Use `gardener_sni_login [garden] [shoot]` to target, then issue kubectl commands.
2. **Multiple clusters:** Use `gardener-run.sh` with `--all` or `--shoots` flag.
3. After each command output, summarize findings clearly (don't dump raw output without interpretation).
4. If an operation will affect multiple clusters, always list the cluster names before asking for confirmation.

### Environment Reference

| Item | Value |
|---|---|
| Landscapes | `sap-landscape-live`, `sap-landscape-canary`, `sap-landscape-ac-live` (China) |
| Default project | `sni` |
| Default namespace | `garden-sni` |
| Login tool | `gardener_sni_login [garden] [shoot]` |
| Multi-cluster tool | `scripts/gardener-run.sh` (in this repo) |
| Required tooling | `gardenctl`, `kubectl` |

### Common Patterns

```bash
# Health check: all clusters
gardener-run.sh --garden live --all "kubectl get nodes"

# Log collection: specific clusters
gardener-run.sh --garden live --shoots shoot-a,shoot-b \
  "kubectl logs -n kube-system -l app=vpn-shoot --tail=50"

# Deep dive: single cluster
gardener_sni_login live shoot-a
kubectl describe shoot shoot-a -n garden-sni

# Dry run before executing
gardener-run.sh --garden live --all --dry-run "kubectl rollout restart deploy/my-app -n default"
```

### What this skill does NOT do
- Does not bypass the safety rules — even if the user asks to skip confirmation.
- Does not operate on non-SNI projects without the user explicitly specifying `--project`.

---

## Out of Scope

- Automated deployment pipelines (CI/CD triggering)
- Gardener API (only kubectl/gardenctl surface)
- Windows shell compatibility
- Parallel execution in `gardener-run.sh` (future enhancement)
