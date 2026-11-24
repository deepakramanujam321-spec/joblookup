"""Two people, one database and dashboard: each has their own jobs, profile,
resumes and scores. Nothing of one is ever visible to, or changeable by, the
other -- except a job that's a strong match for them."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from conftest import make_listing
from jobseeker import ingest, pipeline, preferences, profile as profile_mod, runs, sharing
from jobseeker.database import candidate_profiles, jobs, resumes
from jobseeker.database import runs as runs_table
from jobseeker.profile import CandidateProfile

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
PRIYA = [{"username": "priya", "password": "pw-priya", "account": "priya"}]


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_job(engine, owner, **overrides) -> int:
    with engine.begin() as conn:
        return ingest.upsert_listings(conn, [make_listing(**overrides)], owner=owner).new_ids[0]


def set_profile(engine, owner, **data):
    with engine.begin() as conn:
        profile_mod.get_or_seed(conn, owner)
        conn.execute(sa.update(candidate_profiles).where(candidate_profiles.c.owner == owner).values(
            data=CandidateProfile(**data).model_dump()))


@pytest.fixture()
def two_clients(client, monkeypatch):
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setenv("DASHBOARD_EXTRA_USERS", json.dumps(PRIYA))
    priya = TestClient(main.app)
    priya.auth = ("priya", "pw-priya")
    return client, priya  # (the original user "tom", priya)


# ------------------------------------------------------------------ isolation

def test_each_login_sees_only_its_own_jobs(two_clients, engine):
    tom, priya = two_clients
    mine = add_job(engine, "default", title="Backend Engineer", url="https://jobs.lever.co/a/mine")
    hers = add_job(engine, "priya", title="Gameplay Programmer", url="https://jobs.lever.co/a/hers")

    def ids(c, view="all"):
        return {j["id"] for j in c.get("/api/v2/jobs", params={"view": view}).json()["items"]}
    assert ids(tom) == {mine} and ids(priya) == {hers}
    assert client_total(tom) == 1 and client_total(priya) == 1
    assert {j["id"] for j in priya.get("/api/jobs").json()} == {hers}  # the v1 API too
    assert priya.get("/api/stats").json() == {"new": 1}
    assert priya.get("/api/v2/jobs/facets").json()["sources"] == ["lever"]
    assert priya.get("/api/v2/overview").json()["new_this_week"] == 1


def client_total(c):
    return c.get("/api/v2/jobs", params={"view": "all"}).json()["total"]


def test_one_login_cannot_touch_the_others_job_by_guessing_its_id(two_clients, engine):
    tom, priya = two_clients
    mine = add_job(engine, "default", url="https://jobs.lever.co/a/secret")
    for method, path, body in (
        ("get", f"/api/v2/jobs/{mine}", None),
        ("get", f"/api/jobs/{mine}", None),
        ("post", f"/api/v2/jobs/{mine}/verify", None),
        ("put", f"/api/v2/jobs/{mine}/application", {"status": "shortlisted"}),
        ("post", f"/api/v2/jobs/{mine}/tasks", {"kind": "reminder", "title": "x"}),
        ("post", f"/api/v2/jobs/{mine}/feedback", {"category": "excellent_match"}),
        ("get", f"/api/v2/jobs/{mine}/drafts", None),
        ("post", f"/api/v2/jobs/{mine}/drafts", {"body": "hi"}),
        ("post", f"/api/v2/jobs/{mine}/drafts/generate", {}),
        ("patch", f"/api/jobs/{mine}", {"status": "applied"}),
    ):
        r = getattr(priya, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code in (404, 503), (method, path, r.status_code)  # 503: no LLM configured, checked first
    with engine.connect() as conn:  # and nothing was written
        assert conn.execute(sa.select(jobs.c.status).where(jobs.c.id == mine)).scalar() == "new"
        assert conn.execute(sa.text("select count(*) from jobseeker.applications")).scalar() == 0
        assert conn.execute(sa.text("select count(*) from jobseeker.job_feedback")).scalar() == 0


def test_same_posting_can_exist_for_both_people_and_dedupes_per_person(engine):
    url = "https://jobs.lever.co/acme/shared-posting"
    with engine.begin() as conn:
        first = ingest.upsert_listings(conn, [make_listing(url=url)], owner="default")
        hers = ingest.upsert_listings(conn, [make_listing(url=url)], owner="priya")
        again = ingest.upsert_listings(conn, [make_listing(url=url)], owner="priya")
    assert first.new == 1 and hers.new == 1 and again.new == 0 and again.seen_again == 1
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar() == 2


def test_login_config_is_strict(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_EXTRA_USERS", json.dumps(PRIYA))
    assert client.get("/api/v2/overview", auth=("priya", "wrong")).status_code == 401
    assert client.get("/api/v2/overview", auth=("nobody", "pw-priya")).status_code == 401
    assert client.get("/api/v2/overview", auth=("tom", "s3cret")).status_code == 200  # the original login still works
    monkeypatch.setenv("DASHBOARD_EXTRA_USERS", json.dumps([{"username": "x", "password": "y", "account": "Bad Id!"}]))
    assert client.get("/api/v2/overview").status_code == 500  # a bad entry is reported, never silently ignored
    monkeypatch.setenv("DASHBOARD_EXTRA_USERS", "not json")
    assert client.get("/api/v2/overview").status_code == 500


# --------------------------------------------------------------- new accounts

def test_a_new_account_starts_blank_and_never_inherits_the_original_profile(engine):
    with engine.begin() as conn:
        mine = profile_mod.get_or_seed(conn, "default")["data"]
        hers = profile_mod.get_or_seed(conn, "priya")["data"]
        assert conn.execute(sa.select(sa.func.count()).select_from(resumes).where(resumes.c.owner == "priya")).scalar() == 0
    assert mine["excluded_companies"] == ["Oraczen"] and mine["preferred_titles"]  # seeded from config/ as before
    assert hers["name"] == "" and hers["preferred_titles"] == [] and hers["excluded_companies"] == [] and hers["skills"] == []


def test_each_person_is_searched_from_their_own_profile(engine):
    base = __import__("jobseeker.config", fromlist=["load_profile"]).load_profile()
    mine = {**CandidateProfile().model_dump(), "preferred_titles": ["Backend Engineer"], "search_keywords": ["Python"],
            "excluded_companies": ["Oraczen"]}
    hers = CandidateProfile(preferred_titles=["Gameplay Programmer"], skills=[{"name": "Unity"}, {"name": "C#"}],
                            excluded_companies=["Evil Co"], email="p@example.com").model_dump()
    search = profile_mod.discovery_profile(hers, base, "priya")
    assert search["role_focus"] == {"titles": ["Gameplay Programmer"], "keywords_any": ["Unity", "C#"]}  # keywords from skills
    assert search["exclusions"]["companies"] == ["Evil Co"]  # never the original user's employer
    assert search["candidate"]["digest_email"] == "p@example.com" and search["sources"] == base["sources"]
    assert profile_mod.discovery_profile(CandidateProfile().model_dump(), base, "priya") is None  # nothing to search yet
    assert profile_mod.discovery_profile(CandidateProfile().model_dump(), base, "default")["role_focus"] == base["role_focus"]
    assert profile_mod.discovery_profile(mine, base, "default")["role_focus"]["titles"] == ["Backend Engineer"]


def test_collect_targets_everyone_with_a_search(engine, monkeypatch):
    collect = load("run_collect")
    base = collect.config.load_profile()
    set_profile(engine, "priya", preferred_titles=["Gameplay Programmer"])
    set_profile(engine, "lurker")  # no titles: nothing to search for
    assert [o for o, _ in collect.targets(base, None)] == ["default", "priya"]
    assert [o for o, _ in collect.targets(base, ["priya"])] == ["priya"]


# ------------------------------------------------------------------- pipeline

def test_scoring_and_reranking_stay_within_one_persons_jobs(engine):
    mine = add_job(engine, "default", url="https://jobs.lever.co/a/m1")
    hers = add_job(engine, "priya", url="https://jobs.lever.co/a/h1")
    pipeline.score_pending(engine, "priya")
    with engine.connect() as conn:
        scored = {r.id: r.fit_score for r in conn.execute(sa.select(jobs.c.id, jobs.c.fit_score))}
    assert scored[hers] is not None and scored[mine] is None
    with engine.begin() as conn:
        assert preferences.rerank(conn, "priya") == 1 and preferences.rerank(conn, "default") == 0


def test_run_history_shows_a_persons_own_runs_plus_shared_ones(engine):
    with runs.recorded(engine, "collect", "default"):
        pass
    with runs.recorded(engine, "collect", "priya"):
        pass
    with runs.recorded(engine, "verify"):  # covers everyone
        pass
    with engine.connect() as conn:
        assert sorted(r["run_type"] + ":" + str(r["owner"]) for r in runs.list_runs(conn, 10, "priya")) == ["collect:priya", "verify:None"]
        assert len(runs.list_runs(conn, 10)) == 3


# ------------------------------------------------------------- strong matches

def test_only_strong_matches_cross_over_between_people(engine):
    set_profile(engine, "default", preferred_titles=["Backend Engineer"], skills=[{"name": "Python"}, {"name": "FastAPI"}],
                locations=[profile_mod.parse_location_label("Remote (India)")], total_experience_years=5)
    set_profile(engine, "priya", preferred_titles=["Gameplay Programmer"], skills=[{"name": "Unity"}, {"name": "C#"}],
                locations=[profile_mod.parse_location_label("Remote (India)")], total_experience_years=0)
    gameplay = make_listing(url="https://jobs.lever.co/g/1", title="Gameplay Programmer", description="Unity C# gameplay systems", location="Remote - India")
    backend = make_listing(url="https://jobs.lever.co/b/1", title="Backend Engineer", description="Python FastAPI", location="Remote - India")
    with engine.begin() as conn:
        # found while searching for the original user: only the one that fits Priya crosses over
        shared = sharing.share_high_matches(conn, {"default": [gameplay, backend]})
        assert shared == {"priya": 1}
        assert [r.title for r in conn.execute(sa.select(jobs.c.title).where(jobs.c.owner == "priya"))] == ["Gameplay Programmer"]
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.owner == "default")).scalar() == 0
        # not offered twice, and a game job is no match for the backend profile going the other way
        assert sharing.share_high_matches(conn, {"default": [gameplay]}) == {"priya": 0}
        assert sharing.share_high_matches(conn, {"priya": [gameplay]}) == {}


def test_a_hard_excluded_job_is_never_shared(engine):
    set_profile(engine, "priya", preferred_titles=["Gameplay Programmer"], skills=[{"name": "Unity"}],
                locations=[profile_mod.parse_location_label("Remote (India)")], total_experience_years=0)
    senior = make_listing(url="https://jobs.lever.co/g/2", title="Gameplay Programmer", location="Remote - India",
                          description="Unity C#. 8+ years of experience required.")
    with engine.begin() as conn:
        assert sharing.high_matches_for(conn, "priya", [senior]) == []  # needs far more experience than she has


# --------------------------------------------------------------------- digest

def test_each_person_gets_their_own_digest_only(engine, monkeypatch):
    digest = load("run_digest")
    set_profile(engine, "default", email="tom@example.com", preferred_titles=["Backend Engineer"])
    set_profile(engine, "priya", email="priya@example.com", preferred_titles=["Gameplay Programmer"])
    mine = add_job(engine, "default", url="https://jobs.lever.co/d/1", title="Backend Engineer")
    hers = add_job(engine, "priya", url="https://jobs.lever.co/d/2", title="Gameplay Programmer")
    with engine.begin() as conn:
        conn.execute(sa.update(jobs).where(jobs.c.id.in_([mine, hers])).values(status="queued_for_digest", fit_score=80))
    sent = []
    monkeypatch.setattr(digest, "send_email", lambda html, subject, to, frm, pw: sent.append((to, html)))
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setattr(sys, "argv", ["run_digest.py"])
    assert digest.main() == 0
    assert sorted(to for to, _ in sent) == ["priya@example.com", "tom@example.com"]
    by_to = dict(sent)
    assert "Gameplay Programmer" in by_to["priya@example.com"] and "Backend Engineer" not in by_to["priya@example.com"]
    assert "Backend Engineer" in by_to["tom@example.com"] and "Gameplay Programmer" not in by_to["tom@example.com"]
    with engine.connect() as conn:
        assert {r.id: r.status for r in conn.execute(sa.select(jobs.c.id, jobs.c.status))} == {mine: "sent_in_digest", hers: "sent_in_digest"}


def test_score_and_draft_handles_every_person_in_one_run(engine, tmp_path, monkeypatch):
    set_profile(engine, "default", preferred_titles=["Backend Engineer"], skills=[{"name": "Python"}],
                locations=[profile_mod.parse_location_label("Remote (India)")])
    set_profile(engine, "priya", preferred_titles=["Gameplay Programmer"], skills=[{"name": "Unity"}, {"name": "C#"}],
                locations=[profile_mod.parse_location_label("Remote (India)")], total_experience_years=0)
    batch = tmp_path / "collected"
    batch.mkdir()
    for owner, title in (("default", "Backend Engineer"), ("priya", "Gameplay Programmer")):
        listing = make_listing(url=f"https://jobs.lever.co/{owner}/1", title=title, description="Unity C# gameplay" if owner == "priya" else "Python FastAPI")
        (batch / f"{owner}.json").write_text(json.dumps({"listings": [listing.to_dict()], "source_stats": {}, "raw_count": 1}))
    monkeypatch.setattr(sys, "argv", ["score_and_draft.py", "--jobs-dir", str(batch)])
    assert load("score_and_draft").main() == 0
    with engine.connect() as conn:
        assert sorted((r.owner, r.title) for r in conn.execute(sa.select(jobs.c.owner, jobs.c.title))) == [
            ("default", "Backend Engineer"), ("priya", "Gameplay Programmer")]
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs).where(jobs.c.fit_score.is_(None))).scalar() == 0
        assert sorted(r.owner for r in conn.execute(sa.select(runs_table.c.owner).where(runs_table.c.run_type == "collect"))) == ["default", "priya"]
