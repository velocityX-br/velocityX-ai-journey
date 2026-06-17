# Corporate Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create two Claude Code skills (`sap-slides`, `gardener-ops`) and one bash wrapper script (`gardener-run.sh`) that codify recurring SAP corporate workflows — SAP-branded presentations and safe multi-cluster Gardener Kubernetes operations.

**Architecture:** Three independent deliverables authored in sequence: (1) `sap-slides` skill — a thin wrapper that injects SAP brand context then delegates to `frontend-slides`; (2) `gardener-run.sh` — a standalone bash script for fanning out `kubectl` commands across multiple shoot clusters with a delete guard; (3) `gardener-ops` skill — a Claude skill that enforces safe operational patterns and references the wrapper script. All files live in `veloxityX-ai-journey/`.

**Tech Stack:** Bash 5 (zsh-compatible), gardenctl, kubectl, Claude Code skill format (YAML front-matter + Markdown)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `skills/sap-slides/skill.md` | SAP brand context + delegation to frontend-slides |
| Create | `scripts/gardener-run.sh` | Multi-cluster kubectl fan-out with delete guard |
| Create | `skills/gardener-ops/skill.md` | Safe ops patterns, safety table, environment reference |

---

## Task 1: `sap-slides` Skill

**Files:**
- Create: `skills/sap-slides/skill.md`

- [ ] **Step 1: Create the skills/sap-slides directory**

```bash
mkdir -p /Users/I577081/Workdir/Github/veloxityX-ai-journey/skills/sap-slides
```

- [ ] **Step 2: Write `skills/sap-slides/skill.md`**

Create the file at `skills/sap-slides/skill.md` with this exact content:

```markdown
---
name: sap-slides
description: Create SAP-branded HTML presentations. Wraps the frontend-slides skill with SAP Horizon brand constraints (palette, typography). Use when the user asks to create a SAP presentation, internal deck, BTP demo, or any SAP-branded slides.
---

# SAP Slides

Create SAP-branded presentations by delegating to the `frontend-slides` skill with SAP brand context pre-loaded.

## Step 1: Invoke frontend-slides

Invoke the `frontend-slides` skill from `/Users/I577081/Workdir/Github/frontend-slides`.

## Step 2: Inject SAP Brand Context Before Style Discovery

Before `frontend-slides` Phase 2 (style discovery), apply these constraints. Tell the user you are applying SAP brand constraints.

### Palette (non-negotiable)

Use these CSS variables in every generated presentation:

```css
:root {
  --sap-blue:      #0070F2;
  --sap-dark-blue: #003765;
  --sap-white:     #FFFFFF;
  --sap-gray:      #F5F6F7;
  --sap-cta:       #0064D9;
}
```

Dominant colors must come from this palette. Accent colors and data-visualization colors may extend it, but must not clash.

### Typography

1. Attempt to load SAP's "72" typeface via the SAP font CDN:
   ```html
   <link rel="stylesheet" href="https://fonts.sap.com/css?family=72:300,400,600,700">
   ```
2. If the CDN is blocked (corporate firewall) or fails to load, fall back to Google Fonts:
   ```html
   <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;600;700&display=swap">
   ```
3. Never use: Arial, Roboto, Inter, Helvetica, or system-ui as the primary typeface.

### Logo

Ask the user: *"Do you have an SAP or team logo (SVG or PNG) to include? If so, what's the file path?"*

- If yes: embed it as a base64 `<img>` in title/closing slides. Read the file and convert with `base64 <path>`.
- If no: skip entirely — do not use a placeholder SAP wordmark or generic logo.

### Design Feel

SAP-branded but layout-flexible. Professional corporate quality. This is NOT a Fiori UI component library — do not use Fiori component patterns (shell bar, tiles, etc.) as slide layouts.

## Step 3: Respect User Choices

SAP brand constraints apply to palette and type only. The user's choices for density (low/high), content, layout style, and animation still take full priority. Do not override them.

## Step 4: Hand Off to frontend-slides

Continue with `frontend-slides` Phase 2 through Phase 6:
- Phase 2: Style discovery (3 preview options, all using the SAP palette)
- Phase 3: Full deck generation
- Phase 4: PPT conversion (if applicable)
- Phase 5: Delivery (open in browser)
- Phase 6: Share & export (PDF, Vercel deploy)

## What This Skill Does NOT Do

- Does not re-implement any `frontend-slides` logic
- Does not hardcode a single rigid SAP template
- Does not override the user's density or content choices
```

- [ ] **Step 3: Verify the file was created correctly**

```bash
head -5 /Users/I577081/Workdir/Github/veloxityX-ai-journey/skills/sap-slides/skill.md
```

