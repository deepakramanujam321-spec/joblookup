from datetime import datetime, timezone

import sqlalchemy as sa

from conftest import make_listing
from jobseeker import ats, ingest, verification
from jobseeker.database import job_sources, job_verifications, jobs


class FakeResp:
    def __init__(self, status, payload=None, text="", url=""):
        self.status_code, self._payload, self.text, self.url = status, payload, text, url

    def json(self):
        return self._payload


def test_parse_ats_urls():
    assert ats.parse_ats_url("https://boards.greenhouse.io/paypay/jobs/4089237004") == ats.AtsRef("greenhouse", "paypay", "4089237004")
    assert ats.parse_ats_url("https://job-boards.greenhouse.io/acme/jobs/12?gh_src=x").job_id == "12"
    ref = ats.parse_ats_url("https://jobs.ashbyhq.com/openai/8e301350-62fb-4251-bc34-c7036498f08c/application")
    assert (ref.source, ref.board, ref.job_id) == ("ashby", "openai", "8e301350-62fb-4251-bc34-c7036498f08c")
    assert ats.parse_ats_url("https://jobs.lever.co/qonto/a2a71652-40f6-4c1a-8ae1-90127e0ce1cf").source == "lever"
    assert ats.parse_ats_url("https://jobs.lever.co/qonto").job_id is None
    assert ats.parse_ats_url("https://remoteok.com/remote-jobs/x") is None


def test_greenhouse_api_mapping(monkeypatch):
    payload = {"id": 42, "title": "Backend Engineer", "updated_at": "2026-10-01T10:00:00-04:00",
               "first_published": "2026-09-01T09:00:00-04:00", "location": {"name": "Bengaluru, India"},
               "absolute_url": "https://boards.greenhouse.io/acme/jobs/42", "company_name": "Acme",
               "content": "&lt;h2&gt;About&lt;/h2&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"}
    monkeypatch.setattr(ats, "_get", lambda url, params=None: FakeResp(200, payload))
    result = ats.fetch_greenhouse("acme", "42")
    job = result.job
    assert job.posted_at == "2026-09-01T09:00:00-04:00" and job.posted_at_evidence == "greenhouse_api.first_published"
    assert job.updated_at == "2026-10-01T10:00:00-04:00"  # kept separate, never shown as posting date
    assert "<h2>About</h2>" in job.description_html and "Python" in job.description_text
    assert job.company == "Acme" and job.remote_type == "onsite"


def test_api_404_is_closed_but_errors_are_not(monkeypatch):
    monkeypatch.setattr(ats, "_get", lambda url, params=None: FakeResp(404))
    assert ats.fetch_lever("acme", "x").closed is True
    monkeypatch.setattr(ats, "_get", lambda url, params=None: FakeResp(503))
    result = ats.fetch_lever("acme", "x")
    assert result.closed is False and result.error == "HTTP 503"


def test_ashby_job_missing_from_loaded_board_is_closed(monkeypatch):
    board = {"jobs": [{"id": "keep", "title": "Platform Engineer", "publishedAt": "2026-09-30T00:00:00Z",
                       "location": "Remote", "isRemote": True, "employmentType": "FullTime", "descriptionHtml": "<p>x</p>",
                       "compensation": {"summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR",
                                                               "currencyCode": "USD", "minValue": 150000, "maxValue": 190000}]}}]}
    monkeypatch.setattr(ats, "_get", lambda url, params=None: FakeResp(200, board))
    cache = ats.AshbyBoardCache()
    live = cache.fetch("acme", "keep").job
    assert live.employment_type == "full_time" and live.salary["max"] == 190000 and live.remote_type == "remote"
    assert cache.fetch("acme", "gone").closed is True


def test_ingest_dedupe_rules(engine):
    with engine.begin() as conn:
        first = make_listing(url="https://jobs.lever.co/acme/1", title="Backend Engineer", company="Acme", location="Remote")
        stats = ingest.upsert_listings(conn, [first])
        assert stats.new == 1
        # same posting via tracking params -> seen again, not new
        again = make_listing(url="https://jobs.lever.co/acme/1?lever-source=LinkedIn", title="Backend Engineer", company="Acme", location="Remote")
        # same vacancy from another source -> merged with provenance
        other_source = make_listing(source="weworkremotely", url="https://weworkremotely.com/remote-jobs/acme-backend",
                                    title="Backend Engineer", company="Acme, Inc.", location="Remote", external_id="")
        # same source, different external id, identical title -> a separate vacancy
        sibling = make_listing(url="https://jobs.lever.co/acme/2", title="Backend Engineer", company="Acme", location="Remote", external_id="2")
        search_page = make_listing(source="linkedin", url="https://in.linkedin.com/jobs/backend-jobs")
        stats = ingest.upsert_listings(conn, [again, other_source, sibling, search_page])
        assert (stats.seen_again, stats.merged_duplicates, stats.new, stats.rejected) == (1, 1, 1, 1)
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar() == 2
        provenance = conn.execute(sa.select(job_sources.c.source).where(job_sources.c.job_id == stats.new_ids[0] - 1)).scalars().all()
        assert sorted(provenance) == ["lever", "weworkremotely"]


