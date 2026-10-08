"""Page fetching via Scrapling (https://github.com/D4Vinci/Scrapling).

Two tiers, cheapest first:
  - `fetch_fast`    plain HTTP (Fetcher) — fine for mostly-static ATS pages
    (Greenhouse, Lever).
  - `fetch_stealth` anti-bot-bypass fetch (StealthyFetcher) — needed for
    Ashby (heavily JS-rendered) and for LinkedIn/Indeed public search pages.

Both return the Scrapling page object (or None on failure) so callers can use
.css()/.xpath()/.get_all_text() directly — see parsing.py.
"""

from __future__ import annotations

import sys
import time


def fetch_fast(url: str, timeout: int = 20):
    """`timeout` is SECONDS here -- Fetcher is curl_cffi-based (requests-like
    convention), unlike fetch_stealth below."""
    from scrapling.fetchers import Fetcher

    try:
        return Fetcher.get(url, timeout=timeout)
    except Exception as e:
        print(f"[scrapling_fetch] fast fetch failed for {url}: {e}", file=sys.stderr)
        return None


def fetch_stealth(url: str, timeout_ms: int = 30_000):
    """`timeout_ms` is MILLISECONDS -- StealthyFetcher is Playwright-based,
    which uses ms throughout (confirmed against Scrapling's own docstring:
    "The timeout in milliseconds ... default is 30,000"). Passing a bare
    small int here previously meant "30ms", not "30s" -- every stealth
    fetch timed out instantly as a result."""
    from scrapling.fetchers import StealthyFetcher

    try:
        return StealthyFetcher.fetch(url, headless=True, timeout=timeout_ms)
    except Exception as e:
        print(f"[scrapling_fetch] stealth fetch failed for {url}: {e}", file=sys.stderr)
        return None
    finally:
        # LinkedIn/Indeed in particular are sensitive to request bursts —
        # space fetches out regardless of which tier handled it.
        time.sleep(2)