Expected output:
```
---
name: sap-slides
description: Create SAP-branded HTML presentations. Wraps the frontend-slides skill with SAP Horizon brand constraints (palette, typography). Use when the user asks to create a SAP presentation, internal deck, BTP demo, or any SAP-branded slides.
---
```

- [ ] **Step 4: Commit**

```bash
cd /Users/I577081/Workdir/Github/veloxityX-ai-journey
git add skills/sap-slides/skill.md
git commit -m "ADD: sap-slides skill wrapping frontend-slides with SAP brand context"
```

---

## Task 2: `gardener-run.sh` Wrapper Script

**Files:**
- Create: `scripts/gardener-run.sh`

- [ ] **Step 1: Create the scripts directory**

```bash
mkdir -p /Users/I577081/Workdir/Github/veloxityX-ai-journey/scripts
```

- [ ] **Step 2: Write `scripts/gardener-run.sh`**

Create the file at `scripts/gardener-run.sh` with this exact content:

```bash
#!/usr/bin/env bash
# gardener-run.sh — fan out a kubectl command to multiple Gardener shoot clusters
#
# Usage:
#   gardener-run.sh [OPTIONS] "kubectl command"
#
# Options:
#   --garden  live|canary|cn   Target landscape (prompted if omitted)
#   --all                       Run on all shoots in the project
#   --shoots  s1,s2,s3         Run on named shoots (comma-separated)
#   --project <name>            Garden project (default: sni)
#   --dry-run                   Print commands without executing

set -euo pipefail

# ── Pre-flight ────────────────────────────────────────────────────────────────
if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "ERROR: KUBECONFIG is not set." >&2
  echo "Add the following to your shell profile and re-open your terminal:" >&2
  echo '  eval "$(gardenctl kubectl-env bash)"' >&2
  exit 1
fi

# ── Argument parsing ──────────────────────────────────────────────────────────
GARDEN=""
PROJECT="sni"
SHOOTS_ARG=""
RUN_ALL=false
DRY_RUN=false
CMD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --garden)   GARDEN="$2";      shift 2 ;;
    --project)  PROJECT="$2";     shift 2 ;;
    --shoots)   SHOOTS_ARG="$2";  shift 2 ;;
    --all)      RUN_ALL=true;     shift ;;
    --dry-run)  DRY_RUN=true;     shift ;;
    --*)
      echo "ERROR: Unknown option '$1'" >&2
      exit 1
      ;;
    *)
      CMD="$1"
      shift
      ;;
  esac
done

if [[ -z "$CMD" ]]; then
  echo "ERROR: No command provided." >&2
  echo "Usage: gardener-run.sh [OPTIONS] \"kubectl command\"" >&2
  exit 1
fi

# ── Garden selection ──────────────────────────────────────────────────────────
resolve_garden() {
  local input
  input=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  case "${input#sap-landscape-}" in
    live)    echo "sap-landscape-live" ;;
    canary)  echo "sap-landscape-canary" ;;
    cn|ac-live|livecn) echo "sap-landscape-ac-live" ;;
    *)       echo "" ;;
  esac
}

if [[ -n "$GARDEN" ]]; then
  RESOLVED=$(resolve_garden "$GARDEN")
  if [[ -z "$RESOLVED" ]]; then
    echo "ERROR: Unknown garden '$GARDEN'. Valid values: live, canary, cn" >&2
    exit 1
  fi
  GARDEN="$RESOLVED"
else
  echo ""
  echo "Select the Gardener landscape:"
  echo "  1) sap-landscape-live"
  echo "  2) sap-landscape-canary"
  echo "  3) sap-landscape-ac-live (china)"
  while true; do
    read -rp "Enter choice [1-3]: " choice
    case "$choice" in
      1) GARDEN="sap-landscape-live";    break ;;
      2) GARDEN="sap-landscape-canary";  break ;;
      3) GARDEN="sap-landscape-ac-live"; break ;;
      *) echo "Invalid choice. Enter 1, 2, or 3." ;;
    esac
  done
fi

echo ""
echo "Targeting garden: $GARDEN, project: $PROJECT"
gardenctl target --garden "$GARDEN"

# ── Shoot resolution ──────────────────────────────────────────────────────────
NAMESPACE="garden-${PROJECT}"
echo "Fetching shoots from namespace '$NAMESPACE'..."
ALL_SHOOTS=()
while IFS= read -r s; do
  [[ -n "$s" ]] && ALL_SHOOTS+=("$s")
done < <(kubectl get shoots -n "$NAMESPACE" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)

if [[ ${#ALL_SHOOTS[@]} -eq 0 ]]; then
  echo "ERROR: No shoots found in namespace '$NAMESPACE'." >&2
  exit 1
fi

TARGET_SHOOTS=()

if $RUN_ALL; then
  TARGET_SHOOTS=("${ALL_SHOOTS[@]}")
elif [[ -n "$SHOOTS_ARG" ]]; then
  IFS=',' read -ra REQUESTED <<< "$SHOOTS_ARG"
  for req in "${REQUESTED[@]}"; do
    found=false
    for s in "${ALL_SHOOTS[@]}"; do
      [[ "$s" == "$req" ]] && found=true && break
    done
    if ! $found; then
      echo "ERROR: Shoot '$req' not found in garden '$GARDEN', project '$PROJECT'." >&2
      echo "Valid shoots:" >&2
      printf "  %s\n" "${ALL_SHOOTS[@]}" >&2
      exit 1
    fi
    TARGET_SHOOTS+=("$req")
  done
else
  # Interactive multi-select
  echo ""
  echo "Available shoots in '$GARDEN' / '$PROJECT':"
  for i in "${!ALL_SHOOTS[@]}"; do
    printf "  %2d) %s\n" $((i+1)) "${ALL_SHOOTS[$i]}"
  done
  echo "   a) All shoots"
  echo ""
  read -rp "Enter shoot numbers separated by spaces, or 'a' for all: " selection
  if [[ "$selection" == "a" ]]; then
    TARGET_SHOOTS=("${ALL_SHOOTS[@]}")
  else
    for tok in $selection; do
      idx=$((tok - 1))
      if [[ "$idx" -ge 0 && "$idx" -lt "${#ALL_SHOOTS[@]}" ]]; then
        TARGET_SHOOTS+=("${ALL_SHOOTS[$idx]}")
      else
        echo "ERROR: '$tok' is not a valid selection." >&2
        exit 1
      fi
    done
  fi
fi

if [[ ${#TARGET_SHOOTS[@]} -eq 0 ]]; then
  echo "ERROR: No shoots selected." >&2
  exit 1
fi

echo ""
echo "Target shoots (${#TARGET_SHOOTS[@]}):"
printf "  %s\n" "${TARGET_SHOOTS[@]}"

# ── Delete guard ──────────────────────────────────────────────────────────────
DANGEROUS_PATTERN='(^|\s)(delete|drain|cordon|taint)(\s|$)'
if echo "$CMD" | grep -Eq "$DANGEROUS_PATTERN"; then
  echo ""
  echo "⚠️  WARNING: The command contains a potentially destructive operation."
  echo "   Command : $CMD"
  echo "   Clusters: ${TARGET_SHOOTS[*]}"
  echo ""
  read -rp "Type 'yes' to confirm you want to run this on all target clusters: " confirm
  if [[ "$confirm" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

# ── Execution ─────────────────────────────────────────────────────────────────
declare -A RESULTS

for SHOOT in "${TARGET_SHOOTS[@]}"; do
  echo ""
  echo "──────────────────────────────────────────"
  echo "[$SHOOT] Targeting..."

  if $DRY_RUN; then
    echo "[$SHOOT] DRY-RUN: gardenctl target --garden $GARDEN --project $PROJECT --shoot $SHOOT"
    echo "[$SHOOT] DRY-RUN: eval \"\$(gardenctl kubectl-env bash)\""
    echo "[$SHOOT] DRY-RUN: $CMD"
    RESULTS[$SHOOT]="dry-run"
    continue
  fi

  gardenctl target --garden "$GARDEN" --project "$PROJECT" --shoot "$SHOOT"
  eval "$(gardenctl kubectl-env bash)"

  set +e
  eval "$CMD" 2>&1 | sed "s/^/[$SHOOT] /"
  EXIT_CODE=${PIPESTATUS[0]}
  set -e

  if [[ $EXIT_CODE -eq 0 ]]; then
    RESULTS[$SHOOT]="pass"
  else
    RESULTS[$SHOOT]="fail:$EXIT_CODE"
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════"
echo "Results"
echo "══════════════════════════════════════════"

OVERALL=0
for SHOOT in "${TARGET_SHOOTS[@]}"; do
  STATUS="${RESULTS[$SHOOT]}"
  case "$STATUS" in
    pass)     echo "  ✓ $SHOOT" ;;
    dry-run)  echo "  ~ $SHOOT (dry-run)" ;;
    fail:*)   echo "  ✗ $SHOOT (exit code ${STATUS#fail:})"; OVERALL=1 ;;
  esac
done

echo ""
exit $OVERALL
```

