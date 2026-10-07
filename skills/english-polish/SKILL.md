---
name: english-polish
description: Review English writing for grammar correctness, native/idiomatic phrasing,
  and cultural appropriateness, with a DevOps/cloud-native domain focus (Kubernetes,
  Gardener, CNCF projects, authentication, hyperscalers, Linux). Use when the user
  asks to "check my English", "polish this text", "does this sound native",
  "review grammar", "is this phrasing right", or wants writing checked for a specific
  technical domain. Works on pasted text or a file path.
---

# English Polish

## Overview

Reviews English text for three things, in order of priority:

1. **Grammar** — correctness (tense, agreement, articles, prepositions, punctuation).
2. **Native / idiomatic phrasing** — whether a native speaker would actually say it this way, not just whether it is technically correct. Flag translationese, awkward literal phrasings, and unnatural word order.
3. **Cultural & domain appropriateness** — whether the tone fits the context and whether technical terminology matches how the DevOps / cloud-native community uses it.

**Primary domain:** DevOps and cloud-native — Kubernetes, Gardener, CNCF projects, authentication/authZ, hyperscalers (AWS/Azure/GCP), Linux, containers, CI/CD, observability. Prefer the community-standard term over a generic or literal one.

This skill is **advisory and consent-driven**. It never edits without explicit confirmation. Enrichment is always **optional**, never automatic.

---

## Execution Flow

### Step 1 — Get the Input

Determine what to review:

- **Pasted text** → use it directly.
- **File path** → Read the file. Review only the prose/comment content, not code logic. For code files, focus on comments, docstrings, and string literals unless told otherwise.

If it is unclear which text or file to review, ask before proceeding.

Optionally confirm the intended **audience/context** if it matters (e.g. PR description, internal doc, customer-facing README, commit message, Slack message). Tone expectations differ. If the user did not say, infer from the source but note the assumption.

### Step 2 — Review (do NOT modify anything yet)

Analyze the text against the three priorities above. For each issue found, classify it as one of:

- `grammar` — objectively incorrect.
- `native` — correct but not how a native speaker would phrase it.
- `domain` — terminology that does not match DevOps/cloud-native convention.
- `tone` — register/formality mismatch for the intended audience.

Only flag real issues. Do not invent problems. If the text is already good, say so plainly.

### Step 3 — Present Findings as a Per-Issue Diff

Show each issue in this format. Group nothing — one block per issue, in reading order.

```
[1] grammar
  - The Gardener control plane are managed by...
  + The Gardener control plane is managed by...
  Why: "control plane" is singular → verb must agree.

[2] native
  - We must do the deletion of the old shoot cluster.
  + We should delete the old shoot cluster.
  Why: "do the deletion of" is unnatural; native speakers say "delete".

[3] domain
  - the k8s master node
  + the Kubernetes control plane node
  Why: "master" is deprecated in the community; "control plane" is the current CNCF term.

[4] tone
  - Hey, the pods are totally broken lol
  + The pods are failing to start — see details below.
  Why: too casual for a PR description / incident note.
```

Then a one-line summary, e.g. `Found 4 issues: 1 grammar, 1 native, 1 domain, 1 tone.`

If there are **no** issues, say the text reads well and skip to noting the optional enrich offer (Step 5). Do not fabricate changes.

### Step 4 — Ask to Apply (mandatory confirmation)

**Always ask once before modifying anything.** Use AskUserQuestion or a plain question. Offer clear choices:

- Apply **all** suggested changes
- Apply **only selected** ones (user names the numbers, e.g. "1 and 3")
- Apply **none** (leave as-is)

Never edit a file or produce the "final" text before the user confirms. If reviewing a file, only use the Edit/Write tool **after** confirmation, and edit only the confirmed items.

### Step 5 — Offer Enrichment (optional, opt-in only)

After (or alongside) the confirmation in Step 4, offer enrichment as a separate, explicitly optional step:

> *Optional: I can also enrich the text to sound more native/idiomatic and tighten the domain terminology — same meaning and roughly the same length, just more fluent. Want me to show an enriched version? (yes / no)*

**Enrich scope = "Native + terms":**
- Make phrasing more natural and idiomatic.
- Use precise, community-standard DevOps/cloud-native terminology.
- **Preserve** the original meaning and keep length similar. Do **not** add new content, examples, or padding.

If the user says yes, present the enriched version as a proposal (per-issue diff or full block), then **ask again** before writing it to a file. Enrichment is never applied silently.

---

## Rules

- **Never modify without explicit confirmation.** Always ask once first (Step 4).
- **Enrichment is optional and opt-in.** Never enrich automatically; treat it as a feature the user chooses.
- **Minor changes only by default.** Prefer the smallest change that fixes the issue. Do not rewrite whole paragraphs unless the user asks.
- **Preserve the author's voice.** Fix errors and unnaturalness; don't impose a different personality or inflate formality.
- **Explain the "why"** for each suggestion in one short line — this is a learning aid, not just a corrector.
- **Respect meaning.** If a fix would change the technical meaning, flag it and ask instead of guessing.
- When editing files, edit **only** the confirmed items — do not touch surrounding text.

## Domain Terminology Cheatsheet (prefer the right-hand form)

- master node → **control plane node** (Kubernetes/CNCF standard)
- slave → **replica / worker / follower**
- k8s cluster's brain → **control plane**
- "the docker" → **Docker** (product) / **a container** (the thing running)
- "make a deployment of" → **deploy**
- "do a rollback" → **roll back** (verb) / **rollback** (noun)
- "auth" → **authentication** vs **authorization** — keep them distinct (authN / authZ)
- "the cloud provider Amazon/Google/Microsoft" → **hyperscaler** (AWS / GCP / Azure)
- shoot/seed/garden → keep as-is; these are correct **Gardener** terms, do not "fix" them
- "reconcile loop" → **reconciliation loop** (controller-runtime convention)
- pods/nodes/namespaces → lowercase as common nouns; capitalize only Kubernetes **kinds** in API context (e.g. `Deployment`, `Pod` when referring to the resource type)

## What This Skill Does NOT Do

- Does not edit or overwrite text before the user confirms.
- Does not force enrichment — it is always an offered option.
- Does not rewrite content, add sections, or change technical meaning on its own.
- Does not review code logic — only English prose, comments, docstrings, and strings.
