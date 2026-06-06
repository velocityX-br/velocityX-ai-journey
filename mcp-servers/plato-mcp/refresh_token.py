#!/usr/bin/env python3
"""
OAuth 2.0 token refresh for CIA Plato Agent.

Uses dynamic client registration + PKCE to obtain a fresh JWT via browser SSO.
Can be run standalone or spawned by the MCP server's refresh_cia_token tool.

Usage: python3 refresh_token.py [backend_url]
"""

import hashlib
import json
import os
import secrets
import subprocess
import sys
from base64 import urlsafe_b64decode, urlsafe_b64encode
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Event
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import ssl

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_BACKEND = "https://main-cia-plato-agent.staging-1.cia.net.sap"
TOKEN_PATH = Path.home() / ".local" / "cia_token" / ".cia_token"
CALLBACK_TIMEOUT_S = 120  # 2 minutes for user to complete SSO

# ============================================================================
# Logging (progress to stderr so stdout stays clean for JSON result)
# ============================================================================


def log(msg: str) -> None:
    sys.stderr.write(f"[refresh] {msg}\n")
    sys.stderr.flush()


# ============================================================================
# SSL context
# ============================================================================


def build_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that loads SAP CA certs if PLATO_CA_BUNDLE is set."""
    ctx = ssl.create_default_context()
    ca_path = os.environ.get("PLATO_CA_BUNDLE")
    if ca_path and os.path.isfile(ca_path):
        ctx.load_verify_locations(ca_path)
    return ctx


# ============================================================================
# PKCE helpers
# ============================================================================


def base64url_encode(data: bytes) -> str:
    """Base64url encode without padding."""
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_code_verifier() -> str:
    return base64url_encode(secrets.token_bytes(64))


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64url_encode(digest)


# ============================================================================
# HTTP helpers
# ============================================================================


def http_get_json(url: str, ssl_ctx: ssl.SSLContext) -> dict:
    """Fetch JSON from a URL."""
    req = Request(url)
    with urlopen(req, context=ssl_ctx) as resp:
        return json.loads(resp.read())


def http_post_json(url: str, data: dict, ssl_ctx: ssl.SSLContext) -> dict:
    """POST JSON to a URL and return JSON response."""
    body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urlopen(req, context=ssl_ctx) as resp:
        return json.loads(resp.read())


def http_post_form(url: str, params: dict, ssl_ctx: ssl.SSLContext) -> dict:
    """POST form-encoded data to a URL and return JSON response."""
    body = urlencode(params).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req, context=ssl_ctx) as resp:
        return json.loads(resp.read())


# ============================================================================
# OAuth discovery
# ============================================================================


def fetch_metadata(backend_url: str, ssl_ctx: ssl.SSLContext) -> dict:
    url = f"{backend_url}/.well-known/oauth-authorization-server"
    log(f"Fetching OAuth metadata from {url}")
    data = http_get_json(url, ssl_ctx)
    return {
        "authorization_endpoint": data["authorization_endpoint"],
        "token_endpoint": data["token_endpoint"],
        "registration_endpoint": data["registration_endpoint"],
    }


# ============================================================================
# Dynamic client registration
# ============================================================================


def register_client(
    registration_endpoint: str, redirect_uri: str, ssl_ctx: ssl.SSLContext
) -> dict:
    log("Registering dynamic OAuth client")
    data = http_post_json(
        registration_endpoint,
        {
            "client_name": "plato-mcp-refresh",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
        ssl_ctx,
    )
    return {"client_id": data["client_id"]}


# ============================================================================
# Local callback server
# ============================================================================


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback."""

    # Class-level state shared with the server
    callback_result: dict | None = None
    callback_error: str | None = None
    callback_event: Event = Event()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        params = parse_qs(parsed.query)

        error = params.get("error", [None])[0]
        if error:
            desc = params.get("error_description", ["Unknown error"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h2>Authentication failed</h2>"
                f"<p>{desc}</p><p>You can close this tab.</p></body></html>".encode()
            )
            _CallbackHandler.callback_error = f"OAuth error: {error} - {desc}"
            _CallbackHandler.callback_event.set()
            return

        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if not code or not state:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Missing parameters</h2>"
                b"<p>No authorization code received.</p></body></html>"
            )
            _CallbackHandler.callback_error = "Missing code or state in callback"
            _CallbackHandler.callback_event.set()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Authentication successful!</h2>"
            b"<p>You can close this tab and return to your terminal.</p></body></html>"
        )
        _CallbackHandler.callback_result = {"code": code, "state": state}
        _CallbackHandler.callback_event.set()

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass


