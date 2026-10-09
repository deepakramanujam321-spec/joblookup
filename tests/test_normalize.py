from datetime import datetime, timezone

import pytest

from jobseeker import normalize as n


@pytest.mark.parametrize("value,expected", [
    ("2026-09-20T00:00:25+00:00", datetime(2026, 9, 20, 0, 0, 25, tzinfo=timezone.utc)),
    ("2026-09-20T00:00:25Z", datetime(2026, 9, 20, 0, 0, 25, tzinfo=timezone.utc)),
    ("Tue, 15 Sep 2026 14:12:47 +0000", datetime(2026, 9, 15, 14, 12, 47, tzinfo=timezone.utc)),
    (1759900000000, datetime.fromtimestamp(1759900000, tz=timezone.utc)),
    ("1759900000", datetime.fromtimestamp(1759900000, tz=timezone.utc)),
])
def test_parse_datetime_formats(value, expected):
    assert n.parse_datetime(value) == expected


@pytest.mark.parametrize("value", ["", None, "yesterday", "not a date", "42"])
def test_parse_datetime_never_invents(value):
    assert n.parse_datetime(value) is None


def test_canonical_url_folds_tracking_and_apply_pages():
    assert n.canonical_url("https://jobs.ashbyhq.com/OpenAI/abc/application?utm_source=x") == "https://jobs.ashbyhq.com/OpenAI/abc"
    assert n.canonical_url("https://www.RemoteOK.com/remote-jobs/x-1/") == "https://remoteok.com/remote-jobs/x-1"
    assert n.canonical_url("https://jobs.lever.co/a/b/apply?lever-source=LinkedIn") == "https://jobs.lever.co/a/b"
    assert n.canonical_url("https://boards.greenhouse.io/x/jobs/1?gh_jid=1&gh_src=abc") == "https://boards.greenhouse.io/x/jobs/1?gh_jid=1"


def test_search_pages_are_not_listings():
    assert not n.is_specific_listing_url("https://in.linkedin.com/jobs/backend-developer-jobs", "linkedin")
    assert n.is_specific_listing_url("https://www.linkedin.com/jobs/view/12345", "linkedin")
    assert not n.is_specific_listing_url("https://in.indeed.com/q-backend-jobs.html", "indeed")
    assert n.is_specific_listing_url("https://in.indeed.com/viewjob?jk=abc", "indeed")


@pytest.mark.parametrize("text,expected", [
    ("$120k - $150k", {"min": 120000, "max": 150000, "currency": "USD", "period": "year"}),
    ("₹18-25 LPA", {"min": 1800000, "max": 2500000, "currency": "INR", "period": "year"}),
    ("120,000 - 150,000 USD per year", {"min": 120000, "max": 150000, "currency": "USD", "period": "year"}),
    ("$60 - $80 per hour", {"min": 60, "max": 80, "currency": "USD", "period": "hour"}),
    ("30 LPA", {"min": 3000000, "max": 3000000, "currency": "INR", "period": "year"}),
])
def test_parse_salary(text, expected):
    assert n.parse_salary(text) == expected


@pytest.mark.parametrize("text", ["", "Competitive", "5+ years", "team of 12", "$120 - $150"])
def test_parse_salary_unknown(text):
    assert n.parse_salary(text) is None


def test_skills_use_word_boundaries():
    skills = n.extract_skills("Python, FastAPI and Postgres. Experience with k8s. Go is a plus. Good communication.")
    assert {"Python", "FastAPI", "PostgreSQL", "Kubernetes", "Go"} <= set(skills)
    assert "Go" not in n.extract_skills("We are going to build good things")
    assert "Java" not in n.extract_skills("JavaScript only")


@pytest.mark.parametrize("title,level", [
    ("Senior Backend Engineer", "senior"), ("Sr. Software Engineer", "senior"), ("Staff Engineer, Platform", "staff"),
    ("Software Engineer II", "mid"), ("SDE III", "senior"), ("Backend Engineer", None), ("Engineering Manager", "manager"),
    ("Junior Developer", "junior"), ("Software Engineering Intern", "intern"),
])
def test_seniority(title, level):
    assert n.detect_seniority(title) == level


@pytest.mark.parametrize("title,category", [
    ("Senior Backend Engineer", "backend"), ("AI Platform Engineer", "ai_ml"), ("Platform Engineer", "platform"),
    ("Customer Support Engineer", "support"), ("Solutions Architect", "solutions"), ("Software Engineer", "backend"),
    ("Frontend Engineer", "frontend"), ("Site Reliability Engineer", "devops_sre"),
])
def test_category(title, category):
    assert n.detect_category(title) == category


def test_experience_takes_smallest_minimum():
    years, phrase = n.extract_experience("You have 5+ years of backend experience; 2 years of Go experience is a plus")
    assert years == 2 and "experience" in phrase
    assert n.extract_experience("A team of 40 engineers") == (None, None)


def test_sanitize_html_keeps_structure_drops_scripts():
    out = n.sanitize_html('<h2>About</h2><ul><li>Python</li></ul><script>alert(1)</script><a href="javascript:x" onclick="y">l</a>')
    assert "<h2>About</h2>" in out and "<li>Python</li>" in out
    assert "script" not in out and "javascript" not in out and "onclick" not in out
    assert n.sanitize_html("&lt;p&gt;Escaped&lt;/p&gt;") == "<p>Escaped</p>"  # Greenhouse double-escaping


def test_dedupe_key_is_strict():
    assert n.dedupe_key("Acme, Inc.", "Backend Engineer", "Remote") == n.dedupe_key("acme inc", "backend engineer", "remote")
    assert n.dedupe_key("Acme", "Backend Engineer", "Remote") != n.dedupe_key("Acme", "Senior Backend Engineer", "Remote")


def test_employment_type_vocabularies():
    assert n.normalize_employment_type("FullTime") == "full_time"
    assert n.normalize_employment_type("FULL_TIME") == "full_time"
    assert n.normalize_employment_type("Contract") == "contract"
    assert n.normalize_employment_type(None) is None
