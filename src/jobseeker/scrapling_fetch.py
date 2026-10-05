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

import time


def fetch_fast(url: str, timeout: int = 20):
    from scrapling.fetchers import Fetcher

    try:
        return Fetcher.get(url, timeout=timeout)
    except Exception as e:
        print(f"[scrapling_fetch] fast fetch failed for {url}: {e}")
        return None


def fetch_stealth(url: str, timeout: int = 30):
    from scrapling.fetchers import StealthyFetcher

    try:
        return StealthyFetcher.fetch(url, headless=True, timeout=timeout)
    except Exception as e:
        print(f"[scrapling_fetch] stealth fetch failed for {url}: {e}")
        return None
    finally:
        # LinkedIn/Indeed in particular are sensitive to request bursts —
        # space fetches out regardless of which tier handled it.
        time.sleep(2)
