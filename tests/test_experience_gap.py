"""Years of experience: a posting asking for well beyond what you have is
out of reach (capped, not AI-reviewed, kept out of Recommended); a small
stretch or an unstated requirement is never penalised that way."""

from jobseeker import assessment, matching
from test_matching_learning import PROFILE, job

STRONG = {"score": 92, "rationale": "Great fit", "strengths": [], "gaps": []}
TWO_YEARS = {**PROFILE, "total_experience_years": 2}
NOT_SET = {**PROFILE, "total_experience_years": None}


def test_requirement_well_beyond_experience_caps_fit_and_skips_ai():
    result = matching.assess(job(experience_min_years=5), TWO_YEARS, "", STRONG)
    assert result["overall_score"] == matching.DEAL_BREAKER_CAP
    assert result["deal_breakers"] == ["Experience gap: asks for 5+ years, you have 2"]
    assert sum("5+ years" in g for g in result["gaps"]) == 1  # reported once
    assert not assessment.should_run_semantic(job(experience_min_years=6), TWO_YEARS, result)


def test_stretch_unstated_and_unknown_are_not_ruled_out():
    for years, profile, status in ((3, TWO_YEARS, "stretch"), (2, TWO_YEARS, "meets"),
                                   (None, TWO_YEARS, "not_stated"), (5, NOT_SET, "unknown")):
        result = matching.assess(job(experience_min_years=years), profile, "", STRONG)
        assert matching.experience_gap(job(experience_min_years=years), profile)["status"] == status
        assert not result["deal_breakers"] and result["overall_score"] > matching.DEAL_BREAKER_CAP


def test_hard_exclude_covers_company_phrase_and_experience_but_never_salary():
    """hard_exclude drives auto-dismissal everywhere the user browses (see
    queries._not_excluded); it must never fire for salary alone, since an
    unstated or below-floor salary stays visible with just a badge."""
    excluded_company = job(company="Oraczen Labs")
    deal_breaker = {**PROFILE, "deal_breakers": ["on-call"]}
    below_floor_salary = job(salary_min=100_000, salary_max=150_000, salary_currency="INR", salary_period="year")
    assert matching.hard_exclude(excluded_company, PROFILE)
    assert matching.hard_exclude(job(title="Needs on-call rotation"), deal_breaker)
    assert matching.hard_exclude(job(experience_min_years=8), TWO_YEARS)
    assert not matching.hard_exclude(below_floor_salary, PROFILE)
    assert not matching.hard_exclude(job(experience_min_years=3), TWO_YEARS)  # stretch, not excluded
    assert not matching.hard_exclude(job(), PROFILE)
