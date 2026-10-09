import io
import zipfile

import pytest
import sqlalchemy as sa

from jobseeker import google, llm, pipeline
from jobseeker.database import application_events, applications


def test_auth_required(client):
    assert client.get("/api/v2/jobs", auth=None).status_code == 401
    assert client.get("/api/v2/jobs", auth=("tom", "wrong")).status_code == 401
    assert client.get("/api/health", auth=None).json()["database"] == "ok"


def _seed(make_job, engine):
    ids = [
        make_job(url="https://jobs.lever.co/a/1", title="Senior Backend Engineer", company="Acme"),
        make_job(url="https://jobs.lever.co/a/2", title="AI Platform Engineer", company="Bolt", description="LLM platform in Python and Kafka"),
        make_job(url="https://jobs.lever.co/a/3", title="Customer Support Engineer", company="Cogs", location="Austin, TX", remote_type="onsite"),
    ]
    pipeline.score_pending(engine)
    return ids


def test_list_filter_sort_paginate(client, make_job, engine):
    ids = _seed(make_job, engine)
    body = client.get("/api/v2/jobs", params={"view": "all", "page_size": 2}).json()
    assert body["total"] == 3 and len(body["items"]) == 2 and body["page"] == 1
    assert "description" not in body["items"][0]  # compact rows only
    page2 = client.get("/api/v2/jobs", params={"view": "all", "page_size": 2, "page": 2}).json()
    assert len(page2["items"]) == 1 and page2["total"] == 3
    scores = [i["priority_score"] for i in client.get("/api/v2/jobs", params={"view": "all"}).json()["items"]]
    assert scores == sorted(scores, reverse=True)
    assert [i["id"] for i in client.get("/api/v2/jobs", params={"view": "all", "q": "kafka"}).json()["items"]] == [ids[1]]
    assert client.get("/api/v2/jobs", params={"view": "all", "remote_type": "onsite"}).json()["total"] == 1
    assert client.get("/api/v2/jobs", params={"sort": "nonsense"}).status_code == 422
    by_company = [i["company"] for i in client.get("/api/v2/jobs", params={"view": "all", "sort": "company"}).json()["items"]]
    assert by_company == ["Acme", "Bolt", "Cogs"]


def test_detail_and_overview(client, make_job, engine):
    ids = _seed(make_job, engine)
    detail = client.get(f"/api/v2/jobs/{ids[0]}").json()
    assert detail["job"]["url"] == "https://jobs.lever.co/a/1"
    assert detail["job"]["posted_at_evidence"] == "lever_api.createdAt"
    assert detail["assessment"]["components"]["skills"]["label"] == "Skills alignment"
    assert detail["sources"][0]["source"] == "lever"
    assert client.get("/api/v2/jobs/999999").status_code == 404
    overview = client.get("/api/v2/overview").json()
    for key in ("new_this_week", "worth_reviewing", "high_priority", "in_progress", "interviews_scheduled", "awaiting_feedback"):
        assert key in overview


def test_application_lifecycle(client, make_job, engine):
    job_id = make_job()
    r = client.put(f"/api/v2/jobs/{job_id}/application", json={"status": "interview"})
    assert r.status_code == 409 and "applied" in r.json()["detail"]
    assert client.put(f"/api/v2/jobs/{job_id}/application", json={"saved": True}).json()["application"]["saved"] is True
    assert client.put(f"/api/v2/jobs/{job_id}/application", json={"status": "shortlisted"}).status_code == 200
    applied = client.put(f"/api/v2/jobs/{job_id}/application", json={"status": "applied", "note": "via careers page"}).json()
    assert applied["workflow_status"] == "applied" and applied["application"]["applied_at"]
    assert client.put(f"/api/v2/jobs/{job_id}/application", json={"status": "interview"}).status_code == 200
    task = client.post(f"/api/v2/jobs/{job_id}/tasks", json={"kind": "interview", "title": "Tech screen", "due_at": "2099-01-01T10:00:00Z"})
    assert task.status_code == 201
    assert client.get("/api/v2/overview").json()["interviews_scheduled"] == 1
    assert client.patch(f"/api/v2/tasks/{task.json()['id']}", json={"done": True}).json()["done_at"]
    with engine.connect() as conn:
        events = conn.execute(sa.select(application_events.c.to_status).order_by(application_events.c.id)).scalars().all()
    assert events[:3] == ["shortlisted", "applied", "interview"]
    board = client.get("/api/v2/applications").json()["items"]
    assert board[0]["status"] == "interview"
    assert client.put(f"/api/v2/jobs/{job_id}/application", json={"application_url": "javascript:alert(1)"}).status_code == 422


