---
name: gardener-research
description: Search-first research protocol for SAP Gardener questions. Use when answering any Gardener documentation, architecture, or operational question — always searches MCP documentation before answering from prior knowledge.
---

# gardener-doc-research

When answering any Gardener question:

1. ALWAYS search documentation first.
2. NEVER answer from prior knowledge if MCP is available.
3. Cite all sources used.
4. Include document title.
5. Please mention complete Sources URLs.
6. Distinguish facts from assumptions.

## Search in Parallel

The `gardener-ai-mcp` server exposes independent collections — `search_docs`,
`search_issues`, `search_prs`, `search_proposals`, and `search_code`. These
searches do not depend on each other, so **run them concurrently**, never one
after another.

Two ways, pick by breadth:

- **Simple / focused question** — issue the relevant `search_*` MCP calls
  **in a single message** (parallel tool calls). Fastest; keep it in the main
  thread.
- **Broad / multi-faceted research** — spawn **parallel subagents** (`Agent`
  tool, `subagent_type: "general-purpose"`), one per angle (e.g. one for docs +
  proposals, one for issues + PRs, one for source code). Each subagent runs its
  searches and returns a **short summary with source titles + full URLs** — not
  raw hit dumps. This keeps the large search output out of the main context.

```text
# Broad research → one message, multiple Agent calls run in parallel:
Agent(subagent_type="general-purpose", description="Docs + proposals angle",
  prompt="Use gardener-ai-mcp search_docs and search_proposals for '<topic>'.
          Return a short factual summary with each source's title + full URL.
          Do not answer from prior knowledge; cite only what the search returns.")
Agent(subagent_type="general-purpose", description="Issues + PRs angle",
  prompt="Use gardener-ai-mcp search_issues and search_prs for '<topic>' ...")
```

Then **merge** the subagent summaries into the single output below, de-duplicating
sources. The search-first, cite-everything rules above still apply to every
subagent and to the final answer.

Output:

## Summary

## Evidence

## Sources
