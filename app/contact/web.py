"""
contact/web.py — web transport boundaries for contact discovery (Phase 8).

Follows the same shape as app.llm: the pipeline (and the provider) depends on
the SearchProvider / WebFetcher Protocols, never on a concrete engine, so the
backend can be swapped (DuckDuckGo today; Google CSE / company directories
later) without touching the provider, enricher, or orchestrator.

There is no concept of "try harder and guess": every transport failure raises
an explicit exception so the provider fails CLOSED (NOT_FOUND) instead of
trusting a half-fetched page.
"""
from __future__ import annotations

import html as _htmllib
import re
import time
import urllib.parse
from typing import List, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel

DEFAULT_TIMEOUT_S = 12.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class SearchResult(BaseModel):
    title: str = ""
    url: str = ""
    snippet: str = ""


class WebFetchError(RuntimeError):
    """Raised when a page cannot be fetched. Caller treats it as one failed page."""


class SearchError(RuntimeError):
    """Raised when the search backend fails. Caller must fail closed."""


@runtime_checkable
class SearchProvider(Protocol):
    """Contract: return public search results for a query."""

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        ...


@runtime_checkable
class WebFetcher(Protocol):
    """Contract: fetch a public URL and return its raw HTML string."""

    def get(self, url: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
        ...


class NullSearchProvider:
    """Offline default: returns no results, never touches the network."""

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        return []


class HttpxWebFetcher:
    """Real transport over httpx (already a project dependency). Follows
    redirects, enforces a timeout, and surfaces every failure as WebFetchError so
    the provider degrades safely instead of fabricating from a bad fetch."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT):
        self._user_agent = user_agent

    def get(self, url: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": self._user_agent},
                timeout=timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            raise WebFetchError(f"fetch failed for {url}: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise WebFetchError(f"HTTP {resp.status_code} for {url}")
        return resp.text


class HtmlDuckDuckGoSearch:
    """Real, keyless search transport over DuckDuckGo's public HTML endpoint.

    Only used when the live provider is explicitly enabled (CONTACT_PROVIDER=
    web_search) and approved for a validation run — never during tests/offline.
    Each call hits the network; the provider's cache + budget cap the volume.
    """

    DDG_HTML = "https://html.duckduckgo.com/html/"

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S,
                 user_agent: str = DEFAULT_USER_AGENT):
        self._timeout_s = timeout_s
        self._user_agent = user_agent

    @staticmethod
    def _clean(s: str) -> str:
        try:
            s = re.sub(r"<[^>]+>", "", s or "")
            return _htmllib.unescape(s).strip()
        except Exception:
            return (s or "").strip()

    @staticmethod
    def _decoded(url: str) -> str:
        """DuckDuckGo wraps result URLs in a redirect; unwrap the real target."""
        if "duckduckgo.com/l/" in url and "uddg=" in url:
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                return q.get("uddg", [""])[0]
            except Exception:
                return url
        return url

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        try:
            resp = httpx.get(
                self.DDG_HTML,
                params={"q": query},
                headers={"User-Agent": self._user_agent},
                timeout=self._timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            raise SearchError(f"DuckDuckGo search failed: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise SearchError(f"DuckDuckGo HTTP {resp.status_code}")
        return self._parse(resp.text, max_results)

    def _parse(self, page: str, max_results: int) -> List[SearchResult]:
        results: List[SearchResult] = []
        # DuckDuckGo HTML results: <a class="result__a" href="...">Title</a>
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page):
            url = self._decoded(m.group(1))
            title = self._clean(m.group(2))
            if not url or not title:
                continue
            results.append(SearchResult(title=title, url=url, snippet=""))
            if len(results) >= max_results:
                break
        if not results:  # snippets appear as separate result__snippet blocks in order
            snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page)
            for i, r in enumerate(results):
                if i < len(snips):
                    results[i].snippet = self._clean(snips[i])
        return results


def make_search_provider() -> SearchProvider:
    """Default live search backend (used only when contact discovery is enabled)."""
    return HtmlDuckDuckGoSearch()


def make_web_fetcher() -> WebFetcher:
    return HttpxWebFetcher()