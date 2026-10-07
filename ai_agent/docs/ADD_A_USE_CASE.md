# Add a Use Case

A new chat config is a new YAML file under `configs/`. It changes the prompt,
model, and tools. The control flow stays the `create_agent` loop in `graph.py`.

## 1. Copy the template

```bash
cp configs/generic_react.yaml configs/my_case.yaml
```

## 2. Set identity + behaviour

```yaml
name: my-case-assistant
system_prompt: |
  You are <role>. <what you do>. Always cite tool results. If tools return
  nothing relevant, say so instead of guessing.
```

## 3. Attach tools (any MCP server)

`base.yaml` supplies LLM + memory defaults, so you only add what differs.

### Local (stdio) — reuse an in-repo server

```yaml
mcp_servers:
  sci:
    transport: stdio
    command: uv
    args: ["run", "python", "-m", "sci_mcp.server"]
    cwd: ../mcp-servers/sci-ai-mcp     # relative paths resolve against configs/
```

### Remote (HTTP)

```yaml
mcp_servers:
  remote:
    transport: streamable_http
    url: https://my-mcp-host/mcp
    headers:
      Authorization: Bearer ${MY_MCP_TOKEN}   # ${ENV} is expanded at load time
```

You can list **multiple** servers; all their tools are merged and offered to the agent.

## 4. (Optional) Override the LLM or memory

```yaml
llm:
  provider: openai        # anthropic | openai | ollama
  model: gpt-4o
memory:
  backend: sqlite         # persist + resume conversations
  path: ./my_case.sqlite
```

Env vars override YAML at runtime, e.g. `AGENT_LLM__PROVIDER=ollama`.

## 5. Inspect and run

```bash
uv run agent-chat show my_case                       # print the resolved config
uv run agent-chat run --config my_case --query "..." # run it
```

That's it — another chat config on the same loop. See `configs/sap_ops.yaml` for a
worked example that reuses this repo's `gardener-ai-mcp` server.
