"""Pipeline steps shared by the scripts in scripts/ (and callable from
tests): score whatever needs scoring, within an LLM budget.

Budgeting: deterministic scoring runs on every candidate first (free);
the paid semantic call then goes to the best-looking eligible candidates
up to MAX_LLM_CALLS_PER_RUN. Jobs that miss the budget keep a
deterministic score and are picked up by later runs -- nothing is lost,
and spend per run is bounded.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from . import assessment, drafting, llm, matching, profile as profile_mod
from .database import jobs

DEFAULT_LLM_BUDGET = int(os.environ.get("MAX_LLM_CALLS_PER_RUN", "60"))
DIGEST_THRESHOLD = assessment.DRAFT_THRESHOLD


def _needs_scoring(inputs: dict):
    no_semantic = sa.func.coalesce(jobs.c.match_highlights["semantic"].as_boolean(), False).is_(False)
    scored_with = jobs.c.match_highlights["inputs"]
    return sa.and_(
        jobs.c.listing_quality == "ok",
        sa.or_(
            jobs.c.status == "new",
            jobs.c.fit_score.is_(None),
            jobs.c.scoring_version.is_distinct_from(matching.SCORING_VERSION),
            # scored against an older resume or profile -> stale
            scored_with["resume_sha"].as_string().is_distinct_from(inputs["resume_sha"]),
            scored_with["profile_version"].as_integer().is_distinct_from(inputs["profile_version"]),
            # deterministic-only rows retry the semantic read while still fresh
            sa.and_(no_semantic, jobs.c.verification_status != "closed",
                    jobs.c.discovered_at > sa.func.now() - sa.text("interval '30 days'")),
        ),
    )


def score_pending(engine: Engine, owner: str = profile_mod.DEFAULT_OWNER, llm_budget: int = DEFAULT_LLM_BUDGET,
                  limit: int = 500) -> dict:
    with engine.begin() as conn:
        record = profile_mod.get_or_seed(conn, owner)
        resume_id, resume = profile_mod.default_resume_text(conn, owner)
        learned = assessment.load_learned(conn, owner)
        inputs = assessment.scoring_inputs(record["version"], resume)
        candidates = [dict(r) for r in conn.execute(
            sa.select(jobs).where(_needs_scoring(inputs)).order_by(jobs.c.id).limit(limit)).mappings()]
    profile = record["data"]
    evidence = profile_mod.evidence_text(profile, resume)
    model = llm.resolve_model() if llm.is_configured() else None
    if model is None:
        print("[pipeline] no LLM provider configured: deterministic scoring only", file=sys.stderr)

    ranked = []
    for job in candidates:
        pre = matching.assess(job, profile, resume, None)
        eligible = assessment.should_run_semantic(job, profile, pre)
        ranked.append((eligible, pre["overall_score"], job))
    ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)

    stats = {"candidates": len(candidates), "llm_calls": 0, "cached": 0, "queued_for_digest": 0, "drafts": 0, "model": model}
    budget = llm_budget
    for eligible, _, job in ranked:
        allow = bool(model) and eligible and budget > 0
        with engine.begin() as conn:
            outcome = assessment.assess_job(conn, job, owner, record, resume, evidence, model, allow, learned)
            if outcome["llm_called"]:
                budget -= 1
                stats["llm_calls"] += 1
            if outcome["cached"]:
                stats["cached"] += 1
            semantic = outcome["assessment"]["components"]["semantic"]["score"] is not None
            fields: dict = {}
            fit = outcome["assessment"]["overall_score"]
            if job["status"] in ("new", "scored", "queued_for_digest"):
                fields["status"] = "queued_for_digest" if fit >= DIGEST_THRESHOLD and semantic else "scored"
                if fields["status"] == "queued_for_digest" and job["status"] != "queued_for_digest":
                    stats["queued_for_digest"] += 1
            if outcome["draft"]:
                drafting.save_version(conn, owner, job["id"], "generated", {
                    "subject": f"Application: {job['title']} at {job['company']}", "body": outcome["draft"],
                }, resume_id=resume_id, profile_version=record["version"], model=model)
                fields["outreach_draft"] = outcome["draft"]
                stats["drafts"] += 1
            if fields:
                conn.execute(sa.update(jobs).where(jobs.c.id == job["id"]).values(**fields, updated_at=datetime.now(timezone.utc)))
    return stats


def rescore_all(conn, owner: str = profile_mod.DEFAULT_OWNER) -> int:
    """Deterministic re-score of every job after a profile change -- no
    model calls; each job's latest AI read is carried over (labelled)."""
    record = profile_mod.get_or_seed(conn, owner)
    _, resume = profile_mod.default_resume_text(conn, owner)
    learned = assessment.load_learned(conn, owner)
    evidence = profile_mod.evidence_text(record["data"], resume)
    count = 0
    for job in conn.execute(sa.select(jobs).where(jobs.c.listing_quality == "ok", jobs.c.fit_score.is_not(None))).mappings().all():
        assessment.assess_job(conn, dict(job), owner, record, resume, evidence, None, False, learned)
        count += 1
    return count
