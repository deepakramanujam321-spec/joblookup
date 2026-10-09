"""Application lifecycle: status changes, saved flag, notes, tasks."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException

from auth import CurrentUser
from deps import engine, thresholds
from jobseeker import lifecycle, preferences, queries
from jobseeker.database import application_drafts, application_tasks, applications, jobs, resumes
from schemas import ApplicationUpdate, TaskCreate, TaskUpdate

router = APIRouter(prefix="/api/v2", tags=["applications"])


def _job_or_404(conn, job_id: int):
    job = conn.execute(sa.select(jobs.c.id, jobs.c.fit_score, jobs.c.priority_score, jobs.c.verification_status,
                                 jobs.c.listing_quality).where(jobs.c.id == job_id)).first()
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _initial_status(conn, account: str, job) -> str:
    t = thresholds(conn, account)
    score = job.priority_score if job.priority_score is not None else job.fit_score
    closed = job.verification_status == "closed" or job.listing_quality == "not_a_listing"
    return lifecycle.implicit_status(float(score) if score is not None else None, t["review"], closed)


@router.put("/jobs/{job_id}/application")
def update_application(job_id: int, body: ApplicationUpdate, user: CurrentUser):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "Nothing to update")
    with engine().begin() as conn:
        job = _job_or_404(conn, job_id)
        initial = _initial_status(conn, user.account, job)
        if fields.get("resume_id") is not None:
            owned = conn.execute(sa.select(resumes.c.id).where(
                resumes.c.id == fields["resume_id"], resumes.c.owner == user.account, resumes.c.deleted_at.is_(None))).first()
            if owned is None:
                raise HTTPException(404, "Resume not found")
        if fields.get("final_draft_id") is not None:
            owned = conn.execute(sa.select(application_drafts.c.id).where(
                application_drafts.c.id == fields["final_draft_id"], application_drafts.c.owner == user.account,
                application_drafts.c.job_id == job_id)).first()
            if owned is None:
                raise HTTPException(404, "Draft not found for this job")
        status = fields.pop("status", None)
        note = fields.pop("note", None)
        if status is not None:
            try:
                lifecycle.transition(conn, user.account, job_id, status, initial, note)
            except lifecycle.TransitionError as e:
                raise HTTPException(409, str(e)) from e
        app = lifecycle.ensure(conn, user.account, job_id, initial)
        if fields:
            conn.execute(sa.update(applications).where(applications.c.id == app.id).values(
                **fields, updated_at=datetime.now(timezone.utc)))
        if status is not None:
            preferences.recompute(conn, user.account)  # application progress is a learning signal
        t = thresholds(conn, user.account)
        return queries.job_detail(conn, user.account, job_id, t["review"])


@router.get("/applications")
def board(user: CurrentUser):
    with engine().begin() as conn:
        return {"items": queries.application_board(conn, user.account), "statuses": list(lifecycle.STATUSES)}


def _owned_application(conn, account: str, job_id: int):
    app = conn.execute(sa.select(applications).where(applications.c.owner == account, applications.c.job_id == job_id)).first()
    if app is None:
        raise HTTPException(404, "No application for this job yet")
    return app


@router.post("/jobs/{job_id}/tasks", status_code=201)
def create_task(job_id: int, body: TaskCreate, user: CurrentUser):
    with engine().begin() as conn:
        job = _job_or_404(conn, job_id)
        app = lifecycle.ensure(conn, user.account, job_id, _initial_status(conn, user.account, job))
        row = conn.execute(sa.insert(application_tasks).values(application_id=app.id, **body.model_dump())
                           .returning(application_tasks)).mappings().one()
        if body.kind == "follow_up" and body.due_at:
            conn.execute(sa.update(applications).where(applications.c.id == app.id).values(next_follow_up_at=body.due_at))
        lifecycle.record_event(conn, app.id, "task_added", f"{body.kind}: {body.title}")
        return dict(row)


def _owned_task(conn, account: str, task_id: int):
    row = conn.execute(
        sa.select(application_tasks).select_from(application_tasks.join(applications, applications.c.id == application_tasks.c.application_id))
        .where(application_tasks.c.id == task_id, applications.c.owner == account)
    ).first()
    if row is None:
        raise HTTPException(404, "Task not found")
    return row


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate, user: CurrentUser):
    with engine().begin() as conn:
        _owned_task(conn, user.account, task_id)
        fields = body.model_dump(exclude_unset=True)
        done = fields.pop("done", None)
        if done is not None:
            fields["done_at"] = datetime.now(timezone.utc) if done else None
        if fields:
            conn.execute(sa.update(application_tasks).where(application_tasks.c.id == task_id).values(**fields))
        return dict(conn.execute(sa.select(application_tasks).where(application_tasks.c.id == task_id)).mappings().one())


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, user: CurrentUser):
    with engine().begin() as conn:
        _owned_task(conn, user.account, task_id)
        conn.execute(sa.delete(application_tasks).where(application_tasks.c.id == task_id))
