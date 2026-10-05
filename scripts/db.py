#!/usr/bin/env python3
"""CLI over SupabaseStore — the interface the Claude agent drives via Bash
inside a Routine-fired session (which has no MCP connectors, only a shell).

Subcommands:
    insert-new   --jobs-file PATH
    list         --status STATUS
    update       --id ID [--status S] [--fit-score N] [--fit-rationale TEXT]
                 [--outreach-draft TEXT] [--digest-batch-date YYYY-MM-DD]
    log-run      --type collect|digest [--found N] [--new N] [--error TEXT]

All output is JSON on stdout (or nothing, for update/log-run) so it's easy
to pipe and parse from a shell.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import config
from jobseeker.models import JobListing
from jobseeker.storage import SupabaseStore


def _store() -> SupabaseStore:
    url = config.require_env("SUPABASE_URL")
    key = config.require_env("SUPABASE_SERVICE_ROLE_KEY")
    return SupabaseStore(url, key)


def cmd_insert_new(args: argparse.Namespace) -> int:
    raw = json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
    listings = [JobListing(**row) for row in raw]
    inserted = _store().insert_new_jobs(listings)
    print(json.dumps({"found": len(listings), "inserted": inserted}))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = _store().list_jobs(args.status)
    print(json.dumps(rows, indent=2))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    # --from-file is the recommended path for fit_rationale/outreach_draft:
    # multi-sentence text with quotes/apostrophes is fragile to pass as a
    # shell argument, but trivial to write to a JSON file first.
    fields = json.loads(Path(args.from_file).read_text(encoding="utf-8")) if args.from_file else {}
    if args.status:
        fields["status"] = args.status
    if args.fit_score is not None:
        fields["fit_score"] = args.fit_score
    if args.fit_rationale is not None:
        fields["fit_rationale"] = args.fit_rationale
    if args.outreach_draft is not None:
        fields["outreach_draft"] = args.outreach_draft
    if args.digest_batch_date is not None:
        fields["digest_batch_date"] = args.digest_batch_date
    if not fields:
        print("[db.py] update: nothing to update, pass at least one field", file=sys.stderr)
        return 1
    _store().update_job(args.id, fields)
    return 0


def cmd_log_run(args: argparse.Namespace) -> int:
    _store().log_run(args.type, jobs_found=args.found, jobs_new=args.new, error=args.error or "")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("insert-new")
    p.add_argument("--jobs-file", required=True)
    p.set_defaults(func=cmd_insert_new)

    p = sub.add_parser("list")
    p.add_argument("--status", required=True)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("update")
    p.add_argument("--id", required=True, type=int)
    p.add_argument("--status")
    p.add_argument("--fit-score", type=float)
    p.add_argument("--fit-rationale")
    p.add_argument("--outreach-draft")
    p.add_argument("--digest-batch-date")
    p.add_argument("--from-file", help="JSON file of {field: value} — safest way to pass long text")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("log-run")
    p.add_argument("--type", required=True, choices=["collect", "digest"])
    p.add_argument("--found", type=int, default=0)
    p.add_argument("--new", type=int, default=0)
    p.add_argument("--error", default="")
    p.set_defaults(func=cmd_log_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
