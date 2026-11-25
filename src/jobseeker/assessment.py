"""Scores jobs against the candidate and persists cached, versioned
assessments.

Flow per job: deterministic components (matching.py) are always computed;
the paid semantic call runs only when the posting is a plausible match
(right role family, location not plainly incompatible) and only when the
inputs changed since the last assessment -- `input_hash` covers the job
text, profile version, resume, scoring version and model, so re-running
the pipeline on unchanged data costs nothing.

The LLM never has the last word on facts: skills it claims the candidate
has are kept only if they appear in the candidate's own evidence (profile
+ resume); anything else is dropped before storage.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from . import learning, llm, matching, priority
from .database import job_assessments, jobs, learned_preferences

SEMANTIC_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_job_assessment",
        "description": "Submit the fit assessment for this job posting.",
        "parameters": {
            "type": "object",
            "properties": {
                "fit_score": {"type": "number", "description": "0-100 fit for THIS candidate and THIS posting."},
                "rationale": {"type": "string", "description": "2 sentences, specific to this posting; no boilerplate."},
                "matched_requirements": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Up to 5 requirements of the posting the resume demonstrably meets, each phrased as "
                                   "'<requirement> — <resume evidence>'. Only include ones with explicit resume evidence.",
                },
                "missing_requirements": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Up to 4 important requirements the resume does NOT show evidence for.",
                },
                "outreach_draft": {
                    "type": ["string", "null"],
                    "description": "If fit_score >= 70: a 3-5 sentence note in the candidate's voice, anchored on one real "
                                   "achievement from the resume relevant to this posting, ending with a clear ask. "
                                   "Otherwise null. Never invent experience, metrics or skills.",
                },
            },
            "required": ["fit_score", "rationale", "matched_requirements", "missing_requirements", "outreach_draft"],
        },
    },
}

DRAFT_THRESHOLD = 70


def _job_text(job: dict) -> str:
    return job.get("description") or ""


def input_hash(job: dict, profile_version: int, resume_text: str, model: str | None) -> str:
    payload = json.dumps(
        {
            "v": matching.SCORING_VERSION, "p": profile_version, "m": model,
            "r": hashlib.sha256(resume_text.encode()).hexdigest(),
            "j": [job.get("title"), job.get("company"), job.get("location"), job.get("remote_type"),
                  job.get("salary_min"), job.get("salary_max"), job.get("seniority"), _job_text(job)[:8000]],
        },
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def build_prompt(job: dict, profile: dict, resume_text: str, additional_info: str) -> str:
    locations = ", ".join(p["label"] for p in profile.get("locations", [])) or "not specified"
    comp = profile.get("compensation") or {}
    floor = f"{comp.get('min_annual'):,.0f} {comp.get('currency', '')}/year" if comp.get("min_annual") else "not specified"
    return f"""Assess how well this job posting fits this candidate.

