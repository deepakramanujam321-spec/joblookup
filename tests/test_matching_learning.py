from datetime import datetime, timedelta, timezone

import pytest

from jobseeker import learning, lifecycle, matching, priority
from jobseeker.profile import parse_location_label

PROFILE = {
    "preferred_titles": ["Backend Engineer", "AI Platform Engineer"],
    "skills": [{"name": "Python"}, {"name": "FastAPI"}, {"name": "PostgreSQL"}],
    "locations": [parse_location_label(l).model_dump() for l in [
        "Remote (India)", "Hyderabad (on-site/hybrid)", "Bengaluru (on-site/hybrid)", "Remote (global, India-friendly)"]],
    "compensation": {"currency": "INR", "min_annual": 1_800_000},
    "seniority_levels": ["mid", "senior"],
    "excluded_companies": ["Oraczen"],
    "total_experience_years": 4,
}


def job(**kw):
    base = {"id": 1, "title": "Senior Backend Engineer", "company": "Acme", "location": "Remote - India", "remote_type": "remote",
            "skills": ["Python", "FastAPI", "Kubernetes"], "seniority": "senior", "job_category": "backend",
            "experience_min_years": 3, "salary_min": None, "salary_max": None, "description": "python"}
    base.update(kw)
    return base


def test_location_preferences_parse():
    labels = {p["label"]: p for p in PROFILE["locations"]}
    assert labels["Remote (India)"]["mode"] == "remote" and labels["Remote (India)"]["places"] == ["india"]
    assert "bangalore" in labels["Bengaluru (on-site/hybrid)"]["places"]
    assert labels["Remote (global, India-friendly)"]["places"] == []


@pytest.mark.parametrize("location,remote,expected", [
    ("Remote - India", "remote", 100), ("Hyderabad, India", "hybrid", 90), ("Bangalore", "onsite", 80),
    ("Remote", "remote", 70), ("Remote - US only", "remote", 15), ("San Francisco, CA", "onsite", 15),
])
def test_location_rank_scores(location, remote, expected):
    assert matching.score_location(job(location=location, remote_type=remote), PROFILE).score == expected


def test_unknowns_are_not_negative():
    result = matching.assess(job(location="", remote_type=None, skills=[], seniority=None, experience_min_years=None), PROFILE, "")
    comps = result["components"]
    assert comps["location"]["score"] is None and comps["skills"]["score"] is None and comps["compensation"]["score"] is None
    assert result["confidence"] < 0.5
    # overall is the mean of judged components only (role matched -> high), not dragged down by unknowns
    assert result["overall_score"] >= 80


def test_skill_breakdown_and_gap_language():
    result = matching.assess(job(), PROFILE, "")
    skills = result["components"]["skills"]
    assert skills["matches"] == ["Python", "FastAPI"] and skills["gaps"] == ["Kubernetes"]
    assert any("Kubernetes" in g and "not documented" in g for g in result["gaps"])
    assert any(s.startswith("Strong match: Python, FastAPI") for s in result["strengths"])


def test_salary_compared_only_when_stated():
    assert matching.score_compensation(job(), PROFILE).score is None
    assert matching.score_compensation(job(salary_min=1_000_000, salary_max=1_400_000, salary_currency="INR", salary_period="year"), PROFILE).score == 15
    usd = matching.score_compensation(job(salary_min=90_000, salary_max=120_000, salary_currency="USD", salary_period="year"), PROFILE)
    assert usd.score == 100 and "estimated" in usd.detail


def test_excluded_company_zeroes_fit():
    result = matching.assess(job(company="Oraczen Labs"), PROFILE, "")
    assert result["overall_score"] == 0 and result["deal_breakers"]


def test_semantic_component_weighted_in():
    low = matching.assess(job(), PROFILE, "", {"score": 10, "rationale": "poor", "strengths": [], "gaps": []})
    high = matching.assess(job(), PROFILE, "", {"score": 95, "rationale": "great", "strengths": [], "gaps": []})
    # weights renormalise over judged components (compensation + domain unknown here: 0.95 of total)
    assert high["overall_score"] - low["overall_score"] == pytest.approx(85 * 0.35 / 0.95, abs=0.2)


