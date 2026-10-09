"""Structured feedback and the "What we learned" insights."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException

from auth import CurrentUser
from deps import engine
from jobseeker import matching, preferences, profile as profile_mod
from jobseeker.database import candidate_profiles, job_feedback, jobs, learned_preferences
from jobseeker.learning import FEEDBACK_EFFECTS, MIN_EVIDENCE
from schemas import FeedbackCreate, PreferenceUpdate

router = APIRouter(prefix="/api/v2", tags=["feedback"])

FEEDBACK_LABELS = {
    "excellent_match": "Excellent match", "relevant_not_priority": "Relevant, but not a priority",
    "wrong_seniority": "Wrong seniority", "wrong_stack": "Wrong technology stack", "wrong_category": "Wrong job category",
    "location_mismatch": "Location mismatch", "salary_mismatch": "Salary mismatch",
    "experience_mismatch": "Too much / too little experience required", "company_mismatch": "Company preference mismatch",
    "duplicate": "Duplicate listing", "inaccurate_listing": "Expired or inaccurate listing",
    "not_interested": "Not interested", "other": "Other",
}


@router.get("/feedback/categories")
def categories(user: CurrentUser):
    return [{"value": k, "label": FEEDBACK_LABELS[k], "teaches": bool(v)} for k, v in FEEDBACK_EFFECTS.items()]


@router.post("/jobs/{job_id}/feedback", status_code=201)
def submit(job_id: int, body: FeedbackCreate, user: CurrentUser):
    with engine().begin() as conn:
        job = conn.execute(sa.select(jobs.c.id, jobs.c.fit_score, jobs.c.priority_score, jobs.c.scoring_version)
                           .where(jobs.c.id == job_id)).first()
        if job is None:
            raise HTTPException(404, "Job not found")
        record = profile_mod.get_or_seed(conn, user.account)
        row = conn.execute(sa.insert(job_feedback).values(
            owner=user.account, job_id=job_id, category=body.category, note=body.note,
            fit_score_at=job.fit_score, priority_at=job.priority_score, scoring_version=job.scoring_version,
            profile_version=record["version"],
        ).returning(job_feedback)).mappings().one()
        before = {(p.dimension, p.value): p.active for p in conn.execute(
            sa.select(learned_preferences).where(learned_preferences.c.owner == user.account))}
        rows = preferences.recompute(conn, user.account)
        changed = [r for r in rows if r["active"] and not before.get((r["dimension"], r["value"]), False)]
        new_priority = conn.execute(sa.select(jobs.c.priority_score).where(jobs.c.id == job_id)).scalar()
        return {"feedback": dict(row), "newly_learned": changed, "priority_now": new_priority}


@router.delete("/feedback/{feedback_id}", status_code=204)
def delete(feedback_id: int, user: CurrentUser):
    with engine().begin() as conn:
        result = conn.execute(sa.delete(job_feedback).where(job_feedback.c.id == feedback_id, job_feedback.c.owner == user.account))
        if result.rowcount == 0:
            raise HTTPException(404, "Feedback not found")
        preferences.recompute(conn, user.account)


@router.get("/insights")
def insights(user: CurrentUser):
    with engine().begin() as conn:
        record = profile_mod.get_or_seed(conn, user.account)
        prefs = [dict(r) for r in conn.execute(
            sa.select(learned_preferences).where(learned_preferences.c.owner == user.account)
            .order_by(sa.func.abs(learned_preferences.c.weight).desc())).mappings()]
        counts = dict(conn.execute(
            sa.select(job_feedback.c.category, sa.func.count()).where(job_feedback.c.owner == user.account)
            .group_by(job_feedback.c.category)).all())
        recent = [dict(r) for r in conn.execute(
            sa.select(job_feedback.c.id, job_feedback.c.job_id, job_feedback.c.category, job_feedback.c.note,
                      job_feedback.c.created_at, jobs.c.title, jobs.c.company)
            .select_from(job_feedback.join(jobs, jobs.c.id == job_feedback.c.job_id))
            .where(job_feedback.c.owner == user.account).order_by(job_feedback.c.created_at.desc()).limit(20)).mappings()]
        evaluation = preferences.evaluation(conn, user.account)
    return {
        "preferences": prefs,
        "active": [p for p in prefs if p["active"]],
        "feedback_counts": {FEEDBACK_LABELS.get(k, k): v for k, v in counts.items()},
        "recent_feedback": recent,
        "evaluation": evaluation,
        "learning_reset_at": record["learning_reset_at"],
        "rules": {"min_evidence": MIN_EVIDENCE, "scoring_version": matching.SCORING_VERSION},
    }


@router.patch("/insights/preferences/{pref_id}")
def toggle(pref_id: int, body: PreferenceUpdate, user: CurrentUser):
    with engine().begin() as conn:
        result = conn.execute(sa.update(learned_preferences).where(
            learned_preferences.c.id == pref_id, learned_preferences.c.owner == user.account,
        ).values(disabled_by_user=body.disabled))
        if result.rowcount == 0:
            raise HTTPException(404, "Preference not found")
        preferences.recompute(conn, user.account)
    return {"ok": True}


@router.post("/insights/reset")
def reset(user: CurrentUser):
    """Learning starts over from now. Past feedback is kept (and still
    visible) but no longer influences ranking."""
    with engine().begin() as conn:
        profile_mod.get_or_seed(conn, user.account)
        conn.execute(sa.update(candidate_profiles).where(candidate_profiles.c.owner == user.account).values(
            learning_reset_at=datetime.now(timezone.utc)))
        conn.execute(sa.delete(learned_preferences).where(learned_preferences.c.owner == user.account))
        preferences.recompute(conn, user.account)
    return {"ok": True}
