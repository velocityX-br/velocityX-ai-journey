"""Built-in web search tool — lets the agent retrieve the latest info online.

Consistent with the scaffold's "tools come from config" philosophy: a config
opts in with ``web_search: true`` and the agent gains a ``web_search`` tool that
shows up as a live tool card in the Web UI automatically (no bespoke wiring).

Provider selection is env-driven with graceful degradation, so it works with
zero signup out of the box:

  1. **Tavily** (``TAVILY_API_KEY``) — a search API purpose-built for LLM/agent
     use. Preferred when the key is present. Called via a plain ``httpx`` POST
     so no extra heavy dependency is required.
  2. **DuckDuckGo** HTML endpoint — no API key needed. Used as the fallback so
     the tool is useful immediately.

The tool returns a compact, human/LLM-readable list of results (title, URL,
snippet) the model can read and cite. All network access is lazy and guarded so
importing this module never requires ``httpx`` or a network connection — the
same discipline as ``mcp_tools`` / ``skills``.
"""

from __future__ import annotations

import html
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

# Keep result payloads small so a single tool card / LLM context stays bounded.
_DEFAULT_MAX_RESULTS = 5
_SNIPPET_MAX = 400
_HTTP_TIMEOUT = 15.0

_TAVILY_URL = "https://api.tavily.com/search"
_DDG_URL = "https://html.duckduckgo.com/html/"


def _truncate(text: str, limit: int = _SNIPPET_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _format_results(results: list[dict[str, Any]]) -> str:
    """Render a list of ``{title, url, snippet}`` dicts as readable text."""
    if not results:
        return "No results found."
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title = _truncate(str(r.get("title") or "(untitled)"), 200)
        url = str(r.get("url") or "").strip()
        snippet = _truncate(str(r.get("snippet") or ""))
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}".rstrip())
    return "\n\n".join(lines)


def _search_tavily(query: str, max_results: int, api_key: str) -> list[dict[str, Any]]:
    import httpx
    from ai_observability import span

    with span(
        "external tavily",
        **{"peer.service": "tavily", "server.address": "api.tavily.com", "http.request.method": "POST"},
    ) as current:
        resp = httpx.post(
            _TAVILY_URL,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        out: list[dict[str, Any]] = []
        for item in data.get("results", [])[:max_results]:
            out.append(
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": item.get("content"),
                }
            )
        if current.is_recording():
            status = getattr(resp, "status_code", None)
            if status is not None:
                current.set_attribute("http.response.status_code", status)
            current.set_attribute("ai.external.result_count", len(out))
        return out


# DuckDuckGo HTML result anchors: <a class="result__a" href="URL">TITLE</a>
_DDG_LINK = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_DDG_SNIPPET = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")


def _strip_html(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment)).strip()


def _unwrap_ddg_url(url: str) -> str:
    """DuckDuckGo wraps targets as ``/l/?uddg=<encoded>``; unwrap when present."""
    m = re.search(r"[?&]uddg=([^&]+)", url)
    if m:
        from urllib.parse import unquote

        return unquote(m.group(1))
    if url.startswith("//"):
        return "https:" + url
    return url


def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, Any]]:
    import httpx
    from ai_observability import span

    with span(
        "external duckduckgo",
        **{"peer.service": "duckduckgo", "server.address": "html.duckduckgo.com", "http.request.method": "POST"},
    ) as current:
        resp = httpx.post(
            _DDG_URL,
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (agent-chat web_search)"},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.text
        titles = list(_DDG_LINK.finditer(body))
        snippets = list(_DDG_SNIPPET.finditer(body))
        out: list[dict[str, Any]] = []
        for i, link in enumerate(titles[:max_results]):
            snippet = _strip_html(snippets[i].group("snippet")) if i < len(snippets) else ""
            out.append(
                {
                    "title": _strip_html(link.group("title")),
                    "url": _unwrap_ddg_url(link.group("url")),
                    "snippet": snippet,
                }
            )
        if current.is_recording():
            status = getattr(resp, "status_code", None)
            if status is not None:
                current.set_attribute("http.response.status_code", status)
            current.set_attribute("ai.external.result_count", len(out))
        return out


def run_web_search(query: str, max_results: int = _DEFAULT_MAX_RESULTS) -> str:
    """Search the web and return formatted results (Tavily → DuckDuckGo).

    Never raises: on any error it returns a short diagnostic string so a tool
    call degrades gracefully rather than crashing the agent turn.
    """
    query = (query or "").strip()
    if not query:
        return "web_search error: empty query."

    api_key = os.environ.get("TAVILY_API_KEY")
    try:
        if api_key:
            results = _search_tavily(query, max_results, api_key)
        else:
            results = _search_duckduckgo(query, max_results)
    except ImportError:
        return "web_search error: the 'httpx' package is required for web search."
    except Exception as exc:  # noqa: BLE001 — degrade gracefully to a message
        return f"web_search error: {type(exc).__name__}: {exc}"

    return _format_results(results)


def build_web_search_tool(max_results: int = _DEFAULT_MAX_RESULTS) -> BaseTool:
    """Expose ``run_web_search`` as a LangChain ``web_search(query)`` tool."""
    from langchain_core.tools import StructuredTool

    def _run(query: str) -> str:
        return run_web_search(query, max_results=max_results)

    return StructuredTool.from_function(
        func=_run,
        name="web_search",
        description=(
            "Search the public web for current, up-to-date information (news, "
            "recent events, docs, prices, releases). Input: a natural-language "
            "search query. Returns a numbered list of results with title, URL, "
            "and snippet. Use this whenever the answer may depend on information "
            "newer than your training data, then cite the URLs you used."
        ),
    )
