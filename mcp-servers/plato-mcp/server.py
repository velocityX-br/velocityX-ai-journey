#!/usr/bin/env python3
"""MCP server for CIA Plato Agent — lets Claude Code query the Plato backend directly."""

import asyncio
import json
import os
import sys
import time
import uuid
from base64 import urlsafe_b64decode
from pathlib import Path
from typing import Annotated, Literal

import websockets

from mcp.server.fastmcp import FastMCP

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_BACKEND = "https://plato-agent.cia.net.sap"
DEFAULT_MODEL = "claude-opus"
DEFAULT_AGENT = "claude"
QUERY_TIMEOUT_S = 120  # 2 minutes
TOKEN_PATH = Path.home() / ".local" / "cia_token" / ".cia_token"
AUTO_RESPONSE = (
    "The user cannot answer this question in script mode. "
    "Please continue without user input."
)

# ============================================================================
# Token validation & auto-refresh
# ============================================================================

TOKEN_EXPIRY_BUFFER_S = 60  # consider expired 60s before actual expiry


def decode_jwt_payload(jwt: str) -> dict:
    """Decode a JWT payload without verification (we only need the exp claim)."""
    parts = jwt.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")
    # Add padding for base64url
    payload_b64 = parts[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload_bytes = urlsafe_b64decode(payload_b64)
    return json.loads(payload_bytes)


def is_token_valid(token: str) -> bool:
    """Check whether a JWT token is still valid (not expired)."""
    try:
        payload = decode_jwt_payload(token)
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            return False
        return exp - TOKEN_EXPIRY_BUFFER_S > time.time()
    except Exception:
        return False


def load_token_if_valid() -> str | None:
    """Load a valid token from env var or file, or return None."""
    # Env var takes precedence
    env_token = os.environ.get("CIA_TOKEN", "").strip()
    if env_token and is_token_valid(env_token):
        return env_token

    try:
        file_token = TOKEN_PATH.read_text().strip()
        if file_token and is_token_valid(file_token):
            return file_token
    except (OSError, IOError):
        pass

    return None


async def run_refresh() -> dict:
    """Spawn the refresh_token.py script and return its JSON result."""
    script_path = Path(__file__).parent / "refresh_token.py"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(script_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = (
            stderr.decode().strip()
            or stdout.decode().strip()
            or f"Exit code {proc.returncode}"
        )
        return {"success": False, "token_path": str(TOKEN_PATH), "error": error_msg}

    try:
        return json.loads(stdout.decode().strip())
    except (json.JSONDecodeError, ValueError):
        return {
            "success": False,
            "token_path": str(TOKEN_PATH),
            "error": f"Could not parse refresh output: {stdout.decode()}",
        }


async def ensure_token() -> str:
    """Return a valid token, auto-refreshing via browser SSO if needed."""
    existing = load_token_if_valid()
    if existing:
        return existing

    result = await run_refresh()
    if not result.get("success"):
        raise RuntimeError(f"Auto-refresh failed: {result.get('error')}")

    refreshed = load_token_if_valid()
    if refreshed:
        return refreshed

    raise RuntimeError(
        f"Token still invalid after refresh. Check {TOKEN_PATH} or set CIA_TOKEN env var."
    )


# ============================================================================
# WebSocket helpers
# ============================================================================


def get_websocket_url(backend_url: str, token: str) -> str:
    """Convert an HTTPS backend URL to a WSS URL with token parameter."""
    from urllib.parse import urlencode, urlparse, urlunparse

    parsed = urlparse(backend_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = urlunparse((scheme, parsed.netloc, "/ws", "", urlencode({"token": token}), ""))
    return ws_url


# ============================================================================
# Plato query via WebSocket
# ============================================================================


async def query_plato(
    prompt: str,
    model: str,
    agent: str,
    backend_url: str,
) -> dict:
    """Send a prompt to the Plato backend over WebSocket and collect the response."""
    token = await ensure_token()
    ws_url = get_websocket_url(backend_url, token)
    session_id = str(uuid.uuid4())

    # State
    message_text_map: dict[str, str] = {}
    tools_used: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "model": None}
    result_text: str | None = None

    def get_accumulated_text() -> str:
        if result_text is not None:
            return result_text
        return "".join(message_text_map.values())

    def final_usage() -> dict:
        total = usage["input_tokens"] + usage["output_tokens"]
        return {
            "inputTokens": usage["input_tokens"],
            "outputTokens": usage["output_tokens"],
            "totalTokens": total,
            "model": usage["model"],
        }

    ssl_context = _build_ssl_context()

    try:
        ws = await websockets.connect(ws_url, ssl=ssl_context)
    except Exception as conn_err:
        import ssl as _ssl

        if isinstance(conn_err, _ssl.SSLCertVerificationError):
            ca_path = os.environ.get("PLATO_CA_BUNDLE")
            hint = (
                f" PLATO_CA_BUNDLE is set to '{ca_path}' but the CA bundle may be "
                f"incomplete or incorrect."
                if ca_path
                else " Set PLATO_CA_BUNDLE to the path of your SAP CA bundle "
                "(e.g. /path/to/ca_bundle.pem) in the MCP server env config."
            )
            raise RuntimeError(
                f"SSL certificate verification failed connecting to "
                f"{backend_url}.{hint}"
            ) from conn_err
        raise RuntimeError(
            f"WebSocket connection error ({type(conn_err).__name__}): {conn_err}"
        ) from conn_err

    async with ws:
        try:
            result = await asyncio.wait_for(
                _ws_loop(
                    ws, prompt, session_id, model, agent,
                    message_text_map, tools_used, usage,
                    result_text, get_accumulated_text, final_usage,
                ),
                timeout=QUERY_TIMEOUT_S,
            )
            return result
        except asyncio.TimeoutError:
            accumulated = get_accumulated_text()
            if accumulated:
                return {
                    "text": accumulated + "\n\n[Timed out after 2 minutes]",
                    "usage": final_usage(),
                    "toolsUsed": tools_used,
                }
            raise RuntimeError("Query timed out after 2 minutes with no response")


def _build_ssl_context():
    """Build an SSL context that loads SAP CA certs if PLATO_CA_BUNDLE is set."""
    import ssl

    ca_path = os.environ.get("PLATO_CA_BUNDLE")
    if not ca_path:
        return True  # use default SSL context
    if not os.path.isfile(ca_path):
        raise RuntimeError(
            f"PLATO_CA_BUNDLE is set to '{ca_path}' but the file does not exist. "
            f"Check the path or unset the variable to use default SSL."
        )
    try:
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(ca_path)
        return ctx
    except ssl.SSLError as e:
        raise RuntimeError(
            f"Failed to load CA bundle from '{ca_path}': {e}"
        ) from e


async def _ws_loop(
    ws,
    prompt: str,
    session_id: str,
    model: str,
    agent: str,
    message_text_map: dict,
    tools_used: list,
    usage: dict,
    result_text: str | None,
    get_accumulated_text,
    final_usage,
) -> dict:
    """Process WebSocket messages until completion or error."""
    async for raw_message in ws:
        try:
            msg = json.loads(raw_message)
        except (json.JSONDecodeError, ValueError):
            continue

        msg_type = msg.get("type")

        if msg_type == "authenticated":
            query_msg = {
                "type": "agent-query",
                "id": str(uuid.uuid4()),
                "payload": {
                    "prompt": prompt,
                    "sessionId": session_id,
                    "conversationHistory": [],
                    "model": model,
                    "agent": agent,
                },
            }
            await ws.send(json.dumps(query_msg))

        elif msg_type == "agent-message":
            payload = msg.get("payload", {})

            # Track usage
            msg_usage = payload.get("usage")
            if msg_usage:
                usage["input_tokens"] += msg_usage.get("inputTokens", 0)
                usage["output_tokens"] += msg_usage.get("outputTokens", 0)
                if payload.get("model"):
                    usage["model"] = payload["model"]

            message_id = payload.get("id")

            if payload.get("type") in ("assistant", "user") and isinstance(
                payload.get("content"), list
            ):
                for block in payload["content"]:
                    if block.get("type") == "text" and block.get("text"):
                        key = message_id or str(uuid.uuid4())
                        message_text_map[key] = block["text"]
                    elif block.get("type") == "tool_use" and block.get("name"):
                        tools_used.append(block["name"])

            elif payload.get("type") == "result":
                if payload.get("result") and not payload.get("isError"):
                    result_text = payload["result"]

        elif msg_type == "agent-response-complete":
            return {
                "text": get_accumulated_text(),
                "usage": final_usage(),
                "toolsUsed": tools_used,
            }

        elif msg_type == "agent-error":
            payload = msg.get("payload", {})
            error_msg = payload.get("error", "Unknown agent error")
            if payload.get("recoverable"):
                continue
            accumulated = get_accumulated_text()
            if accumulated:
                return {
                    "text": accumulated + f"\n\n[Error: {error_msg}]",
                    "usage": final_usage(),
                    "toolsUsed": tools_used,
                }
            raise RuntimeError(error_msg)

        elif msg_type == "ask-user-question":
            payload = msg.get("payload", {})
            questions = payload.get("questions", [])
            answers = {}
            if questions:
                for i in range(len(questions)):
                    answers[f"q{i}"] = AUTO_RESPONSE
            else:
                answers["q0"] = AUTO_RESPONSE

            response = {
                "type": "user-question-response",
                "id": str(uuid.uuid4()),
                "payload": {
                    "toolUseId": payload.get("toolUseId"),
                    "sessionId": session_id,
                    "response": {
                        "answers": answers,
                        "cancelled": False,
                    },
                    "correlationId": payload.get("correlationId"),
                },
            }
            await ws.send(json.dumps(response))

        elif msg_type == "server-shutting-down":
            accumulated = get_accumulated_text()
            if accumulated:
                return {
                    "text": accumulated + "\n\n[Server shutting down]",
                    "usage": final_usage(),
                    "toolsUsed": tools_used,
                }
            raise RuntimeError("Server is shutting down, please retry")

        elif msg_type == "error":
            payload = msg.get("payload", {})
            raise RuntimeError(payload.get("message", "WebSocket error from server"))

    # Connection closed without completion
    accumulated = get_accumulated_text()
    if accumulated:
        return {
            "text": accumulated + "\n\n[Disconnected]",
            "usage": final_usage(),
            "toolsUsed": tools_used,
        }
    raise RuntimeError("WebSocket closed unexpectedly")


# ============================================================================
# MCP Server
# ============================================================================

mcp = FastMCP("plato")


@mcp.tool(
    description=(
        "Query the CIA Plato AI Agent. Sends a prompt to the Plato backend and "
        "returns the agent's response. Use this to ask questions about SAP systems, "
        "code, architecture, or any topic the Plato agent can help with."
    ),
)
async def plato_query(
    prompt: Annotated[str, "The question or prompt to send to Plato"],
    model: Annotated[
        Literal["claude-opus", "claude-sonnet", "claude-4.5-haiku"],
        "Model to use (default: claude-opus)",
    ] = DEFAULT_MODEL,
    agent: Annotated[
        Literal["claude", "opencode"],
        "Agent type: 'opencode' (default, OpenCode SDK adapter) or 'claude' (full Claude Agent SDK)",
    ] = DEFAULT_AGENT,
    backend_url: Annotated[
        str,
        "Backend URL (default: main staging)",
    ] = DEFAULT_BACKEND,
) -> str:
    """Query the CIA Plato AI Agent."""
    try:
        result = await query_plato(prompt, model, agent, backend_url)

        response = result["text"]

        # Append metadata
        meta: list[str] = []
        if result["usage"].get("model"):
            meta.append(f"Model: {result['usage']['model']}")
        meta.append(
            f"Tokens: {result['usage']['totalTokens']} "
            f"(in: {result['usage']['inputTokens']}, out: {result['usage']['outputTokens']})"
        )
        if result["toolsUsed"]:
            meta.append(f"Tools used: {', '.join(result['toolsUsed'])}")

        response += f"\n\n---\n{' | '.join(meta)}"
        return response
    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error ({type(e).__name__}): {e}"


@mcp.tool(
    description=(
        "Refresh the CIA JWT token via OAuth 2.0 browser-based SSO. Opens a browser "
        "for SAP IAS authentication, receives the token via local callback, and writes "
        "it to ~/.local/cia_token/.cia_token. Use this when you need to force a token "
        "refresh (plato_query auto-refreshes when needed)."
    ),
)
async def refresh_cia_token() -> str:
    """Refresh the CIA JWT token via browser-based SSO."""
    try:
        result = await run_refresh()

        if result.get("success"):
            msg = f"CIA token refreshed successfully.\nToken written to: {result['token_path']}"
            if result.get("expires_at"):
                msg += f"\nExpires at: {result['expires_at']}"
            return msg
        else:
            return f"Token refresh failed: {result.get('error')}"
    except Exception as e:
        return f"Error: {e}"


# ============================================================================
# Start
# ============================================================================

if __name__ == "__main__":
    if sys.stdin.isatty():
        print(
            "plato MCP server — stdio mode\n"
            "This server communicates via JSON-RPC over stdin/stdout.\n"
            "Run it via Claude Code MCP config, not directly in a terminal.\n",
            file=sys.stderr,
        )
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        pass
