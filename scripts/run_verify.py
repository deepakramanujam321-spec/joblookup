#!/usr/bin/env python3
"""Re-checks whether stored listings are still open (official ATS APIs,
or a plain page check for RemoteOK/WWR), tracked applications first, and
re-ranks so freshness shows up in priority. LinkedIn/Indeed are never
fetched for verification.

Usage: python scripts/run_verify.py [--limit 150]
Requires DATABASE_URL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import preferences, profile, runs, verification  # noqa: E402
from jobseeker.database import get_engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=150)
    args = parser.parse_args()
    engine = get_engine()
    with runs.recorded(engine, "verify") as rec:
        stats = verification.verify_due(engine, limit=args.limit)
        with engine.begin() as conn:
            reranked = preferences.rerank(conn, profile.DEFAULT_OWNER)
        rec.values.update(jobs_found=stats["checked"], jobs_updated=reranked)
        rec.details = stats
        print(f"[run_verify] {stats}, re-ranked {reranked}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
