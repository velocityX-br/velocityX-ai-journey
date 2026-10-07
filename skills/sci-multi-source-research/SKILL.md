---
name: sci-multi-source-research
description: Research a topic across three sources in parallel — SAP Wiki (Confluence), sci-ai-mcp (SAP Converged Infrastructure docs), and the public internet — then synthesize a single cited research report. Uses parallel subagents so each source is investigated independently and heavy search output stays out of the main context. Use when the user asks to "research", "investigate", "gather info on", "write a research report on", or "cross-check <topic> across SAP wiki / SCI docs / the web".
---

# SCI Multi-Source Research

Fan out research on a single topic to **three independent sources at once**, then
merge the findings into one structured, cited report. Each source is handled by its
own **subagent** so the searches run in parallel and their large/verbose output never
pollutes the main context — the main thread only sees each subagent's short summary.

## Sources

| # | Source | What it covers | How the subagent queries it |
|---|--------|----------------|-----------------------------|
| 1 | **SAP Wiki** (Confluence) | Internal SAP docs, ops handovers, team pages | `sap-wiki` skill (REST API) or `mcp__sap-wiki__*` tools |
| 2 | **sci-ai-mcp** | SAP Converged Infrastructure (SCI/CCloud) operation + customer docs, RAG corpus | `mcp__sci-ai-mcp__search_docs` / `search_operation_docs` / `search_customer_docs` |
| 3 | **Public internet** | Community/standard/upstream knowledge, CNCF, vendor docs | `WebSearch` + `WebFetch` |

## Workflow

### Step 1 — Frame the topic

1. Confirm the **topic/question** in one sentence. If the user's request is broad,
   ask one clarifying question (scope, timeframe, or angle) before fanning out.
2. Derive **2–4 search angles/keywords** the subagents should use (e.g. synonyms,
   the SCI-specific term vs the upstream/community term). Pass these to every subagent
   so each source is probed consistently.
3. Note whether any source should be **skipped** (e.g. topic is internal-only → skip
   internet; topic is upstream-only → skip SCI). Default: query all three.

### Step 2 — Fan out three subagents IN PARALLEL

Issue the three `Agent` tool calls **in a single message** so they run concurrently.
Use `subagent_type: "general-purpose"` for all three (they need MCP tools, WebSearch,
and/or the sap-wiki skill — the `Explore` type is file-search only and cannot do this).

Give each subagent: the topic, the shared search angles, its **assigned source only**,
and a strict instruction to return a **short structured summary with citations — not
raw dumps**. Each subagent must distinguish **facts (with a source)** from **gaps**.

```text
# All three Agent calls in ONE message → parallel execution.

Agent(subagent_type="general-purpose", description="SAP Wiki research: <topic>",
  prompt="""
  Research the topic: '<TOPIC>' using ONLY the SAP Wiki (Confluence).
  Use the sap-wiki skill (or mcp__sap-wiki__* tools) to search these angles:
  <ANGLE 1>, <ANGLE 2>, <ANGLE 3>.
  For each relevant page: capture the PAGE TITLE, the full PAGE URL, and 2-4
  factual bullet points. Prefer authoritative/ops-handover pages over drafts.
  Return a SHORT structured summary (NOT raw page dumps):
    - Key findings (bullet points, each with its source page title + URL)
    - Any contradictions or clearly outdated pages you noticed
    - Gaps: what the wiki does NOT cover on this topic
  Cite every fact with a page title + URL. Do not invent content.
  """)

Agent(subagent_type="general-purpose", description="SCI docs research: <topic>",
  prompt="""
  Research the topic: '<TOPIC>' using ONLY the sci-ai-mcp tools.
  Query mcp__sci-ai-mcp__search_docs (and search_operation_docs /
  search_customer_docs as relevant) for these angles:
  <ANGLE 1>, <ANGLE 2>, <ANGLE 3>.
  Return a SHORT structured summary (NOT raw hit dumps):
    - Key findings (bullet points), each tagged with the doc title/source
      returned by the tool and its relevance
    - Note whether coverage is operation-facing vs customer-facing
    - Gaps: topics with near-zero relevance / not present in the SCI corpus
  Cite every fact with the doc title/source from the tool result. Do not answer
  from prior knowledge — only report what the SCI RAG search returns.
  """)

Agent(subagent_type="general-purpose", description="Web research: <topic>",
  prompt="""
  Research the topic: '<TOPIC>' using ONLY WebSearch + WebFetch (public internet).
  Search these angles: <ANGLE 1>, <ANGLE 2>, <ANGLE 3>. Prefer official/vendor
  docs, CNCF/upstream project docs, and reputable sources over blogs.
  Return a SHORT structured summary (NOT raw page dumps):
    - Key findings (bullet points), each with the source Title + full URL
    - The current community-standard terminology / best practice
    - Gaps or conflicting guidance across sources
  Cite every fact with a Title + URL. Flag anything that looks outdated.
  """)
```

