"""
DuckDuckGo search wrapper with rate limiting and 403 retry fallback.
Uses the free duckduckgo-search library - no API key required.
"""
import time
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

_LAST_SEARCH_TS = 0.0
_MIN_INTERVAL   = 3.0   # seconds between requests to avoid DDG throttle
_NEWS_FAIL_STREAK = 0


def _throttle(extra: float = 0.0):
    global _LAST_SEARCH_TS
    elapsed = time.time() - _LAST_SEARCH_TS
    wait = _MIN_INTERVAL + extra - elapsed
    if wait > 0:
        time.sleep(wait)
    _LAST_SEARCH_TS = time.time()


def _run_ddgs(method: str, query: str, max_results: int) -> List[Dict[str, Any]]:
    from ddgs import DDGS
    with DDGS() as ddgs:
        fn = getattr(ddgs, method)
        return list(fn(query, max_results=max_results))


def ddg_text(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """DuckDuckGo text search. Returns list of {title, href, body} dicts."""
    _throttle()
    try:
        results = _run_ddgs("text", query, max_results)
        logger.info(f"DDG text '{query[:55]}' -> {len(results)} results")
        return results
    except Exception as e:
        logger.warning(f"DDG text search failed '{query[:55]}': {e}")
        return []


def ddg_news(query: str, max_results: int = 15) -> List[Dict[str, Any]]:
    """
    DuckDuckGo news search with retry/backoff.
    Falls back to text search when news endpoint is rate-limited.
    """
    global _NEWS_FAIL_STREAK
    _throttle(extra=min(_NEWS_FAIL_STREAK * 2.0, 8.0))
    try:
        results = _run_ddgs("news", query, max_results)
        _NEWS_FAIL_STREAK = 0
        logger.info(f"DDG news  '{query[:55]}' -> {len(results)} results")
        return results
    except Exception as e:
        _NEWS_FAIL_STREAK += 1
        logger.warning(f"DDG news search failed '{query[:55]}': {e}")
        # Fallback: news-style text query
        time.sleep(2)
        fallback = ddg_text(f"{query} funding startup India news", max_results=max_results)
        return [
            {
                "title": r.get("title", ""),
                "body":  r.get("body", ""),
                "url":   r.get("href", ""),
                "source": "DuckDuckGo (text fallback)",
            }
            for r in fallback
        ]


def ddg_urls_for_company_jobs(company: str) -> List[str]:
    """Run targeted job search queries for a company and return unique URLs."""
    queries = [
        f'"{company}" internship apply 2025',
        f'"{company}" jobs hiring 2025 India',
        f'{company} careers site:internshala.com',
        f'{company} jobs site:naukri.com',
        f'{company} internship site:unstop.com',
    ]
    seen_urls = set()
    results   = []
    for q in queries:
        for r in ddg_text(q, max_results=5):
            url = r.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(r)
    return results


def ddg_urls_for_leadership(company: str) -> List[Dict]:
    """Search LinkedIn profiles for company founders / executives."""
    queries = [
        f'"{company}" founder CEO linkedin.com/in India',
        f'"{company}" co-founder CTO linkedin.com/in',
    ]
    seen_urls = set()
    results   = []
    for q in queries:
        for r in ddg_text(q, max_results=5):
            url = r.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(r)
    return results


def ddg_urls_for_hr(company: str) -> List[Dict]:
    """Search LinkedIn profiles for HR / talent acquisition contacts."""
    queries = [
        f'"{company}" HR recruiter "talent acquisition" linkedin.com/in',
        f'"{company}" "head of people" OR "people operations" linkedin.com/in India',
        f'"{company}" recruiter site:linkedin.com/in India',
    ]
    seen_urls = set()
    results   = []
    for q in queries:
        for r in ddg_text(q, max_results=4):
            url = r.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(r)
    return results
