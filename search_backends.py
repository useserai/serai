"""Search backend chain for company discovery: Brave -> Tavily (optional) -> DuckDuckGo.
Brave 429/422/403/401 (quota/rate/auth) triggers automatic fallover. Tavily is skipped
if TAVILY_API_KEY is not set. DuckDuckGo is the always-available floor."""

import os
import requests

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class BackendUnavailable(Exception):
    """Raised when a backend is unusable (no key, quota exhausted, rate limited)."""


def brave_search(query: str, count: int) -> list:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        raise BackendUnavailable("Brave: no API key set")

    response = requests.get(
        BRAVE_SEARCH_URL,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        },
        params={
            "q": query,
            "count": count,
            "search_lang": "en",
            "country": "us",
            "spellcheck": 1,
        },
        timeout=30,
    )

    if response.status_code in (429, 422, 403, 401):
        raise BackendUnavailable(
            f"Brave: HTTP {response.status_code} ({response.text[:200]})"
        )

    response.raise_for_status()

    data = response.json()
    results = data.get("web", {}).get("results", [])
    return [
        {
            "url": r.get("url"),
            "title": r.get("title", "") or "",
            "description": r.get("description", "") or "",
        }
        for r in results
        if r.get("url")
    ]


def tavily_search(query: str, count: int) -> list:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise BackendUnavailable("Tavily: no API key set")

    response = requests.post(
        TAVILY_SEARCH_URL,
        json={
            "api_key": api_key,
            "query": query,
            "max_results": count,
            "search_depth": "basic",
        },
        timeout=30,
    )

    if response.status_code in (429, 401, 403):
        raise BackendUnavailable(
            f"Tavily: HTTP {response.status_code} ({response.text[:200]})"
        )

    response.raise_for_status()

    data = response.json()
    results = data.get("results", [])
    return [
        {
            "url": r.get("url"),
            "title": r.get("title", "") or "",
            "description": r.get("content", "") or "",
        }
        for r in results
        if r.get("url")
    ]


def duckduckgo_search(query: str, count: int) -> list:
    try:
        from ddgs import DDGS
    except ImportError as e:
        raise BackendUnavailable(f"DuckDuckGo: ddgs not installed ({e})")

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=count))

    return [
        {
            "url": r.get("href"),
            "title": r.get("title", "") or "",
            "description": r.get("body", "") or "",
        }
        for r in results
        if r.get("href")
    ]


def search_web(query: str, count: int = 20) -> list:
    """Try Brave, then Tavily (if key), then DuckDuckGo. Returns first successful results."""
    backends = [
        ("brave", brave_search),
        ("tavily", tavily_search),
        ("duckduckgo", duckduckgo_search),
    ]

    last_error = None
    for name, fn in backends:
        try:
            results = fn(query, count)
            print(f"[search] {name} -> {len(results)} results | {query}")
            return results
        except BackendUnavailable as e:
            print(f"[search] {name} unavailable: {e} | falling through")
            last_error = e
        except Exception as e:
            print(f"[search] {name} unexpected error: {e} | falling through")
            last_error = e

    raise RuntimeError(f"All search backends failed for query: {query} | last: {last_error}")
