#!/usr/bin/env python3
"""Mechanical collection step: fetch candidate jobs from every enabled
source for each person, apply the deterministic filters, dedupe, and write JSON.

Everyone is searched separately, from their own profile (preferred titles,
keywords, locations, excluded companies, salary floor); config/profile.yaml
supplies only the global settings (which sources are on). Does NOT talk to an
LLM, and only *reads* the database to find who to search for and what each
person wants. Without DATABASE_URL it falls back to the original single
person described by config/profile.yaml.

Usage:
    python scripts/run_collect.py --output-dir collected     # collected/<owner>.json for everyone
    python scripts/run_collect.py --owner default --output jobs.json
    python scripts/run_collect.py --pretty                   # one person, human-readable, to stdout

--output/--output-dir write the JSON directly via a file handle rather than
relying on shell `>` redirection of stdout. That's not just style: Scrapling's
stealth fetcher launches a real browser subprocess, and in practice that kind
of dependency chain can end up writing bytes to the process's stdout file
descriptor outside Python's own print()/logging (a crash dump, a driver
message, anything). `>` redirection captures the fd as-is and would
silently corrupt the JSON; writing to a file handle never shares that fd.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import config, sources
from jobseeker import profile as profile_mod
from jobseeker.database import get_engine
from jobseeker.filters import apply_filters, dedupe_by_url
from jobseeker.profile import DEFAULT_OWNER

OWNER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")  # also a file name, so keep it boring


def _collect(name: str, fn, source_stats: dict) -> list:
    """Each source is isolated: one failing never takes the run down, and
    its failure is recorded so pipeline health can say which one broke."""
    try:
        found = fn()
        source_stats[name] = {"found": len(found), "error": None}
        return found
    except Exception as e:
        print(f"[run_collect] {name} failed: {e}", file=sys.stderr)
        source_stats[name] = {"found": 0, "error": f"{type(e).__name__}: {e}"[:300]}
        return []


def collect_for(profile: dict, brave_api_key: str) -> dict:
    """One person's search: `profile` is the yaml-shaped dict built from their own profile."""
    enabled = profile["sources"]
    all_listings = []
    source_stats: dict = {}

    if enabled.get("ats_boards"):
        print("[run_collect] discovering ATS board postings...", file=sys.stderr)
        all_listings += _collect("ats_boards", lambda: sources.discover_ats_boards(profile, brave_api_key), source_stats)

    if enabled.get("remote_boards"):
        print("[run_collect] fetching WeWorkRemotely...", file=sys.stderr)
        all_listings += _collect("weworkremotely", lambda: sources.fetch_weworkremotely(profile), source_stats)

    if enabled.get("linkedin_indeed"):
        print("[run_collect] discovering LinkedIn/Indeed (best-effort)...", file=sys.stderr)
        all_listings += _collect("linkedin_indeed", lambda: sources.discover_linkedin_indeed(profile, brave_api_key), source_stats)

    print(f"[run_collect] {len(all_listings)} raw candidates before filtering", file=sys.stderr)

    filtered = apply_filters(all_listings, profile)
    deduped = dedupe_by_url(filtered)

    print(f"[run_collect] {len(deduped)} candidates after filtering + dedupe", file=sys.stderr)

    return {
        "listings": [listing.to_dict() for listing in deduped],
        "source_stats": source_stats,
        "raw_count": len(all_listings),
        "filtered_out": len(all_listings) - len(filtered),
        "in_run_duplicates": len(filtered) - len(deduped),
    }


def targets(base: dict, only: list[str] | None) -> list[tuple[str, dict]]:
    """(owner, search profile) for everyone to search for."""
    if not os.environ.get("DATABASE_URL"):
        return [(DEFAULT_OWNER, base)]  # no database: the original single-person setup
    out = []
    with get_engine().begin() as conn:
        for owner in only or profile_mod.list_owners(conn):
            record = profile_mod.get_or_seed(conn, owner)
            search = profile_mod.discovery_profile(record["data"], base, owner)
            if search is None:
                print(f"[run_collect] {owner}: no preferred job titles in the profile yet, skipping", file=sys.stderr)
                continue
            out.append((owner, search))
    return out


def _write(payload: dict, path: Path | None, indent: int | None) -> None:
    if path is None:
        json.dump(payload, sys.stdout, indent=indent, default=str)
        print()
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent, default=str)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--output", help="write JSON here instead of stdout (single person; see module docstring for why)")
    parser.add_argument("--output-dir", help="write <owner>.json here for each person searched")
    parser.add_argument("--owner", action="append", help="only this person (repeatable); default: everyone")
    args = parser.parse_args()

    base = config.load_profile()
    brave_api_key = config.require_env("BRAVE_API_KEY")
    people = targets(base, args.owner)
    if args.output and len(people) != 1:
        print(f"[run_collect] --output is for one person, but {len(people)} were selected; use --output-dir", file=sys.stderr)
        return 2
    if not args.output and not args.output_dir and len(people) > 1:
        print("[run_collect] several people selected: use --output-dir", file=sys.stderr)
        return 2

    indent = 2 if args.pretty else None
    for owner, search in people:
        if not OWNER_RE.fullmatch(owner):
            print(f"[run_collect] skipping unsafe owner id {owner!r}", file=sys.stderr)
            continue
        print(f"[run_collect] === {owner} ===", file=sys.stderr)
        try:
            payload = collect_for(search, brave_api_key)
        except Exception as e:  # one person's failure never costs the others their search
            print(f"[run_collect] {owner} failed: {type(e).__name__}: {e}", file=sys.stderr)
            payload = {"listings": [], "source_stats": {"collect": {"found": 0, "error": f"{type(e).__name__}: {e}"[:300]}},
                       "raw_count": 0, "filtered_out": 0, "in_run_duplicates": 0}
        payload["owner"] = owner
        if args.output:
            _write(payload, Path(args.output), indent)
        elif args.output_dir:
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)
            _write(payload, Path(args.output_dir) / f"{owner}.json", indent)
        else:
            _write(payload, None, indent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