def test_feedback_changes_ranking_after_enough_evidence(client, make_job, engine):
    support = [make_job(url=f"https://jobs.lever.co/s/{i}", title="Support Engineer", company=f"S{i}") for i in range(3)]
    pipeline.score_pending(engine)
    before = client.get(f"/api/v2/jobs/{support[2]}").json()["job"]["priority_score"]
    first = client.post(f"/api/v2/jobs/{support[0]}/feedback", json={"category": "wrong_category"}).json()
    assert first["newly_learned"] == []  # one data point teaches nothing yet
    second = client.post(f"/api/v2/jobs/{support[1]}/feedback", json={"category": "wrong_category"}).json()
    assert [p["value"] for p in second["newly_learned"]] == ["support"]
    after = client.get(f"/api/v2/jobs/{support[2]}").json()["job"]
    assert after["priority_score"] < before
    assert any("support" in r["label"] for r in after["priority_explanation"]["reasons"])
    insights = client.get("/api/v2/insights").json()
    pref = insights["active"][0]
    assert client.patch(f"/api/v2/insights/preferences/{pref['id']}", json={"disabled": True}).status_code == 200
    restored = client.get(f"/api/v2/jobs/{support[2]}").json()["job"]["priority_score"]
    assert restored == before
    assert client.post("/api/v2/insights/reset").status_code == 200
    assert client.get("/api/v2/insights").json()["active"] == []
    assert client.post(f"/api/v2/jobs/{support[0]}/feedback", json={"category": "made_up"}).status_code == 422


def test_drafts_versioning_and_generation(client, make_job, monkeypatch):
    job_id = make_job()
    r = client.post(f"/api/v2/jobs/{job_id}/drafts/generate", json={})
    assert r.status_code == 503  # no provider configured: says so, doesn't fake it
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "call_tool", lambda *a, **k: ({
        "subject": "Backend Engineer application", "email_body": "I build Python services at scale.",
        "cover_letter": None, "qualifications": [
            {"requirement": "Python", "evidence": "Python services"},
            {"requirement": "Rust", "evidence": "Wrote the Rust compiler"}],
        "missing_info": ["Do you have Kubernetes experience?"],
        "answers": [{"question": "Notice period?", "answer": None, "needs_input": True}],
    }, "openai/gpt-4o-mini"))
    gen = client.post(f"/api/v2/jobs/{job_id}/drafts/generate", json={"questions": ["Notice period?"]}).json()
    assert gen["version"] == 1 and gen["origin"] == "generated"
    assert [q["requirement"] for q in gen["qualifications"]] == ["Python"]
    assert any("Rust" in m for m in gen["missing_info"])  # unsupported claim moved to "confirm first"
    assert client.get(f"/api/v2/jobs/{job_id}").json()["workflow_status"] == "draft_ready"
    edit = client.post(f"/api/v2/jobs/{job_id}/drafts", json={"body": "My edited text", "subject": "Hi", "parent_id": gen["id"]}).json()
    assert edit["version"] == 2 and edit["origin"] == "edited"
    same = client.post(f"/api/v2/jobs/{job_id}/drafts", json={"body": "My edited text", "subject": "Hi", "parent_id": edit["id"]}).json()
    assert same["id"] == edit["id"]  # identical edit isn't a new version
    regen = client.post(f"/api/v2/jobs/{job_id}/drafts/generate", json={}).json()
    versions = client.get(f"/api/v2/jobs/{job_id}/drafts").json()["items"]
    assert [v["version"] for v in versions] == [3, 2, 1] and versions[1]["body"] == "My edited text"
    assert regen["version"] == 3
    # Generating never marks anything applied
    assert client.get(f"/api/v2/jobs/{job_id}").json()["application"]["applied_at"] is None


