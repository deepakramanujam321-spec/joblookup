#!/usr/bin/env python3
"""Stores the collected listings and scores everything that needs it, for
each person separately (their own job list, profile, resume, budget).

Steps (one recorded `collect` run per person, visible in the dashboard's
pipeline health -- including when a step fails):
  1. ingest: normalize + dedupe + store each person's listings
     (src/jobseeker/ingest.py), then offer each person's finds to the others:
     only strong matches cross over (src/jobseeker/sharing.py)
  2. enrich: official ATS API data for rows that don't have it yet, which
     also verifies they're still open (src/jobseeker/verification.py)
  3. score: explainable fit breakdown for every new/changed job, with the
     one paid step -- the semantic LLM read -- budgeted per person per run
     (src/jobseeker/pipeline.py), drafting a note for strong matches

Provider-agnostic via LiteLLM: set one or more provider keys (OPENAI_API_KEY,
GEMINI_API_KEY, ...); extra providers are automatic fallbacks. Without any key,
scoring still runs on the deterministic signals alone.

Usage:
    python scripts/run_collect.py --output-dir collected
    python scripts/score_and_draft.py --jobs-dir collected
    python scripts/score_and_draft.py --jobs-file jobs.json [--owner default]   # one person

Requires DATABASE_URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import ingest, pipeline, runs, sharing, verification  # noqa: E402
from jobseeker.database import get_engine  # noqa: E402
from jobseeker.models import JobListing  # noqa: E402
from jobseeker.profile import DEFAULT_OWNER  # noqa: E402


def load_collect_output(path: str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):  # v1 collect output: a bare list of listings
        raw = {"listings": raw, "source_stats": {}}
    raw["listings"] = [JobListing.from_dict(row) for row in raw.get("listings", [])]
    return raw


def load_batches(args) -> dict[str, dict]:
    """owner -> collected payload."""
    if args.jobs_dir:
        batches = {}
        for path in sorted(Path(args.jobs_dir).glob("*.json")):
            batches[path.stem] = load_collect_output(str(path))
        return batches
    return {args.owner: load_collect_output(args.jobs_file)}


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--jobs-file", help="JSON output of run_collect.py for one person")
    source.add_argument("--jobs-dir", help="directory of <owner>.json files from run_collect.py --output-dir")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="whose file --jobs-file is (default: the original account)")
    parser.add_argument("--llm-budget", type=int, default=pipeline.DEFAULT_LLM_BUDGET, help="AI reads per person per run")
    args = parser.parse_args()

    engine = get_engine()
    batches = load_batches(args)
    ingested: dict[str, ingest.IngestStats] = {}
    errors: dict[str, str] = {}
    for owner, collected in batches.items():  # 1. everyone's own finds
        try:
            with engine.begin() as conn:
                ingested[owner] = ingest.upsert_listings(conn, collected["listings"], owner=owner)
            print(f"[score_and_draft] {owner} ingest: {ingested[owner]}", file=sys.stderr)
        except Exception as e:  # one person's failure never blocks the other
            errors[owner] = f"{type(e).__name__}: {e}"
            print(f"[score_and_draft] {owner} ingest failed: {errors[owner]}", file=sys.stderr)

    shared: dict[str, int] = {}
    try:  # strong matches cross over between people
        with engine.begin() as conn:
            shared = sharing.share_high_matches(conn, {o: batches[o]["listings"] for o in ingested})
        if shared:
            print(f"[score_and_draft] strong matches shared across people: {shared}", file=sys.stderr)
    except Exception as e:
        print(f"[score_and_draft] sharing failed (not fatal): {type(e).__name__}: {e}", file=sys.stderr)

    enrich_stats = verification.enrich_pending(engine)  # not per person: it just reads the ATS for each stored job
    print(f"[score_and_draft] enrich: {enrich_stats}", file=sys.stderr)

    exit_code = 0
    for owner in ingested:  # each person scored on their own
        collected, stats = batches[owner], ingested[owner]
        llm_failed = False
        with runs.recorded(engine, "collect", owner) as rec:
            for name, stat in (collected.get("source_stats") or {}).items():
                rec.source(name, stat.get("found", 0), stat.get("error"))
            rec.values.update(
                jobs_found=collected.get("raw_count", stats.found),
                jobs_new=stats.new + shared.get(owner, 0),
                jobs_duplicate=stats.seen_again + stats.merged_duplicates + collected.get("in_run_duplicates", 0),
                jobs_rejected=stats.rejected + collected.get("filtered_out", 0),
            )
            score_stats = pipeline.score_pending(engine, owner, llm_budget=args.llm_budget)
            llm_error, llm_failed = pipeline.llm_health(score_stats)
            if llm_error:
                rec.errors.append(llm_error)  # run shows as degraded on the dashboard
            rec.values["jobs_updated"] = score_stats["candidates"]
            print(f"[score_and_draft] {owner} score: {score_stats}", file=sys.stderr)
            rec.details = {
                "ingest": {"new": stats.new, "seen_again": stats.seen_again, "merged_duplicates": stats.merged_duplicates,
                           "rejected": stats.rejected, "rejected_reasons": stats.rejected_reasons,
                           "shared_in": shared.get(owner, 0)},
                "enrich": enrich_stats, "score": score_stats,
            }
        if llm_failed:
            print(f"::error::{owner}: {llm_error}", file=sys.stderr)
            exit_code = 1  # every AI call failed: fail the job so GitHub notifies
    for owner, message in errors.items():
        print(f"::error::{owner}: ingest failed: {message}", file=sys.stderr)
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