**Scaling / batching:** three subagents is the norm. If an angle is huge, a source
subagent may internally do several searches — that's fine; it still returns one
summary. Do not spawn more than ~5 subagents at once.

### Step 3 — Synthesize the report

Wait for all three summaries, then merge them in the main thread into ONE report.
**Cross-reference** the sources: where they agree, where they disagree, and what only
one source knows. Never silently pick a winner — surface conflicts explicitly.

Use this structure:

```markdown
# Research Report: <TOPIC>
_Date: <today> · Sources: SAP Wiki, sci-ai-mcp, Web_

## TL;DR
2–4 sentence answer to the original question.

## Key Findings
Synthesized bullet points. Tag each with its origin: `[Wiki]`, `[SCI]`, `[Web]`
(or multiple if corroborated, e.g. `[SCI][Web]`).

## Source Comparison
| Aspect | SAP Wiki | sci-ai-mcp | Web / Community |
|--------|----------|------------|-----------------|
| Coverage | … | … | … |
| Key point | … | … | … |
| Terminology | … | … | … |

## Agreements & Conflicts
- ✅ All sources agree: …
- ⚠️ Conflict: Wiki says X, Web says Y — <which is likely current + why>.
- ❓ Only <source> mentions: …

## Gaps & Open Questions
What none of the sources answered; suggested next step.

## Sources
### SAP Wiki
- <Page Title> — <full URL>
### sci-ai-mcp
- <Doc title/source> — <relevance/collection>
### Web
- <Title> — <full URL>
```

## Rules

- **Fan out in parallel** — the three `Agent` calls go in a single message. Never
  research the sources sequentially in the main thread.
- **Right subagent type** — always `general-purpose` (needs MCP tools / WebSearch /
  the sap-wiki skill). `Explore` is file-search only and will fail on these sources.
- **One source per subagent** — each subagent touches only its assigned source, so
  citations stay unambiguous and searches don't overlap.
- **Summaries, not dumps** — every subagent returns a short cited summary. Raw pages,
  full RAG hits, and full web pages stay inside the subagent. This is the whole point:
  it keeps the main context clean.
- **Cite everything** — every fact in the final report traces to a Wiki page URL, an
  SCI doc title/source, or a web Title+URL. Distinguish facts from assumptions.
- **Surface conflicts** — when sources disagree, show both and give a reasoned view
  of which is current; don't hide the disagreement.
- **Auth note** — subagents may not inherit an interactive SAP session. If the Wiki
  subagent reports an auth failure, tell the user to run the `sap-authentication`
  skill (or refresh cookies) and re-run; do not fabricate wiki content.

## What This Skill Does NOT Do

- Does not query the sources sequentially — it always fans out in parallel.
- Does not use `Explore` subagents for MCP/web/wiki work (wrong capability).
- Does not merge raw dumps into the report — only cited summaries.
- Does not invent content for a source that returned nothing — it reports the gap.
- Does not write the report to the knowledge base; hand off to `kb-capture` if the
  user wants it persisted.
