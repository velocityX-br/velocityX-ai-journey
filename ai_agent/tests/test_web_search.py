"""web_search tool tests — network-free.

Exercise the pure formatting/parsing logic and the provider-selection branch
(Tavily vs DuckDuckGo) by monkeypatching the module's ``httpx`` calls, so no
network is touched (CI-safe).
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_chat import web_search


class _FakeResponse:
    def __init__(self, *, json_data: Any = None, text: str = "") -> None:
        self._json = json_data
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._json


def test_format_results_empty() -> None:
    assert web_search._format_results([]) == "No results found."


def test_format_results_shape() -> None:
    out = web_search._format_results([{"title": "Hello", "url": "https://example.com", "snippet": "a snippet"}])
    assert "1. Hello" in out
    assert "https://example.com" in out
    assert "a snippet" in out


def test_truncate_collapses_whitespace_and_caps() -> None:
    long = "word " * 200
    out = web_search._truncate(long, limit=20)
    assert len(out) <= 21  # 20 + ellipsis char
    assert out.endswith("…")


def test_run_web_search_empty_query() -> None:
    assert "empty query" in web_search.run_web_search("   ")


def test_run_web_search_uses_tavily_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    captured: dict[str, Any] = {}

    def fake_search_tavily(query: str, max_results: int, api_key: str):
        captured["query"] = query
        captured["api_key"] = api_key
        return [{"title": "T", "url": "https://t.example", "snippet": "s"}]

    def boom_ddg(*a: Any, **k: Any):
        raise AssertionError("DuckDuckGo should not be used when Tavily key is set")

    monkeypatch.setattr(web_search, "_search_tavily", fake_search_tavily)
    monkeypatch.setattr(web_search, "_search_duckduckgo", boom_ddg)

    out = web_search.run_web_search("latest python release")
    assert captured["api_key"] == "tvly-test"
    assert "https://t.example" in out


def test_run_web_search_falls_back_to_ddg_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    called: dict[str, Any] = {}

    def fake_ddg(query: str, max_results: int):
        called["query"] = query
        return [{"title": "D", "url": "https://d.example", "snippet": "ddg"}]

    monkeypatch.setattr(web_search, "_search_duckduckgo", fake_ddg)
    out = web_search.run_web_search("newest kernel")
    assert called["query"] == "newest kernel"
    assert "https://d.example" in out


def test_run_web_search_degrades_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    def boom(query: str, max_results: int):
        raise RuntimeError("network down")

    monkeypatch.setattr(web_search, "_search_duckduckgo", boom)
    out = web_search.run_web_search("anything")
    assert out.startswith("web_search error:")
    assert "network down" in out


def test_tavily_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        assert url == web_search._TAVILY_URL
        assert kwargs["json"]["api_key"] == "k"
        return _FakeResponse(
            json_data={
                "results": [
                    {"title": "A", "url": "https://a", "content": "ca"},
                    {"title": "B", "url": "https://b", "content": "cb"},
                ]
            }
        )

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    results = web_search._search_tavily("q", max_results=5, api_key="k")
    assert results == [
        {"title": "A", "url": "https://a", "snippet": "ca"},
        {"title": "B", "url": "https://b", "snippet": "cb"},
    ]


def test_duckduckgo_parsing_unwraps_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        '<a class="result__a" href="/l/?uddg=https%3A%2F%2Freal.example%2Fpage">Real Title</a>'
        '<a class="result__snippet">A useful <b>snippet</b> here</a>'
    )

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        assert url == web_search._DDG_URL
        return _FakeResponse(text=body)

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    results = web_search._search_duckduckgo("q", max_results=5)
    assert results[0]["title"] == "Real Title"
    assert results[0]["url"] == "https://real.example/page"
    assert "snippet" in results[0]["snippet"]


def test_build_web_search_tool_is_langchain_tool() -> None:
    pytest.importorskip("langchain_core")
    tool = web_search.build_web_search_tool()
    assert tool.name == "web_search"
    assert "web" in tool.description.lower()
