#!/usr/bin/env python3
"""Health check script for the SCI AI MCP server.

Probes the Qdrant /healthz endpoint to verify that the vector store is
reachable.  Suitable for a Docker HEALTHCHECK instruction, a brew
services readiness gate, or a manual check via ``kubectl exec``.

Exit codes:
    0 — Qdrant responded with HTTP 200.
    1 — Connection failed or a non-200 status was returned.

Environment variables:
    SCI_MCP_QDRANT_URL / QDRANT_URL: Base URL of the Qdrant instance.
                                     Default: http://localhost:6333

Usage::

    python scripts/healthcheck.py

This script intentionally uses only the Python standard library so it
remains dependency-free and runnable in any Python 3.12-slim image layer
without importing the project virtualenv.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    """Probe the Qdrant health endpoint and return an exit code.

    Returns:
        0 if Qdrant is healthy, 1 otherwise.
    """
    qdrant_url: str = (
        os.environ.get("SCI_MCP_QDRANT_URL")
        or os.environ.get("QDRANT_URL")
        or "http://localhost:6333"
    )
    health_url: str = f"{qdrant_url.rstrip('/')}/healthz"

    try:
        with urllib.request.urlopen(health_url, timeout=5) as response:
            if response.status == 200:
                print(f"OK: Qdrant healthy at {qdrant_url}")
                return 0
            print(f"FAIL: Qdrant returned status {response.status} at {health_url}")
            return 1
    except urllib.error.URLError as exc:
        print(f"FAIL: Cannot reach Qdrant at {qdrant_url}: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — catch-all for timeout, decode errors, etc.
        print(f"FAIL: Unexpected error checking Qdrant at {qdrant_url}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