def start_callback_server() -> tuple[HTTPServer, int]:
    """Start a local HTTP server on a random port and return (server, port)."""
    # Reset handler state
    _CallbackHandler.callback_result = None
    _CallbackHandler.callback_error = None
    _CallbackHandler.callback_event = Event()

    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    return server, port


# ============================================================================
# Browser launch
# ============================================================================


def open_browser(url: str) -> None:
    for cmd in (["wslview", url], ["xdg-open", url], ["open", url]):
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    log("Could not open browser automatically. Please open this URL manually:")
    log(url)


# ============================================================================
# Token exchange
# ============================================================================


def exchange_code(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
    ssl_ctx: ssl.SSLContext,
) -> dict:
    log("Exchanging authorization code for token")
    return http_post_form(
        token_endpoint,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
        ssl_ctx,
    )


# ============================================================================
# JWT decode (no verification — we just need the expiry claim)
# ============================================================================


def decode_jwt_payload(jwt: str) -> dict:
    parts = jwt.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")
    payload_b64 = parts[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload_bytes = urlsafe_b64decode(payload_b64)
    return json.loads(payload_bytes)


# ============================================================================
# Write token file
# ============================================================================


def write_token(token: str) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token)
    TOKEN_PATH.chmod(0o600)
    log(f"Token written to {TOKEN_PATH}")


# ============================================================================
# Main flow
# ============================================================================


def refresh_token(backend_url: str = DEFAULT_BACKEND) -> dict:
    """Run the full OAuth 2.0 PKCE flow and return a result dict."""
    ssl_ctx = build_ssl_context()

    # 1. Discover OAuth endpoints
    metadata = fetch_metadata(backend_url, ssl_ctx)

    # 2. Start local callback server
    server, port = start_callback_server()
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    log(f"Callback server listening on {redirect_uri}")

    try:
        # 3. Register dynamic client
        client = register_client(metadata["registration_endpoint"], redirect_uri, ssl_ctx)
        log(f"Registered client: {client['client_id']}")

        # 4. Generate PKCE pair
        code_verifier = generate_code_verifier()
        code_challenge = generate_code_challenge(code_verifier)
        state = base64url_encode(secrets.token_bytes(32))

        # 5. Build authorization URL
        auth_params = urlencode({
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        })
        auth_url = f"{metadata['authorization_endpoint']}?{auth_params}"

        # 6. Open browser
        log("Opening browser for SSO authentication...")
        open_browser(auth_url)

        # 7. Wait for callback
        log(f"Waiting for authentication callback ({CALLBACK_TIMEOUT_S}s timeout)...")

        # Handle requests in a loop until callback received or timeout
        import threading

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        if not _CallbackHandler.callback_event.wait(timeout=CALLBACK_TIMEOUT_S):
            raise TimeoutError(f"Authentication timed out after {CALLBACK_TIMEOUT_S} seconds")

        if _CallbackHandler.callback_error:
            raise RuntimeError(_CallbackHandler.callback_error)

        callback_result = _CallbackHandler.callback_result
        if not callback_result:
            raise RuntimeError("No callback result received")

        # Verify state
        if callback_result["state"] != state:
            raise RuntimeError("State mismatch — possible CSRF attack")

        # 8. Exchange code for token
        token_response = exchange_code(
            metadata["token_endpoint"],
            callback_result["code"],
            redirect_uri,
            client["client_id"],
            code_verifier,
            ssl_ctx,
        )

        # 9. Extract JWT — prefer access_token, fall back to id_token
        jwt = token_response.get("access_token") or token_response.get("id_token")
        if not jwt:
            raise RuntimeError(
                f"No access_token or id_token in token response: {json.dumps(token_response)}"
            )

        # 10. Decode for expiry info
        expires_at = None
        try:
            payload = decode_jwt_payload(jwt)
            exp = payload.get("exp")
            if isinstance(exp, (int, float)):
                from datetime import datetime, timezone

                expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
                log(f"Token expires at {expires_at}")
        except Exception:
            log("Could not decode JWT payload for expiry — token still saved")

        # 11. Write token
        write_token(jwt)

        result = {"success": True, "token_path": str(TOKEN_PATH)}
        if expires_at:
            result["expires_at"] = expires_at
        return result

    finally:
        server.shutdown()


# ============================================================================
# CLI entry point
# ============================================================================

if __name__ == "__main__":
    backend = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BACKEND
    try:
        result = refresh_token(backend)
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        sys.exit(0)
    except Exception as e:
        log(f"Error: {e}")
        result = {
            "success": False,
            "token_path": str(TOKEN_PATH),
            "error": str(e),
        }
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        sys.exit(1)
