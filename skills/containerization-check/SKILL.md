---
name: containerization-check
description: Use when you want to audit a project's Helm charts, Kubernetes manifests,
  or Dockerfiles against SAP GCS DevOps containerization standards. Triggers when
  user asks to "check containerization", "audit k8s config", "review helm chart
  compliance", or "does this project meet containerization standards".
---

# Containerization Check

## Overview

Audits a project against the [SAP GCS DevOps Containerization Guide](https://wiki.one.int.sap/wiki/spaces/GCSDevOpforCIS/pages/5054171249/Containerisation+Guide).

Checks both **Dockerfile/image configuration** and **Helm chart/Kubernetes manifests**.
Reports every check as ✅ / ❌ / ⚠️ with file location and fix hint.

`MUST` = blocking requirement · `SHOULD` = recommendation

---

## Execution Flow

When the user runs `/containerization-check` (or asks to audit containerization compliance):

### Step 1 — Load Rules

Read `checklist.yaml` from the same directory as this skill. This file contains all check definitions with IDs, levels, descriptions, and fix hints.

### Step 2 — Discover Project Files

Use Glob and Grep to find relevant files in the current working directory:

```
Dockerfile*         → image checks
*.yaml / *.yml      → helm/k8s checks (filter by 'kind:' field)
entrypoint*.sh      → entrypoint checks
renovate.json       → renovate config check
.renovaterc*        → renovate config check
```

Key Kubernetes `kind` values to look for:
- `Deployment`, `StatefulSet`, `DaemonSet` → probe, affinity, replica, resource checks
- `PodDisruptionBudget` → PDB check
- `HorizontalPodAutoscaler` → HPA check
- `NetworkPolicy` → network policy check
- `ApplicationSet` → emergency sync check

### Step 3 — Run Checks

For each check in `checklist.yaml`, apply the `how_to_check` logic against the discovered files.

**Reporting symbols:**
- ✅ `[MUST]` or `✅ [SHOULD]` — check passed
- ❌ `[MUST]` — blocking failure
- ⚠️ `[SHOULD]` — recommendation not met
- ➖ `[SKIPPED]` — no relevant files found for this check (state reason)

When a check fails, always include:
1. The file path (and line number if applicable)
2. The `fix_hint` from checklist.yaml

### Step 4 — Output Report

Use the exact report format below.

---

## Report Format

```
# Containerization Check Report
Project: <current working directory>
Date: <today's date>

## Summary
MUST  : X/Y ✅ passed  |  N ❌ blocking issues
SHOULD: X/Y ✅ passed  |  N ⚠️ recommendations

---

## Image Checks
[one line per check, sorted by ID]

✅ [MUST]   img-001  Base image is SUSE/SAP-based           → Dockerfile:1
❌ [MUST]   img-003  Log output must include timestamp      → entrypoint.sh:12
                     Fix: Pipe output through `ts %Y-%m-%dT%H:%M:%.S%z`
⚠️ [SHOULD] img-006  Renovate metadata comments present     → Dockerfile:8
                     Fix: Add `# renovate: depName=...` above each ENV *_VERSION line
➖ [SKIPPED] img-007 No Dockerfile found

---

## Helm / Kubernetes Checks
[one line per check, sorted by ID]

✅ [MUST]   k8s-001  livenessProbe configured               → deployment.yaml:34
✅ [MUST]   k8s-002  readinessProbe configured              → deployment.yaml:45
❌ [MUST]   k8s-003  startupProbe missing                   → deployment.yaml
                     Fix: Add startupProbe block to each container spec
...

---

## Blocking Issues  (MUST failures — must fix before deployment)
1. [img-003] entrypoint.sh:12 — Log output missing timestamp
2. [k8s-003] deployment.yaml — startupProbe not configured

## Recommendations  (SHOULD — strongly advised)
1. [img-006] Dockerfile:8 — Add renovate metadata comments for package versions
2. [k8s-011] No HPA found — Consider HorizontalPodAutoscaler for dynamic scaling
```

---

## Check Logic Reference

### Image Checks

**img-001 Base image**
- Read all `FROM` lines in Dockerfile
- PASS if image starts with `registry.suse.com/`, `suse/`, `sap/`, or is a documented exception (vendor, OSS community, SAP team)
- WARN if non-SUSE image with no documented exception comment in Dockerfile

**img-002 No latest tag**
- Scan `FROM` lines and Helm values for `:latest` or images with no tag
- FAIL if any found

**img-003 Log timestamp**
- Check entrypoint scripts and CMD/ENTRYPOINT for `ts ` pipe or native timestamp configuration
- FAIL if logs likely have no timestamps (no `ts` pipe and app doesn't obviously emit timestamps)

**img-004 PID 1 / single process**
- Check entrypoint scripts: if a shell script is used, verify it ends with `exec /path/to/bin`
- FAIL if shell script exits without `exec` (service won't be PID 1)

**img-005 No elevated privileges**
- Search for `privileged: true`, `runAsUser: 0`, `hostPID: true`, `hostNetwork: true`, `USER root`
- FAIL if found without documented exception

**img-006 Renovate comments (SHOULD)**
- Look for `ENV *_VERSION=` lines in Dockerfile
- PASS if each has a preceding `# renovate:` metadata comment

**img-007 Renovate config exists (SHOULD)**
- Look for `renovate.json` or `.renovaterc*` in repo root
- PASS if found

### Helm / Kubernetes Checks

**k8s-001/002/003 Health probes**
- For each `Deployment`, `StatefulSet`, `DaemonSet` found, check containers section
- FAIL if `livenessProbe:`, `readinessProbe:`, or `startupProbe:` is missing from any container

**k8s-004 PDB**
- Search all YAML for `kind: PodDisruptionBudget`
- FAIL if none found (note: exceptions for non-LoB-visible components)

**k8s-005 Pod Anti-Affinity**
- Search Deployment/StatefulSet specs for `podAntiAffinity:` under `affinity:`
- FAIL if missing

**k8s-006 AZ Topology Spread**
- Search for `topologySpreadConstraints:` with `topology.kubernetes.io/zone` topologyKey
- FAIL if missing

**k8s-007 Replicas >= 3**
- Check `replicas:` in Deployment/StatefulSet, or `minReplicas:` in HPA
- FAIL if value < 3 for LoB-facing services (flag for review if unclear whether LoB-facing)

**k8s-008 Resource requests/limits**
- For each container, verify `resources.requests` and `resources.limits` are both present
- FAIL if either is missing

**k8s-009 priorityClassName**
- Search pod specs for `priorityClassName:`
- FAIL if missing

**k8s-010 ApplicationSet emergency sync**
- Search ApplicationSet YAML in `argocd/appsets/` for `ignoreApplicationDifferences`
- FAIL if ApplicationSets exist but lack this field

**k8s-011 HPA (SHOULD)**
- Search for `kind: HorizontalPodAutoscaler`
- WARN if none found (unless DaemonSet-only or static-replica service)

**k8s-012 FQDN (SHOULD)**
- Search env vars, ConfigMaps, and values for service references
- WARN if short names used instead of `<svc>.<ns>.svc.cluster.local.`

**k8s-013 NetworkPolicy (SHOULD)**
- Search for `kind: NetworkPolicy`
- WARN if none found

**k8s-014 Resource ratio 2:1 (SHOULD)**
- For containers with both requests and limits, compare CPU and memory ratios
- WARN if limits/requests ratio is < 1.5 or > 4 (likely misconfigured)

---

## Common Patterns That Mean PASS

- `exec /usr/sbin/myservice` in entrypoint → img-004 ✅
- `2>&1 | ts %Y-%m-%dT%H:%M:%.S%z` in entrypoint → img-003 ✅
- `preferredDuringSchedulingIgnoredDuringExecution` with hostname → k8s-005 ✅ (soft anti-affinity)
- `requiredDuringSchedulingIgnoredDuringExecution` with hostname → k8s-005 ✅ (hard anti-affinity, preferred)
- HPA with `minReplicas: 3` → k8s-007 ✅

## Common Patterns That Mean FAIL

- `FROM ubuntu:latest` → img-001 ❌ + img-002 ❌
- Entrypoint script without `exec` → img-004 ❌
- No `startupProbe:` in container spec → k8s-003 ❌
- `resources:` block present but only `limits:` (no `requests:`) → k8s-008 ❌
- ApplicationSet without `ignoreApplicationDifferences` → k8s-010 ❌
