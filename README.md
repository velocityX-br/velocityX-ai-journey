# velocityX-ai-journey

A working collection of **Claude Code skills**, **MCP servers**, and supporting
docs built to accelerate day-to-day engineering and operations at SAP — Gardener
Kubernetes operations, SAP-branded presentations, Confluence/wiki access, Slack
analysis, and more.

Everything here is designed to be dropped into a Claude Code session: skills load
automatically when you open the repo, and the MCP servers register with a single
`claude mcp add` command.

---

## Repository Layout

| Path | What's inside |
|------|---------------|
| `skills/` | Claude Code skills — reusable, auto-loaded task protocols |
| `mcp-servers/` | Model Context Protocol servers that connect Claude to internal SAP systems |
| `docs/` | Reference material and `superpowers` documentation |
| `agentic_chat/` | Planning/scratch workspace (`task_plan.md`, `findings.md`, `progress.md`) |

---

## Skills

Skills live under `skills/` and are picked up automatically when the repo is open
in Claude Code. Each has a `SKILL.md` with its trigger and instructions.

| Skill | Trigger | Description |
|-------|---------|-------------|
| `gardener-ops` | "Run kubectl on Gardener / check cluster health / operate on shoots" | Safe multi-cluster Kubernetes operations on SAP Gardener landscapes with read / mutate / delete guardrails |
| `gardener-research` | "Answer a Gardener docs / architecture / ops question" | Search-first research protocol — always queries the Gardener MCP docs before answering, and cites sources |
| `containerization-check` | "Check containerization / audit k8s config / review helm compliance" | Audits Helm charts, Kubernetes manifests, and Dockerfiles against SAP GCS DevOps containerization standards |
| `sap-slides` | "Create a SAP presentation / internal deck / BTP demo" | Generates SAP-branded HTML presentations by wrapping `frontend-slides` with the SAP Horizon palette and 72 typeface |
| `slack-hot-topics` | "Top topics in #channel / 热门话题 / summarize active discussions" | Ranks the most-engaged Slack discussions over a time window by real reply + reaction data |

---

## MCP Servers

Four MCP servers connect Claude Code directly to internal SAP systems. See
[`mcp-servers/README.md`](mcp-servers/README.md) for full tool lists, example
prompts, and per-server setup.

| Server | Connects Claude to |
|--------|--------------------|
| `gardener-ai-mcp` | Gardener knowledge base — docs, GitHub issues/PRs, GEPs, and Go source, plus RAG-backed root-cause analysis |
| `sap-wiki-mcp` | SAP Confluence spaces — read, search, create, and update wiki pages |
| `plato-mcp` | CIA Plato AI Agent — SAP's internal assistant for systems, architecture, and incident analysis |
| `mcp-proxy` | Local proxy utilities for MCP transports |

### Quick start

```bash
cd mcp-servers

# Build all servers, check .env files, and print the `claude mcp add` commands
./install.sh

# Or scope to a subset
./install.sh --only gardener,sap-wiki
```

`install.sh` builds each server and prints the exact registration command — it
never runs `claude mcp add` or writes secrets for you.

---

## Scripts

### `skills/gardener-ops/scripts/gardener-run.sh`

Fan out a `kubectl` command to multiple Gardener shoot clusters. Bundled with the
`gardener-ops` skill.

```bash
# Add to PATH (put this in your ~/.zshrc or ~/.bashrc; adjust to your clone path)
export PATH="$PATH:$(git rev-parse --show-toplevel)/skills/gardener-ops/scripts"

# Run on all shoots in a landscape
gardener-run.sh --garden live --all "kubectl get nodes"

# Run on specific shoots
gardener-run.sh --garden live --shoots shoot-a,shoot-b "kubectl get pods -A"

# Preview without executing
gardener-run.sh --garden live --all --dry-run "kubectl rollout restart deploy/myapp -n default"

# Full usage
gardener-run.sh --help
```

**Prerequisites:** `gardenctl`, `kubectl`, and a `KUBECONFIG` set via
`eval "$(gardenctl kubectl-env bash)"`.
