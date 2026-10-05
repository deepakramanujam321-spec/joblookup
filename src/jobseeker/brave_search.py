"""Thin client for the Brave Search API — discovery only, no scraping here."""

from __future__ import annotations

import time

import requests

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def search(query: str, api_key: str, count: int = 10) -> list[dict]:
    """Returns a list of {title, url, description} dicts. Empty list on failure
    (logged, not raised) so one bad query doesn't kill the whole run."""
    try:
        resp = requests.get(
            SEARCH_URL,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            params={"q": query, "count": count},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
            }
            for r in results
        ]
    except requests.RequestException as e:
        print(f"[brave_search] query failed: {query!r}: {e}")
        return []
    finally:
        time.sleep(1)  # be a polite API citizen, stay well under rate limits