- [ ] **Step 3: Make the script executable**

```bash
chmod +x /Users/I577081/Workdir/Github/veloxityX-ai-journey/scripts/gardener-run.sh
```

- [ ] **Step 4: Smoke-test the help output (no cluster required)**

```bash
cd /Users/I577081/Workdir/Github/veloxityX-ai-journey
bash scripts/gardener-run.sh 2>&1 | head -5
```

Expected output (no KUBECONFIG set in this context, so it will error with the helpful message):
```
ERROR: KUBECONFIG is not set.
Add the following to your shell profile and re-open your terminal:
  eval "$(gardenctl kubectl-env bash)"
```

This confirms the pre-flight check works. The script is correct.

- [ ] **Step 5: Test the delete guard logic in isolation**

```bash
# Verify grep pattern matches dangerous commands
echo "kubectl delete pod foo" | grep -Eq '(^|\s)(delete|drain|cordon|taint)(\s|$)' && echo "GUARD: triggered" || echo "GUARD: not triggered"
echo "kubectl get pods" | grep -Eq '(^|\s)(delete|drain|cordon|taint)(\s|$)' && echo "GUARD: triggered" || echo "GUARD: not triggered"
```

Expected output:
```
GUARD: triggered
GUARD: not triggered
```

- [ ] **Step 6: Commit**