def test_gmail_save_is_idempotent_and_states_distinct(client, make_job, monkeypatch):
    job_id = make_job()
    v1 = client.post(f"/api/v2/jobs/{job_id}/drafts", json={"body": "Version one"}).json()
    r = client.post(f"/api/v2/drafts/{v1['id']}/gmail", json={})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "not_connected"
    assert client.get(f"/api/v2/jobs/{job_id}/drafts").json()["items"][0]["gmail_status"] == "failed"
    calls = []

    def fake_save(conn, owner, subject, body, to, existing):
        calls.append(existing)
        return {"draft_id": existing or "d-1", "message_id": "m-1"}
    monkeypatch.setattr(google, "save_gmail_draft", fake_save)
    saved = client.post(f"/api/v2/drafts/{v1['id']}/gmail", json={}).json()
    assert saved["gmail_status"] == "saved" and "compose=m-1" in saved["gmail_open_url"]
    client.post(f"/api/v2/drafts/{v1['id']}/gmail", json={})
    assert calls == [None]  # same version saved twice -> no second Gmail call
    v2 = client.post(f"/api/v2/jobs/{job_id}/drafts", json={"body": "Version two"}).json()
    client.post(f"/api/v2/drafts/{v2['id']}/gmail", json={})
    assert calls == [None, "d-1"]  # new version updates the existing Gmail draft, no duplicate
    statuses = {d["version"]: d["gmail_status"] for d in client.get(f"/api/v2/jobs/{job_id}/drafts").json()["items"]}
    assert statuses == {2: "saved", 1: "not_saved"}
    detail = client.get(f"/api/v2/jobs/{job_id}").json()
    assert detail["application"] is None or detail["application"]["applied_at"] is None


