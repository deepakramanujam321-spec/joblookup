#!/usr/bin/env python3
"""Mechanical collection step: fetch candidate jobs from every enabled
source, apply the deterministic filters, dedupe, and write JSON.

Deliberately does NOT talk to Supabase or an LLM — this script's only job is
"find candidate postings, reliably, and hand them off as data". The calling
workflow (see README.md) reads this output and does fit-scoring itself.

Usage:
    python scripts/run_collect.py --output jobs.json
    python scripts/run_collect.py --pretty    # human-readable, to stdout

--output writes the JSON directly via a file handle rather than relying on
shell `>` redirection of stdout. That's not just style: Scrapling's stealth
fetcher launches a real browser subprocess, and in practice that kind of
dependency chain can end up writing bytes to the process's stdout file
descriptor outside Python's own print()/logging (a crash dump, a driver
message, anything). `>` redirection captures the fd as-is and would
silently corrupt the JSON; writing to a file handle never shares that fd.
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
    parser.add_argument("--output", help="write JSON here instead of stdout (see module docstring for why)")
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
    indent = 2 if args.pretty else None
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent)
    else:
        json.dump(payload, sys.stdout, indent=indent)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