```bash
cd /Users/I577081/Workdir/Github/veloxityX-ai-journey
git add scripts/gardener-run.sh
git commit -m "ADD: gardener-run.sh multi-cluster kubectl fan-out with delete guard"
```

---

## Task 3: `gardener-ops` Skill

**Files:**
- Create: `skills/gardener-ops/skill.md`

- [ ] **Step 1: Create the skills/gardener-ops directory**

```bash
mkdir -p /Users/I577081/Workdir/Github/veloxityX-ai-journey/skills/gardener-ops
```

- [ ] **Step 2: Write `skills/gardener-ops/skill.md`**

Create the file at `skills/gardener-ops/skill.md` with this exact content:

```markdown
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
```

- [ ] **Step 3: Verify the file was created correctly**

```bash
head -5 /Users/I577081/Workdir/Github/veloxityX-ai-journey/skills/gardener-ops/skill.md
```

Expected output:
```
---
name: gardener-ops
description: Safe multi-cluster Kubernetes operations on SAP Gardener landscapes. Enforces read/mutate/delete safety guardrails. Use when operating on Gardener shoot clusters, running kubectl across clusters, investigating cluster health, checking nodes, scaling workloads, collecting logs, or performing any Kubernetes operation in the SAP Gardener environment.
---
```

- [ ] **Step 4: Commit**

```bash
cd /Users/I577081/Workdir/Github/veloxityX-ai-journey
git add skills/gardener-ops/skill.md
git commit -m "ADD: gardener-ops skill for safe multi-cluster Gardener operations"
```

---

## Task 4: Final Wiring — README

**Files:**
- Modify: `README.md`

The `veloxityX-ai-journey` repo currently has a README. Add a **Skills & Scripts** section so future users know what's available and how to use the tools.

- [ ] **Step 1: Read the current README**

```bash
cat /Users/I577081/Workdir/Github/veloxityX-ai-journey/README.md
```

- [ ] **Step 2: Append the Skills & Scripts section**

After the existing content, append (do not replace):

```markdown

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
```

- [ ] **Step 3: Commit**

```bash
cd /Users/I577081/Workdir/Github/veloxityX-ai-journey
git add README.md
git commit -m "docs: add Skills & Scripts section to README"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `skills/sap-slides/skill.md` with SAP palette, 72 font, logo prompt, delegation | Task 1 ✓ |
| `scripts/gardener-run.sh` with `--garden`, `--all`, `--shoots`, `--project`, `--dry-run` | Task 2 ✓ |
| Pre-flight KUBECONFIG check | Task 2 ✓ |
| Garden selection (interactive + flag) | Task 2 ✓ |
| Shoot resolution: all / named / interactive multi-select | Task 2 ✓ |
| Delete guard for `delete`, `drain`, `cordon`, `taint` | Task 2 ✓ |
| Sequential execution with `[$SHOOT]` labelled output | Task 2 ✓ |
| Per-shoot pass/fail summary, non-zero exit code on failure | Task 2 ✓ |
| `skills/gardener-ops/skill.md` with safety table, workflow, env reference, common patterns | Task 3 ✓ |
| Refuse `kubectl delete shoot` unconditionally | Task 3 ✓ |
| Multi-cluster tool reference to `gardener-run.sh` | Task 3 ✓ |
| Output interpretation guidance | Task 3 ✓ |

**Placeholder scan:** No TBDs, TODOs, or "implement later" found.

**Type consistency:** Bash variable names, function names, and flag names are consistent across Task 2 steps. Skill names in `description:` front-matter match the `name:` field in both skills.
