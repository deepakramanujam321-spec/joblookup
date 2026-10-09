#!/usr/bin/env python3
"""Stores the collected listings and scores everything that needs it.

Steps (one recorded `collect` run, visible in the dashboard's pipeline
health -- including when a step fails):
  1. ingest: normalize + dedupe + store (src/jobseeker/ingest.py)
  2. enrich: official ATS API data for rows that don't have it yet, which
     also verifies they're still open (src/jobseeker/verification.py)
  3. score: explainable fit breakdown for every new/changed job, with the
     one paid step -- the semantic LLM read -- budgeted per run
     (src/jobseeker/pipeline.py), drafting a note for strong matches

Provider-agnostic via LiteLLM: set ONE provider key (OPENAI_API_KEY, ...)
or LLM_MODEL explicitly. Without any key, scoring still runs on the
deterministic signals alone.

Usage:
    python scripts/run_collect.py --output jobs.json
    python scripts/score_and_draft.py --jobs-file jobs.json

Requires DATABASE_URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import ingest, pipeline, runs, verification  # noqa: E402
from jobseeker.database import get_engine  # noqa: E402
from jobseeker.models import JobListing  # noqa: E402


def load_collect_output(path: str) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):  # v1 collect output: a bare list of listings
        raw = {"listings": raw, "source_stats": {}}
    raw["listings"] = [JobListing.from_dict(row) for row in raw.get("listings", [])]
    return raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs-file", required=True, help="JSON output of run_collect.py")
    parser.add_argument("--llm-budget", type=int, default=pipeline.DEFAULT_LLM_BUDGET)
    args = parser.parse_args()

    engine = get_engine()
    collected = load_collect_output(args.jobs_file)
    with runs.recorded(engine, "collect") as rec:
        for name, stat in (collected.get("source_stats") or {}).items():
            rec.source(name, stat.get("found", 0), stat.get("error"))

        with engine.begin() as conn:
            stats = ingest.upsert_listings(conn, collected["listings"])
        rec.values.update(
            jobs_found=collected.get("raw_count", stats.found),
            jobs_new=stats.new,
            jobs_duplicate=stats.seen_again + stats.merged_duplicates + collected.get("in_run_duplicates", 0),
            jobs_rejected=stats.rejected + collected.get("filtered_out", 0),
        )
        print(f"[score_and_draft] ingest: {stats}", file=sys.stderr)

        enrich_stats = verification.enrich_pending(engine)
        print(f"[score_and_draft] enrich: {enrich_stats}", file=sys.stderr)

        score_stats = pipeline.score_pending(engine, llm_budget=args.llm_budget)
        rec.values["jobs_updated"] = score_stats["candidates"]
        print(f"[score_and_draft] score: {score_stats}", file=sys.stderr)
        rec.details = {
            "ingest": {"new": stats.new, "seen_again": stats.seen_again, "merged_duplicates": stats.merged_duplicates,
                       "rejected": stats.rejected, "rejected_reasons": stats.rejected_reasons},
            "enrich": enrich_stats, "score": score_stats,
        }
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
