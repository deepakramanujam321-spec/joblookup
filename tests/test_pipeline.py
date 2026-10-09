import sqlalchemy as sa

from jobseeker import llm, pipeline
from jobseeker.database import application_drafts, job_assessments, jobs


def fake_llm(calls):
    def _call(prompt, tool, max_tokens=1500, model=None):
        calls.append(prompt)
        assert "<untrusted_document" in prompt  # job text always delimited as data
        return {
            "fit_score": 88, "rationale": "Strong Python/FastAPI overlap.",
            "matched_requirements": ["Python services — Python/FastAPI", "Kubernetes operator authoring — wrote 12 operators"],
            "missing_requirements": ["Kubernetes"], "outreach_draft": "Hello, I build Python services.",
        }, "openai/gpt-4o-mini"
    return _call


def test_scoring_budget_cache_and_grounding(engine, make_job, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = []
    monkeypatch.setattr(llm, "call_tool", fake_llm(calls))
    good = [make_job(url=f"https://jobs.lever.co/acme/{i}") for i in range(3)]
    offsite = make_job(url="https://jobs.lever.co/acme/sf", location="San Francisco, CA", remote_type="onsite")

    stats = pipeline.score_pending(engine, llm_budget=2)
    assert stats["llm_calls"] == 2 and len(calls) == 2  # budget respected
    with engine.connect() as conn:
        rows = {r["id"]: r for r in conn.execute(sa.select(jobs)).mappings()}
        assert all(rows[i]["fit_score"] is not None for i in good + [offsite])
        assert rows[offsite]["match_highlights"]["semantic"] is False  # location-incompatible: no paid call
        assessed = conn.execute(sa.select(job_assessments).where(job_assessments.c.llm_score.is_not(None))).mappings().first()
        strengths = assessed["components"]["semantic"]["matches"]
        assert strengths == ["Python services — Python/FastAPI"]  # ungrounded Kubernetes claim dropped
        assert conn.execute(sa.select(sa.func.count()).select_from(application_drafts)).scalar() == 2

    stats = pipeline.score_pending(engine, llm_budget=5)
    assert stats["llm_calls"] == 1  # only the one that missed the budget; others cached
    stats = pipeline.score_pending(engine, llm_budget=5)
    assert stats["llm_calls"] == 0


def test_rescore_keeps_previous_ai_read(engine, make_job, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "call_tool", fake_llm([]))
    job_id = make_job()
    pipeline.score_pending(engine)
    with engine.begin() as conn:
        before = conn.execute(sa.select(jobs.c.fit_score).where(jobs.c.id == job_id)).scalar()
        pipeline.rescore_all(conn)
        after = conn.execute(sa.select(jobs.c.fit_score, jobs.c.match_highlights).where(jobs.c.id == job_id)).one()
    assert after.fit_score == before and after.match_highlights["semantic"] is True


def test_deterministic_only_without_provider(engine, make_job):
    make_job()
    stats = pipeline.score_pending(engine)
    assert stats["model"] is None and stats["llm_calls"] == 0
    with engine.connect() as conn:
        row = conn.execute(sa.select(jobs.c.fit_score, jobs.c.fit_rationale, jobs.c.status)).one()
    assert row.fit_score is not None and "structured signals only" in row.fit_rationale
    assert row.status == "scored"  # never queued for the digest without an AI read
