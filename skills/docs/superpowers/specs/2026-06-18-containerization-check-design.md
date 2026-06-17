# Containerization Check Skill — Design Spec

**Date:** 2026-06-18
**Status:** Approved

---

## Problem

The SAP GCS DevOps Containerization Guide defines ~20 MUST/SHOULD requirements for
container images and Kubernetes/Helm configurations. Manually reviewing a project
against this guide is time-consuming and error-prone.

## Goal

A Claude Code skill (`/containerization-check`) that audits any project's Dockerfiles
and Kubernetes/Helm manifests against the guide, producing a structured per-item report
with file locations and fix hints.

---

## Architecture

### Files

```
skills/containerization-check/
├── SKILL.md          # Skill trigger, execution flow, report format, check logic
└── checklist.yaml    # Structured rules: IDs, levels, titles, how_to_check, fix_hints
```

### Approach

**Chosen:** SKILL.md + separate checklist.yaml (rules/logic separation)

- `checklist.yaml` is the single source of truth for what to check
- `SKILL.md` defines how to check it and how to report results
- Updating wiki standards → update checklist.yaml only

### Execution Flow

1. Claude reads `checklist.yaml` for all rule definitions
2. Globs project for `Dockerfile*`, `*.yaml`, `*.yml`, `entrypoint*.sh`, `renovate.json`
3. For each check, applies `how_to_check` logic against discovered files
4. Outputs structured report with ✅ / ❌ / ⚠️ / ➖ per check item

---

## Check Categories

### Image Checks (7 checks)

| ID | Level | Title |
|----|-------|-------|
| img-001 | MUST | Base image is SUSE/SAP-based |
| img-002 | MUST | No `latest` tag |
| img-003 | MUST | Log output includes timestamp |
| img-004 | MUST | Container has one process / PID 1 is the service |
| img-005 | MUST | No elevated privileges |
| img-006 | SHOULD | Renovate metadata comments present |
| img-007 | SHOULD | Renovate config exists |

### Helm / Kubernetes Checks (14 checks)

| ID | Level | Title |
|----|-------|-------|
| k8s-001 | MUST | livenessProbe configured |
| k8s-002 | MUST | readinessProbe configured |
| k8s-003 | MUST | startupProbe configured |
| k8s-004 | MUST | PodDisruptionBudget configured |
| k8s-005 | MUST | Pod Anti-Affinity configured |
| k8s-006 | MUST | AZ Topology Spread Constraints configured |
| k8s-007 | MUST | Replicas >= 3 for LoB-facing services |
| k8s-008 | MUST | Resource requests and limits configured |
| k8s-009 | MUST | priorityClassName configured |
| k8s-010 | MUST | ApplicationSet has ignoreApplicationDifferences |
| k8s-011 | SHOULD | HorizontalPodAutoscaler configured |
| k8s-012 | SHOULD | FQDN used for service communication |
| k8s-013 | SHOULD | NetworkPolicy configured |
| k8s-014 | SHOULD | Resource limits ~2x requests (2:1 ratio) |

---

## Report Format

```
# Containerization Check Report
Project: <path>
Date: <date>

## Summary
MUST  : X/Y ✅ passed  |  N ❌ blocking issues
SHOULD: X/Y ✅ passed  |  N ⚠️ recommendations

## Image Checks
✅ [MUST]   img-001  Base image is SUSE/SAP-based  → Dockerfile:1
❌ [MUST]   img-003  Log output must include timestamp → entrypoint.sh:12
                     Fix: Pipe through `ts %Y-%m-%dT%H:%M:%.S%z`
...

## Blocking Issues
## Recommendations
```

---

## Source

Wiki: https://wiki.one.int.sap/wiki/spaces/GCSDevOpforCIS/pages/5054171249/Containerisation+Guide