def test_priority_separates_fit_recency_freshness():
    now = datetime.now(timezone.utc)
    fresh = {"posted_at_ts": now - timedelta(days=1), "verification_status": "active", "last_verified_at": now, "listing_quality": "ok"}
    stale = {"posted_at_ts": now - timedelta(days=60), "verification_status": "active", "last_verified_at": now - timedelta(days=30), "listing_quality": "ok"}
    closed = {**fresh, "verification_status": "closed"}
    p_fresh, exp_fresh = priority.compute(fresh, 70, {}, [], 14, now)
    p_stale, exp_stale = priority.compute(stale, 70, {}, [], 14, now)
    p_closed, _ = priority.compute(closed, 90, {}, [], 14, now)
    assert p_fresh == 78 and p_stale == 59 and p_closed == 0
    assert exp_fresh["freshness"]["state"] == "verified" and exp_stale["freshness"]["state"] == "possibly_stale"
    unknown_date, exp = priority.compute({"verification_status": "unchecked", "listing_quality": "ok"}, 70, {}, [], 14, now)
    assert unknown_date == 70 and exp["recency"]["state"] == "unknown"


SKILLS = {"Python", "FastAPI", "PostgreSQL"}


def fb(category, **job_kw):
    j = job(**job_kw)
    return {"kind": "feedback", "category": category, "signals": learning.job_signals(j, SKILLS)}


def test_single_feedback_never_activates():
    prefs = learning.compute_preferences([fb("wrong_category", title="Support Engineer", job_category="support")], PROFILE, SKILLS)
    assert prefs and not any(p["active"] for p in prefs)


def test_repeated_feedback_activates_only_its_dimension():
    events = [fb("wrong_category", title="Support Engineer", job_category="support") for _ in range(3)]
    prefs = {(p["dimension"], p["value"]): p for p in learning.compute_preferences(events, PROFILE, SKILLS)}
    support = prefs[("category", "support")]
    assert support["active"] and support["weight"] < 0
    assert ("category", "backend") not in prefs  # no contamination of other categories
    assert not any(d == "seniority" for d, _ in prefs)


def test_explicit_preference_beats_learned_negative():
    events = [fb("wrong_seniority", seniority="senior") for _ in range(4)]
    pref = next(p for p in learning.compute_preferences(events, PROFILE, SKILLS) if p["dimension"] == "seniority")
    assert pref["weight"] < 0 and not pref["active"] and "profile explicitly asks" in pref["explanation"]


def test_wrong_stack_only_penalises_unfamiliar_skills():
    events = [fb("wrong_stack", skills=["Python", "Scala"]) for _ in range(2)]
    prefs = {(p["dimension"], p["value"]) for p in learning.compute_preferences(events, PROFILE, SKILLS)}
    assert ("skill", "Scala") in prefs and ("skill", "Python") not in prefs


def test_user_disable_is_respected():
    events = [fb("company_mismatch", company="BigCo") for _ in range(3)]
    prefs = learning.compute_preferences(events, PROFILE, SKILLS, {("company", "bigco"): True})
    assert prefs[0]["disabled_by_user"]
    _, reasons = priority.learned_adjustment({"company": ["bigco"]}, prefs)
    assert reasons == []


def test_evaluation_uses_leave_one_out():
    labelled = []
    for i in range(4):
        j = job(id=i, title="Support Engineer", job_category="support", company=f"S{i}")
        labelled.append({"job_id": i, "label": 0, "base_score": 72, "signals": learning.job_signals(j, SKILLS),
                         "event": {"kind": "feedback", "category": "wrong_category", "signals": learning.job_signals(j, SKILLS)}})
    for i in range(4, 8):
        j = job(id=i, company=f"B{i}")
        labelled.append({"job_id": i, "label": 1, "base_score": 70, "signals": learning.job_signals(j, SKILLS),
                         "event": {"kind": "feedback", "category": "excellent_match", "signals": learning.job_signals(j, SKILLS)}})
    result = learning.evaluate(labelled, PROFILE, SKILLS)
    assert result["status"] == "ok" and result["auc_without_learning"] == 0.0 and result["auc_with_learning"] == 1.0
    assert learning.evaluate(labelled[:2], PROFILE, SKILLS)["status"] == "insufficient_data"


def test_lifecycle_rules():
    lifecycle.validate("needs_review", "shortlisted", False)
    lifecycle.validate("applied", "interview", True)
    with pytest.raises(lifecycle.TransitionError):
        lifecycle.validate("shortlisted", "interview", False)  # can't interview without applying
    with pytest.raises(lifecycle.TransitionError):
        lifecycle.validate("dismissed", "offer", False)
    with pytest.raises(lifecycle.TransitionError):
        lifecycle.validate("needs_review", "bogus", False)
    lifecycle.validate("dismissed", "needs_review", False)  # restore
