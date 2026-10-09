"""Your minimum salary: a stated figure below it rules a job out; an
unstated one never counts against it."""

from jobseeker import matching
from jobseeker.filters import meets_salary_floor
from test_matching_learning import PROFILE, job

STRONG = {"score": 92, "rationale": "Great fit", "strengths": [], "gaps": []}


def test_stated_pay_below_minimum_caps_fit():
    result = matching.assess(job(salary_min=1_000_000, salary_max=1_400_000, salary_currency="INR", salary_period="year"), PROFILE, "", STRONG)
    assert result["overall_score"] == matching.BELOW_SALARY_FLOOR_CAP
    assert any("₹14 LPA vs ₹18 LPA" in b for b in result["deal_breakers"])
    assert sum("below your minimum" in g.lower() for g in result["gaps"]) == 1  # reported once


def test_pay_meeting_minimum_helps_and_unknown_is_neutral():
    meets = matching.assess(job(salary_min=2_000_000, salary_max=3_000_000, salary_currency="INR", salary_period="year"), PROFILE, "", STRONG)
    unknown = matching.assess(job(), PROFILE, "", STRONG)
    assert meets["overall_score"] > matching.BELOW_SALARY_FLOOR_CAP and not meets["deal_breakers"]
    assert unknown["components"]["compensation"]["score"] is None and not unknown["deal_breakers"]
    assert unknown["overall_score"] > 70


def test_estimated_foreign_currency_never_rules_out_on_its_own():
    result = matching.assess(job(salary_min=10_000, salary_max=15_000, salary_currency="USD", salary_period="year"), PROFILE, "", STRONG)
    assert result["components"]["compensation"]["score"] == 15 and not result["deal_breakers"]


def test_collection_filter_reads_any_rupee_format():
    assert not meets_salary_floor("12 LPA", 18)
    assert not meets_salary_floor("₹8-12 lakh", 18)
    assert not meets_salary_floor("₹12,00,000 per annum", 18)
    assert meets_salary_floor("₹18-25 LPA", 18)
    assert meets_salary_floor("$90k - $120k", 18)  # other currencies are scored, not dropped
    assert meets_salary_floor("Competitive", 18)
    assert meets_salary_floor("", 18)


def test_salary_status_for_badges():
    from jobseeker.assessment import salary_status

    assert salary_status(job(), {"score": None}) == "not_stated"
    assert salary_status(job(salary_min=2e6, salary_max=3e6), {"score": 100}) == "meets"
    assert salary_status(job(salary_min=1e6, salary_max=1.4e6), {"score": 15}) == "below"
    assert salary_status(job(salary_text="Competitive"), {"score": None}) == "stated"