def test_resighting_fills_gaps_without_overwriting(engine):
    with engine.begin() as conn:
        ingest.upsert_listings(conn, [make_listing(url="https://jobs.lever.co/acme/9", salary_text="", location="Remote - India")])
        ingest.upsert_listings(conn, [make_listing(url="https://jobs.lever.co/acme/9", salary_text="$100k-$120k", location="Somewhere else")])
        row = conn.execute(sa.select(jobs)).mappings().one()
    assert row["salary_max"] == 120000 and row["location"] == "Remote - India"


def test_posting_date_requires_evidence(engine):
    with engine.begin() as conn:
        ingest.upsert_listings(conn, [make_listing(url="https://jobs.lever.co/a/x", posted_at="", posted_at_evidence="")])
        row = conn.execute(sa.select(jobs.c.posted_at_ts, jobs.c.posted_at_evidence, jobs.c.discovered_at)).one()
    assert row.posted_at_ts is None and row.posted_at_evidence is None and row.discovered_at is not None


def _job(engine):
    with engine.connect() as conn:
        return dict(conn.execute(sa.select(jobs)).mappings().one())


def test_transient_failures_never_close(engine, make_job):
    make_job(url="https://jobs.lever.co/acme/v1")
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        verification.apply_check(conn, _job(engine), verification.Check("active", "lever_api", 200), now)
    for _ in range(2):
        with engine.begin() as conn:
            status = verification.apply_check(conn, _job(engine), verification.Check("verification_failed", "lever_api", 503, "HTTP 503"), now)
        assert status == "active"
    with engine.begin() as conn:
        status = verification.apply_check(conn, _job(engine), verification.Check("verification_failed", "lever_api", 503), now)
    assert status == "verification_failed"
    job = _job(engine)
    assert job["closed_at"] is None and job["last_verified_at"] is not None
    with engine.begin() as conn:
        assert verification.apply_check(conn, job, verification.Check("closed", "lever_api", 404), now) == "closed"
        assert conn.execute(sa.select(sa.func.count()).select_from(job_verifications)).scalar() == 5


def test_page_check_outcomes(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp(404, url="https://x.com/jobs/1"))
    assert verification.check_page("https://x.com/jobs/1").result == "closed"
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp(200, text="This position has been filled", url="https://x.com/jobs/1"))
    assert verification.check_page("https://x.com/jobs/1").result == "closed"
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp(200, text="Apply now", url="https://x.com/"))
    assert verification.check_page("https://x.com/jobs/1").result == "closed"
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp(429, url="https://x.com/jobs/1"))
    assert verification.check_page("https://x.com/jobs/1").result == "source_unavailable"

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "get", boom)
    assert verification.check_page("https://x.com/jobs/1").result == "verification_failed"


def test_enrich_marks_search_pages_and_uses_api(engine, monkeypatch):
    with engine.begin() as conn:
        conn.execute(sa.insert(jobs).values(source="linkedin", url="https://in.linkedin.com/jobs/backend-jobs",
                                            title="Backend jobs", company="", status="scored", enrichment_version=0))
        conn.execute(sa.insert(jobs).values(source="lever", url="https://jobs.lever.co/acme/aaaaaaaa-0000-0000-0000-000000000000",
                                            title="Backend", company="Acme", status="scored", enrichment_version=0,
                                            description="scraped page text"))
    monkeypatch.setattr(ats, "_get", lambda url, params=None: FakeResp(200, {
        "id": "aaaaaaaa-0000-0000-0000-000000000000", "text": "Senior Backend Engineer", "createdAt": 1759000000000,
        "categories": {"location": "Bengaluru", "commitment": "Full-time"}, "workplaceType": "hybrid",
        "description": "<p>Python and Kafka</p>", "lists": [{"text": "Requirements", "content": "<li>5+ years of experience</li>"}],
        "hostedUrl": "https://jobs.lever.co/acme/aaaaaaaa-0000-0000-0000-000000000000"}))
    stats = verification.enrich_pending(engine, delay=0)
    assert stats["not_a_listing"] == 1 and stats["api_enriched"] == 1
    with engine.connect() as conn:
        rows = {r["source"]: r for r in conn.execute(sa.select(jobs)).mappings()}
    assert rows["linkedin"]["listing_quality"] == "not_a_listing"
    lever = rows["lever"]
    assert lever["posted_at_evidence"] == "lever_api.createdAt" and lever["posted_at_ts"] is not None
    assert lever["description_is_partial"] is False and "<h3>Requirements</h3>" in lever["description_html"]
    assert lever["employment_type"] == "full_time" and lever["remote_type"] == "hybrid" and lever["experience_min_years"] == 5
    assert lever["verification_status"] == "active" and lever["company"] == "Acme"
