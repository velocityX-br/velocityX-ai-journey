---
name: containerization-jira
description: Create SIDEVOPS Containerization JIRA tickets quickly with pre-filled
  metadata (Type, Product Area, Component, Epic Link) and a standard 3-section
  description template. Use when the user wants to "create a containerization ticket",
  "raise a SIDEVOPS containerization Activity", "open a containerization JIRA", or
  file a ticket modeled on SIDEVOPS-16819. The user supplies Summary plus the
  Description, Acceptance Criteria, and Additional Information content; everything
  else is defaulted and confirmed.
---

# Containerization JIRA Ticket Creator

## Overview

Streamlines creation of **CIS Stack Containerization** tickets in the **SIDEVOPS**
project (SCI Platform Delivery Systems). Modeled on the reference ticket
**SIDEVOPS-16819** — `[dns Landscape] Component design phase / KickOff`.

The skill pre-fills all fixed metadata, renders a standard 3-section description
template, and lets the user fill in only the variable parts. All defaults are
**shown and confirmed** before the ticket is created.

This skill **delegates all Jira API calls to the `sap-jira` skill/MCP tools**. It
never calls the Jira REST API directly — it only prepares the field payload and
invokes `sap-jira` create/update tools.

---

## Fixed Metadata (from SIDEVOPS-16819)

| Field | Field ID | Default value | Notes |
|-------|----------|---------------|-------|
| Project | `project` | `SIDEVOPS` | Fixed |
| Issue Type | `issuetype.id` | `12` (Activity) | Confirm; changeable |
| Component/s | `components` | `Project: CIS Stack Containerization Services` (id `322097`) | **Required**; confirm |
| Product Area | `customfield_10242` | `Containerization` (id `118371`) | **Required**, multiselect; confirm |
| Requesting LOB | `customfield_18741` | *(none — ask every time)* | **Required**, multiselect |
| Epic Link | `customfield_15140` | `SIDEVOPS-15633` | Optional; confirm/override |
| Priority | `priority` | `Medium` | Confirm; changeable |
| Description | `description` | 3-section template (below) | User fills sections |

### Allowed values reference

**Product Area** (`customfield_10242`, multiselect) — common options:
`Containerization` (118371), `DNS` (118373), `Linux` (118375), `Monitoring` (118379),
`Documentation` (118372), `Operations` (118381), `Security` (118388), `Other` (118382),
`GitOps Automation` (150062), `Ansible` (118367), `Terraform (OpenTofu)` (150070),
`Persephone` (172172).

**Requesting LOB** (`customfield_18741`, multiselect) — all options:
`S&I DevOps` (135666), `BTP` (135662), `CLMAM` (135663), `CIEA` (135664),
`SAP Security` (135665), `SCI` (172173).

**Component/s** — most relevant: `Project: CIS Stack Containerization Services`
(322097). Others include `Project: Edge-Repo Containerization` (254218),
`Project: Gardener on SCI (Persephone)` (326001), `S&ID T-Team` (185101). If the
user names a component not in this list, fetch fresh allowed values via
`sap-jira` `get_field_metadata_by_name` (fieldNames=`Component/s`, issueTypeId=`12`,
projectKey=`SIDEVOPS`).

> Metadata can drift. If any create call fails with an unknown-value or
> required-field error, re-fetch allowed values with `sap-jira`
> `get_field_metadata_by_name` / `get_required_fields_structure` and retry.

---

## Description Template

The description uses Jira wiki markup with three `h3.` sections. Fill each section
with the user's input verbatim (preserve their wiki markup / bullet lists). If the
user leaves a section empty, keep the heading with a `TODO` placeholder so the
structure stays intact.

```
h3. Description

{{USER_DESCRIPTION}}

h3. Acceptance Criteria

{{USER_ACCEPTANCE_CRITERIA}}

h3. Additional Information

{{USER_ADDITIONAL_INFORMATION}}
```

Formatting rules:
- Acceptance Criteria are typically a bullet list. If the user gives plain lines,
  convert each to a Jira bullet (`* item`), nested with `** subitem`.
- Preserve Jira link syntax the user provides: `[label|https://url]`.
- Do not invent content. Only use what the user supplies plus their chosen headings.

---

## Workflow

### Step 1 — Gather variable input

