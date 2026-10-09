#!/usr/bin/env python3
"""Backfill / re-processing: brings every stored job up to the current
enrichment and scoring versions (official ATS data, posting dates with
evidence, skills, salary, listing-quality checks), then scores whatever
changed. Safe to run repeatedly -- unchanged rows are skipped and cached
assessments are reused.

Usage: python scripts/run_enrich.py [--limit 500] [--llm-budget 120]
Requires DATABASE_URL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import pipeline, runs, verification  # noqa: E402
from jobseeker.database import get_engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--llm-budget", type=int, default=pipeline.DEFAULT_LLM_BUDGET)
    args = parser.parse_args()
    engine = get_engine()
    with runs.recorded(engine, "enrich") as rec:
        enrich_stats = verification.enrich_pending(engine, limit=args.limit)
        score_stats = pipeline.score_pending(engine, llm_budget=args.llm_budget, limit=args.limit)
        rec.values.update(jobs_found=enrich_stats["processed"], jobs_updated=score_stats["candidates"])
        rec.details = {"enrich": enrich_stats, "score": score_stats}
        print(f"[run_enrich] enrich={enrich_stats} score={score_stats}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
