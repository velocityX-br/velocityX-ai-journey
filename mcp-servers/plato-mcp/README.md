# Plato MCP Server

An MCP (Model Context Protocol) server that lets Claude Code query the [CIA Plato AI Agent](https://portal.cia.net.sap) directly. Instead of launching `plato-tui` separately, Claude Code calls the `plato_query` tool to get answers about SAP systems, code, architecture, and more. Also provides automated token refresh via OAuth SSO.

```
Claude Code → MCP (stdio) → server.py → WebSocket → Plato Backend
                           → server.py → subprocess → refresh_token.py → OAuth → Browser SSO
```

## Prerequisites

- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- `PLATO_CA_BUNDLE` pointing to an SAP CA bundle (for TLS to internal services)

## Installation

### 1. Clone the repository

```bash
git clone <repo-url> ~/plato-mcp
cd ~/plato-mcp
```

### 2. Install dependencies

On Fedora:

```bash
sudo dnf install python3-mcp+cli python3-websockets
```

Or via pip:

```bash
pip3 install -r requirements.txt
```

### 3. Set up CA certificates

The server needs SAP internal CA certificates for TLS. The CA bundle is shipped with the [plato-tui](https://github.tools.sap/cia-web-services/cia-plato-agent) repository as [`ca_bundle.pem`](https://github.tools.sap/cia-web-services/cia-plato-agent/blob/main/ca_bundle.pem) in the repo root.

Export `PLATO_CA_BUNDLE` in your shell profile (e.g. `~/.bashrc`):

```bash
export PLATO_CA_BUNDLE=/path/to/cia-plato-agent/ca_bundle.pem
```

### 4. Register with Claude Code

```bash
claude mcp add --scope user plato -- python3 /absolute/path/to/plato-mcp/server.py
```

This registers the MCP server globally (user-scoped) so it's available in all projects. The config is stored in `~/.claude.json`.

### 5. Verify

Restart Claude Code, then check the server is connected:

```bash
claude mcp list
```

You should see:

```
plato: python3 /path/to/plato-mcp/server.py - ✓ Connected
```

## Authentication

Authentication happens automatically — the first `plato_query` call will open your browser for SAP IAS single sign-on if no valid token exists. The token is saved to `~/.local/cia_token/.cia_token` with a **12-hour expiry** and auto-refreshes when it expires.

You can also set the `CIA_TOKEN` environment variable or manually place a token from https://portal.cia.net.sap/preferences/settings ("Copy JWT") into the token file. To force a refresh at any time, run `python3 refresh_token.py` or call the `refresh_cia_token` MCP tool.

## Usage

Once registered, Claude Code can use the `plato_query` and `refresh_cia_token` tools in conversation. Examples:

- *"Ask Plato to investigate the failure in UUID 27E7E8420BEF11F1BE56A38E4828B749"*
- *"Use the plato tool to check what services are running on host xyz"*
- *"Query plato about the architecture of the inventory system"*
- *"Refresh the CIA token"* (opens browser for SSO)

### `plato_query` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `prompt` | string | *(required)* | The question to send to Plato |
| `model` | enum | `claude-opus` | `claude-opus`, `claude-sonnet`, or `claude-4.5-haiku` |
| `agent` | enum | `opencode` | `opencode` (OpenCode SDK adapter) or `claude` (full Claude Agent SDK) |
| `backend_url` | string | main staging | Override the Plato backend URL |

### `refresh_cia_token`

No parameters. Forces a token refresh by opening a browser for SAP IAS SSO authentication via OAuth 2.0 (PKCE, dynamic client registration). Writes the new JWT to `~/.local/cia_token/.cia_token`. Normally not needed — `plato_query` auto-refreshes when the token is missing or expired.

## Development

```bash
# Run directly
python3 server.py

# Refresh token standalone
python3 refresh_token.py
```

## Uninstall

```bash
claude mcp remove plato
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `jwt expired` | Should auto-refresh via browser SSO. If that fails, run `python3 refresh_token.py` manually or call the `refresh_cia_token` MCP tool |
| `WebSocket connection error` | Check that `PLATO_CA_BUNDLE` is set and points to a valid CA bundle |
| Server not showing in `claude mcp list` | Re-run the `claude mcp add` command and restart Claude Code |
| Timeout after 2 minutes | The Plato backend may be slow or down — try again or check https://portal.cia.net.sap |