CANDIDATE RESUME (the only source of truth about the candidate's experience):
{resume_text[:7000]}

CANDIDATE PREFERENCES:
- Target titles: {", ".join(profile.get("preferred_titles", [])) or "not specified"}
- Location preference, most to least preferred: {locations}
- Minimum compensation: {floor} (only penalise if the posting states a lower figure)
- Target seniority: {", ".join(profile.get("seniority_levels", [])) or "not specified"}
- Additional notes from the candidate: {additional_info.strip()[:1500] or "none"}

JOB POSTING METADATA:
Title: {job.get("title")}
Company: {job.get("company")}
Location: {job.get("location") or "not stated"} | Remote type: {job.get("remote_type") or "not stated"}

{llm.untrusted("job_description", _job_text(job), 6000)}

Rules: score from the candidate's ACTUAL demonstrated experience; never credit skills the resume
doesn't show; weigh location by the stated priority order; apply the salary floor only to stated
figures. Call submit_job_assessment."""


def semantic_assessment(job: dict, profile: dict, resume_text: str, additional_info: str, evidence: str, model: str) -> tuple[dict, str | None]:
    raw, used_model = llm.call_tool(build_prompt(job, profile, resume_text, additional_info), SEMANTIC_TOOL, 1200, model)
    score = max(0.0, min(100.0, float(raw.get("fit_score", 0))))
    matched = [m for m in raw.get("matched_requirements") or [] if isinstance(m, str)]
    # Keep only strengths whose evidence half is grounded in the candidate's own text.
    strengths = [m for m in matched if llm.grounded(m.split("—")[-1], evidence)]
    gaps = [f"Not evidenced in your resume: {g}" for g in (raw.get("missing_requirements") or []) if isinstance(g, str)][:4]
    draft = raw.get("outreach_draft") if score >= DRAFT_THRESHOLD else None
    return {
        "score": score, "rationale": raw.get("rationale") or "", "strengths": strengths[:5], "gaps": gaps,
        "dropped_unsupported": len(matched) - len(strengths), "model": used_model,
    }, draft


def should_run_semantic(job: dict, profile: dict, deterministic: dict, review_all: bool = False) -> bool:
    """Cost gate for the AI read. `review_all` drops the fit-based skips
    (deal breakers, role, location) for a one-off full review; closed
    postings and non-listings are never worth a call."""
    if job.get("listing_quality") == "not_a_listing" or job.get("verification_status") == "closed":
        return False
    if review_all:
        return True
    if deterministic["deal_breakers"]:
        return False
    role = deterministic["components"]["role"]["score"]
    if role is not None and role < 30:
        return False
    return matching.location_compatible(job, profile)


def load_learned(conn: Connection, owner: str) -> list[dict]:
    rows = conn.execute(
        sa.select(learned_preferences).where(learned_preferences.c.owner == owner, learned_preferences.c.active.is_(True))
    ).mappings().all()
    return [dict(r) for r in rows]


def compute_priority(job: dict, fit: float | None, profile: dict, candidate_skills: set[str], learned: list[dict], now: datetime) -> tuple[float, dict]:
    """Pure computation, no DB -- split out of persist_priority so a batch
    re-rank (preferences.rerank) can compute every job in Python first and
    write them all in one round trip, instead of one UPDATE per job."""
    signals = learning.ranking_signals(learning.job_signals(job, candidate_skills))
    stale_days = (profile.get("thresholds") or {}).get("stale_days", 14)
    score, explanation = priority.compute(job, fit, signals, learned, stale_days, now)
    return score, json.loads(json.dumps(explanation, default=str))


def persist_priority(conn: Connection, job: dict, fit: float | None, profile: dict, candidate_skills: set[str], learned: list[dict], now: datetime) -> None:
    score, explanation = compute_priority(job, fit, profile, candidate_skills, learned, now)
    conn.execute(
        sa.update(jobs).where(jobs.c.id == job["id"]).values(
            priority_score=score, priority_explanation=explanation
        )
    )


def assess_job(conn: Connection, job: dict, owner: str, profile_record: dict, resume_text: str, evidence: str,
               model: str | None, allow_llm: bool, learned: list[dict], now: datetime | None = None,
               review_all: bool = False) -> dict:
    """Returns {"assessment", "draft", "llm_called", "cached", "llm_error"}."""
    now = now or datetime.now(timezone.utc)
    profile = profile_record["data"]
    deterministic = matching.assess(job, profile, resume_text, None)
    run_llm = allow_llm and model is not None and should_run_semantic(job, profile, deterministic, review_all)
    previous = None if run_llm else previous_semantic(conn, job["id"], owner)
    key = input_hash(job, profile_record["version"], resume_text, model if run_llm else (previous or {}).get("key"))

    cached = conn.execute(
        sa.select(job_assessments).where(
            job_assessments.c.job_id == job["id"], job_assessments.c.owner == owner, job_assessments.c.input_hash == key
        )
    ).mappings().first()
    if cached and run_llm and cached["llm_score"] is None:
        cached = None  # an earlier AI attempt failed under this key: retry, don't trust it
    draft = None
    llm_called = False
    llm_error = None
    if cached:
        result = {"overall_score": float(cached["overall_score"]), "components": cached["components"],
                  "strengths": cached["strengths"], "gaps": cached["gaps"], "rationale": cached["rationale"]}
    else:
        semantic = previous
        if run_llm:
            try:
                semantic, draft = semantic_assessment(job, profile, resume_text, profile_record["additional_info"], evidence, model)
                llm_called = True
            except Exception as e:  # a model hiccup degrades to deterministic-only, never loses the job
                llm_error = f"{type(e).__name__}: {e}"[:300]
                print(f"[assessment] semantic call failed for job {job['id']}: {llm_error}", file=sys.stderr)
        result = matching.assess(job, profile, resume_text, semantic)
        rationale = (semantic or {}).get("rationale") or _deterministic_rationale(result)
        result["rationale"] = rationale
        if llm_error is None:  # never cache a failed AI attempt, or later runs would skip the retry
            row = pg_insert(job_assessments).values(
                job_id=job["id"], owner=owner, scoring_version=matching.SCORING_VERSION,
                profile_version=profile_record["version"], input_hash=key,
                model=(semantic or {}).get("model"), overall_score=result["overall_score"],
                llm_score=(semantic or {}).get("score"), components=result["components"],
                strengths=result["strengths"], gaps=result["gaps"], rationale=rationale,
            )
            refreshed = ("model", "overall_score", "llm_score", "components", "strengths", "gaps", "rationale")
            conn.execute(row.on_conflict_do_update(  # replaces a row left by a failed attempt before this fix
                constraint="job_assessments_cache_key", set_={c: row.excluded[c] for c in refreshed}))

    highlights = build_highlights(
        job, profile, result, scoring_inputs(profile_record["version"], resume_text),
        # True only when the AI read reflects *current* inputs (fresh call, or
        # a cache hit on the full input hash). A carried-over read from an
        # older resume/profile counts as False, so the pipeline refreshes it.
        semantic=llm_called or (bool(cached) and run_llm and cached["llm_score"] is not None),
    )
    conn.execute(
        sa.update(jobs).where(jobs.c.id == job["id"]).values(
            fit_score=result["overall_score"], fit_rationale=result["rationale"], match_highlights=highlights,
            scoring_version=matching.SCORING_VERSION, updated_at=now,
        )
    )
    candidate_skills = matching.candidate_skill_set(profile, resume_text)
    persist_priority(conn, job, result["overall_score"], profile, candidate_skills, learned, now)
    return {"assessment": result, "draft": draft, "llm_called": llm_called, "cached": bool(cached), "llm_error": llm_error}


def build_highlights(job: dict, profile: dict, result: dict, inputs: dict, semantic: bool) -> dict:
    """The `jobs.match_highlights` payload for a freshly assessed job."""
    return {
        "matched_skills": result["components"]["skills"]["matches"][:6],
        "gaps": result["gaps"][:3],
        "confidence": result.get("confidence"),
        "semantic": semantic,
        "salary": salary_status(job, result["components"]["compensation"]),
        "experience": matching.experience_gap(job, profile),
        # Excluded company / deal-breaker phrase / experience gap: hard-filtered
        # from every browsing view, not just scored down. Salary is never part
        # of this -- it stays visible with just the badge above.
        "excluded": matching.hard_exclude(job, profile),
        "inputs": inputs,
    }


def scoring_inputs(profile_version: int, resume_text: str) -> dict:
    """What a score was computed against; a change marks the score stale."""
    return {
        "profile_version": profile_version,
        "resume_sha": hashlib.sha256(resume_text.encode()).hexdigest()[:16],
        "scoring_version": matching.SCORING_VERSION,
    }


def salary_status(job: dict, compensation: dict) -> str:
    """not_stated | meets | below | stated (stated but not comparable, e.g.
    no minimum set or an unconvertible currency). Shown on every job so it's
    obvious at a glance whether pay is known and whether it clears the floor."""
    if job.get("salary_min") is None and job.get("salary_max") is None and not job.get("salary_text"):
        return "not_stated"
    if compensation.get("score") == 100:
        return "meets"
    if compensation.get("score") == 15:
        return "below"
    return "stated"


_PREVIOUS_COLUMNS = (job_assessments.c.id, job_assessments.c.job_id, job_assessments.c.llm_score, job_assessments.c.components,
                     job_assessments.c.rationale, job_assessments.c.model, job_assessments.c.profile_version)


def _reused_semantic(row) -> dict:
    sem = (row.components or {}).get("semantic") or {}
    return {
        "score": float(row.llm_score), "rationale": sem.get("detail") or row.rationale or "",
        "strengths": sem.get("matches") or [], "gaps": sem.get("gaps") or [], "model": row.model,
        "key": f"reuse:{row.id}", "reused_from_profile_version": row.profile_version,
    }


def previous_semantic(conn: Connection, job_id: int, owner: str) -> dict | None:
    """The most recent AI read of this job, reused when re-scoring without
    a model call (profile edits, feedback re-ranks, exhausted budget) so a
    deterministic re-score never silently discards it. Labelled as reused."""
    row = conn.execute(
        sa.select(*_PREVIOUS_COLUMNS)
        .where(job_assessments.c.job_id == job_id, job_assessments.c.owner == owner, job_assessments.c.llm_score.is_not(None))
        .order_by(job_assessments.c.created_at.desc()).limit(1)
    ).first()
    return None if row is None else _reused_semantic(row)


# Rows per statement for the bulk paths below: keeps each statement well under
# the driver's 65535 bind-parameter limit however many jobs a person has.
BULK_CHUNK = 500


def _chunks(items: list, size: int = BULK_CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def previous_semantics(conn: Connection, job_ids: list[int], owner: str) -> dict[int, dict]:
    """`previous_semantic` for many jobs in one query per chunk."""
    out: dict[int, dict] = {}
    for chunk in _chunks(job_ids):
        rows = conn.execute(
            sa.select(*_PREVIOUS_COLUMNS)
            .where(job_assessments.c.job_id.in_(chunk), job_assessments.c.owner == owner, job_assessments.c.llm_score.is_not(None))
            .distinct(job_assessments.c.job_id)
            .order_by(job_assessments.c.job_id, job_assessments.c.created_at.desc(), job_assessments.c.id.desc())
        )
        out.update({r.job_id: _reused_semantic(r) for r in rows})
    return out


def _case_by_job(updates: list[dict], column: str, jsonb: bool = False):
    # type_coerce (not json.dumps) for JSONB: the bind processor already
    # serializes, so dumping first would store a JSON string, not an object.
    return sa.case(*((jobs.c.id == u["id"], sa.type_coerce(u[column], JSONB) if jsonb else u[column]) for u in updates))


def rescore_jobs(conn: Connection, jobs_to_score: list[dict], owner: str, profile_record: dict, resume_text: str,
                 learned: list[dict], now: datetime | None = None) -> int:
    """`assess_job(..., model=None, allow_llm=False)` for many jobs with a
    fixed number of round trips instead of ~5 per job: read what's needed
    (previous AI reads, cached assessments) for all jobs at once, compute
    everything in Python, then write new assessments and the job rows in
    bulk. Same results as looping assess_job. Over the pooler from Render a
    profile save that re-scored hundreds of jobs one at a time took tens of
    seconds."""
    if not jobs_to_score:
        return 0
    now = now or datetime.now(timezone.utc)
    profile, version = profile_record["data"], profile_record["version"]
    ids = [j["id"] for j in jobs_to_score]
    previous = previous_semantics(conn, ids, owner)
    keys = {j["id"]: input_hash(j, version, resume_text, (previous.get(j["id"]) or {}).get("key")) for j in jobs_to_score}
    cached: dict[tuple[int, str], dict] = {}
    for chunk in _chunks(ids):
        for row in conn.execute(sa.select(job_assessments).where(
                job_assessments.c.owner == owner, job_assessments.c.job_id.in_(chunk),
                job_assessments.c.input_hash.in_({keys[i] for i in chunk}))).mappings():
            cached[(row["job_id"], row["input_hash"])] = dict(row)

    candidate_skills = matching.candidate_skill_set(profile, resume_text)
    inputs = scoring_inputs(version, resume_text)
    new_assessments: list[dict] = []
    updates: list[dict] = []
    for job in jobs_to_score:
        hit = cached.get((job["id"], keys[job["id"]]))
        if hit:
            result = {"overall_score": float(hit["overall_score"]), "components": hit["components"],
                      "strengths": hit["strengths"], "gaps": hit["gaps"], "rationale": hit["rationale"]}
        else:
            semantic = previous.get(job["id"])
            result = matching.assess(job, profile, resume_text, semantic)
            result["rationale"] = (semantic or {}).get("rationale") or _deterministic_rationale(result)
            new_assessments.append({
                "job_id": job["id"], "owner": owner, "scoring_version": matching.SCORING_VERSION, "profile_version": version,
                "input_hash": keys[job["id"]], "model": (semantic or {}).get("model"), "overall_score": result["overall_score"],
                "llm_score": (semantic or {}).get("score"), "components": result["components"], "strengths": result["strengths"],
                "gaps": result["gaps"], "rationale": result["rationale"],
            })
        priority_score, explanation = compute_priority(job, result["overall_score"], profile, candidate_skills, learned, now)
        updates.append({
            "id": job["id"], "fit_score": result["overall_score"], "fit_rationale": result["rationale"],
            "match_highlights": build_highlights(job, profile, result, inputs, semantic=False),
            "priority_score": priority_score, "priority_explanation": explanation,
        })

    refreshed = ("model", "overall_score", "llm_score", "components", "strengths", "gaps", "rationale")
    for chunk in _chunks(new_assessments):
        stmt = pg_insert(job_assessments).values(chunk)
        conn.execute(stmt.on_conflict_do_update(  # same upsert as assess_job; (job, owner, hash) is unique per job here
            constraint="job_assessments_cache_key", set_={c: stmt.excluded[c] for c in refreshed}))
    for chunk in _chunks(updates):
        # One CASE per column, one UPDATE per chunk.
        conn.execute(sa.update(jobs).where(jobs.c.id.in_([u["id"] for u in chunk])).values(
            fit_score=_case_by_job(chunk, "fit_score"), fit_rationale=_case_by_job(chunk, "fit_rationale"),
            match_highlights=_case_by_job(chunk, "match_highlights", jsonb=True),
            priority_score=_case_by_job(chunk, "priority_score"),
            priority_explanation=_case_by_job(chunk, "priority_explanation", jsonb=True),
            scoring_version=matching.SCORING_VERSION, updated_at=now))
    return len(jobs_to_score)


def _deterministic_rationale(result: dict) -> str:
    parts = []
    if result["strengths"]:
        parts.append(result["strengths"][0])
    if result["gaps"]:
        parts.append(result["gaps"][0])
    note = "Scored from structured signals only (no AI analysis for this posting)."
    sentences = [p.rstrip(".") + "." for p in parts]
    return " ".join(sentences + [note])
