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


def should_run_semantic(job: dict, profile: dict, deterministic: dict) -> bool:
    if job.get("listing_quality") == "not_a_listing" or job.get("verification_status") == "closed":
        return False
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


def persist_priority(conn: Connection, job: dict, fit: float | None, profile: dict, candidate_skills: set[str], learned: list[dict], now: datetime) -> None:
    signals = learning.ranking_signals(learning.job_signals(job, candidate_skills))
    stale_days = (profile.get("thresholds") or {}).get("stale_days", 14)
    score, explanation = priority.compute(job, fit, signals, learned, stale_days, now)
    conn.execute(
        sa.update(jobs).where(jobs.c.id == job["id"]).values(
            priority_score=score, priority_explanation=json.loads(json.dumps(explanation, default=str))
        )
    )


def assess_job(conn: Connection, job: dict, owner: str, profile_record: dict, resume_text: str, evidence: str,
               model: str | None, allow_llm: bool, learned: list[dict], now: datetime | None = None) -> dict:
    """Returns {"assessment", "draft", "llm_called", "cached"}."""
    now = now or datetime.now(timezone.utc)
    profile = profile_record["data"]
    deterministic = matching.assess(job, profile, resume_text, None)
    run_llm = allow_llm and model is not None and should_run_semantic(job, profile, deterministic)
    previous = None if run_llm else previous_semantic(conn, job["id"], owner)
    key = input_hash(job, profile_record["version"], resume_text, model if run_llm else (previous or {}).get("key"))

    cached = conn.execute(
        sa.select(job_assessments).where(
            job_assessments.c.job_id == job["id"], job_assessments.c.owner == owner, job_assessments.c.input_hash == key
        )
    ).mappings().first()
    draft = None
    llm_called = False
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
                print(f"[assessment] semantic call failed for job {job['id']}: {e}", file=sys.stderr)
        result = matching.assess(job, profile, resume_text, semantic)
        rationale = (semantic or {}).get("rationale") or _deterministic_rationale(result)
        result["rationale"] = rationale
        conn.execute(
            pg_insert(job_assessments).values(
                job_id=job["id"], owner=owner, scoring_version=matching.SCORING_VERSION,
                profile_version=profile_record["version"], input_hash=key,
                model=(semantic or {}).get("model"), overall_score=result["overall_score"],
                llm_score=(semantic or {}).get("score"), components=result["components"],
                strengths=result["strengths"], gaps=result["gaps"], rationale=rationale,
            ).on_conflict_do_nothing(constraint="job_assessments_cache_key")
        )

    highlights = {
        "matched_skills": result["components"]["skills"]["matches"][:6],
        "gaps": result["gaps"][:3],
        "confidence": result.get("confidence"),
        # False = deterministic-only; the pipeline retries the semantic read later.
        "semantic": result["components"]["semantic"]["score"] is not None,
    }
    conn.execute(
        sa.update(jobs).where(jobs.c.id == job["id"]).values(
            fit_score=result["overall_score"], fit_rationale=result["rationale"], match_highlights=highlights,
            scoring_version=matching.SCORING_VERSION, updated_at=now,
        )
    )
    candidate_skills = matching.candidate_skill_set(profile, resume_text)
    persist_priority(conn, job, result["overall_score"], profile, candidate_skills, learned, now)
    return {"assessment": result, "draft": draft, "llm_called": llm_called, "cached": bool(cached)}


def previous_semantic(conn: Connection, job_id: int, owner: str) -> dict | None:
    """The most recent AI read of this job, reused when re-scoring without
    a model call (profile edits, feedback re-ranks, exhausted budget) so a
    deterministic re-score never silently discards it. Labelled as reused."""
    row = conn.execute(
        sa.select(job_assessments.c.id, job_assessments.c.llm_score, job_assessments.c.components,
                  job_assessments.c.rationale, job_assessments.c.model, job_assessments.c.profile_version)
        .where(job_assessments.c.job_id == job_id, job_assessments.c.owner == owner, job_assessments.c.llm_score.is_not(None))
        .order_by(job_assessments.c.created_at.desc()).limit(1)
    ).first()
    if row is None:
        return None
    sem = (row.components or {}).get("semantic") or {}
    return {
        "score": float(row.llm_score), "rationale": sem.get("detail") or row.rationale or "",
        "strengths": sem.get("matches") or [], "gaps": sem.get("gaps") or [], "model": row.model,
        "key": f"reuse:{row.id}", "reused_from_profile_version": row.profile_version,
    }


def _deterministic_rationale(result: dict) -> str:
    parts = []
    if result["strengths"]:
        parts.append(result["strengths"][0])
    if result["gaps"]:
        parts.append(result["gaps"][0])
    note = "Scored from structured signals only (no AI analysis for this posting)."
    sentences = [p.rstrip(".") + "." for p in parts]
    return " ".join(sentences + [note])
