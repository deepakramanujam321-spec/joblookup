#!/usr/bin/env python3
"""Mechanical collection step: fetch candidate jobs from every enabled
source, apply the deterministic filters, dedupe, and print JSON to stdout.

Deliberately does NOT talk to Supabase or an LLM — this script's only job is
"find candidate postings, reliably, and hand them off as data". The calling
Routine (see README.md) reads this output, deduplicates against the
database, and does fit-scoring itself.

Usage:
    python scripts/run_collect.py > /tmp/jobs.json
    python scripts/run_collect.py --pretty    # human-readable, for debugging
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import config, sources
from jobseeker.filters import apply_filters, dedupe_by_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    profile = config.load_profile()
    brave_api_key = config.require_env("BRAVE_API_KEY")
    enabled = profile["sources"]

    all_listings = []

    if enabled.get("ats_boards"):
        print("[run_collect] discovering ATS board postings...", file=sys.stderr)
        all_listings += sources.discover_ats_boards(profile, brave_api_key)

    if enabled.get("remote_boards"):
        print("[run_collect] fetching RemoteOK...", file=sys.stderr)
        all_listings += sources.fetch_remoteok(profile)
        print("[run_collect] fetching WeWorkRemotely...", file=sys.stderr)
        all_listings += sources.fetch_weworkremotely(profile)

    if enabled.get("linkedin_indeed"):
        print("[run_collect] discovering LinkedIn/Indeed (best-effort)...", file=sys.stderr)
        all_listings += sources.discover_linkedin_indeed(profile, brave_api_key)

    print(f"[run_collect] {len(all_listings)} raw candidates before filtering", file=sys.stderr)

    filtered = apply_filters(all_listings, profile)
    deduped = dedupe_by_url(filtered)

    print(f"[run_collect] {len(deduped)} candidates after filtering + dedupe", file=sys.stderr)

    payload = [listing.to_dict() for listing in deduped]
    json.dump(payload, sys.stdout, indent=2 if args.pretty else None)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
