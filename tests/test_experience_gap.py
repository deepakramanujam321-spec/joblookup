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