Ask the user for (batch these; accept whatever they've already provided in the prompt):

1. **Summary** (title). Suggest the `[<service> <scope>] <phase>` style, e.g.
   `[dns Landscape] Component design phase / KickOff`.
2. **Description** content (goes under `h3. Description`).
3. **Acceptance Criteria** content (goes under `h3. Acceptance Criteria`).
4. **Additional Information** content (goes under `h3. Additional Information`).
5. **Requesting LOB** — required, no default. Present the 6 options and let them pick
   one or more.

If the user already supplied any of these in their request, do not re-ask — reuse it.

### Step 2 — Confirm defaults (all overridable)

Present a compact confirmation block showing the defaulted fields and ask the user to
confirm or override each:

```
Project:        SIDEVOPS
Issue Type:     Activity
Component/s:    Project: CIS Stack Containerization Services
Product Area:   Containerization
Epic Link:      SIDEVOPS-15633
Priority:       Medium
Requesting LOB: <user selection>
```

Use `AskUserQuestion` when a quick confirm/override is useful; otherwise a short
inline confirmation is fine. Apply any overrides the user states (e.g. different
Component, additional Product Area value, no Epic Link, higher Priority).

### Step 3 — Assemble the payload

Build the field payload for the `sap-jira` create tool. Map values to IDs:

- `components` → `[{ "id": "322097" }]` (or the confirmed component's id/name)
- `customfield_10242` (Product Area) → `[{ "id": "118371" }]` (add more ids if the
  user selected additional product areas)
- `customfield_18741` (Requesting LOB) → `[{ "id": "<selected id>" }]`
- `customfield_15140` (Epic Link) → `"SIDEVOPS-15633"` (or override; omit if none)
- `priority` → `{ "name": "Medium" }`
- `issuetype` → `{ "id": "12" }`
- `summary` → user summary
- `description` → rendered 3-section template

Prefer the `sap-jira` `create_issue` MCP tool with named fields. If a field is not
exposed by that tool, fall back to the `sap-jira` skill's raw
`POST /rest/api/2/issue` flow (which handles auth, cookies, and CSRF headers).
Never build a direct curl/API call inside this skill — always go through `sap-jira`.

Example payload (values only; the create tool wraps them in `fields`):

```json
{
  "projectKey": "SIDEVOPS",
  "issuetype": { "id": "12" },
  "summary": "[<service>] <phase>",
  "description": "h3. Description\n\n...\n\nh3. Acceptance Criteria\n\n* ...\n\nh3. Additional Information\n\n* ...",
  "components": [{ "id": "322097" }],
  "customfield_10242": [{ "id": "118371" }],
  "customfield_18741": [{ "id": "172173" }],
  "customfield_15140": "SIDEVOPS-15633",
  "priority": { "name": "Medium" }
}
```

### Step 4 — Create and report

1. Invoke the `sap-jira` create tool with the payload.
2. On success, report the new key and browse URL:
   `https://jira.tools.sap/browse/<KEY>`.
3. On failure:
   - **Required field / invalid value** → re-fetch metadata via `sap-jira`
     `get_required_fields_structure` (projectKey=`SIDEVOPS`, type=`Activity`) or
     `get_field_metadata_by_name`, correct the payload, retry once.
   - **Auth error (401/403/redirect)** → the `sap-jira` skill handles
     re-authentication via `sap-authentication`; retry after it completes.

### Step 5 — Optional follow-ups

After creation, offer (only if the user asks):
- Assign to a sprint (`sap-jira` sprint tools).
- Set assignee/reporter.
- Link to a related ticket.

---

## Guardrails

1. **Delegate to `sap-jira`.** All Jira reads/writes go through the `sap-jira` skill
   or its MCP tools. This skill only prepares the payload and confirms fields.
2. **Never fabricate content.** Description sections contain only what the user
   provided. Empty sections keep the heading with a `TODO` placeholder.
3. **Always confirm defaults before creating.** The user can override any field.
4. **Requesting LOB has no default** — always ask.
5. **Preserve Jira wiki markup** (`h3.`, `*` bullets, `[label|url]` links) exactly.
6. **Re-fetch metadata on value errors** rather than guessing IDs.
7. **One ticket per run** unless the user explicitly asks to batch-create.

---

## Quick reference — the one-shot happy path

If the user provides Summary + the three sections + a Requesting LOB up front and
says "just create it with the defaults":

1. Render description template.
2. Assemble payload with the fixed defaults above.
3. Call `sap-jira` create.
4. Reply with `<KEY>` and browse URL.