def _docx(text: str) -> bytes:
    import docx

    d = docx.Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_resume_upload_versions_and_validation(client):
    seeded = client.get("/api/v2/resumes").json()["items"]
    assert seeded and seeded[0]["source"] == "seed" and seeded[0]["is_default"]
    content = _docx("Jane Doe\nBackend engineer with Python, FastAPI and PostgreSQL experience building distributed systems.")
    up = client.post("/api/v2/resumes", files={"file": ("cv.docx", content, "application/octet-stream")},
                     data={"display_name": "Backend CV", "purpose": "backend roles"})
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["extraction_status"] == "done" and body["content_type"].endswith("document") and "extracted_text" not in body
    v2 = client.post("/api/v2/resumes", files={"file": ("cv2.docx", content, "x")}, data={"replaces_id": body["id"]}).json()
    assert v2["version"] == 2 and v2["display_name"] == "Backend CV"
    assert client.get(f"/api/v2/resumes/{body['id']}/file").content == content  # original untouched
    bad = client.post("/api/v2/resumes", files={"file": ("evil.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    assert bad.status_code == 422
    macro = io.BytesIO()
    with zipfile.ZipFile(macro, "w") as zf:
        zf.writestr("word/document.xml", "<w/>")
        zf.writestr("word/vbaProject.bin", "x")
    assert client.post("/api/v2/resumes", files={"file": ("m.docx", macro.getvalue(), "x")}).status_code == 422
    big = b"%PDF-" + b"0" * (5 * 1024 * 1024)
    assert client.post("/api/v2/resumes", files={"file": ("big.pdf", big, "application/pdf")}).status_code == 422
    assert client.patch(f"/api/v2/resumes/{v2['id']}", json={"is_default": True}).json()["is_default"] is True
    assert client.delete(f"/api/v2/resumes/{v2['id']}").status_code == 204
    remaining = client.get("/api/v2/resumes").json()["items"]
    assert v2["id"] not in [r["id"] for r in remaining] and sum(r["is_default"] for r in remaining) == 1


def test_resume_extract_apply_requires_explicit_sections(client, monkeypatch):
    content = _docx("Jane Doe\nBackend engineer. Skills: Python, FastAPI, Terraform. Built payment APIs at Acme 2020-2024.")
    rid = client.post("/api/v2/resumes", files={"file": ("cv.docx", content, "x")}).json()["id"]
    before = client.get("/api/v2/profile").json()
    extracted = client.post(f"/api/v2/resumes/{rid}/extract").json()
    assert extracted["extracted_profile"]["method"] == "vocabulary" and "Terraform" in extracted["extracted_profile"]["skills"]
    assert client.get("/api/v2/profile").json()["version"] == before["version"]  # nothing applied yet
    applied = client.post(f"/api/v2/resumes/{rid}/apply", json={"sections": ["skills"]}).json()
    assert "Terraform" in [s["name"] for s in applied["data"]["skills"]]
    assert applied["data"]["evidence_sources"][0]["resume_id"] == rid


def test_profile_optimistic_concurrency(client):
    current = client.get("/api/v2/profile").json()
    data = current["data"]
    data["seniority_levels"] = ["senior"]
    ok = client.put("/api/v2/profile", json={"data": data, "additional_info": "Prefer product companies", "version": current["version"]})
    assert ok.status_code == 200 and ok.json()["version"] == current["version"] + 1
    stale = client.put("/api/v2/profile", json={"data": data, "version": current["version"]})
    assert stale.status_code == 409


def test_account_scoping(client, make_job, monkeypatch, engine):
    job_id = make_job()
    draft = client.post(f"/api/v2/jobs/{job_id}/drafts", json={"body": "mine"}).json()
    client.put(f"/api/v2/jobs/{job_id}/application", json={"saved": True})
    monkeypatch.setenv("DASHBOARD_ACCOUNT_ID", "someone-else")
    assert client.get(f"/api/v2/jobs/{job_id}/drafts").json()["items"] == []
    assert client.post(f"/api/v2/drafts/{draft['id']}/gmail", json={}).status_code == 404
    assert client.get(f"/api/v2/jobs/{job_id}").json()["application"] is None
    with engine.connect() as conn:
        assert conn.execute(sa.select(applications.c.owner)).scalar() == "default"


def test_integrations_unconfigured_is_explicit(client):
    status = client.get("/api/v2/integrations").json()
    assert status["configured"] is False and status["services"]["gmail"] is None
    r = client.post("/api/v2/integrations/google/gmail/connect")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "not_configured"


def test_legacy_v1_contract(client, make_job, engine):
    job_id = make_job()
    rows = client.get("/api/jobs").json()
    assert isinstance(rows, list) and rows[0]["id"] == job_id and "status" in rows[0]
    updated = client.patch(f"/api/jobs/{job_id}", json={"status": "applied"}).json()
    assert updated["status"] == "applied"
    assert client.get(f"/api/v2/jobs/{job_id}").json()["workflow_status"] == "applied"
    assert client.patch(f"/api/jobs/{job_id}", json={"status": "bogus"}).status_code == 400
    assert isinstance(client.get("/api/stats").json(), dict)
    assert isinstance(client.get("/api/runs").json(), list)


def test_pipeline_health_reports_failures(client, engine):
    from jobseeker import runs

    assert client.get("/api/v2/pipeline").json()["state"] == "unknown"
    with pytest.raises(RuntimeError):
        with runs.recorded(engine, "collect"):
            raise RuntimeError("Brave API quota exceeded")
    health = client.get("/api/v2/pipeline").json()
    assert health["state"] == "failing" and "quota" in health["message"]
    with runs.recorded(engine, "collect") as rec:
        rec.source("remoteok", 0, "HTTP 403")
    assert client.get("/api/v2/pipeline").json()["state"] == "degraded"


def test_spa_deep_links(client, tmp_path, monkeypatch):
    import main

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<div id=root></div>")
    monkeypatch.setattr(main, "STATIC_DIR", dist)
    assert client.get("/jobs/123", auth=None).text == "<div id=root></div>"
    assert client.get("/api/v2/nope").status_code == 404
    assert client.get("/../../etc/passwd", auth=None).text == "<div id=root></div>"
